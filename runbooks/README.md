# Run guide

Everything needed to run and verify the project locally, top to bottom. Run all
commands from the repo root (`freight-carrier-agent/`). `uv run` handles the
virtualenv for you — no `activate` needed.

> The deployed app is the primary deliverable; this guide is for running the
> pipeline and agent locally.
>
> **Live:** UI → https://freight-carrier-agent.vercel.app  ·
> API → https://freight-carrier-agent.fastapicloud.dev/docs

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed.
- The dataset folder next to the repo at `../goodlane-interview-dataset/`
  (override with `DATASET_DIR` in `.env`).
- An `OPENAI_API_KEY` for ingestion/agent steps (the data-foundation step and
  the offline tests need no key).

## Setup

```bash
uv venv
uv sync --extra dev --extra ai --extra pg   # pg = Supabase/Postgres driver
cp .env.example .env                         # then set OPENAI_API_KEY
```

Defaults use a local SQLite store at `data/freight.db` — no DB to provision
(the `pg` extra is harmless if you stay on SQLite). To use Supabase instead, see
[Using Supabase](#using-supabase-optional) below.

## Step 1 — Data foundation (no API key needed)

Loads the clean structured artifacts (`loads.csv`, `rate_history.csv`,
`carrier_profiles.json`) into the relational system-of-record and verifies them.

```bash
uv run python -m freight_agent init-db   # create schema (+ pgvector on Postgres)
uv run python -m freight_agent load      # load loads / carriers / rate_history
uv run python -m freight_agent verify    # row counts + a sample rate check
```

Expected `verify`:

```
       Row counts (primary store)
┏━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━┳━━━━┓
┃ table        ┃ count ┃ expected ┃ ok ┃
┡━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━╇━━━━┩
│ loads        │    50 │       50 │ ok │
│ carriers     │    48 │       48 │ ok │
│ rate_history │   720 │      720 │ ok │
└──────────────┴───────┴──────────┴────┘
Rate check load 29372289 (PA->DE, Box Truck): $310.0 / 82mi = 3.78/mi; market avg 3.66/mi -> near
```

`verify` exits non-zero on any wrong count, so it doubles as a smoke test.

## Step 2 — Multi-modal ingestion (needs API key)

Turns 274 emails + 55 call recordings into the evidence layer the agent reasons
over: `comm_events`, `offers`, and embedded `knowledge_chunks`, with carriers and
loads linked (and cross-channel carriers flagged).

```bash
uv run python -m freight_agent ingest all
```

One command runs emails → calls → reconcile → embed → verify. The first run
transcribes all 55 WAVs (~25–30 min) with `gpt-4o-transcribe-diarize` and caches
each transcript under `data/transcripts/`, so re-runs take a couple of minutes.

Flags: `--no-llm` (deterministic extraction only, faster/cheaper) ·
`--skip-calls` (emails only).

Expected tail:

```
pipeline complete
Ingestion counts (primary store)
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ metric                ┃ count ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ email events          │   274 │
│ call events           │    55 │
│ offers                │   329 │
│ carrier-linked events │  ~250 │
└───────────────────────┴───────┘
```

Re-running is safe: each step clears and rebuilds its own slice.

<details>
<summary>How ingestion works / individual steps</summary>

```
emails ─┐  deterministic regex   ┌─ comm_events
        ├─ parse + gpt-5.4-mini ──┤─ offers
calls ──┘  (diarized transcribe)  └─ knowledge_chunks (text-embedding-3-small)
                                     then reconcile: link carrier + load, flag cross-channel
```

- **Deterministic first:** regexes pull MC numbers, $ / per-mile rates, 8-digit
  load ids, equipment, availability. `rate_quoted_usd` is null on all 274 emails,
  so the rate is parsed from the body — identity and money stay grounded in text.
- **LLM second (`gpt-5.4-mini`):** fills intent, nuanced availability, dates;
  merged so deterministic identity/rate wins.
- **Calls:** diarized transcripts cached in `data/transcripts/`; garbled spoken
  MC numbers corrected against the carrier roster.
- **Reconcile:** carrier via MC → email domain → fuzzy name; load via
  `load_reference`; carriers in both channels flagged cross-channel.

Run a single stage to debug it:

```bash
uv run python -m freight_agent ingest emails [--no-llm]
uv run python -m freight_agent ingest calls
uv run python -m freight_agent reconcile
uv run python -m freight_agent embed
uv run python -m freight_agent verify-ingest
```
</details>

## Step 3 — Ask the agent (needs API key)

The agent (Pydantic AI, `gpt-5.5`) answers broker questions over the ingested
data using seven typed tools (load lookup, carrier resolution, rate context,
hybrid comms search, best offer, lane availability) with structured-first routing
and a compliance gate.

```bash
uv run python -m freight_agent ask "What is the best rate on offer for load #29372289, and how does it compare to market?"
uv run python -m freight_agent ask "Which carriers have confirmed availability for PA to DE Box Truck loads?"
uv run python -m freight_agent ask "Draft a reply to the carrier with the best rate on load #29372289 confirming next steps."
```

Each answer prints the grounding record IDs, a confidence score, a follow-up flag
(true when data is missing/ambiguous), and a draft email when one is requested.
The compliance gate surfaces non-ACTIVE authority or expired/missing insurance
before suggesting a booking.

## Step 4 — Run the web app (API + UI)

The same agent is exposed over a FastAPI backend and a Next.js chat UI. The
backend streams the answer over SSE (a `status` event, a `tool` event per tool
call, then the typed result) and exposes supporting lookups.

**Backend (FastAPI).** Needs the `api` extra:

```bash
uv sync --extra dev --extra ai --extra pg --extra api
uv run uvicorn freight_agent.api.app:app --reload --port 8000
```

Quick checks (no API key needed for these):

```bash
curl localhost:8000/health
curl localhost:8000/loads/29372289
curl "localhost:8000/rates/context?origin=PA&destination=NJ&equipment=Box%20Truck&flat_usd=285&distance_miles=75"
curl -N -X POST localhost:8000/query -H 'content-type: application/json' \
  -d '{"question":"best rate on offer for load #29372289 vs market?"}'   # SSE; needs OPENAI_API_KEY
```

Endpoints: `POST /query` (SSE), `POST /query/sync` (one-shot JSON), `GET /health`,
`GET /loads/{id}`, `GET /carriers/resolve?q=`, `GET /carriers/{id}/history`,
`GET /rates/context`. Interactive docs at `http://localhost:8000/docs`. The API's
DB sessions are read-only on Postgres; it only ever reads the store.

**Frontend (Next.js).** In a second terminal:

```bash
cd frontend
npm install
cp .env.example .env.local        # NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev                        # http://localhost:3000
```

Open `http://localhost:3000`, click an example query or type your own. You'll see
live tool chips while the agent works, then the answer with a confidence bar,
follow-up badge, supporting-record chips, a collapsible tool trace, evidence
cards, and an editable/copyable draft when one is requested.

## Tests

```bash
uv run pytest
```

All offline (no API calls): row counts and rate math, messy-field parsing,
deterministic extractors, MC fuzzy-correction, the carrier-resolution cascade,
the full 274-email pipeline on real data, idempotency, cross-channel flagging,
chunking/embedding (via a fake embedder), the agent's tools + hybrid retrieval +
compliance gate, the agent wiring (via Pydantic AI's `TestModel`), and the API
(query sync/SSE, supporting lookups, rate limiting — `TestModel` + a read-only
deps override), and the eval scorers (deterministic, stub task). Frontend:
`cd frontend && npx tsc --noEmit && npm run build`.

## Step 5 — Evaluation (needs API key)

A Pydantic Evals harness scores the core workflow over 13 goldens grounded in the
real data. It runs the **real agent** against the primary store, so set
`OPENAI_API_KEY` (and `DATABASE_URL` to evaluate against Supabase).

```bash
uv run python -m evals.run                  # deterministic scorers + LLM judges
uv run python -m evals.run --no-judges      # cheaper: skip the LLM judges
uv run python -m evals.run --json evals/last_run.json   # also write JSON
```

You'll get a per-case table and averages for: entity resolution, tool selection,
fact coverage, no-fabrication, follow-up correctness, draft presence, and (with
judges) answer quality + draft factuality. Lower `--max-concurrency` if you hit
rate limits. The deterministic scorers are covered offline by `tests/test_evals.py`.

## Deploying (pre-seeded; the app never ingests on boot)

Live: UI → https://freight-carrier-agent.vercel.app  ·
API → https://freight-carrier-agent.fastapicloud.dev/docs

1. **Seed Supabase once, locally** — with `DATABASE_URL` set, run Steps 1–2
   (`init-db`, `load`, `ingest all`). The deployed backend just reads this.
2. **Backend** — serve the ASGI app on FastAPI Cloud (`fastapi deploy`) or any
   host (`uvicorn freight_agent.api.app:app`). Set `DATABASE_URL`,
   `OPENAI_API_KEY`, and `CORS_ORIGINS=<deployed frontend URL>`.
3. **Frontend** — on Vercel set Root Directory to `frontend/` and
   `NEXT_PUBLIC_API_URL=<deployed backend URL>`; Next.js is auto-detected.

CI (`.github/workflows/ci.yml`) runs ruff + mypy + pytest and the frontend build
on every push — all offline, green on a fresh clone.

## Updating with a newer dataset

The pipeline is built to fold in new/updated data through the same commands —
point `DATASET_DIR` at the newer dataset (or drop the new files into the
existing one) and re-run. Two modes:

**Full re-run (convergent, default).** Re-runs everything and converges to the
new dataset's exact state:

```bash
uv run python -m freight_agent load          # upserts loads & carriers (FK-safe)
uv run python -m freight_agent ingest all     # rebuilds comms, reconciles, embeds
```

`load` upserts rather than wipes: loads merge on `load_id`, carriers merge on a
stable business key (MC → email → company name). So a carrier's `carrier_id`
stays put across reloads and the `comm_events` links from reconciliation are
never orphaned (this is enforced by the FK in Postgres).

**Incremental (only new records).** For adding a fresh batch without
reprocessing (or re-billing) the whole corpus — ideal for a quick live update:

```bash
uv run python -m freight_agent load                      # cheap upsert
uv run python -m freight_agent ingest all --incremental  # new emails/calls + embed only
```

In incremental mode, emails/calls already ingested (by `email_id` / file stem)
are skipped, only unembedded events are embedded, and reconcile still runs over
everything (cheap, no API) so cross-channel flags and links stay correct.

## Using Supabase (optional)

Paste the connection string from the Supabase dashboard into `.env` as-is — the
app auto-rewrites it to the psycopg v3 driver, so a plain `postgresql://` URL
works (URL-encode special chars in the password, e.g. `@` → `%40`):

```
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

The `pg` extra from Setup already includes the driver, so just run Steps 1–2.
`init-db` enables the pgvector extension automatically. With `DATABASE_URL` set,
Postgres is primary and the local SQLite file is kept as an automatic backup
(writes fan out to both).

## Troubleshooting

- **`carrier-linked events: 0`** — run `reconcile` (or `ingest all`); linking
  happens there, not during ingest.
- **`dataset not found`** — point `DATASET_DIR` in `.env` at the dataset folder
  (default `../goodlane-interview-dataset`).
- **`No cached transcript ... and no transcriber provided`** — set
  `OPENAI_API_KEY` so calls can transcribe, or restore `data/transcripts/`.
- **`ModuleNotFoundError: freight_agent`** — run via
  `uv run python -m freight_agent ...` from the repo root (the bare `freight`
  console script is unreliable when the install path contains spaces).
- **Model availability** — models are configurable in `.env` (`AGENT_MODEL`,
  `EXTRACTION_MODEL`, `TRANSCRIBE_MODEL`, `EMBED_MODEL`); swap to
  `gpt-4o-transcribe` if diarization is unavailable.
