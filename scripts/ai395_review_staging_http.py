#!/usr/bin/env python3
"""Small HTTP bridge for the isolated AI395 staging n8n workflow.

It exposes only health, describe, and fixture E2E endpoints.  The bridge uses
the same dependency-light runner as local tests, keeps the database URL and
artifact root in the container environment, and refuses arbitrary commands or
production target overrides.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ai395_review_staging import DEFAULT_CONFIG_DIR, describe, run_e2e


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class State:
    def __init__(self, database_url: str, artifact_root: Path):
        self.database_url = database_url
        self.artifact_root = artifact_root
        self.lock = threading.Lock()
        self.active = False
        self.runs: dict[str, dict[str, Any]] = {}

    def launch(self, fixture: str, run_id: str) -> None:
        with self.lock:
            if self.active:
                raise RuntimeError("a staging run is already active")
            self.active = True
            self.runs[run_id] = {"run_id": run_id, "status": "running"}

        def execute() -> None:
            try:
                args = argparse.Namespace(
                    config_dir=DEFAULT_CONFIG_DIR,
                    fixture=fixture,
                    source_manifest=None,
                    mineru_manifest=None,
                    artifact_dir=self.artifact_root,
                    database_url=self.database_url,
                    run_id=run_id,
                    source_mode="mock_fixture",
                    mineru_mode="mock_fixture",
                )
                report = run_e2e(args)
                with self.lock:
                    self.runs[run_id] = {"run_id": run_id, "status": "succeeded", "report": report}
            except Exception as exc:  # noqa: BLE001 - persisted as API job evidence
                with self.lock:
                    self.runs[run_id] = {
                        "run_id": run_id,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            finally:
                with self.lock:
                    self.active = False

        threading.Thread(target=execute, name=f"staging-{run_id}", daemon=True).start()


def json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ai395-review-staging-http/1"

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def send_json(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self.send_json(200, {"status": "pass", "component_id": "ai395_review_staging_http", "active": self.state.active})
            return
        if self.path == "/describe":
            self.send_json(200, describe())
            return
        prefix = "/runs/"
        if self.path.startswith(prefix):
            run_id = self.path[len(prefix):]
            with self.state.lock:
                result = self.state.runs.get(run_id)
            self.send_json(200 if result else 404, result or {"status": "not_found", "run_id": run_id})
            return
        self.send_json(404, {"status": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/runs/e2e":
            self.send_json(404, {"status": "not_found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length < 0 or content_length > 64 * 1024:
                raise ValueError("request body is too large")
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            fixture = str(payload.get("fixture", "mini20"))
            if not SAFE_ID.fullmatch(fixture):
                raise ValueError("fixture contains unsupported characters")
            run_id = str(payload.get("run_id") or f"http-{int(time.time())}")
            if not SAFE_ID.fullmatch(run_id):
                raise ValueError("run_id contains unsupported characters")
            self.state.launch(fixture, run_id)
        except RuntimeError as exc:
            self.send_json(409, {"status": "busy", "error": str(exc)})
            return
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(400, {"status": "failed", "error": str(exc)})
            return
        self.send_json(202, {"status": "accepted", "run_id": run_id, "status_url": f"/runs/{run_id}"})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[staging-http] {format % args}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--database-url", default=os.environ.get("AI395_STAGING_DATABASE_URL", "sqlite:///tmp/ai395_review_staging_http.sqlite3"))
    parser.add_argument("--artifact-root", type=Path, default=Path(os.environ.get("AI395_STAGING_ARTIFACT_ROOT", "/var/lib/ai395-review-staging")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.artifact_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = State(args.database_url, args.artifact_root)  # type: ignore[attr-defined]
    print(json.dumps({"status": "ready", "host": args.host, "port": args.port}, sort_keys=True), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
