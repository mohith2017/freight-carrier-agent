from __future__ import annotations

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, LLMJudge

from evals.schema import EvalOutput, Expected
from evals.scorers import (
    BehaviourContract,
    EntityResolution,
    FactCoverage,
    ToolSelection,
)

_ANSWER_RUBRIC = (
    "You are grading a freight broker assistant's reply. PASS if the Output is a "
    "direct, on-topic, professional response to the broker's question that is "
    "specific (cites concrete ids/lanes/values or clearly states the data is "
    "unavailable and review is needed) and internally consistent. A clear "
    "'not found / needs broker review' answer for missing data PASSES — that is "
    "correct behavior. FAIL only if the reply is off-topic, empty, evasive when an "
    "answer is given in supporting_records, or self-contradictory. score = 1.0 for a "
    "clean broker-ready reply, around 0.5 for partially helpful, 0.0 for unusable."
)


def _cases() -> list[Case[str, EvalOutput, Expected]]:
    return [
        Case(
            name="load_lookup_open",
            inputs="What are the lane, equipment, and offered rate for load #29372343?",
            metadata=Expected(
                load_id="29372343",
                expected_tools=["get_load"],
                must_include=["PA", "NJ", "Box Truck"],
                expects_follow_up=False,
                note="email-style: direct structured lookup",
            ),
        ),
        Case(
            name="best_offer_vs_market",
            inputs="What is the best rate currently on offer for load #29372289, "
            "and how does it compare to market?",
            metadata=Expected(
                load_id="29372289",
                expected_tools=["best_offer_for_load"],
                must_include=["29372289"],
                note="email-style: commercial read; may also call get_rate_context",
            ),
        ),
        Case(
            name="rate_context_lane",
            inputs="What is the market per-mile rate for MD to PA Box Truck loads?",
            metadata=Expected(
                expected_tools=["get_rate_context"],
                must_include=["mile"],
                expects_follow_up=False,
                note="email-style: rate_history aggregate",
            ),
        ),
        Case(
            name="availability_lane",
            inputs="Which carriers have confirmed availability for PA to NJ "
            "Box Truck loads?",
            metadata=Expected(
                expected_tools=["carriers_available_for_lane"],
                note="email-style: availability roll-up across channels",
            ),
        ),
        Case(
            name="compliance_conditional",
            inputs="Is MC 1198743 cleared to book, or are there compliance issues?",
            metadata=Expected(
                carrier_mc="1198743",
                carrier_id=13,
                expected_tools=["resolve_carrier"],
                must_include=["conditional"],
                expects_follow_up=True,
                note="compliance gate: CONDITIONAL authority -> broker review",
            ),
        ),
        Case(
            name="compliance_null_authority",
            inputs="Can we book HKR Logistics LLC right now?",
            metadata=Expected(
                carrier_id=5,
                expected_tools=["resolve_carrier"],
                must_include=["review"],
                expects_follow_up=True,
                note="messy: missing authority + insurance -> review, no false clear",
            ),
        ),
        Case(
            name="compliance_clean_active",
            inputs="Is MC 776491 cleared to book?",
            metadata=Expected(
                carrier_mc="776491",
                carrier_id=1,
                expected_tools=["resolve_carrier"],
                must_include=["active"],
                note="compliance gate: ACTIVE + insured + onboarded -> cleared",
            ),
        ),
        Case(
            name="carrier_not_found",
            inputs="Is MC 123456 cleared to book?",
            metadata=Expected(
                expected_tools=["resolve_carrier"],
                must_include=["123456"],
                must_not_include=["cleared to book", "is active and"],
                expects_follow_up=True,
                note="fabrication guard: nonexistent MC -> say so, flag review",
            ),
        ),
        Case(
            name="draft_confirm_best_offer",
            inputs="Draft a reply to the carrier with the best rate on load "
            "#29372289 confirming next steps.",
            metadata=Expected(
                load_id="29372289",
                expected_tools=["best_offer_for_load"],
                expects_draft=True,
                draft_rubric="Grade the draft_email in the Output. PASS if it is a "
                "professional broker reply that references load #29372289 and states a "
                "concrete next step (confirm/book/await paperwork). It should read as "
                "ready to send. score=1.0 if polished and specific, ~0.5 if generic, "
                "0.0 if missing, off-topic, or self-contradictory.",
                note="draft factuality",
            ),
        ),
        Case(
            name="draft_offer_open_load",
            inputs="Draft an email to a carrier offering load #29372343 at the "
            "posted rate.",
            metadata=Expected(
                load_id="29372343",
                expected_tools=["get_load"],
                expects_draft=True,
                draft_rubric="Grade the draft_email in the Output. PASS if it is a "
                "concise carrier-outreach email that references load #29372343 with a "
                "clear ask (cover the load at the posted rate). score=1.0 if polished "
                "and specific, ~0.5 if generic, 0.0 if missing or off-topic.",
                note="draft factuality on a structured load",
            ),
        ),
        Case(
            name="cross_channel_search",
            inputs="What did carriers say about availability on the PA to DE "
            "box truck lane?",
            metadata=Expected(
                expected_tools=["search_communications"],
                note="cross-channel: semantic evidence from emails + transcripts",
            ),
        ),
        Case(
            name="status_delivered",
            inputs="Has load #29372289 been delivered, or is it still open?",
            metadata=Expected(
                load_id="29372289",
                expected_tools=["get_load"],
                must_include=["delivered"],
                note="lifecycle: report true status",
            ),
        ),
        Case(
            name="status_cancelled",
            inputs="Is load #29000844 still available to cover?",
            metadata=Expected(
                load_id="29000844",
                expected_tools=["get_load"],
                must_include=["cancel"],
                note="messy edge: cancelled load shouldn't look bookable",
            ),
        ),
    ]


def build_dataset(
    *, with_judges: bool = True, judge_model: str | None = None
) -> Dataset[str, EvalOutput, Expected]:
    cases = _cases()

    if with_judges and judge_model is None:
        from freight_agent.config import get_settings

        judge_model = f"openai:{get_settings().agent_model}"

    if with_judges:
        for case in cases:
            rubric = case.metadata.draft_rubric if case.metadata else None
            if rubric:
                draft_judge: Evaluator[str, EvalOutput, Expected] = LLMJudge(
                    rubric=rubric,
                    model=judge_model,
                    include_input=True,
                    score={"evaluation_name": "draft_factuality"},
                    assertion=False,
                )
                case.evaluators = [*case.evaluators, draft_judge]

    dataset_evaluators: list[Evaluator[str, EvalOutput, Expected]] = [
        EntityResolution(),
        ToolSelection(),
        FactCoverage(),
        BehaviourContract(),
    ]
    if with_judges:
        dataset_evaluators.append(
            LLMJudge(
                rubric=_ANSWER_RUBRIC,
                model=judge_model,
                include_input=True,
                score={"evaluation_name": "answer_quality"},
                assertion=False,
            )
        )

    return Dataset(
        name="freight_core_workflow",
        cases=cases,
        evaluators=dataset_evaluators,
    )
