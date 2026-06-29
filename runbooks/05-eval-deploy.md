# Runbook 05 — Eval, deploy, docs

> Status: Planned. This runbook will be filled in when the eval suite and
> deployment land. Requires `OPENAI_API_KEY`.

## Scope (planned)

- Pydantic Evals: 12-15 goldens on the core workflow (carrier + load
  identification, rate/availability extraction, answer correctness, draft
  factuality); run and record scores.
- Deploy backend (FastAPI Cloud) + frontend (Vercel); CI (ruff/mypy/pytest +
  frontend build).

## Commands (planned)

```bash
uv run python -m freight_agent eval run
```

Depends on [04-product-surface.md](04-product-surface.md) being complete.
