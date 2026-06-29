from __future__ import annotations

import json

from pydantic_evals import Case, Dataset

from evals.goldens import build_dataset
from evals.run import _write_json
from evals.schema import EvalOutput, Expected
from evals.scorers import (
    BehaviourContract,
    EntityResolution,
    FactCoverage,
    ToolSelection,
)

_PERFECT = EvalOutput(
    answer="Load 29372289 runs PA->DE on a Box Truck; status is delivered.",
    supporting_records=["load:29372289", "carrier:13"],
    confidence=0.9,
    follow_up_needed=False,
    draft_email=None,
    tools_used=["get_load", "get_rate_context"],
)

_WRONG = EvalOutput(
    answer="I booked it and the carrier is cleared to book for sure.",
    supporting_records=[],
    confidence=0.9,
    follow_up_needed=False,
    draft_email=None,
    tools_used=["search_communications"],
)

_DRAFT = EvalOutput(
    answer="Drafted a reply for load 29372289.",
    supporting_records=["load:29372289"],
    follow_up_needed=False,
    draft_email="Hi, confirming load #29372289 at the agreed rate. "
    "Please reply to lock it in. Thanks!",
    tools_used=["best_offer_for_load"],
)


def _run(case: Case[str, EvalOutput, Expected], output: EvalOutput) -> dict:
    """Evaluate one case against a stub task that returns `output`."""
    ds: Dataset[str, EvalOutput, Expected] = Dataset(
        name="t",
        cases=[case],
        evaluators=[
            EntityResolution(),
            ToolSelection(),
            FactCoverage(),
            BehaviourContract(),
        ],
    )
    report = ds.evaluate_sync(lambda _q: output, progress=False)
    c = report.cases[0]
    scores = {k: v.value for k, v in c.scores.items()}
    asserts = {k: v.value for k, v in c.assertions.items()}
    return {**scores, **asserts}


def test_perfect_case_scores_all_ones() -> None:
    case = Case(
        name="good",
        inputs="status of load #29372289?",
        metadata=Expected(
            load_id="29372289",
            carrier_id=13,
            expected_tools=["get_load"],
            must_include=["PA", "DE", "delivered"],
            must_not_include=["booked it"],
            expects_follow_up=False,
        ),
    )
    r = _run(case, _PERFECT)
    assert r["entity_resolution"] == 1.0
    assert r["tool_selection"] == 1.0
    assert r["fact_coverage"] == 1.0
    assert r["no_fabrication"] is True
    assert r["follow_up_correct"] is True


def test_wrong_case_fails_every_dimension() -> None:
    case = Case(
        name="bad",
        inputs="status of load #29372289?",
        metadata=Expected(
            load_id="29372289",
            carrier_id=13,
            expected_tools=["get_load"],
            must_include=["PA", "DE", "delivered"],
            must_not_include=["cleared to book"],
            expects_follow_up=True,
        ),
    )
    r = _run(case, _WRONG)
    assert r["entity_resolution"] == 0.0
    assert r["tool_selection"] == 0.0
    assert r["fact_coverage"] == 0.0
    assert r["no_fabrication"] is False
    assert r["follow_up_correct"] is False


def test_partial_entity_and_tool_scores() -> None:
    case = Case(
        name="partial",
        inputs="q",
        metadata=Expected(
            load_id="29372289",  # present
            carrier_mc="999999",  # absent
            expected_tools=["get_load", "best_offer_for_load"],
        ),
    )
    r = _run(case, _PERFECT)
    assert r["entity_resolution"] == 0.5
    assert r["tool_selection"] == 0.5


def test_draft_present_requires_load_reference() -> None:
    ok = Case(
        name="draft_ok",
        inputs="draft",
        metadata=Expected(load_id="29372289", expects_draft=True),
    )
    assert _run(ok, _DRAFT)["draft_present"] is True

    missing = Case(
        name="draft_missing",
        inputs="draft",
        metadata=Expected(load_id="29372289", expects_draft=True),
    )
    assert _run(missing, _PERFECT)["draft_present"] is False


def test_dimensions_absent_when_not_asserted() -> None:
    case = Case(name="bare", inputs="q", metadata=Expected())
    r = _run(case, _PERFECT)
    for k in ("entity_resolution", "tool_selection", "fact_coverage"):
        assert k not in r


def test_write_json_handles_float_assertion_average(tmp_path) -> None:
    case = Case(
        name="c",
        inputs="q",
        metadata=Expected(load_id="29372289", expects_follow_up=False),
    )
    ds: Dataset[str, EvalOutput, Expected] = Dataset(
        name="t", cases=[case], evaluators=[EntityResolution(), BehaviourContract()]
    )
    report = ds.evaluate_sync(lambda _q: _PERFECT, progress=False)

    out = tmp_path / "report.json"
    _write_json(report, str(out))
    data = json.loads(out.read_text())
    assert data["dataset"] == "freight_core_workflow"
    assert "assertion_pass_rate" in data["averages"]
    assert data["cases"][0]["name"] == "c"


def test_golden_dataset_wellformed() -> None:
    ds = build_dataset(with_judges=False)
    assert len(ds.cases) == 13
    names = [c.name for c in ds.cases]
    assert len(names) == len(set(names)), "case names must be unique"
    for c in ds.cases:
        assert c.metadata is not None
        assert c.metadata.expected_tools, f"{c.name} has no expected tools"
