from __future__ import annotations

import os
from typing import Any

from pydantic_ai.messages import ToolCallPart

from evals.schema import EvalOutput

_OUTPUT_TOOL = "final_result"


def _tools_used(messages: list[Any]) -> list[str]:
    """Ordered, de-duplicated domain tool names the agent actually called."""
    seen: list[str] = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolCallPart) and part.tool_name != _OUTPUT_TOOL:
                if part.tool_name not in seen:
                    seen.append(part.tool_name)
    return seen


def build_runner(model: str | None = None):
    from freight_agent.agent import AgentDeps, build_agent
    from freight_agent.config import get_settings
    from freight_agent.db import primary_engine, session_factory

    settings = get_settings()
    if settings.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key

    embedder = None
    if settings.openai_api_key:
        from freight_agent.ingestion.llm import OpenAIEmbedder

        embedder = OpenAIEmbedder(settings)

    agent = build_agent(model)
    engine = primary_engine(settings)
    factory = session_factory(engine)

    async def run_case(question: str) -> EvalOutput:
        deps = AgentDeps(session_factory=factory, embedder=embedder, settings=settings)
        result = await agent.run(question, deps=deps)
        out = result.output
        return EvalOutput(
            answer=out.answer,
            supporting_records=out.supporting_records,
            confidence=out.confidence,
            follow_up_needed=out.follow_up_needed,
            draft_email=out.draft_email,
            tools_used=_tools_used(result.all_messages()),
        )

    return run_case
