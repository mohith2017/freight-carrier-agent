from __future__ import annotations

import csv
import json
from pathlib import Path

from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from freight_agent.models import Carrier, Load, RateHistory
from freight_agent.schemas import CarrierIn, LoadIn, RateRowIn


def _load_csv_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_loads(session: Session, dataset_dir: Path) -> int:
    rows = _load_csv_rows(dataset_dir / "loads.csv")
    session.execute(delete(Load))
    for raw in rows:
        parsed = LoadIn.model_validate(raw)
        session.add(Load(**parsed.model_dump(), load_raw=raw))
    session.commit()
    return len(rows)


def load_rate_history(session: Session, dataset_dir: Path) -> int:
    rows = _load_csv_rows(dataset_dir / "rate_history.csv")
    session.execute(delete(RateHistory))
    for raw in rows:
        parsed = RateRowIn.model_validate(raw)
        session.add(RateHistory(**parsed.model_dump()))
    session.commit()
    return len(rows)


def load_carriers(session: Session, dataset_dir: Path) -> int:
    raw_list = json.loads((dataset_dir / "carrier_profiles.json").read_text(encoding="utf-8"))
    session.execute(delete(Carrier))
    for raw in raw_list:
        parsed = CarrierIn.model_validate(raw)
        session.add(Carrier(**parsed.model_dump(), profile_raw=raw))
    session.commit()
    return len(raw_list)


def load_all(engine: Engine, dataset_dir: Path) -> dict[str, int]:
    from freight_agent.db import session_factory

    Session = session_factory(engine)
    with Session() as session:
        counts = {
            "loads": load_loads(session, dataset_dir),
            "rate_history": load_rate_history(session, dataset_dir),
            "carriers": load_carriers(session, dataset_dir),
        }
    return counts
