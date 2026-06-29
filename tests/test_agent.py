from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import Engine, select

from freight_agent.config import get_settings
from freight_agent.db import init_schema, make_engine, session_factory
from freight_agent.db.models import Carrier, CommEvent, KnowledgeChunk, Load, Offer
from freight_agent.ingestion.loaders import load_all
from freight_agent.retrieval import cosine, looks_structured
from freight_agent.tools import (
    best_offer_for_load,
    carriers_available_for_lane,
    compliance_flags,
    get_load,
    get_rate_context,
    resolve_carrier,
    search_comms,
)

from .conftest import dataset_available

_DIM = 32


class FakeEmbedder:

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * _DIM
            for tok in t.lower().split():
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16) % _DIM
                vec[h] += 1.0
            out.append(vec)
        return out


@dataclass
class Fixture:
    engine: Engine
    load_id: str
    carrier_id: int
    mc_number: str
    origin_state: str
    destination_state: str
    equipment_type: str
    company_name: str


@pytest.fixture(scope="module")
def comms(tmp_path_factory: pytest.TempPathFactory) -> Fixture:
    if not dataset_available():
        pytest.skip("raw dataset not present (set DATASET_DIR to run dataset-backed tests)")
    settings = get_settings()
    db_path = tmp_path_factory.mktemp("agentdb") / "comms.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_schema(engine)
    load_all(engine, settings.dataset_path)

    embedder = FakeEmbedder()
    Session = session_factory(engine)
    with Session() as s:
        load = s.scalars(
            select(Load).where(
                Load.distance_miles.isnot(None),
                Load.offered_rate_usd.isnot(None),
                Load.origin_state.isnot(None),
                Load.destination_state.isnot(None),
                Load.equipment_type.isnot(None),
            )
        ).first()
        assert load is not None
        carrier = s.scalars(
            select(Carrier).where(Carrier.mc_number.isnot(None)).limit(1)
        ).first()
        assert carrier is not None

        text = (
            f"Available. {load.equipment_type}. MC {carrier.mc_number}. "
            f"Load #{load.load_id}."
        )
        event = CommEvent(
            source_type="email",
            source_id="email_test_1",
            carrier_id=carrier.carrier_id,
            load_id=load.load_id,
            normalized_text=text,
            extracted={"intent": "confirm", "available": True},
            raw_payload={"from_email": carrier.email},
        )
        s.add(event)
        s.flush()

        s.add_all(
            [
                Offer(
                    event_id=event.event_id,
                    carrier_id=carrier.carrier_id,
                    load_id=load.load_id,
                    quoted_rate_usd=900.0,
                    rate_type="all_in",
                    intent="confirm",
                    equipment_type=load.equipment_type,
                    availability_date=date(2026, 4, 1),
                ),
                Offer(
                    event_id=event.event_id,
                    carrier_id=carrier.carrier_id,
                    load_id=load.load_id,
                    quoted_rate_usd=800.0,
                    rate_type="all_in",
                    intent="counter",
                    equipment_type=load.equipment_type,
                ),
            ]
        )
        s.add_all(
            [
                KnowledgeChunk(
                    event_id=event.event_id,
                    chunk_type="email",
                    text=text,
                    meta={
                        "event_id": event.event_id,
                        "source_type": "email",
                        "source_id": "email_test_1",
                        "carrier_id": carrier.carrier_id,
                        "load_id": load.load_id,
                        "equipment_type": load.equipment_type,
                    },
                    embedding=embedder.embed([text])[0],
                ),
                KnowledgeChunk(
                    event_id=event.event_id,
                    chunk_type="email",
                    text="Unrelated note about a refrigerated lane in Texas.",
                    meta={"event_id": event.event_id, "source_type": "email"},
                    embedding=embedder.embed(
                        ["Unrelated note about a refrigerated lane in Texas."]
                    )[0],
                ),
            ]
        )
        s.commit()

        return Fixture(
            engine=engine,
            load_id=load.load_id,
            carrier_id=carrier.carrier_id,
            mc_number=carrier.mc_number or "",
            origin_state=load.origin_state or "",
            destination_state=load.destination_state or "",
            equipment_type=load.equipment_type or "",
            company_name=carrier.company_name or "",
        )


@pytest.fixture
def sess(comms: Fixture):
    Session = session_factory(comms.engine)
    with Session() as s:
        yield s


def test_get_load_returns_lane_and_per_mile(comms: Fixture, sess) -> None:
    info = get_load(sess, comms.load_id)
    assert info is not None
    assert info.load_id == comms.load_id
    assert info.lane == f"{comms.origin_state}->{comms.destination_state}"
    assert info.offered_rate_per_mile is not None


def test_get_load_strips_hash_and_missing_returns_none(comms: Fixture, sess) -> None:
    assert get_load(sess, f"#{comms.load_id}") is not None
    assert get_load(sess, "00000000") is None


def test_resolve_carrier_by_mc(comms: Fixture, sess) -> None:
    info = resolve_carrier(sess, f"MC {comms.mc_number}")
    assert info is not None
    assert info.carrier_id == comms.carrier_id
    assert info.match_method == "mc"


def test_resolve_carrier_fuzzy_name(comms: Fixture, sess) -> None:
    info = resolve_carrier(sess, comms.company_name)
    assert info is not None
    assert info.carrier_id == comms.carrier_id


def test_compliance_flags_catches_authority_and_insurance() -> None:
    bad = Carrier(
        carrier_id=999,
        authority_status="CONDITIONAL",
        insurance_expiry=date(2020, 1, 1),
        onboarded=False,
    )
    flags = compliance_flags(bad, as_of=date(2026, 6, 1))
    assert any("authority" in f for f in flags)
    assert any("insurance" in f for f in flags)
    assert "not_onboarded" in flags

    good = Carrier(
        carrier_id=1,
        authority_status="ACTIVE",
        insurance_expiry=date(2030, 1, 1),
        onboarded=True,
    )
    assert compliance_flags(good, as_of=date(2026, 6, 1)) == []


def test_get_rate_context_aggregates_and_judges(comms: Fixture, sess) -> None:
    ctx = get_rate_context(
        sess,
        comms.origin_state,
        comms.destination_state,
        comms.equipment_type,
        flat_usd=900.0,
        distance_miles=300,
    )
    assert ctx.lane == f"{comms.origin_state}->{comms.destination_state}"
    if ctx.avg_rate_per_mile is not None:
        assert ctx.market_position in {"below", "near", "above"}
        assert ctx.quoted_per_mile is not None


def test_best_offer_picks_lowest_quote(comms: Fixture, sess) -> None:
    offer = best_offer_for_load(sess, comms.load_id)
    assert offer is not None
    assert offer.quoted_rate_usd == 800.0
    assert offer.carrier_id == comms.carrier_id


def test_carriers_available_for_lane(comms: Fixture, sess) -> None:
    rows = carriers_available_for_lane(
        sess, comms.origin_state, comms.destination_state, comms.equipment_type
    )
    assert any(r.carrier_id == comms.carrier_id for r in rows)


def test_search_comms_ranks_relevant_chunk_first(comms: Fixture, sess) -> None:
    qvec = FakeEmbedder().embed([f"availability for load {comms.load_id}"])[0]
    hits = search_comms(sess, f"availability for load {comms.load_id}", qvec, limit=2)
    assert hits
    assert hits[0].load_id == comms.load_id
    assert hits[0].score >= hits[-1].score


def test_search_comms_metadata_filter(comms: Fixture, sess) -> None:
    hits = search_comms(sess, "availability", None, load_id=comms.load_id, limit=5)
    assert all(h.load_id == comms.load_id for h in hits)


def test_cosine_handles_numpy_arrays() -> None:
    np = pytest.importorskip("numpy")
    a = np.array([1.0, 0.0, 1.0])
    b = np.array([1.0, 0.0, 1.0])
    assert cosine(a, b) == pytest.approx(1.0)
    assert cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest.approx(0.0)
    assert cosine([1.0, 2.0], np.array([1.0, 2.0])) == pytest.approx(1.0)
    assert cosine(None, np.array([1.0])) == 0.0


def test_search_comms_with_numpy_embeddings(comms: Fixture, sess) -> None:
    np = pytest.importorskip("numpy")
    qvec = np.array(FakeEmbedder().embed(["availability box truck"])[0])
    hits = search_comms(sess, "availability box truck", qvec, limit=3)
    assert hits


def test_looks_structured() -> None:
    assert looks_structured("best rate on load 29372289")
    assert looks_structured("carriers from PA to NJ on Friday")
    assert not looks_structured("what should I say to this carrier?")


def test_agent_wiring_with_testmodel(comms: Fixture) -> None:
    from pydantic_ai.models.test import TestModel

    from freight_agent.agent import AgentDeps, AgentResponse, build_agent

    agent = build_agent()
    Session = session_factory(comms.engine)
    deps = AgentDeps(
        session_factory=Session, embedder=FakeEmbedder(), settings=get_settings()
    )
    with agent.override(model=TestModel()):
        result = agent.run_sync("best rate for load 29372289?", deps=deps)
    assert isinstance(result.output, AgentResponse)
    assert isinstance(result.output.answer, str)
