from __future__ import annotations

import os
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = REPO_ROOT / "data" / "bowlyzer.duckdb"


def warehouse_path() -> Path:
    raw = os.environ.get("WAREHOUSE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_WAREHOUSE


def connect(*, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    path = warehouse_path()
    if not path.is_file():
        raise FileNotFoundError(f"No warehouse at {path}. Run: uv run python scripts/run_import.py")
    return duckdb.connect(str(path), read_only=read_only)


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

