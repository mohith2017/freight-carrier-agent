# Decision Log

An ADR-style running log of the meaningful decisions on this project: the
context, the choice, the alternatives considered, and the trade-off. It is
written as the build progresses (not reconstructed at the end), so it doubles as
the "documentation of decisions" deliverable.

Format per entry: **Context → Decision → Alternatives → Trade-off / what I'd
improve.** Most recent first within each phase.

---

## Data store & vendor

### D1 — Supabase Postgres + `pgvector` as the primary store, SQLite as local backup
- **Context.** The high-value broker questions ("best rate on load #X", "who's
  available Friday on this lane", compliance status) are relational/aggregate;
  semantic search is only needed for supporting evidence (email bodies, call
  utterances) and drafting tone.
- **Decision.** One managed Postgres holds both the normalized system-of-record
  **and** the vector index (`pgvector`). A SQLite mirror is written in parallel
  so ingestion can run fully offline and the structured data is portable and
  committable.
- **Alternatives.** A dedicated vector DB (Qdrant / Chroma / Pinecone /
  Weaviate) alongside Postgres.
- **Trade-off.** For ~329 documents a separate vector service is pure operational
  overhead (second store to deploy, sync, and keep consistent). `pgvector`
  comfortably covers this scale, and keeping vectors next to the relational rows
  means joins/filters (by `load_id`, `carrier_id`, lane) happen in one query.
  At much larger scale a purpose-built ANN store would win.

### D2 — Single vendor (OpenAI) for agent + transcription + embeddings
- **Decision.** OpenAI for everything: one API key, one SDK, one bill, one set
  of failure modes to reason about.
- **Alternatives.** Best-of-breed per task (e.g. ElevenLabs Scribe / Deepgram
  Nova for STT, a different LLM for the agent).
- **Trade-off.** Slightly less than absolute-best transcription accuracy, which
  the brief explicitly does not require ("Perfect transcription accuracy" is a
  non-goal). Simplicity and demo reliability win here.

---

## Models (verified GA as of Jun 28, 2026)

### D3 — Model selection
- **Agent:** `gpt-5.5` — latest *generally available* flagship. (GPT-5.6 shipped
  Jun 26 but is limited-preview to a handful of vetted partners, **not GA** —
  cannot use it.) `gpt-5.4` is the cheaper fallback if cost matters.
- **Extraction:** `gpt-5.4-mini` — cheap, fast, strict structured outputs; the
  right tier for high-volume deterministic-ish extraction across 329 docs.
- **Transcription:** `gpt-4o-transcribe-diarize` — adds speaker labels, ideal
  for 2-party broker/carrier calls with cross-talk; ~$0.006/min so the whole
  55-file corpus costs a few cents.
- **Embeddings:** `text-embedding-3-small` (1536 dims) — current default; corpus
  is tiny so `3-large` is unnecessary.

---

## Agent & tools

### D4 — Pydantic AI native typed tools, **not** FastMCP
- **Context.** The agent needs tool calls (load lookup, carrier resolution, rate
  context, comms search). The user initially considered FastMCP.
- **Decision.** Native Pydantic AI typed function tools in-process.
- **Alternatives.** FastMCP / MCP transport between agent and tools.
- **Trade-off.** MCP adds a transport hop and a live-demo failure surface for
  zero benefit when agent and tools share one process. Typed deps + validated
  args/outputs are exactly what guards against fabricated load IDs or compliance
  status. FastMCP remains an optional sidecar if we later want to expose the
  same tools to external MCP clients (possible live-extension flair).

---

## Ingestion & data quality

### D5 — Deterministic parse first, LLM extraction second
- **Decision.** Run a cheap, auditable regex pass (MC numbers, `$rate` in body,
  load refs, dates, equipment) before sending to `gpt-5.4-mini` for structured
  extraction with confidence + evidence spans. Prompt rule: prefer `null` over
  guessing; record ambiguity, never invent identity/rate.
- **Why.** `carrier_emails.json` has `rate_quoted_usd` **null on all 274** rows —
  the rate lives in free-text bodies ("We could do $735"), so the field can
  never be trusted and the body must be parsed. Deterministic-first keeps the
  common cases cheap and traceable; the LLM handles the messy tail.

### D6 — Carrier identity by stable business key + upsert (FK-safe re-runs)
- **Context.** Re-running `load` after ingestion threw
  `ForeignKeyViolation: comm_events_carrier_id_fkey` / `..._load_id_fkey`,
  because loaders did `DELETE` + `INSERT` and reconciliation had already linked
  `comm_events` to those parent rows. A follow-up interview scenario ("re-load
  with a newer dataset") would hit this immediately.
- **Decision.** Loaders now **upsert**: `loads` by natural key (`load_id`);
  `carriers` by a `carrier_business_key` (MC → email → company name → file
  position). Existing rows are updated in place (surrogate `carrier_id`
  preserved); unseen rows are inserted. No deletes, so reconciled
  `comm_events` links are never orphaned.
- **Alternatives.** Truncate-and-reload (simple but destroys FK links and any
  reconciliation work); `ON CONFLICT` raw SQL (less portable across the
  SQLite/Postgres dialects we dual-write to).
- **Trade-off.** Slightly more code than delete+insert, but it makes re-loading
  an updated/expanded dataset converge correctly — which is the realistic
  operational requirement.

### D7 — Incremental ingestion mode
- **Decision.** `--incremental` flags on `ingest emails`, `ingest calls`,
  `embed`, and `ingest all` process only new records (new email IDs, new WAV
  stems, events lacking chunks).
- **Why.** Folding in a newer dataset shouldn't re-transcribe 55 calls or
  re-embed 329 chunks (cost + time). Full rebuild remains the default for a
  clean slate.

---

## Persistence resilience

### D8 — Harden embedding writes against the Supabase pooler
- **Context.** The `embed` step intermittently died with
  `OperationalError: SSL error: ssl/tls alert bad record mac` mid-run. Cause:
  one ~2 MB `INSERT … VALUES` per 64-row batch (each row carries a ~30 KB
  1536-dim vector string) stressing the pooled TLS connection; a single hiccup
  aborted the whole pipeline with no retry.
- **Decision.** Defense in depth:
  (a) Postgres engine uses `pool_pre_ping=True`, TCP keepalives, and
  `insertmanyvalues_page_size=20` so each round-trip is small;
  (b) embed writes go out in 20-row batches that catch transient
  `OperationalError`/`DBAPIError`, roll back, **dispose the poisoned
  connection**, back off, and retry (up to 4×).
- **Trade-off.** More, smaller INSERTs (marginally slower) in exchange for a run
  that self-heals instead of failing on a network blip.
- **What I'd improve.** The deeper fix is the connection target: Supabase's
  *transaction pooler* (6543) is hostile to large/prepared statements. Switching
  `DATABASE_URL` to the *session pooler* (5432) or direct connection — or setting
  `prepare_threshold=None` for the pooler — would remove the root cause.

### D12 — Defer HNSW index on `knowledge_chunks`
- **Decision.** Skip HNSW at ~1.8k chunks: exact pgvector KNN is sub-millisecond with perfect recall; HNSW is approximate and only worth adding past ~10k rows (with tuned `m` / `ef_construction`).

---

## Project structure & migrations

### D9 — `freight_agent/db/` package
- **Decision.** Database concerns live under `freight_agent/db/`:
  `engine.py` (engine/session/init helpers), `models.py` (ORM tables),
  `schemas.py` (Pydantic input validation), with `db/__init__.py` re-exporting
  the engine helpers so `from freight_agent.db import make_engine, …` still
  works. `reconcile.py`/`loaders.py` live under `ingestion/` as pipeline steps.
- **Why.** Clear separation: `db` = how to talk to the store, `db.models` =
  tables, `db.schemas` = validation; ingestion = the multi-modal pipeline.

### D10 — `create_all` now, Alembic noted as an improvement
- **Decision.** Schema is created with SQLAlchemy `Base.metadata.create_all` via
  `init_schema`; **Alembic is not wired in.**
- **Why.** For an MVP with a fresh `init-db`, `create_all` is enough, and the
  cross-dialect (Postgres + SQLite) models stay the single source of truth.
- **What I'd improve.** `create_all` does not migrate an existing schema (no
  column adds, no down-migrations). For anything beyond a clean re-seed, add
  Alembic with autogenerate. Called out explicitly as a known limitation /
  talking point.

### D11 — Tooling: `uv`, single-document runbook
- **Decision.** `uv` for all dependency/venv/run commands (replacing `pip`); a
  single consolidated `runbooks/README.md` instead of per-phase runbooks.
- **Why.** `uv` is faster and lock-based; one runbook is far easier for an
  interviewer to follow end-to-end than five files.

---

## How decisions, issues & PRs are tracked

- **This file** is the canonical decision record.
- **Commits** follow Conventional Commits (`feat:`, `fix:`, `refactor:`); each
  decision-bearing change references its rationale here (e.g. *D6* → the carrier
  upsert commit).
- When a change is non-trivial it goes through a PR whose description links back
  to the relevant `Dn` entry, so the "issues / docs / PRs" trail is coherent
  rather than scattered.
