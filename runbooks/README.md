# Runbooks

Operational, copy-pasteable instructions for running and verifying each part of
the system. Each runbook is self-contained: what it does, prerequisites, the
exact commands, the output you should expect, and how to troubleshoot.

As new modules land (ingestion, agent, API/UI, evals), each gets its own runbook
here so run instructions stay separated and don't bloat the root README.

## Index

| # | Runbook | Status | Needs OpenAI key? |
|---|---------|--------|-------------------|
| 01 | [Data foundation](01-data-foundation.md) — schema + load + verify | Available | No |
| 02 | [Ingestion](02-ingestion.md) — emails, call transcription, embeddings | Planned | Yes |
| 03 | [Agent](03-agent.md) — tools + broker Q&A + draft emails | Planned | Yes |
| 04 | [Product surface](04-product-surface.md) — FastAPI backend + Next.js UI | Planned | Yes |
| 05 | [Eval, deploy, docs](05-eval-deploy.md) — eval run + deployment | Planned | Yes |

## Conventions used in every runbook

- Run all commands from the repo root: `freight-carrier-agent/`.
- Commands are prefixed with `uv run` so the virtualenv is handled for you
  (no `source .venv/bin/activate` needed).
- "Expected output" blocks show a trimmed version of a successful run.

## Seeing and capturing command output

All commands print results to your terminal (stdout). To also save output to a
file while still seeing it live, pipe through `tee`:

```bash
mkdir -p runbooks/logs
uv run python -m freight_agent verify | tee runbooks/logs/verify.log
```

`runbooks/logs/` is gitignored. Note that piping to a file strips the colored
formatting (plain text is written), which is expected.
