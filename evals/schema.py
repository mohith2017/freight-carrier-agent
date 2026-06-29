from __future__ import annotations

from pydantic import BaseModel, Field


class Expected(BaseModel):
    load_id: str | None = None
    carrier_mc: str | None = None
    carrier_id: int | None = None

    expected_tools: list[str] = Field(default_factory=list)

    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)

    expects_follow_up: bool | None = None
    expects_draft: bool = False

    draft_rubric: str | None = None

    note: str = ""


class EvalOutput(BaseModel):
    answer: str
    supporting_records: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    follow_up_needed: bool = False
    draft_email: str | None = None
    tools_used: list[str] = Field(default_factory=list)

    @property
    def grounding_text(self) -> str:
        return (self.answer + " " + " ".join(self.supporting_records)).lower()
