from __future__ import annotations

import argparse
import sys

from evals.goldens import build_dataset
from evals.task import build_runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the freight-agent core eval.")
    parser.add_argument(
        "--no-judges",
        action="store_true",
        help="Skip the LLM judges (deterministic scorers only; no extra cost).",
    )
    parser.add_argument(
        "--model", default=None, help="Override the agent model for the run."
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Model for the LLM judges (defaults to the agent model).",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=4,
        help="Parallel cases (lower if you hit rate limits).",
    )
    parser.add_argument(
        "--json", dest="json_path", default=None, help="Write the report JSON here."
    )
    args = parser.parse_args(argv)

    from freight_agent.config import get_settings

    if not get_settings().openai_api_key:
        print("ERROR: OPENAI_API_KEY is required to run the eval.", file=sys.stderr)
        return 1

    dataset = build_dataset(
        with_judges=not args.no_judges, judge_model=args.judge_model
    )
    runner = build_runner(args.model)

    report = dataset.evaluate_sync(
        runner, max_concurrency=args.max_concurrency, progress=True
    )
    report.print(include_input=True, include_output=False, include_reasons=True)

    if args.json_path:
        _write_json(report, args.json_path)
        print(f"\nWrote report JSON to {args.json_path}")

    return 0


def _write_json(report: object, path: str) -> None:
    """Serialize a compact summary: per-dimension averages + per-case results."""
    import json
    from pathlib import Path

    avg = report.averages()  # type: ignore[attr-defined]
    summary = {
        "dataset": "freight_core_workflow",
        "averages": {
            "scores": dict(getattr(avg, "scores", {}) or {}),
            "assertion_pass_rate": getattr(avg, "assertions", None),
        },
        "cases": [
            {
                "name": c.name,
                "scores": {k: v.value for k, v in c.scores.items()},
                "assertions": {k: v.value for k, v in c.assertions.items()},
            }
            for c in report.cases  # type: ignore[attr-defined]
        ],
    }
    Path(path).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
