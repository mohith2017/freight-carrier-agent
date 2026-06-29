# Runbook 04 — Product surface

> Status: Planned. This runbook will be filled in when the backend API and
> frontend land. Requires `OPENAI_API_KEY`.

## Scope (planned)

- FastAPI backend (`/query` SSE stream, `/draft`, `/loads/{id}`,
  `/carriers/resolve`, `/rates/context`, `/health`).
- Next.js + TypeScript chat UI: streaming answer, tool-call trace, retrieved
  evidence tabs, editable draft-email composer.

## Commands (planned)

```bash
# Backend
uv sync --extra dev --extra ai --extra api
uv run uvicorn freight_agent.api.app:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

Depends on [03-agent.md](03-agent.md) being complete.
