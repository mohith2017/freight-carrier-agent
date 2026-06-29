# Freight Carrier Agent

AI intake assistant for a freight broker's inbound queue: ingests carrier emails
and call recordings, normalizes them into a relational + vector store, and
answers questions / drafts replies via a typed, tool-using agent.

## Live demo

- **App:** https://freight-carrier-agent.vercel.app
- **API:** https://freight-carrier-agent.fastapicloud.dev/docs

Backend reads a pre-seeded Supabase; it never ingests on boot.

## Stack

- **Store:** Supabase Postgres + `pgvector` (primary), SQLite (local mirror)
- **Agent:** Pydantic AI (`gpt-5.5`), typed tools, structured-first hybrid retrieval
- **Ingestion:** `gpt-4o-transcribe-diarize` + `gpt-5.4-mini` extraction + `text-embedding-3-small`
- **Backend:** FastAPI / FastAPI Cloud  ·  **Frontend:** Next.js + TS / Vercel  ·  **Eval:** Pydantic Evals

## Quickstart

Requires [uv](https://docs.astral.sh/uv/). From the repo root:

```bash
uv sync --extra dev --extra ai --extra pg   # pg = Postgres driver
cp .env.example .env                         # set OPENAI_API_KEY
uv run python -m freight_agent init-db
uv run python -m freight_agent load          # 50 loads / 48 carriers / 720 rates
uv run python -m freight_agent ingest all    # emails -> calls -> reconcile -> embed
uv run python -m freight_agent ask "Best rate on offer for load #29372289 vs market?"
```

Web app (add `--extra api`): `uvicorn freight_agent.api.app:app` + `cd frontend && npm i && npm run dev`.
Full setup/verify steps: **[`runbooks/README.md`](runbooks/README.md)**.

## Evaluation

**Pydantic Evals** over **13 goldens** from the real dataset ([`evals/`](evals/)):

```bash
uv run python -m evals.run                  # deterministic scorers + LLM judges
uv run python -m evals.run --no-judges      # cheap: scorers only
```

Scores entity resolution, tool selection, fact coverage, no-fabrication,
follow-up correctness, draft presence, and (with judges) answer quality + draft
factuality. Deterministic scorers are unit-tested offline (`tests/test_evals.py`).

## Docs

- **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — diagram, data model, ingestion, retrieval, layout
- **[`docs/DECISIONS.md`](docs/DECISIONS.md)** — decision log (the "why" + trade-offs)
- **[`docs/AI_ARTIFACTS.md`](docs/AI_ARTIFACTS.md)** — how AI built and powers the project
- **[`runbooks/README.md`](runbooks/README.md)** — end-to-end run/verify guide
