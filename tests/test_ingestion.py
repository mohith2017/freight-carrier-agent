from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from freight_agent.config import get_settings
from freight_agent.db import init_schema, make_engine, session_factory
from freight_agent.db.models import Carrier, CommEvent, KnowledgeChunk, Offer
from freight_agent.ingestion.embed import chunk_event, embed_events
from freight_agent.ingestion.extract import (
    deterministic_extract,
    extract_equipment,
    extract_load_refs,
    extract_mc_numbers,
    extract_rates,
    merge_extractions,
)
from freight_agent.ingestion.llm import Embedder
from freight_agent.ingestion.loaders import carrier_business_key, load_all, load_carriers
from freight_agent.ingestion.parse_emails import ingest_emails
from freight_agent.ingestion.reconcile import (
    CarrierRef,
    build_carrier_index,
    closest_mc,
    normalize_name,
    reconcile,
    resolve_carrier,
)

from .conftest import dataset_available


@pytest.fixture
def ingest_engine(tmp_path: Path) -> Engine:
    if not dataset_available():
        pytest.skip("raw dataset not present (set DATASET_DIR to run dataset-backed tests)")
    engine = make_engine(f"sqlite:///{tmp_path / 'ingest.db'}")
    init_schema(engine)
    load_all(engine, get_settings().dataset_path)
    return engine


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7), float(sum(map(ord, t)) % 11)] for t in texts]


def test_extract_mc_numbers_handles_variants() -> None:
    assert extract_mc_numbers("MC 774321 here") == ["774321"]
    assert extract_mc_numbers("MC#567234. Box Truck") == ["567234"]
    assert extract_mc_numbers("Our MC is 876-543.") == ["876543"]
    assert extract_mc_numbers("MC 1480355, DEMIX") == ["1480355"]
    assert extract_mc_numbers("no identifier here") == []


def test_extract_load_refs_ignores_non_8_digit() -> None:
    assert extract_load_refs("Interested in #29001091. Refrigerated.") == ["29001091"]
    # Phone numbers / weights should not be picked up as load ids.
    assert extract_load_refs("call (610) 438-0512, weight 5200") == []


def test_extract_equipment_and_synonyms() -> None:
    assert extract_equipment("we have a reefer available") == "Refrigerated"
    assert extract_equipment("Box Truck ready") == "Box Truck"
    assert extract_equipment("sprinter on standby") == "Sprinter Van"
    assert extract_equipment("nothing relevant") is None


def test_extract_rates_picks_carrier_ask() -> None:
    text = "but $250 won't cover fuel + tolls. Can you do $290? MC 774321."
    event = deterministic_extract(text)
    assert event.quoted_rate_usd == 290.0 
    assert event.mc_numbers == ["774321"]


def test_extract_rates_per_mile_classification() -> None:
    mentions = extract_rates("market is around 2.35/mile on this lane")
    assert mentions and mentions[0].amount == 2.35
    assert mentions[0].rate_type == "per_mile"


def test_deterministic_confidence_and_review_flags() -> None:
    text = "We could do $735 on load #29000279 / #29372515. MC 166960 or MC 218765."
    event = deterministic_extract(text)
    assert event.quoted_rate_usd == 735.0
    assert event.needs_human_review is True
    assert any("multiple" in n for n in event.confidence_notes)


def test_merge_prefers_deterministic_identity() -> None:
    det = deterministic_extract("We could do $735. MC 166960.")
    llm = deterministic_extract("MC 999999 totally different")
    llm.intent = "counter"
    llm.equipment_type = "Box Truck"
    merged = merge_extractions(det, llm)
    assert merged.mc_numbers == ["166960"]
    assert merged.quoted_rate_usd == 735.0
    assert merged.intent == "counter"
    assert merged.equipment_type == "Box Truck"
    assert merged.source == "merged"



def test_closest_mc_corrects_garbled_digits() -> None:
    known = ["774321", "166960", "876543"]
    best, score = closest_mc("774320", known)
    assert best == "774321"
    assert score >= 0.8


def test_closest_mc_transposition_scores_high() -> None:
    best, score = closest_mc("747321", ["774321"])
    assert best == "774321"
    assert score >= 0.9


def test_normalize_name_strips_suffixes() -> None:
    assert normalize_name("Capital City Transport LLC") == "capital city"
    assert normalize_name("SMR TRUCKING INC") == "smr"


def test_resolve_carrier_cascade() -> None:
    refs = [
        CarrierRef(1, "774321", "p@northboundexpress.com", "Northbound Express LLC"),
        CarrierRef(2, "166960", "marie@bbkagent.com", "Capital City Transport"),
    ]
    index = build_carrier_index(refs)

    by_mc = resolve_carrier(["774321"], None, None, index)
    assert by_mc.carrier_id == 1 and by_mc.method == "mc"

    by_domain = resolve_carrier([], "newrep@northboundexpress.com", None, index)
    assert by_domain.carrier_id == 1 and by_domain.method == "domain"

    by_name = resolve_carrier([], "x@gmail.com", "Capital City Transport", index)
    assert by_name.carrier_id == 2 and by_name.method == "fuzzy_name"

    miss = resolve_carrier([], "x@gmail.com", "Totally Unknown Hauler", index)
    assert miss.carrier_id is None


def test_generic_domains_not_used_for_resolution() -> None:
    refs = [CarrierRef(1, None, "chahaltrucking@gmail.com", "Chahal Trucking Inc")]
    index = build_carrier_index(refs)
    assert "gmail.com" not in index.by_domain


def test_email_ingestion_creates_274_events_and_offers(ingest_engine: Engine) -> None:
    Session = session_factory(ingest_engine)
    with Session() as session:
        counts = ingest_emails(session, get_settings().dataset_path)
    assert counts["comm_events"] == 274
    assert counts["offers"] == 274

    with Session() as session:
        n_events = session.scalar(select(func.count()).select_from(CommEvent))
        n_offers = session.scalar(select(func.count()).select_from(Offer))
        rated = session.scalar(
            select(func.count()).select_from(Offer).where(
                Offer.quoted_rate_usd.isnot(None)
            )
        )
        equipped = session.scalar(
            select(func.count()).select_from(Offer).where(
                Offer.equipment_type.isnot(None)
            )
        )
    assert n_events == 274
    assert n_offers == 274
    assert rated and rated > 30
    assert equipped and equipped > 200


def test_email_ingestion_is_idempotent(ingest_engine: Engine) -> None:
    Session = session_factory(ingest_engine)
    dataset = get_settings().dataset_path
    with Session() as session:
        ingest_emails(session, dataset)
        ingest_emails(session, dataset)
        n = session.scalar(select(func.count()).select_from(CommEvent))
    assert n == 274


def test_reconcile_links_carriers_and_loads(ingest_engine: Engine) -> None:
    Session = session_factory(ingest_engine)
    with Session() as session:
        ingest_emails(session, get_settings().dataset_path)
        report = reconcile(session)
    assert report.events == 274
    assert report.carrier_linked > 200
    assert report.load_linked > 200
    assert report.by_method.get("mc", 0) > 0


def test_cross_channel_carrier_flagged(ingest_engine: Engine) -> None:
    Session = session_factory(ingest_engine)
    dataset = get_settings().dataset_path
    with Session() as session:
        ingest_emails(session, dataset)
        reconcile(session)
        linked = session.scalars(
            select(CommEvent).where(CommEvent.carrier_id.isnot(None)).limit(1)
        ).first()
        assert linked is not None
        mc = (linked.extracted or {}).get("mc_numbers", [])
        session.add(
            CommEvent(
                source_type="call",
                source_id="call_999_synthetic",
                direction="inbound",
                normalized_text="Synthetic call from a known carrier.",
                extracted={"mc_numbers": mc},
                raw_payload={},
            )
        )
        session.commit()
        report = reconcile(session)
    assert len(report.cross_channel_carrier_ids) >= 1



def test_chunk_event_email_single_chunk() -> None:
    event = CommEvent(
        source_type="email",
        source_id="CE0001",
        normalized_text="Subject: hi\n\nWe could do $735.",
        extracted={"intent": "counter", "equipment_type": "Box Truck"},
        raw_payload={},
    )
    chunks = chunk_event(event)
    assert len(chunks) == 1
    assert chunks[0].chunk_type == "email"
    assert chunks[0].metadata["intent"] == "counter"


def test_chunk_event_call_per_utterance() -> None:
    event = CommEvent(
        source_type="call",
        source_id="call_001_rate_negotiation",
        normalized_text="full transcript",
        extracted={},
        raw_payload={
            "segments": [
                {"speaker": "BROKER", "text": "What's your rate?"},
                {"speaker": "CARRIER", "text": "We can do $500 all in."},
                {"speaker": "CARRIER", "text": ""},
            ]
        },
    )
    chunks = chunk_event(event)
    assert len(chunks) == 2
    assert all(c.chunk_type == "utterance" for c in chunks)
    assert chunks[1].metadata["speaker"] == "CARRIER"


def test_embed_events_writes_chunks(ingest_engine: Engine) -> None:
    Session = session_factory(ingest_engine)
    embedder: Embedder = FakeEmbedder()
    with Session() as session:
        ingest_emails(session, get_settings().dataset_path)
        result = embed_events(session, embedder)
        n_chunks = session.scalar(select(func.count()).select_from(KnowledgeChunk))
        sample = session.scalars(select(KnowledgeChunk).limit(1)).first()
    assert result["chunks"] == 274
    assert n_chunks == 274
    assert sample is not None and sample.embedding is not None


def test_carrier_business_key_priority() -> None:
    assert carrier_business_key("MC-12 3", "x@y.com", "Acme Inc") == "mc:123"
    assert carrier_business_key(None, "X@Y.com", "Acme Inc") == "email:x@y.com"
    assert carrier_business_key(None, None, "Acme Inc") == "name:acme inc"
    raw = {"company_name": None, "phone": "555"}
    k1 = carrier_business_key(None, None, None, raw)
    k2 = carrier_business_key(None, None, None, dict(raw))
    assert k1 == k2 and k1.startswith("raw:")
    assert carrier_business_key(None, None, None, {"phone": "999"}) != k1


def _write_carriers(ds: Path, carriers: list[dict]) -> None:
    (ds / "carrier_profiles.json").write_text(json.dumps(carriers), encoding="utf-8")


def test_carrier_upsert_stable_across_dataset_update(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'c.db'}")
    init_schema(engine)
    Session = session_factory(engine)
    ds = tmp_path / "ds"
    ds.mkdir()

    _write_carriers(
        ds,
        [
            {"mc_number": "111", "company_name": "Alpha Inc", "email": "a@alpha.com"},
            {"mc_number": "222", "company_name": "Beta LLC", "email": "b@beta.com"},
        ],
    )
    with Session() as s:
        load_carriers(s, ds)
        beta_id = s.scalars(
            select(Carrier).where(Carrier.mc_number == "222")
        ).one().carrier_id
        # Simulate a reconciled comm_event linked to Beta.
        ev = CommEvent(
            source_type="email",
            source_id="X1",
            carrier_id=beta_id,
            normalized_text="hi",
            raw_payload={},
        )
        s.add(ev)
        s.commit()
        ev_id = ev.event_id

    # Newer dataset: Alpha renamed, Gamma onboarded.
    _write_carriers(
        ds,
        [
            {"mc_number": "111", "company_name": "Alpha Freight Inc", "email": "a@alpha.com"},
            {"mc_number": "222", "company_name": "Beta LLC", "email": "b@beta.com"},
            {"mc_number": "333", "company_name": "Gamma Co", "email": "g@gamma.com"},
        ],
    )
    with Session() as s:
        load_carriers(s, ds)
        assert s.scalar(select(func.count()).select_from(Carrier)) == 3
        assert (
            s.scalars(select(Carrier).where(Carrier.mc_number == "222")).one().carrier_id
            == beta_id
        )
        alpha = s.scalars(select(Carrier).where(Carrier.mc_number == "111")).one()
        assert alpha.company_name == "Alpha Freight Inc"
        assert s.get(CommEvent, ev_id).carrier_id == beta_id


def _write_emails(ds: Path, emails: list[dict]) -> None:
    (ds / "carrier_emails.json").write_text(json.dumps(emails), encoding="utf-8")


def test_incremental_email_ingest_adds_only_new(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'e.db'}")
    init_schema(engine)
    Session = session_factory(engine)
    ds = tmp_path / "ds"
    ds.mkdir()

    emails = [
        {"email_id": "CE1", "body": "We can do $500. MC 111.", "intent": "counter"},
        {"email_id": "CE2", "body": "Box Truck ready. MC 222.", "intent": "terse"},
    ]
    _write_emails(ds, emails)
    with Session() as s:
        ingest_emails(s, ds)
        first = {e.source_id: e.event_id for e in s.scalars(select(CommEvent))}
    assert len(first) == 2

    emails.append({"email_id": "CE3", "body": "New one. MC 333.", "intent": "inquiry"})
    _write_emails(ds, emails)
    with Session() as s:
        counts = ingest_emails(s, ds, incremental=True)
        events = list(s.scalars(select(CommEvent)))
    assert counts["comm_events"] == 1
    assert len(events) == 3
    for e in events:
        if e.source_id in first:
            assert e.event_id == first[e.source_id]


def test_incremental_embed_only_new(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'em.db'}")
    init_schema(engine)
    Session = session_factory(engine)
    ds = tmp_path / "ds"
    ds.mkdir()
    embedder: Embedder = FakeEmbedder()

    _write_emails(
        ds,
        [
            {"email_id": "CE1", "body": "one", "intent": "terse"},
            {"email_id": "CE2", "body": "two", "intent": "terse"},
        ],
    )
    with Session() as s:
        ingest_emails(s, ds)
        embed_events(s, embedder)
        assert s.scalar(select(func.count()).select_from(KnowledgeChunk)) == 2

        emails = [
            {"email_id": "CE1", "body": "one", "intent": "terse"},
            {"email_id": "CE2", "body": "two", "intent": "terse"},
            {"email_id": "CE3", "body": "three", "intent": "terse"},
        ]
        _write_emails(ds, emails)
        ingest_emails(s, ds, incremental=True)
        result = embed_events(s, embedder, incremental=True)
        total = s.scalar(select(func.count()).select_from(KnowledgeChunk))
    assert result["chunks"] == 1  # only the new event embedded
    assert total == 3
