# bowlyzer-api

Read-only **stats HTTP API** for Bowl-A-Lyzer. It consumes **published Parquet**
from the sibling `bowlyzer_deploy` repo (`database/data/*.parquet`) into
DuckDB and will serve `/api/v1`. It does **not** scrape, import Excel, or run
the Gravity Forms pipeline.

Language of the serving process is still open (Go + DuckDB is the default
candidate). This repo currently uses a small Python placeholder so we can
import data and probe health without locking the stack.

## What this repo is not

- No data acquisition
- No Flask JSON cache warmup
- No `/pipeline/*` diagnosis UI
- No `?database=` session switcher — one warehouse

## Layout

| Path | Role |
|------|------|
| `scripts/import_parquet.sql` | Typed `game_line` + `tournament_line` |
| `scripts/run_import.py` | Runs SQL + registry dims + `warehouse_meta` |
| `app/server.py` | Placeholder `GET /api/v1/health` |
| `data/bowlyzer.duckdb` | Local warehouse (gitignored) |
| `openapi/` | `/api/v1` contract (next) |
| `docs/` | Flask → v1 map and benches (next) |

KO config JSON (`tournament_ko_config.json`, stage definitions) stays as files
in the publish dir; it is not loaded into DuckDB.

## Local import (no Docker)

From this directory, with the sibling deploy repo at `../bowlyzer_deploy`:

```bash
uv sync --system-certs
uv run python scripts/run_import.py
uv run python -m app.server
```

Then `GET http://127.0.0.1:8080/api/v1/health`. Expect `game_line` on the order
of 700k+ rows and `tournament_line` ~64k.

Override paths:

```bash
set PARQUET_DIR=C:\Users\cfell\repositories\bowlyzer_deploy\database\data
set WAREHOUSE_PATH=C:\Users\cfell\repositories\bowlyzer-api\data\bowlyzer.duckdb
uv run python scripts/run_import.py
```

## Docker

```bash
docker compose run --rm import
docker compose up api
```

`BOWLYZER_PARQUET_DIR` defaults to `../bowlyzer_deploy/database/data`. Point it
at any published Parquet directory. After a weekend league drop, re-run
`import` — do not rebuild a JSON cache farm.

## Next

1. Feasibility spike: DuckDB field-progress / tournament section vs Flask cold
   `GET /tournament/get_section`
2. OpenAPI for `GET /api/v1/tournaments/{season}/{event}`
3. Flask RPC → v1 keep/merge/drop map
