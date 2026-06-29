from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from freight_agent.config import Settings, get_settings
from freight_agent.models import Base


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///"):
        db_path = Path(url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)


def make_engine(url: str) -> Engine:
    _ensure_sqlite_dir(url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, connect_args=connect_args)


def primary_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    return make_engine(settings.primary_url)


def target_urls(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    urls = [settings.primary_url]
    if settings.uses_postgres:
        urls.append(settings.sqlite_url)
    return urls


def target_engines(settings: Settings | None = None) -> list[Engine]:
    return [make_engine(url) for url in target_urls(settings)]


def init_schema(engine: Engine) -> None:
    if engine.url.get_backend_name() == "postgresql":
        with engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
