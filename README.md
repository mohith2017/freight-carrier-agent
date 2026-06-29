# Freight Carrier Agent

AI-native intake assistant for a freight broker's inbound queue. Ingests carrier
emails and call recordings, normalizes the messiness into a relational
system-of-record plus a vector evidence layer, and answers broker questions /
drafts carrier replies via a typed, tool-using agent.

Built phase by phase; each phase ends in a test gate.

## Stack

- **Datastore:** Supabase Postgres + `pgvector` (primary), SQLite (local backup/dev)
- **Agent:** Pydantic AI (`gpt-5.5`), native typed tools, structured-first hybrid retrieval
- **Ingestion:** deterministic parse + `gpt-5.4-mini` extraction; `gpt-4o-transcribe-diarize`; `text-embedding-3-small`
- **Backend:** FastAPI on FastAPI Cloud  ·  **Frontend:** Next.js + TS on Vercel
- **Eval:** Pydantic Evals on one core workflow

## Getting started

Requires [uv](https://docs.astral.sh/uv/). From the repo root:

```bash
uv venv
uv sync --extra dev
cp .env.example .env
```

Then follow a runbook below to run and verify a given module.

## Runbooks

Run/verify instructions live in [`runbooks/`](runbooks/README.md), one per
module, so they stay separated as the system grows. Each lists its commands,
expected output, and troubleshooting.

| # | Runbook | Status |
|---|---------|--------|
| 01 | [Data foundation](runbooks/01-data-foundation.md) — schema + load + verify | Available |
| 02 | [Ingestion](runbooks/02-ingestion.md) — emails, transcription, embeddings | Planned |
| 03 | [Agent](runbooks/03-agent.md) — tools + broker Q&A + draft emails | Planned |
| 04 | [Product surface](runbooks/04-product-surface.md) — FastAPI + Next.js UI | Planned |
| 05 | [Eval, deploy, docs](runbooks/05-eval-deploy.md) | Planned |

Quick check that the data foundation works:

```bash
uv run python -m freight_agent init-db
uv run python -m freight_agent load
uv run python -m freight_agent verify
uv run pytest
```

## Repo layout

- `freight_agent/` — package: `config`, `db`, `models`, `schemas`, `rates`, `cli`, `ingestion/`
- `runbooks/` — per-module run/verify instructions
- `tests/` — test gates
- `frontend/` — Next.js UI (added later)
- `data/` — local SQLite store (gitignored)
