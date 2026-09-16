"""Read-only HTTP API over the DuckDB warehouse. Serving language is still TBD."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from bowlyzerapi.http import dispatch
from bowlyzerapi.warehouse import data_revision


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        print(f"{self.address_string()} {format % args}", flush=True)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        revision = body.get("revision") or (data_revision() if status < 500 else None)
        if revision and "revision" not in body and status < 400:
            body = {**body, "revision": revision}
        payload = _json(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if revision:
            self.send_header("X-Data-Revision", str(revision))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        qs = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        try:
            status, body = dispatch(parsed.path, qs)
        except FileNotFoundError as exc:
            self._send(503, {"error": {"code": "warehouse_missing", "message": str(exc)}})
            return
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": {"code": "internal", "message": str(exc)}})
            return
        self._send(status, body)


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"bowlyzer-api listening on {host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
