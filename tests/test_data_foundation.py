from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from freight_agent.config import Settings, get_settings
from freight_agent.db import init_schema, make_engine, session_factory, target_urls
from freight_agent.db.models import Carrier, CommEvent, Load, Offer, RateHistory
from freight_agent.ingestion.loaders import load_carriers, load_loads
from freight_agent.rates import assess_offer, flat_to_per_mile


def test_row_counts(session: Session) -> None:
    assert session.scalar(select(func.count()).select_from(Load)) == 50
    assert session.scalar(select(func.count()).select_from(Carrier)) == 48
    assert session.scalar(select(func.count()).select_from(RateHistory)) == 720


def test_load_fields_parsed(session: Session) -> None:
    load = session.get(Load, "29372289")
    assert load is not None
    assert load.origin_state == "PA"
    assert load.destination_state == "DE"
    assert load.distance_miles == 82
    assert load.offered_rate_usd == 310.0
    assert load.status == "delivered"
    assert load.load_raw["shipper_name"] == "Goodlane Internal"


def test_blank_weight_is_null(session: Session) -> None:
    blanks = session.scalar(
        select(func.count()).select_from(Load).where(Load.weight_lbs.is_(None))
    )
    assert blanks == 23


def test_compliance_fields_present(session: Session) -> None:
    conditional = session.scalar(
        select(func.count()).select_from(Carrier).where(Carrier.authority_status == "CONDITIONAL")
    )
    null_authority = session.scalar(
        select(func.count()).select_from(Carrier).where(Carrier.authority_status.is_(None))
    )
    assert conditional == 2
    assert null_authority == 3


def test_flat_to_per_mile() -> None:
    assert flat_to_per_mile(310, 82) == round(310 / 82, 4)
    assert flat_to_per_mile(None, 82) is None
    assert flat_to_per_mile(310, 0) is None


def test_assess_offer_positions() -> None:
    assert assess_offer(310, 82, 5.0).position == "below"
    assert assess_offer(500, 82, 6.1).position == "near"
    assert assess_offer(900, 82, 5.0).position == "above"
    assert assess_offer(310, 82, None).position == "unknown"


def test_sqlite_backup_fanout_when_postgres_primary() -> None:
    s_sqlite = Settings(database_url="", sqlite_path="data/freight.db")
    sqlite_urls = target_urls(s_sqlite)
    assert len(sqlite_urls) == 1
    assert sqlite_urls[0].startswith("sqlite:///")

    s_pg = Settings(
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        sqlite_path="data/freight.db",
    )
    pg_urls = target_urls(s_pg)
    assert len(pg_urls) == 2
    assert pg_urls[0].startswith("postgresql+psycopg://")
    assert pg_urls[1].startswith("sqlite:///")


def test_db_url_normalized_to_psycopg_v3() -> None:
    for raw in (
        "postgresql://postgres:pw@host.supabase.com:5432/postgres",
        "postgres://postgres:pw@host.supabase.com:5432/postgres",
        "postgresql+psycopg2://postgres:pw@host.supabase.com:5432/postgres",
        "postgresql+psycopg://postgres:pw@host.supabase.com:5432/postgres",
    ):
        s = Settings(database_url=raw, sqlite_path="data/freight.db")
        assert s.primary_url.startswith("postgresql+psycopg://")
        assert s.primary_url.endswith("@host.supabase.com:5432/postgres")


def test_empty_db_url_falls_back_to_sqlite() -> None:
    s = Settings(database_url="", sqlite_path="data/freight.db")
    assert s.primary_url.startswith("sqlite:///")
    assert s.uses_postgres is False


def test_load_carriers_self_heals_duplicates(tmp_path: Path) -> None:
    dataset = get_settings().dataset_path
    engine = make_engine(f"sqlite:///{tmp_path / 'dupes.db'}")
    init_schema(engine)
    Session = session_factory(engine)

    with Session() as s:
        load_loads(s, dataset)
        load_carriers(s, dataset)
        assert s.scalar(select(func.count()).select_from(Carrier)) == 48

        originals = list(s.scalars(select(Carrier)))
        dup_id_for_mc = {}
        for c in originals:
            dup = Carrier(
                mc_number=c.mc_number,
                email=c.email,
                company_name=c.company_name,
                authority_status=c.authority_status,
                profile_raw=c.profile_raw,
            )
            s.add(dup)
            s.flush()
            dup_id_for_mc[c.mc_number] = dup.carrier_id
        assert s.scalar(select(func.count()).select_from(Carrier)) == 96

        mc, dup_id = next(
            (mc, i) for mc, i in dup_id_for_mc.items() if mc is not None
        )
        ev = CommEvent(source_type="email", source_id="x1", carrier_id=dup_id)
        s.add(ev)
        s.flush()
        s.add(Offer(event_id=ev.event_id, carrier_id=dup_id))
        s.commit()

        load_carriers(s, dataset)
        assert s.scalar(select(func.count()).select_from(Carrier)) == 48
        assert s.get(Carrier, dup_id) is None
        survivor = s.scalars(select(Carrier).where(Carrier.mc_number == mc)).one()
        relinked = s.scalars(
            select(CommEvent).where(CommEvent.source_id == "x1")
        ).one()
        assert relinked.carrier_id == survivor.carrier_id
