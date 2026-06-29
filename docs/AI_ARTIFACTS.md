# AI Artifacts & How AI Was Used

How AI was used to **build** the project and how it **powers** the product.

## Build-time

Built with an AI coding agent in Cursor as the primary driver: scaffolding,
SQLAlchemy/cross-dialect wiring, the ingestion pipeline and agent; debugging from
real tracebacks (FK-on-reload → D6; Supabase `bad record mac` → D8; thread-unsafe
sessions → D14); refactors; and pressure-testing choices (rejected a separate
vector DB / LangGraph at this scale). Working style: small steps, `ruff`+`mypy`+
`pytest` green before moving on, decisions logged as they happen.

**Artifacts:** `[DECISIONS.md](DECISIONS.md)` (the reasoning trail), the
Conventional-commit/PR history that references each `Dn`, and the exported Cursor
transcripts (linked at submission).

## Runtime


| Stage         | Model                       | Why                                                  |
| ------------- | --------------------------- | ---------------------------------------------------- |
| Transcription | `gpt-4o-transcribe-diarize` | speaker labels for 2-party calls; cents for 55 files |
| Extraction    | `gpt-5.4-mini`              | cheap strict JSON over 329 docs                      |
| Agent         | `gpt-5.5`                   | latest GA flagship for reasoning + tool use          |
| Embeddings    | `text-embedding-3-small`    | 1536-dim, ample for a tiny corpus                    |


**Designs that carry the quality:**

- **Deterministic-then-LLM extraction (D5):** regex grounds identity/rate, then a
strict schema with "prefer `null`, never invent" — keeps messy data out of
canonical records.
- **Transcription glossary:** diarization primed with our load IDs, lanes,
equipment, shippers, and carrier names/MCs to fix garbled spoken MC numbers.
- **Structured-first retrieval (D13/D15):** load/MC/lane ⇒ SQL tool before
semantic search; hybrid score `0.55·vector + 0.25·lexical + 0.20·metadata`.
- **Typed contract + compliance gate (D15):** typed `AgentResponse` validation
guards against fabricated IDs; authority/insurance surfaced before booking.



## Reproducibility

Prompts/schemas live in `freight_agent/ingestion/` (`extract.py`, `llm.py`) and
the agent modules (`agent.py`, `tools.py`, `retrieval.py`). One vendor (D2); set
`OPENAI_API_KEY`. Transcription/embeddings cache and support `--incremental` (D7),
so re-runs are cheap and demo-safe.