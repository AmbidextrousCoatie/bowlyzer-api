"""Minimal HTTP placeholder: warehouse health only. Language of the real API is still TBD."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = REPO_ROOT / "data" / "bowlyzer.duckdb"


def warehouse_path() -> Path:
    raw = os.environ.get("WAREHOUSE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_WAREHOUSE


def health_payload() -> tuple[int, dict[str, Any]]:
    path = warehouse_path()
    if not path.is_file():
        return 503, {
            "status": "empty",
            "error": {
                "code": "warehouse_missing",
                "message": f"No warehouse at {path}. Run: python scripts/run_import.py",
            },
        }

    con = duckdb.connect(str(path), read_only=True)
    try:
        tables: dict[str, int] = {}
        for name in ("game_line", "tournament_line", "player", "club", "affiliation", "verein"):
            exists = con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                [name],
            ).fetchone()
            if exists:
                tables[name] = int(con.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            else:
                tables[name] = 0
        meta = {}
        meta_exists = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'warehouse_meta'"
        ).fetchone()
        if meta_exists:
            for key, value in con.execute("SELECT key, value FROM warehouse_meta").fetchall():
                meta[str(key)] = value
    finally:
        con.close()

    return 200, {
        "status": "ok",
        "warehouse": str(path.resolve()),
        "revision": meta.get("source_run_id") or meta.get("imported_at"),
        "meta": meta,
        "tables": tables,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        print(f"{self.address_string()} {format % args}")

    def _send(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if body.get("revision"):
            self.send_header("X-Data-Revision", str(body["revision"]))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/api/v1/health", "/healthz", "/health"):
            status, body = health_payload()
            self._send(status, body)
            return
        self._send(
            404,
            {
                "error": {
                    "code": "not_found",
                    "message": "Placeholder API. Only GET /api/v1/health is implemented.",
                }
            },
        )


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"bowlyzer-api placeholder listening on {host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
