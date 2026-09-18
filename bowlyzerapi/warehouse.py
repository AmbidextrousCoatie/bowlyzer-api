from __future__ import annotations

import os
from pathlib import Path

from contextlib import contextmanager
from collections.abc import Iterator

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = REPO_ROOT / "data" / "bowlyzer.duckdb"
DEFAULT_PARQUET = REPO_ROOT.parent / "bowlyzer_deploy" / "database" / "data"


def warehouse_path() -> Path:
    raw = os.environ.get("WAREHOUSE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_WAREHOUSE


def parquet_dir() -> Path:
    """Publish dir (Parquet + tournament_ko_config.json)."""
    raw = (
        os.environ.get("PARQUET_DIR", "").strip()
        or os.environ.get("BOWLYZER_PARQUET_DIR", "").strip()
    )
    return Path(raw) if raw else DEFAULT_PARQUET


def connect(*, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    path = warehouse_path()
    if not path.is_file():
        raise FileNotFoundError(f"No warehouse at {path}. Run: uv run python scripts/run_import.py")
    return duckdb.connect(str(path), read_only=read_only)


@contextmanager
def session(*, read_only: bool = True) -> Iterator[duckdb.DuckDBPyConnection]:
    con = connect(read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def data_revision() -> str | None:
    """Publish run id from warehouse_meta, used as X-Data-Revision."""
    path = warehouse_path()
    if not path.is_file():
        return None
    con = duckdb.connect(str(path), read_only=True)
    try:
        exists = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'warehouse_meta'"
        ).fetchone()
        if not exists:
            return None
        rows = dict(con.execute("SELECT key, value FROM warehouse_meta").fetchall())
        value = rows.get("source_run_id") or rows.get("imported_at")
        return None if value is None else str(value)
    finally:
        con.close()

