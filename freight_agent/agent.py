from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from sqlalchemy.orm import Session, sessionmaker

from freight_agent import tools
from freight_agent.config import Settings, get_settings
from freight_agent.ingestion.llm import Embedder
from freight_agent.tools import (
    CarrierAvailability,
    CarrierHistory,
    CarrierInfo,
    LoadInfo,
    OfferInfo,
    RateContext,
)


class CommEvidence(BaseModel):
    event_id: int | None = None
    source_type: str | None = None
    source_id: str | None = None
    load_id: str | None = None
    carrier_id: int | None = None
    text: str
    score: float


class AgentResponse(BaseModel):
    answer: str = Field(description="Direct answer to the broker's question.")
    supporting_records: list[str] = Field(
        default_factory=list,
        description="IDs/records grounding the answer (load ids, carrier ids, offer ids).",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="0-1 confidence in the answer."
    )
    follow_up_needed: bool = Field(
        default=False, description="True if data was missing/ambiguous or review is needed."
    )
    draft_email: str | None = Field(
        default=None, description="Draft carrier reply when one was requested."
    )


@dataclass
class AgentDeps:
    session_factory: sessionmaker[Session]
    embedder: Embedder | None = None
    settings: Settings | None = None


SYSTEM_PROMPT = """\
You are an intake assistant for a freight broker. You help process inbound carrier
inquiries (emails and call transcripts), answer operational questions, and draft
carrier replies.

ROUTING (structured-first):
- If the question names a concrete entity — a load id (8 digits), an MC number, a
  lane (two states), an equipment type, or a date — call the matching structured
  tool FIRST (get_load, resolve_carrier, get_rate_context, best_offer_for_load,
  carriers_available_for_lane). Use search_communications only for supporting
  evidence (what was said, tone) or when no structured tool fits.
- Never invent load ids, MC numbers, rates, carriers, or availability. If a tool
  returns nothing, say so and set follow_up_needed=true.

COMPLIANCE GATE:
- Before suggesting booking or committing a carrier, check compliance_flags on the
  carrier. If authority_status is not ACTIVE, or insurance is missing/expired, or
  the carrier is not onboarded, surface that clearly and recommend broker review.

GROUNDING & OUTPUT:
- Base every claim on tool results. Put the concrete ids you used in
  supporting_records.
- Set confidence to reflect evidence quality; lower it on conflicting/missing data.
- For rate questions, prefer per-mile comparisons against rate_history (loads quote
  a flat total; divide by distance).
- Only fill draft_email when the broker asks for a reply/draft. Keep it concise and
  professional, quote only figures returned by tools, and never promise a booking
  for a non-compliant carrier.
"""


def build_agent(model: str | None = None) -> Agent[AgentDeps, AgentResponse]:
    settings = get_settings()
    agent: Agent[AgentDeps, AgentResponse] = Agent(
        model or f"openai:{settings.agent_model}",
        deps_type=AgentDeps,
        output_type=AgentResponse,
        system_prompt=SYSTEM_PROMPT,
        retries=2,
        defer_model_check=True,
    )

    @agent.tool
    def get_load(ctx: RunContext[AgentDeps], load_id: str) -> LoadInfo | None:
        with ctx.deps.session_factory() as s:
            return tools.get_load(s, load_id)

    @agent.tool
    def resolve_carrier(ctx: RunContext[AgentDeps], query: str) -> CarrierInfo | None:
        with ctx.deps.session_factory() as s:
            return tools.resolve_carrier(s, query)

    @agent.tool
    def get_carrier_history(
        ctx: RunContext[AgentDeps], carrier_id: int
    ) -> CarrierHistory | None:
        with ctx.deps.session_factory() as s:
            return tools.get_carrier_history(s, carrier_id)

    @agent.tool
    def get_rate_context(
        ctx: RunContext[AgentDeps],
        origin_state: str,
        destination_state: str,
        equipment_type: str | None = None,
        flat_usd: float | None = None,
        distance_miles: int | None = None,
    ) -> RateContext:
        with ctx.deps.session_factory() as s:
            return tools.get_rate_context(
                s,
                origin_state,
                destination_state,
                equipment_type,
                flat_usd=flat_usd,
                distance_miles=distance_miles,
            )

    @agent.tool
    def best_offer_for_load(ctx: RunContext[AgentDeps], load_id: str) -> OfferInfo | None:
        with ctx.deps.session_factory() as s:
            return tools.best_offer_for_load(s, load_id)

    @agent.tool
    def carriers_available_for_lane(
        ctx: RunContext[AgentDeps],
        origin_state: str,
        destination_state: str,
        equipment_type: str | None = None,
    ) -> list[CarrierAvailability]:
        with ctx.deps.session_factory() as s:
            return tools.carriers_available_for_lane(
                s, origin_state, destination_state, equipment_type
            )

    @agent.tool
    def search_communications(
        ctx: RunContext[AgentDeps],
        query: str,
        load_id: str | None = None,
        carrier_id: int | None = None,
        source_type: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        qvec = None
        if ctx.deps.embedder is not None:
            vecs = ctx.deps.embedder.embed([query])
            qvec = vecs[0] if vecs else None
        with ctx.deps.session_factory() as s:
            hits = tools.search_comms(
                s,
                query,
                qvec,
                load_id=load_id,
                carrier_id=carrier_id,
                source_type=source_type,
                limit=limit,
            )
        return [
            CommEvidence(
                event_id=h.event_id,
                source_type=h.source_type,
                source_id=h.source_id,
                load_id=h.load_id,
                carrier_id=h.carrier_id,
                text=h.text,
                score=h.score,
            ).model_dump()
            for h in hits
        ]

    return agent


def run_agent(
    question: str,
    *,
    model: str | None = None,
    embedder: Embedder | None = None,
) -> AgentResponse:
    from freight_agent.db import primary_engine, session_factory

    settings = get_settings()
    if settings.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key

    if embedder is None and settings.openai_api_key:
        from freight_agent.ingestion.llm import OpenAIEmbedder

        embedder = OpenAIEmbedder(settings)

    agent = build_agent(model)
    engine = primary_engine(settings)
    Session = session_factory(engine)
    deps = AgentDeps(session_factory=Session, embedder=embedder, settings=settings)
    result = agent.run_sync(question, deps=deps)
    return result.output
