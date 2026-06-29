# Decision Log

Meaningful decisions, written as the build progressed. Each is one line of
**decision → why / trade-off**. This is the "documentation of decisions"
deliverable.

## Data store & vendor

- **D1 — Supabase Postgres +** `pgvector` **primary, SQLite mirror.** Broker questions
are relational/aggregate, semantic search only supports evidence; one store for
rows + vectors means single-query joins by `load_id`/`carrier_id`/lane. A
dedicated vector DB is pure ops overhead at ~329 docs (revisit past large scale).
- **D2 — Single vendor (OpenAI) for agent + STT + embeddings.** One key/SDK/bill.
Costs marginal STT accuracy, which the brief explicitly doesn't require.



## Models (GA as of Jun 28, 2026)

- **D3 — Tiers:** agent `gpt-5.5` (latest GA flagship; 5.6 is preview-only),
extraction `gpt-5.4-mini` (cheap strict JSON ×329), transcription
`gpt-4o-transcribe-diarize` (speaker labels, ~cents for 55 files), embeddings
`text-embedding-3-small` (1536-dim, plenty for this corpus).



## Agent & retrieval

- **D4 — Pydantic AI native typed tools, not FastMCP.** Agent and tools share a
process, so MCP adds only a transport hop + demo failure surface; typed
deps/outputs are the guard against fabricated IDs. FastMCP stays an optional
sidecar.
- **D13 — In-app hybrid retrieval scoring, not SQL-side ANN/FTS.**
`0.55·vector + 0.25·lexical + 0.20·metadata_boost` in Python over ~1.8k chunks:
sub-ms, identical on SQLite/Postgres, offline-testable. SQL-side pgvector/FTS is
the scale path (with D12's HNSW past ~10k rows).
- **D14 — Session-per-tool-call.** Pydantic AI runs sync tools in worker threads;
sharing one `Session` threw `IllegalStateChangeError` live, so `AgentDeps` holds
a `sessionmaker` and each tool opens its own. Tool fns still take a `Session` to
stay unit-testable.
- **D18 — Numpy-safe cosine in retrieval.** `pgvector` returns embeddings as numpy
arrays, so `if not vec` in `cosine()` raised "truth value of an array is
ambiguous" — `search_communications` crashed on every Postgres run while offline
list-based tests passed. Fixed by coercing operands to float lists and using
explicit `is None`/`len()` checks; added regression tests with numpy arrays.
- **D15 — Structured-first routing + compliance gate + typed** `AgentResponse`**.**
Load/MC/lane/date ⇒ SQL tool before semantic search; surface non-ACTIVE
authority / expired insurance before suggesting booking; return
answer/records/confidence/follow_up/draft. Verified live: a `delivered` load set
`follow_up_needed=true` instead of inventing a confirmation.



## Ingestion & data quality

- **D5 — Deterministic parse first, LLM second.** Regex pulls MC/`$rate`/refs/dates
before `gpt-5.4-mini` (prompt rule: prefer `null`, never invent). Needed because
`rate_quoted_usd` is null on all 274 emails — the rate lives in free text.
- **D6 — Carrier identity by stable business key + upsert (FK-safe).** Re-`load`
threw FK violations (delete+insert orphaned reconciled `comm_events`). Loaders
now upsert `loads` by `load_id` and `carriers` by MC→email→name→content-hash, so
`carrier_id` is preserved and re-loading a newer dataset converges.
- **D7 —** `--incremental` **ingestion.** Process only new emails/WAVs/unembedded
events so folding in a newer dataset doesn't re-transcribe/re-embed; full rebuild
stays the default.
- **D16 — Self-healing carrier load.** Source nulls are intentional and preserved
verbatim (we never fabricate). But Supabase had drifted to 96 carriers (every row
duplicated by an early pre-upsert load) while SQLite held the correct 48.
`load_carriers` now collapses duplicates by business key — keeps the lowest
`carrier_id`, repoints `comm_events`/`offers` FKs, deletes orphans — and fully-null
carriers dedupe on a content hash, so a plain `freight load` self-heals. Verified
live: 96→48, 0 duplicate MCs, 0 orphaned FKs, links preserved.



## Persistence resilience

- **D8 — Harden embed writes against the Supabase pooler.** Big per-batch vector
INSERTs intermittently died with `ssl/tls alert bad record mac`. Fix:
`pool_pre_ping` + keepalives + `insertmanyvalues_page_size=20`, plus 20-row
batched writes that dispose the poisoned connection and retry (4×). Deeper fix:
use the session pooler / `prepare_threshold=None`.
- **D12 — Defer HNSW index.** At ~1.8k chunks exact KNN is sub-ms with perfect
recall; HNSW (approximate) only pays off past ~10k rows.
- **D17 — Fail-fast DB timeouts.** A draft-email `ask` once hung 13+ min (0% CPU,
blocked on I/O) when it collided with a concurrent `load`'s row locks — the
pooler's default `lock_timeout` is 0 (wait forever). Supabase's pooler ignores
libpq `options`, so timeouts are now applied via a `SET` on each new connection:
`statement_timeout=30s`, `lock_timeout=10s`, plus `connect_timeout=10s`. A stuck
query/lock now aborts in seconds instead of wedging the agent — important for the
live demo.



## Project structure & migrations

- **D9 —** `freight_agent/db/` **package.** `engine.py` / `models.py` / `schemas.py`
with `__init__` re-exports; `reconcile.py`/`loaders.py` live under `ingestion/`.
- **D10 —** `create_all` **now, Alembic later.** Fine for a fresh `init-db`; doesn't
migrate an existing schema — known limitation, add Alembic autogenerate beyond a
clean re-seed.
- **D11 —** `uv` **+ single runbook.** Faster lock-based tooling; one
`runbooks/README.md` is easier to follow end-to-end than per-phase files.



## Product surface (API + UI)

- **D19 — FastAPI: read-only sessions, SSE streaming, in-app rate limit.** The public
API only reads, so its Postgres connections set `default_transaction_read_only=on`
on connect — a cheap, real guard that a buggy/compromised tool can't mutate the
store through the web surface (writes happen only via the offline `freight` CLI).
`POST /query` streams as SSE — a `status` event, then a `tool` event per tool call
as the agent decides it (via `agent.iter()` graph nodes), then a typed `result`
event — so the UI can show a live "what the agent is doing" trace; `/query/sync`
returns the same payload in one shot for tests/eval. Cross-cutting: request-id +
timing logs, CORS from settings, and a dependency-free per-IP fixed-window rate
limit (fine for a low-traffic MVP; swap for Redis/`slowapi` at scale). The
structured-output tool (`final_result`) is filtered out of the user-facing trace.
- **D20 — Next.js 15 + TS, plain CSS, fetch-stream SSE.** EventSource is GET-only, so
the client reads `POST /query`'s body with a `ReadableStream` reader and parses the
`event:/data:` frames itself. The UI is intentionally minimal (the brief asks for a
working minimum, not polish): example queries, live tool chips while streaming, an
answer card (confidence bar, follow-up badge, supporting-record chips), a
collapsible tool trace, evidence cards parsed from `search_communications` results,
and an editable+copyable draft composer. No Tailwind/UI kit — one global stylesheet
keeps the build dependency-light and the deploy trivial on Vercel.
- **D21 — Backend at root, frontend in `frontend/`.** Two deployables: the Python
service (FastAPI Cloud) and the Next.js UI (Vercel). Rather than a symmetric
`backend/` + `frontend/` split, the importable Python package stays at the repo root
(`freight_agent.api.app:app`, `freight` CLI) — idiomatic for Python and avoids
churning pyproject/console-script/pytest paths — and the one non-Python subproject is
boxed in `frontend/` (renamed from `web/`). The API is a subpackage (`freight_agent/api/`)
because it shares the agent, tools, and db models with the CLI. `docs/` and `runbooks/`
stay repo-wide since they describe both apps; `data/` is backend runtime state.

## Tracking

Commits use Conventional Commits and reference the relevant `Dn`; non-trivial
changes go through PRs that link back here, so the issues/docs/PRs trail stays
coherent.