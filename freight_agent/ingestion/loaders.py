from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from freight_agent.db.models import Carrier, Load, RateHistory
from freight_agent.db.schemas import CarrierIn, LoadIn, RateRowIn


def _load_csv_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def carrier_business_key(
    mc_number: str | None,
    email: str | None,
    company_name: str | None,
    fallback_idx: int | None = None,
) -> str:
    if mc_number:
        digits = re.sub(r"\D", "", str(mc_number))
        if digits:
            return f"mc:{digits}"
    if email and email.strip():
        return f"email:{email.strip().lower()}"
    if company_name and company_name.strip():
        return f"name:{company_name.strip().lower()}"
    return f"row:{fallback_idx}"


def load_loads(session: Session, dataset_dir: Path) -> int:
    rows = _load_csv_rows(dataset_dir / "loads.csv")
    for raw in rows:
        parsed = LoadIn.model_validate(raw)
        session.merge(Load(**parsed.model_dump(), load_raw=raw))
    session.commit()
    return len(rows)


def load_rate_history(session: Session, dataset_dir: Path) -> int:
    # rate_history has no inbound foreign keys, so a clean delete+insert is safe.
    rows = _load_csv_rows(dataset_dir / "rate_history.csv")
    session.execute(delete(RateHistory))
    for raw in rows:
        parsed = RateRowIn.model_validate(raw)
        session.add(RateHistory(**parsed.model_dump()))
    session.commit()
    return len(rows)


def load_carriers(session: Session, dataset_dir: Path) -> int:
    raw_list = json.loads((dataset_dir / "carrier_profiles.json").read_text(encoding="utf-8"))

    existing = list(session.scalars(select(Carrier)))
    by_key: dict[str, Carrier] = {}
    for c in existing:
        by_key.setdefault(
            carrier_business_key(c.mc_number, c.email, c.company_name, c.carrier_id), c
        )

    for idx, raw in enumerate(raw_list, start=1):
        parsed = CarrierIn.model_validate(raw)
        key = carrier_business_key(
            parsed.mc_number, parsed.email, parsed.company_name, idx
        )
        fields = parsed.model_dump()
        obj = by_key.get(key)
        if obj is not None:
            for field, value in fields.items():
                setattr(obj, field, value)
            obj.profile_raw = raw
        else:
            new_obj = Carrier(**fields, profile_raw=raw)
            session.add(new_obj)
            by_key[key] = new_obj
    session.commit()
    return len(raw_list)


def load_all(engine: Engine, dataset_dir: Path) -> dict[str, int]:
    from freight_agent.db.engine import session_factory

    Session = session_factory(engine)
    with Session() as session:
        counts = {
            "loads": load_loads(session, dataset_dir),
            "rate_history": load_rate_history(session, dataset_dir),
            "carriers": load_carriers(session, dataset_dir),
        }
    return counts
