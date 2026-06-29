# Runbook 03 — Agent

> Status: Planned. This runbook will be filled in when the agent module lands.
> Requires `OPENAI_API_KEY`.

## Scope (planned)

- Native typed tools: `get_load`, `resolve_carrier`, `get_carrier_history`,
  `get_rate_context`, `search_communications`, `best_offer_for_load`,
  `carriers_available_for_lane`.
- Pydantic AI agent (`gpt-5.5`) with structured-first routing and a typed
  response contract (answer, supporting records, tool calls, draft email).
- A CLI to ask the canonical broker questions and request a draft email.

## Commands (planned)

```bash
uv sync --extra dev --extra ai
uv run python -m freight_agent ask "What is the best rate on offer for load 29372515?"
uv run python -m freight_agent ask "Which carriers have confirmed availability for PA-NJ Box Truck loads this week?"
```

Depends on [02-ingestion.md](02-ingestion.md) being complete.
