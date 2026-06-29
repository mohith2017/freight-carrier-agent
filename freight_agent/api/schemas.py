from __future__ import annotations

from pydantic import BaseModel, Field

from freight_agent.agent import AgentResponse


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    model: str | None = None


class ToolCallTrace(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    result_summary: str | None = None


class QueryResponse(AgentResponse):
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
