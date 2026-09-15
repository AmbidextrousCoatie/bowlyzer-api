"""Load published Parquet from bowlyzer_deploy into a local DuckDB warehouse.

No acquisition. Re-run after the sibling repo publishes new Parquet.

Environment:
  PARQUET_DIR      directory with *.parquet (default: ../bowlyzer_deploy/database/data)
  WAREHOUSE_PATH   DuckDB file (default: <repo>/data/bowlyzer.duckdb)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = Path(__file__).resolve().parent / "import_parquet.sql"
DEFAULT_PARQUET = REPO_ROOT.parent / "bowlyzer_deploy" / "database" / "data"
DEFAULT_WAREHOUSE = REPO_ROOT / "data" / "bowlyzer.duckdb"

DIM_SPECS: list[tuple[str, str, str]] = [
    (
        "player",
        "players_registry.parquet",
        """
        CREATE OR REPLACE TABLE player AS
        SELECT
            NULLIF(TRIM(CAST(player_id AS VARCHAR)), '') AS player_id,
            NULLIF(TRIM(CAST(player_id_legacy AS VARCHAR)), '') AS player_id_legacy,
            NULLIF(TRIM(CAST(player_id_pass AS VARCHAR)), '') AS player_id_pass,
            NULLIF(TRIM(CAST(canonical_name AS VARCHAR)), '') AS canonical_name,
            NULLIF(TRIM(CAST(source AS VARCHAR)), '') AS source,
            TRY_CAST(NULLIF(TRIM(CAST(updated_at AS VARCHAR)), '') AS TIMESTAMP) AS updated_at,
            NULLIF(TRIM(CAST(aliases AS VARCHAR)), '') AS aliases
        FROM read_parquet('{path}')
        """,
    ),
    (
        "club",
        "clubs_registry.parquet",
        """
        CREATE OR REPLACE TABLE club AS
        SELECT
            NULLIF(TRIM(CAST(canonical_name AS VARCHAR)), '') AS canonical_name,
            NULLIF(TRIM(CAST(aliases AS VARCHAR)), '') AS aliases,
            NULLIF(TRIM(CAST(team_labels AS VARCHAR)), '') AS team_labels,
            NULLIF(TRIM(CAST(source AS VARCHAR)), '') AS source,
            TRY_CAST(NULLIF(TRIM(CAST(updated_at AS VARCHAR)), '') AS TIMESTAMP) AS updated_at
        FROM read_parquet('{path}')
        """,
    ),
    (
        "affiliation",
        "affiliation_index.parquet",
        """
        CREATE OR REPLACE TABLE affiliation AS
        SELECT
            NULLIF(TRIM(CAST(player_id AS VARCHAR)), '') AS player_id,
            NULLIF(TRIM(CAST(season AS VARCHAR)), '') AS season,
            NULLIF(TRIM(CAST(club_raw AS VARCHAR)), '') AS club_raw,
            NULLIF(TRIM(CAST(verein_raw AS VARCHAR)), '') AS verein_raw,
            NULLIF(TRIM(CAST(club_canonical AS VARCHAR)), '') AS club_canonical,
            NULLIF(TRIM(CAST(verein_canonical AS VARCHAR)), '') AS verein_canonical,
            CASE lower(TRIM(CAST(is_einzelmitglied AS VARCHAR)))
                WHEN 'true' THEN TRUE
                WHEN '1' THEN TRUE
                WHEN 'false' THEN FALSE
                WHEN '0' THEN FALSE
                ELSE NULL
            END AS is_einzelmitglied,
            NULLIF(TRIM(CAST(source AS VARCHAR)), '') AS source,
            TRY_CAST(NULLIF(TRIM(CAST(updated_at AS VARCHAR)), '') AS TIMESTAMP) AS updated_at
        FROM read_parquet('{path}')
        """,
    ),
    (
        "verein",
        "vereine_registry.parquet",
        """
        CREATE OR REPLACE TABLE verein AS
        SELECT
            NULLIF(TRIM(CAST(canonical_verein AS VARCHAR)), '') AS canonical_verein,
            NULLIF(TRIM(CAST(aliases AS VARCHAR)), '') AS aliases,
            NULLIF(TRIM(CAST(member_clubs AS VARCHAR)), '') AS member_clubs,
            NULLIF(TRIM(CAST(source AS VARCHAR)), '') AS source,
            TRY_CAST(NULLIF(TRIM(CAST(updated_at AS VARCHAR)), '') AS TIMESTAMP) AS updated_at
        FROM read_parquet('{path}')
        """,
    ),
]

EMPTY_DIM_DDL = {
    "player": """
        CREATE OR REPLACE TABLE player (
            player_id VARCHAR, player_id_legacy VARCHAR, player_id_pass VARCHAR,
            canonical_name VARCHAR, source VARCHAR, updated_at TIMESTAMP, aliases VARCHAR
        )
        """,
    "club": """
        CREATE OR REPLACE TABLE club (
            canonical_name VARCHAR, aliases VARCHAR, team_labels VARCHAR,
            source VARCHAR, updated_at TIMESTAMP
        )
        """,
    "affiliation": """
        CREATE OR REPLACE TABLE affiliation (
            player_id VARCHAR, season VARCHAR, club_raw VARCHAR, verein_raw VARCHAR,
            club_canonical VARCHAR, verein_canonical VARCHAR, is_einzelmitglied BOOLEAN,
            source VARCHAR, updated_at TIMESTAMP
        )
        """,
    "verein": """
        CREATE OR REPLACE TABLE verein (
            canonical_verein VARCHAR, aliases VARCHAR, member_clubs VARCHAR,
            source VARCHAR, updated_at TIMESTAMP
        )
        """,
}


def _posix(path: Path) -> str:
    return path.resolve().as_posix()


def _parquet_dir() -> Path:
    raw = os.environ.get("PARQUET_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_PARQUET


def _warehouse_path() -> Path:
    raw = os.environ.get("WAREHOUSE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_WAREHOUSE


def _required_parquet(parquet_dir: Path) -> None:
    missing = [
        name
        for name in ("league_results_merged.parquet", "tournaments_postprocessed.parquet")
        if not (parquet_dir / name).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"Missing {missing} under {parquet_dir}. "
            "Set PARQUET_DIR or BOWLYZER_PARQUET_DIR to bowlyzer_deploy/database/data."
        )


def _file_usable(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    return True


def _publish_run_id(parquet_dir: Path) -> str | None:
    latest = parquet_dir / "runs" / "latest.json"
    if not latest.is_file():
        return None
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    run_id = payload.get("run_id")
    return str(run_id) if run_id else None


def _import_dim(con: duckdb.DuckDBPyConnection, parquet_dir: Path, table: str, filename: str, select_sql: str) -> int:
    path = parquet_dir / filename
    if not _file_usable(path):
        con.execute(EMPTY_DIM_DDL[table])
        return 0
    try:
        n = con.execute(f"SELECT count(*) FROM read_parquet('{_posix(path)}')").fetchone()[0]
    except duckdb.Error:
        n = 0
    if not n:
        con.execute(EMPTY_DIM_DDL[table])
        return 0
    con.execute(select_sql.format(path=_posix(path)))
    return int(n)


def main() -> int:
    parquet_dir = _parquet_dir()
    warehouse = _warehouse_path()
    _required_parquet(parquet_dir)
    warehouse.parent.mkdir(parents=True, exist_ok=True)

    sql = SQL_PATH.read_text(encoding="utf-8").replace("{{PARQUET_DIR}}", _posix(parquet_dir))
    print(f"import parquet_dir={parquet_dir}")
    print(f"import warehouse={warehouse}")

    con = duckdb.connect(str(warehouse))
    try:
        con.execute(sql)
        dim_counts: dict[str, int] = {}
        for table, filename, select_sql in DIM_SPECS:
            dim_counts[table] = _import_dim(con, parquet_dir, table, filename, select_sql)

        game_n = int(con.execute("SELECT count(*) FROM game_line").fetchone()[0])
        tour_n = int(con.execute("SELECT count(*) FROM tournament_line").fetchone()[0])
        now = datetime.now(timezone.utc).isoformat()
        run_id = _publish_run_id(parquet_dir) or ""
        con.execute("CREATE OR REPLACE TABLE warehouse_meta (key VARCHAR, value VARCHAR)")
        rows = [
            ("imported_at", now),
            ("parquet_dir", str(parquet_dir.resolve())),
            ("source_run_id", run_id),
            ("game_line_rows", str(game_n)),
            ("tournament_line_rows", str(tour_n)),
        ]
        for table, n in dim_counts.items():
            rows.append((f"{table}_rows", str(n)))
        con.executemany("INSERT INTO warehouse_meta VALUES (?, ?)", rows)
    finally:
        con.close()

    print(f"game_line={game_n} tournament_line={tour_n} dims={dim_counts}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"import failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
