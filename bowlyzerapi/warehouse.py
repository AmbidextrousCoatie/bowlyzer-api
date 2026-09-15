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
