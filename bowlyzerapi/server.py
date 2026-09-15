"""Placeholder HTTP API over the DuckDB warehouse. Serving language is still TBD."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bowlyzerapi.queries.club import club_history
from bowlyzerapi.queries.tournament import tournament_section
from bowlyzerapi.warehouse import connect, warehouse_path


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def health_payload() -> tuple[int, dict[str, Any]]:
    path = warehouse_path()
    if not path.is_file():
        return 503, {
            "status": "empty",
            "error": {
                "code": "warehouse_missing",
                "message": f"No warehouse at {path}. Run: uv run python scripts/run_import.py",
            },
        }

    con = connect(read_only=True)
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
        print(f"{self.address_string()} {format % args}", flush=True)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        payload = _json(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        revision = body.get("revision")
        if revision:
            self.send_header("X-Data-Revision", str(revision))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}

        try:
            if path in ("/api/v1/health", "/healthz", "/health"):
                self._send(*health_payload())
                return
            if path == "/api/v1/tournaments/section":
                season = (qs.get("season") or "").strip()
                event = (qs.get("event") or qs.get("tournament") or "").strip()
                if not season or not event:
                    self._send(
                        400,
                        {
                            "error": {
                                "code": "missing_parameters",
                                "message": "Query params season and event are required.",
                            }
                        },
                    )
                    return
                self._send(200, tournament_section(season, event))
                return
            if path == "/api/v1/clubs/history":
                club = (qs.get("club") or "").strip()
                if not club:
                    self._send(
                        400,
                        {
                            "error": {
                                "code": "missing_parameters",
                                "message": "Query param club is required.",
                            }
                        },
                    )
                    return
                self._send(200, club_history(club))
                return
        except FileNotFoundError as exc:
            self._send(503, {"error": {"code": "warehouse_missing", "message": str(exc)}})
            return
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": {"code": "internal", "message": str(exc)}})
            return

        self._send(
            404,
            {
                "error": {
                    "code": "not_found",
                    "message": "Try GET /api/v1/health, /api/v1/tournaments/section, /api/v1/clubs/history.",
                }
            },
        )


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"bowlyzer-api listening on {host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
