from __future__ import annotations

from dataclasses import dataclass

from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from evals.schema import EvalOutput, Expected

EvalCtx = EvaluatorContext[str, EvalOutput, Expected]


@dataclass
class EntityResolution(Evaluator[str, EvalOutput, Expected]):
    def evaluate(self, ctx: EvalCtx) -> dict[str, float]:
        exp, out = ctx.metadata, ctx.output
        if exp is None:
            return {}
        targets: list[bool] = []
        text = out.grounding_text
        if exp.load_id:
            targets.append(exp.load_id.lower() in text)
        if exp.carrier_mc:
            targets.append(exp.carrier_mc.lower() in text)
        if exp.carrier_id is not None:
            targets.append(str(exp.carrier_id) in text)
        if not targets:
            return {}
        return {"entity_resolution": sum(targets) / len(targets)}


@dataclass
class ToolSelection(Evaluator[str, EvalOutput, Expected]):
    def evaluate(self, ctx: EvalCtx) -> dict[str, float]:
        exp, out = ctx.metadata, ctx.output
        if exp is None or not exp.expected_tools:
            return {}
        used = set(out.tools_used)
        hit = sum(1 for t in exp.expected_tools if t in used)
        return {"tool_selection": hit / len(exp.expected_tools)}


@dataclass
class FactCoverage(Evaluator[str, EvalOutput, Expected]):
    def evaluate(self, ctx: EvalCtx) -> dict[str, float | bool]:
        exp, out = ctx.metadata, ctx.output
        if exp is None:
            return {}
        res: dict[str, float | bool] = {}
        ans = out.answer.lower()
        if exp.must_include:
            hit = sum(1 for s in exp.must_include if s.lower() in ans)
            res["fact_coverage"] = hit / len(exp.must_include)
        if exp.must_not_include:
            res["no_fabrication"] = not any(
                s.lower() in ans for s in exp.must_not_include
            )
        return res


@dataclass
class BehaviourContract(Evaluator[str, EvalOutput, Expected]):
    def evaluate(self, ctx: EvalCtx) -> dict[str, bool]:
        exp, out = ctx.metadata, ctx.output
        if exp is None:
            return {}
        res: dict[str, bool] = {}
        if exp.expects_follow_up is not None:
            res["follow_up_correct"] = out.follow_up_needed == exp.expects_follow_up
        if exp.expects_draft:
            draft = (out.draft_email or "").strip()
            ok = len(draft) >= 40
            if ok and exp.load_id:
                ok = exp.load_id.lower() in draft.lower()
            res["draft_present"] = ok
        return res
