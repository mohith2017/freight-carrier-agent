# Runbook 01 — Data foundation

Loads the clean structured artifacts (`loads.csv`, `rate_history.csv`,
`carrier_profiles.json`) into the relational core and verifies them. This is the
system-of-record that everything else builds on.

- **Needs OpenAI key?** No.
- **Default store:** local SQLite at `data/freight.db`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed.
- The dataset folder present next to the repo at `../goodlane-interview-dataset/`
  (override with `DATASET_DIR` in `.env`).

## One-time setup

From the repo root:

```bash
uv venv
uv sync --extra dev
cp .env.example .env
```

Defaults in `.env` work out of the box; no edits required for this runbook.

## Run

Run these in order. Each prints its result to the terminal.

### 1. Create the schema

```bash
uv run python -m freight_agent init-db
```

Expected output (one line per target store):

```
schema ready -> sqlite:////.../freight-carrier-agent/data/freight.db
```

### 2. Load the data

```bash
uv run python -m freight_agent load
```

Expected output:

```
loaded {'loads': 50, 'rate_history': 720, 'carriers': 48} -> sqlite:////.../data/freight.db
```

### 3. Verify

```bash
uv run python -m freight_agent verify
```

Expected output:

```
       Row counts (primary store)
┏━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━┳━━━━┓
┃ table        ┃ count ┃ expected ┃ ok ┃
┡━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━╇━━━━┩
│ loads        │    50 │       50 │ ok │
│ carriers     │    48 │       48 │ ok │
│ rate_history │   720 │      720 │ ok │
└──────────────┴───────┴──────────┴────┘

Rate check load 29372289 (PA->DE, Box Truck): $310.0 / 82mi = 3.7805/mi; market avg 3.66/mi -> near

primary store: sqlite:////.../data/freight.db
```

`verify` exits non-zero if any row count is wrong, so it doubles as a smoke test.

## Automated tests

```bash
uv run pytest
```

Expected: `7 passed`. Covers row counts, messy-field parsing (blank weights ->
null, CONDITIONAL/null authority counts), the flat->per-mile rate conversion,
and the Postgres+SQLite backup fan-out logic.

## Fresh rebuild from scratch

```bash
rm -f data/freight.db
uv run python -m freight_agent init-db
uv run python -m freight_agent load
uv run python -m freight_agent verify
```

## Optional: use Supabase Postgres as the primary store

Paste the connection string from the Supabase dashboard into `.env` as-is:

```
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

The app auto-rewrites the scheme to the psycopg v3 driver
(`postgresql+psycopg://`), so a plain `postgresql://` (or `postgres://`) URL
works without edits. URL-encode special characters in the password (e.g.
`@` -> `%40`).

Then install the Postgres extra and re-run:

```bash
uv sync --extra dev --extra pg
uv run python -m freight_agent init-db   # also enables the pgvector extension
uv run python -m freight_agent load
```

With `DATABASE_URL` set, Postgres becomes primary and the local SQLite file is
maintained automatically as a backup (writes fan out to both).

## Troubleshooting

- **`dataset not found`** — check `DATASET_DIR` in `.env` points at the dataset
  folder; the default is `../goodlane-interview-dataset`.
- **`ModuleNotFoundError: freight_agent`** — run via `uv run python -m freight_agent ...`
  from the repo root. The bare `freight` console script is unreliable here because
  the install path contains spaces.
- **Wrong Python version** — uv uses the version pinned in `.python-version` (3.12).
