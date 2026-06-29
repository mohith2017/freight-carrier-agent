# AI Artifacts & How AI Was Used

The brief asks to "demonstrate your ability to use AI-native engineering
practices" and to include "artifacts relevant to your use of AI." This documents
how AI was used to build the project (development-time) and how AI powers the
product itself (runtime), plus the prompts/schemas that matter.

---

## 1. AI in the build loop (development-time)

This project was built with an AI coding agent in Cursor as the primary driver,
used for:

- **Scaffolding & implementation** — package layout, SQLAlchemy models,
  cross-dialect (Postgres/SQLite) wiring, Typer CLI, the ingestion pipeline.
- **Debugging from real tracebacks** — pasting failing runs and having the agent
  root-cause them. Notable cases captured in [`DECISIONS.md`](DECISIONS.md):
  - FK violation on re-`load` → carrier upsert by business key (D6).
  - Supabase pooler `ssl/tls alert bad record mac` on embed → batched + retrying
    writes and engine keepalives (D8).
  - `unhashable type: 'Settings'` in the OpenAI client cache, diarization models
    rejecting `prompt`, datetime deprecation, mypy/ruff fixes.
- **Refactors** — moving DB code into `freight_agent/db/` and updating imports.
- **Decision pressure-testing** — comparing two prior deep-research proposals,
  correcting stale model names, and rejecting over-engineered options (separate
  vector DB, LangGraph) for this scale.

**Working style:** small, verifiable steps. After substantive edits the agent
runs `ruff`, `mypy`, and `pytest` before moving on, so changes stay green rather
than accumulating. Decisions are written to the log *as they happen*.

### Artifacts to point reviewers at
- `docs/DECISIONS.md` — the reasoning trail (D1–D11), the clearest evidence of
  AI-assisted decision-making.
- **Conventional-commit history / PRs** — each decision-bearing change links back
  to its `Dn` entry.
- **Cursor agent transcripts** — exported chat logs of the build sessions
  (attach/link before submission).
- This file.

---

## 2. AI in the product (runtime)

Three distinct AI roles, deliberately tiered by cost/capability (D3):

| Stage | Model | Why this tier |
|---|---|---|
| Call transcription | `gpt-4o-transcribe-diarize` | speaker labels for 2-party calls with cross-talk; cents for 55 files |
| Structured extraction | `gpt-5.4-mini` | cheap, strict JSON over 329 docs |
| Agent / answers & drafts | `gpt-5.5` | latest GA flagship for reasoning + tool use |
| Embeddings | `text-embedding-3-small` | 1536-dim, sufficient for a tiny corpus |

### Key prompt/schema designs (the parts that carry the quality)

- **Deterministic-then-LLM extraction (D5).** A regex pass extracts the
  unambiguous signals (MC numbers with/without dashes, `$` rates in the body,
  load refs, dates, equipment) *before* the LLM. The LLM fills a strict schema
  (`carrier_identifiers`, `load_reference`, `intent`, `quoted_rate_usd`,
  `rate_type`, `equipment_type`, `availability`, `questions[]`,
  `confidence_notes[]`, `needs_human_review`) with the explicit rule: **prefer
  `null` over guessing; record ambiguity; never invent identity or rate.** This
  is what keeps messy data from corrupting canonical records.
- **Transcription glossary prompt.** The diarization call is primed with a
  domain glossary built from our own load IDs, state pairs, equipment types,
  shipper names, and known carrier names/MCs — directly targeting garbled or
  corrected-mid-sentence MC numbers.
- **Structured-first retrieval policy (enforced in code, not just the prompt).**
  If a question names a load / MC / lane / date, the agent must call the
  structured tool before semantic search; hybrid score is
  `0.55*vector + 0.25*fts + 0.20*metadata_boost`. (Built in Phase 3.)
- **Typed output contract & compliance gate.** The agent returns a typed object
  (`answer`, `supporting_records`, `tool_calls`, `confidence`,
  `follow_up_needed`, `draft_email`) and must surface `authority_status` /
  `insurance_expiry` before suggesting a booking. Typed validation is the guard
  against fabricated load IDs or compliance claims.

---

## 3. Reproducibility notes

- Exact prompts and schemas live in code under `freight_agent/ingestion/`
  (`extract.py`, `llm.py`) and, for the agent, in the Phase-3 agent/tools modules.
- All AI calls go through one vendor/SDK (D2); set `OPENAI_API_KEY` in `.env`.
- Expensive AI steps (transcription, embedding) cache to `data/transcripts/` and
  the DB, and support `--incremental` (D7), so re-runs are cheap and
  deterministic enough to demo live.

> _Update before submission: attach/link the exported Cursor transcripts and the
> PR list so this section points at concrete files._
