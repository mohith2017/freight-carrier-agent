from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from freight_agent.agent import build_agent
from freight_agent.api import app as app_module
from freight_agent.api.app import app
from freight_agent.api.deps import Resources, get_resources
from freight_agent.config import Settings, get_settings
from freight_agent.db import init_schema, make_engine, session_factory
from freight_agent.db.models import Carrier, Load
from freight_agent.ingestion.loaders import load_all

_DIM = 32


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * _DIM
            for tok in t.lower().split():
                vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % _DIM] += 1.0
            out.append(vec)
        return out


@pytest.fixture(scope="module")
def resources(tmp_path_factory: pytest.TempPathFactory) -> Resources:
    db_path = tmp_path_factory.mktemp("apidb") / "api.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_schema(engine)
    load_all(engine, get_settings().dataset_path)
    factory: sessionmaker = session_factory(engine)
    settings = Settings(openai_api_key="test-key")
    return Resources(
        settings=settings,
        session_factory=factory,
        embedder=FakeEmbedder(),
        agent=build_agent(),
    )


@pytest.fixture()
def client(resources: Resources) -> Iterator[TestClient]:
    app.dependency_overrides[get_resources] = lambda: resources
    app_module._rl_hits.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def sample(resources: Resources) -> dict:
    with resources.session_factory() as s:
        load = s.scalars(
            select(Load).where(
                Load.origin_state.isnot(None),
                Load.destination_state.isnot(None),
                Load.equipment_type.isnot(None),
            )
        ).first()
        carrier = s.scalars(
            select(Carrier).where(Carrier.mc_number.isnot(None))
        ).first()
        assert load is not None and carrier is not None
        return {
            "load_id": load.load_id,
            "origin": load.origin_state,
            "destination": load.destination_state,
            "equipment": load.equipment_type,
            "carrier_id": carrier.carrier_id,
            "mc": carrier.mc_number,
            "company": carrier.company_name,
        }


def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "x-request-id" in r.headers


def test_get_load_and_404(client: TestClient, sample: dict) -> None:
    r = client.get(f"/loads/{sample['load_id']}")
    assert r.status_code == 200
    assert r.json()["load_id"] == sample["load_id"]
    assert client.get("/loads/00000000").status_code == 404


def test_resolve_carrier_by_mc(client: TestClient, sample: dict) -> None:
    r = client.get("/carriers/resolve", params={"q": f"MC {sample['mc']}"})
    assert r.status_code == 200
    assert r.json()["carrier_id"] == sample["carrier_id"]
    assert client.get("/carriers/resolve", params={"q": "zzz nobody"}).status_code == 404


def test_carrier_history(client: TestClient, sample: dict) -> None:
    r = client.get(f"/carriers/{sample['carrier_id']}/history")
    assert r.status_code == 200
    assert r.json()["carrier_id"] == sample["carrier_id"]
    assert client.get("/carriers/999999/history").status_code == 404


def test_rate_context(client: TestClient, sample: dict) -> None:
    r = client.get(
        "/rates/context",
        params={
            "origin": sample["origin"],
            "destination": sample["destination"],
            "equipment": sample["equipment"],
            "flat_usd": 900,
            "distance_miles": 300,
        },
    )
    assert r.status_code == 200
    assert r.json()["lane"] == f"{sample['origin']}->{sample['destination']}"


def test_query_sync_with_testmodel(client: TestClient, resources: Resources) -> None:
    with resources.agent.override(model=TestModel()):
        r = client.post("/query/sync", json={"question": "best rate for load 29372289?"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["answer"], str)
    assert isinstance(body["tool_calls"], list)
    assert body["tool_calls"], "agent should have exercised at least one tool"


def test_query_stream_emits_result_event(
    client: TestClient, resources: Resources
) -> None:
    with resources.agent.override(model=TestModel()):
        with client.stream(
            "POST", "/query", json={"question": "carriers from PA to NJ?"}
        ) as r:
            assert r.status_code == 200
            raw = "".join(chunk for chunk in r.iter_text())
    assert "event: result" in raw
    assert "event: done" in raw
    lines = raw.splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.strip() == "event: result")
    data_line = next(ln for ln in lines[idx + 1 :] if ln.startswith("data:"))
    payload = json.loads(data_line.removeprefix("data:").strip())
    assert "answer" in payload and "tool_calls" in payload


def test_rate_limit_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app_module, "get_settings", lambda: Settings(api_rate_limit_per_min=2)
    )
    app_module._rl_hits.clear()
    codes = [client.get("/loads/00000000").status_code for _ in range(4)]
    assert 429 in codes
