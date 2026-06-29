# Runbook 02 — Ingestion

> Status: Planned. This runbook will be filled in when the ingestion module
> lands. Requires `OPENAI_API_KEY`.

## Scope (planned)

- Parse and normalize `carrier_emails.json` (deterministic regex pass +
  `gpt-5.4-mini` structured extraction).
- Transcribe the 55 call recordings with `gpt-4o-transcribe-diarize`.
- Extract structured fields (carrier identity, rate, availability, load ref).
- Reconcile carriers across email/call channels.
- Embed communications with `text-embedding-3-small` into the vector store.

## Commands (planned)

```bash
uv sync --extra dev --extra ai
uv run python -m freight_agent ingest emails
uv run python -m freight_agent ingest calls
uv run python -m freight_agent reconcile
uv run python -m freight_agent embed
```

See [01-data-foundation.md](01-data-foundation.md) — it must succeed first.
