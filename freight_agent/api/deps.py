from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from pydantic_ai import Agent
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from freight_agent.agent import AgentDeps, AgentResponse, build_agent
from freight_agent.config import Settings, get_settings
from freight_agent.db import make_engine
from freight_agent.ingestion.llm import Embedder

__all__ = ["AgentResponse", "Resources", "get_resources", "make_agent_deps"]


def _readonly_engine(settings: Settings) -> Engine:
    engine = make_engine(settings.primary_url)
    if engine.url.get_backend_name() == "postgresql":

        @event.listens_for(engine, "connect")
        def _set_readonly(dbapi_conn, _record):
            with dbapi_conn.cursor() as cur:
                cur.execute("SET SESSION default_transaction_read_only = on")
            dbapi_conn.commit()

    return engine


@dataclass
class Resources:
    settings: Settings
    session_factory: sessionmaker[Session]
    embedder: Embedder | None
    agent: Agent[AgentDeps, AgentResponse]


@lru_cache
def get_resources() -> Resources:
    settings = get_settings()
    if settings.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key

    embedder: Embedder | None = None
    if settings.openai_api_key:
        from freight_agent.ingestion.llm import OpenAIEmbedder

        embedder = OpenAIEmbedder(settings)

    engine = _readonly_engine(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    agent = build_agent()
    return Resources(
        settings=settings,
        session_factory=factory,
        embedder=embedder,
        agent=agent,
    )


def make_agent_deps(res: Resources) -> AgentDeps:
    return AgentDeps(
        session_factory=res.session_factory,
        embedder=res.embedder,
        settings=res.settings,
    )
