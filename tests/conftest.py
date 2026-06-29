from __future__ import annotations

import pytest
from sqlalchemy import Engine

from freight_agent.config import get_settings
from freight_agent.db import init_schema, make_engine, session_factory
from freight_agent.ingestion.loaders import load_all


def dataset_available() -> bool:
    return (get_settings().dataset_path / "loads.csv").exists()


requires_dataset = pytest.mark.skipif(
    not dataset_available(),
    reason="raw dataset not present (set DATASET_DIR to run dataset-backed tests)",
)


@pytest.fixture(scope="session")
def loaded_engine(tmp_path_factory: pytest.TempPathFactory) -> Engine:
    if not dataset_available():
        pytest.skip("raw dataset not present (set DATASET_DIR to run dataset-backed tests)")
    settings = get_settings()
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_schema(engine)
    load_all(engine, settings.dataset_path)
    return engine


@pytest.fixture
def session(loaded_engine: Engine):
    Session = session_factory(loaded_engine)
    with Session() as s:
        yield s
