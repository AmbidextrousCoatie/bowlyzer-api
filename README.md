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
| `bowlyzerapi/http.py` | `/api/v1` router for resources in `docs/resources.md` |
| `bowlyzerapi/server.py` | Placeholder HTTP server |
| `bowlyzerapi/engine/` | Typed SQL catalog + parameterized query compiler |
| `openapi/openapi.yaml` | `/api/v1` contract (source of truth) |
| `docs/resources.md` | Flask RPC → v1 keep / merge / drop |
| `docs/bench-get-section.md` | DuckDB vs Flask kernel timings |
| `data/bowlyzer.duckdb` | Local warehouse (gitignored) |

KO config JSON (`tournament_ko_config.json`, stage definitions) stays as files
in the publish dir; it is not loaded into DuckDB.

## Local import (no Docker)

From this directory, with the sibling deploy repo at `../bowlyzer_deploy`:

```bash
uv sync --system-certs
uv run python scripts/run_import.py
uv run python -m bowlyzerapi.server
```

Then `GET http://127.0.0.1:8080/api/v1/health`. Expect `game_line` on the order
of 700k+ rows and `tournament_line` ~64k.

Query kernels:

```text
GET /api/v1/leagues/25%2F26/{league}/standings
GET /api/v1/tournaments/25%2F26/Bayerische%20Meisterschaft%20-%20M%C3%A4nner%20Einzel
GET /api/v1/clubs/BC%20EMAX%20Unterf%C3%B6hring/history
```

Query-string aliases (same payloads; easier when `season` contains `/`):

```text
GET /api/v1/leagues/standings?season=25/26&league=…
GET /api/v1/tournaments/section?season=25/26&event=Bayerische Meisterschaft - Männer Einzel
GET /api/v1/clubs/history?club=BC EMAX Unterföhring
```

Correctness vs Flask (run from `bowlyzer_deploy` so pandas adapters work):

```bash
uv run --system-certs --with duckdb python ..\bowlyzer-api\scripts\compare_queries.py
```

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

1. Query performance — N+1 standings / podiums / player-tournament places; see [`docs/perf.md`](docs/perf.md)
2. Staging → publish for live league days (verified results only)
3. Remaining Flask: i18n, session/Datenquelle, diagnosis / pipeline
