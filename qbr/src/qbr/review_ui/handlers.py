"""Review-UI HTTP request handlers.

Extracted verbatim from `scripts/serve_question_review_ui.py` so one file is no longer 10,700 lines.
`serve_question_review_ui` re-exports every name here; that indirection is deliberate and is why the
test files that load the server by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations



import sys as _sys
from pathlib import Path as _Path

# The server's guarded imports fall back to `from scripts.x import ...`. `scripts/` has no
# `__init__.py`, so that is a namespace-package import and needs the *repository root* on `sys.path`
# — not the `scripts/` directory. Both are added: the root for `scripts.x`, the directory for the
# plain `import x` form that a bare `sys.path` entry would otherwise be needed for.
_REPO_ROOT = str(_Path(__file__).resolve().parents[4])
for _p in (_REPO_ROOT, _REPO_ROOT + "/scripts"):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from typing import Any
from http.server import BaseHTTPRequestHandler
import base64
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.request
from .ai_audit import normalized_correction
from .constants import ANSWER_REVIEW_ACTIONS, QUESTION_REVIEW_ACTIONS, SqlWriteError
from .legacy_assets import html_page, mobile_asset_response, mobile_review_event, workflow_page
from .paths import content_type_of, safe_file_path
from .queue_view import principles_projection, repair_questions_projection
from .review_state import ReviewState

class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes the socket after every response, so a page that loads one paper, its six
    # crops and its PDF pays a fresh TCP handshake for each of them - and the queue is fetched as
    # 32 separate responses. Measured: `curl` reports a new connection per request and each is a
    # full round trip. HTTP/1.1 keeps the connection, which is what makes the parallel fetch in
    # `v2.html` worth doing. It is only safe because **every** response here declares
    # `Content-Length` (checked across all `send_response` sites), so the client always knows where
    # one response ends; `send_error` closes the connection explicitly, which is also correct.
    protocol_version = "HTTP/1.1"
    state: ReviewState
    _post_rate_lock = threading.Lock()
    _post_rate_events: dict[str, list[float]] = {}

    def setup(self) -> None:
        super().setup()
        raw = os.environ.get("REVIEW_UI_REQUEST_TIMEOUT_SECONDS", "60")
        try:
            timeout = max(1.0, float(raw))
        except ValueError:
            timeout = 60.0
        self.connection.settimeout(timeout)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), format % args))

    def require_authorization(self) -> bool:
        username = os.environ.get("REVIEW_UI_BASIC_AUTH_USERNAME", "")
        password = os.environ.get("REVIEW_UI_BASIC_AUTH_PASSWORD", "")
        if not username and not password:
            return False
        if not username or not password:
            self.send_error(500, "Review UI authentication is misconfigured")
            return True
        expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        actual = self.headers.get("Authorization", "")
        if hmac.compare_digest(actual, expected):
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="National Exam Review", charset="UTF-8"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    @staticmethod
    def writes_are_blocked() -> bool:
        return os.environ.get("REVIEW_UI_READ_ONLY", "0").lower() in {"1", "true", "yes"}

    @staticmethod
    def max_request_bytes() -> int:
        raw = os.environ.get("REVIEW_UI_MAX_REQUEST_BYTES", str(16 * 1024 * 1024))
        try:
            value = int(raw)
        except ValueError:
            return 16 * 1024 * 1024
        return max(1, value)

    def post_rate_limited(self) -> bool:
        raw = os.environ.get("REVIEW_UI_POST_RATE_LIMIT_PER_MINUTE", "120")
        try:
            limit = max(1, int(raw))
        except ValueError:
            limit = 120
        client_ip = str(self.client_address[0])
        now = time.monotonic()
        with self._post_rate_lock:
            recent = [value for value in self._post_rate_events.get(client_ip, []) if now - value < 60]
            if len(recent) >= limit:
                self._post_rate_events[client_ip] = recent
                return True
            recent.append(now)
            self._post_rate_events[client_ip] = recent
        return False

    def send_mobile_asset(self, path: str, *, head_only: bool = False) -> bool:
        asset = mobile_asset_response(path)
        if asset is None:
            return False
        data, content_type, cache_control = asset
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        if path.rstrip("/") == "/mobile/sw.js":
            self.send_header("Service-Worker-Allowed", "/mobile/")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)
        return True

    def accepts_gzip(self) -> bool:
        return "gzip" in self.headers.get("Accept-Encoding", "").lower()

    def compressible(self, data: bytes, content_type: str) -> bytes:
        """Gzip a response body when the client asked for it and the type is worth compressing.

        Measured on the served queue: one sitting is 2.13 MB of JSON and the whole 物理治療師
        category is 80 MB, none of it compressed. The same bytes gzip to 227 KB from 5.19 MB - a
        23x reduction - and the queue is mostly repeated field names and CJK text, which is exactly
        what gzip is good at. Images are already compressed, so they are skipped by type rather
        than by trying and measuring.

        The threshold exists so a tiny error document is not wrapped in a gzip header that can be
        larger than it is; below it, the identity body is returned unchanged.
        """
        if len(data) < 1024 or not self.accepts_gzip():
            return data
        if not (content_type.startswith("application/json") or content_type.startswith("text/")):
            return data
        return gzip.compress(data, 5)

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        content_type = "application/json; charset=utf-8"
        body = self.compressible(data, content_type)
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            if len(body) != len(data):
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            return

    def do_HEAD(self) -> None:
        if self.require_authorization():
            return
        parsed = urllib.parse.urlparse(self.path)
        if (parsed.path.startswith("/mobile") or parsed.path.startswith("/v2")) \
                and self.send_mobile_asset(parsed.path, head_only=True):
            return
        if parsed.path in {"/", "/workflow", "/workflow/"}:
            data = workflow_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return
        if parsed.path in {"/legacy", "/legacy/"}:
            data = html_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return
        if parsed.path == "/evidence-file":
            query = urllib.parse.parse_qs(parsed.query)
            path = self.state.evidence_file_path(query.get("key", [""])[0], query.get("asset", [""])[0])
            if path is None:
                self.send_error(404, "Evidence file not found or not allowed")
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            return
        if parsed.path == "/file":
            query = urllib.parse.parse_qs(parsed.query)
            path = safe_file_path(query.get("path", [""])[0])
            if path is None or not path.exists() or not path.is_file():
                self.send_error(404, "File not found or not allowed")
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            return
        else:
            self.send_error(404, "Not found")
            return

    def do_GET(self) -> None:
        if self.require_authorization():
            return
        parsed = urllib.parse.urlparse(self.path)
        if (parsed.path.startswith("/mobile") or parsed.path.startswith("/v2")) \
                and self.send_mobile_asset(parsed.path):
            return
        if parsed.path in {"/", "/workflow", "/workflow/"}:
            data = workflow_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path in {"/legacy", "/legacy/"}:
            data = html_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/evidence-file":
            query = urllib.parse.parse_qs(parsed.query)
            path = self.state.evidence_file_path(query.get("key", [""])[0], query.get("asset", [""])[0])
            if path is None:
                self.send_error(404, "Evidence file not found or not allowed")
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/workflow":
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            self.send_json(self.state.workflow_payload(params))
            return
        if parsed.path == "/api/queue_index":
            # The taxonomy the review list is navigated by, written beside the queue by
            # `build_review_queue.py`. Served rather than recomputed in the browser because the
            # browser would have to read every candidate row to answer "which subjects are in this
            # queue", and that question is asked before the first question is rendered. A queue
            # without an index answers 404 and the UI rebuilds the tree from the questions, so an
            # older run keeps working instead of losing its navigation.
            index_path = self.state.candidate_path.parent / "queue_index.json"
            if index_path.exists():
                try:
                    self.send_json(json.loads(index_path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError) as exc:
                    self.send_json({"error": f"queue_index unreadable: {exc}"}, status=500)
            else:
                self.send_json({"error": "no queue_index.json beside the candidates"}, status=404)
            return
        if parsed.path == "/api/candidates":
            self.state.refresh_event_logs()
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            payload = self.state.filtered_candidate_payloads(params)
            self.send_json(
                {
                    "candidate_jsonl": str(self.state.candidate_path),
                    "candidate_source_jsonl": str(self.state.candidate_path),
                    "issue_csv": str(self.state.issue_path) if self.state.issue_path else None,
                    "review_log": str(self.state.review_log),
                    "legacy_review_log": str(self.state.review_log),
                    "storage": self.state.candidate_data_status(),
                    **payload,
                }
            )
            return
        if parsed.path == "/api/answer-candidates":
            self.state.refresh_event_logs()
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            payload = self.state.filtered_answer_payloads(params)
            self.send_json(
                {
                    "candidate_jsonl": str(self.state.candidate_path),
                    "candidate_source_jsonl": str(self.state.candidate_path),
                    "review_log": str(self.state.review_log),
                    "legacy_review_log": str(self.state.review_log),
                    "answer_review_log": str(self.state.answer_review_log),
                    "storage": self.state.candidate_data_status(),
                    **payload,
                }
            )
            return
        if parsed.path == "/api/group-candidates":
            self.state.refresh_event_logs()
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            payload = self.state.filtered_group_payloads(params)
            self.send_json(
                {
                    "candidate_jsonl": str(self.state.candidate_path),
                    "candidate_source_jsonl": str(self.state.candidate_path),
                    "review_log": str(self.state.review_log),
                    "legacy_review_log": str(self.state.review_log),
                    "storage": self.state.candidate_data_status(),
                    **payload,
                }
            )
            return
        if parsed.path == "/api/pipeline":
            self.state.refresh_event_logs()
            self.send_json(self.state.pipeline_payload())
            return
        if parsed.path == "/api/preferences":
            query = urllib.parse.parse_qs(parsed.query)
            reviewer = query.get("reviewer", ["local"])[0] or "local"
            self.send_json({"ok": True, "reviewer": reviewer, "preferences": self.state.load_preferences(reviewer)})
            return
        if parsed.path == "/api/reload-status":
            self.state.refresh_event_logs()
            self.send_json(self.state.candidate_data_status())
            return
        if parsed.path == "/api/correction-feedback":
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            self.send_json(self.state.correction_feedback_payload(params))
            return
        if parsed.path == "/api/discuss":
            # One request carries the whole 錯題討論區: the stuck rows, the principles in force, and
            # the agent's open questions. They are read together because they are one screen's
            # state, and a second round trip per panel is three places the screen can disagree
            # with itself about which questions are stuck.
            self.state.refresh_event_logs()
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            params.setdefault("reviewStatus", "discuss")
            params.setdefault("limit", "500")
            self.send_json(self.state.discuss_payload(params))
            return
        if parsed.path == "/file":
            query = urllib.parse.parse_qs(parsed.query)
            path = safe_file_path(query.get("path", [""])[0])
            if path is None or not path.exists() or not path.is_file():
                self.send_error(404, "File not found or not allowed")
                return
            data = path.read_bytes()
            mime = content_type_of(path.name, data)
            # A crop is immutable: it is addressed by path and a rebuild writes a new queue
            # directory, so the bytes behind a given path never change. Measured, the server sent
            # no caching header at all and HTTP/1.0 closed the socket, so every step onto question
            # 31 re-downloaded all four option pictures of question 30 - the reviewer's crops were
            # re-fetched once per press. The ETag makes a repeat request a 304 instead of a
            # 484 KB body, and the same token lets the PDF viewer and the browser's image cache
            # agree about what they already hold.
            data_signature = hashlib.sha256(data).hexdigest()[:32]
            etag = f'"{data_signature}"'
            self.send_response(200)
            if self.headers.get("If-None-Match", "") == etag:
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "public, max-age=86400, immutable")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_header("Content-Type", mime)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=86400, immutable")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        if self.require_authorization():
            return
        if self.post_rate_limited():
            self.send_json({"ok": False, "error": "Too many write requests"}, status=429)
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/api/review", "/api/mobile-review", "/api/review-batch-accept", "/api/group-confirm-not-group", "/api/group-confirm-group", "/api/group-reset-review", "/api/manual-asset", "/api/answer-review", "/api/answer-review-batch", "/api/ai-question-audit", "/api/ai-question-audit-reset", "/api/ai-feedback", "/api/ai-learning", "/api/principles", "/api/repair-question", "/api/preferences", "/api/reload-candidates"}:
            self.send_error(404, "Not found")
            return
        if self.writes_are_blocked():
            self.send_json({"ok": False, "error": "Review UI is in read-only cutover mode"}, status=503)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"ok": False, "error": "Invalid Content-Length"}, status=400)
            return
        if length < 0 or length > self.max_request_bytes():
            self.send_json({"ok": False, "error": "Request body is too large"}, status=413)
            return
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
            return
        self.state.refresh_event_logs()
        if parsed.path == "/api/mobile-review":
            try:
                event = mobile_review_event(payload)
                saved_event = self.state.append_review(event)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "event": saved_event,
                    "ai_followup": event["ai_followup"],
                }
            )
            return
        if parsed.path == "/api/reload-candidates":
            force = bool(payload.get("force"))
            status = self.state.reload_candidate_data(force=force, block=False)
            status_code = 409 if status.get("busy") else (500 if not status.get("ok") else 200)
            self.send_json(status, status=status_code)
            return
        if parsed.path == "/api/preferences":
            reviewer = payload.get("reviewer") or "local"
            preferences = payload.get("preferences")
            if not isinstance(preferences, dict):
                self.send_json({"ok": False, "error": "preferences must be an object"}, status=400)
                return
            self.state.save_preferences(reviewer, preferences)
            self.send_json({"ok": True, "reviewer": reviewer, "preferences": preferences})
            return
        if parsed.path == "/api/review-batch-accept":
            keys = payload.get("candidate_keys")
            if not isinstance(keys, list) or not keys:
                self.send_json({"ok": False, "error": "candidate_keys must be a non-empty list"}, status=400)
                return
            result = self.state.batch_accept_questions(
                [str(key) for key in keys],
                reviewer=payload.get("reviewer") or "local",
                notes=payload.get("notes") or "批次通過：人工快速瀏覽目前畫面，parser pass 且未被標記 block / needs_review。",
            )
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "saved_count": len(result["saved"]),
                    "skipped_count": len(result["skipped"]),
                    "skipped": result["skipped"][:50],
                }
            )
            return
        if parsed.path == "/api/group-confirm-not-group":
            keys = payload.get("candidate_keys")
            if not isinstance(keys, list) or not keys:
                self.send_json({"ok": False, "error": "candidate_keys must be a non-empty list"}, status=400)
                return
            result = self.state.confirm_not_group(
                [str(key) for key in keys],
                reviewer=payload.get("reviewer") or "local",
                notes=payload.get("notes") or "",
                group_sheet_key=payload.get("group_sheet_key") or "",
            )
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "saved_count": len(result["saved"]),
                    "skipped_count": len(result["skipped"]),
                    "skipped": result["skipped"][:50],
                    "events": result["saved"],
                }
            )
            return
        if parsed.path == "/api/group-reset-review":
            keys = payload.get("candidate_keys")
            if not isinstance(keys, list) or not keys:
                self.send_json({"ok": False, "error": "candidate_keys must be a non-empty list"}, status=400)
                return
            result = self.state.reset_group_review(
                [str(key) for key in keys],
                reviewer=payload.get("reviewer") or "local",
                notes=payload.get("notes") or "",
                group_sheet_key=payload.get("group_sheet_key") or "",
            )
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "saved_count": len(result["saved"]),
                    "skipped_count": len(result["skipped"]),
                    "skipped": result["skipped"][:50],
                    "events": result["saved"],
                }
            )
            return
        if parsed.path == "/api/group-confirm-group":
            keys = payload.get("candidate_keys")
            if not isinstance(keys, list):
                keys = []
            if not keys and payload.get("seed_candidate_key") and payload.get("range"):
                keys = self.state.candidate_keys_for_manual_group_range(
                    str(payload.get("seed_candidate_key") or ""),
                    str(payload.get("range") or ""),
                )
            if not keys and payload.get("range"):
                keys = self.state.candidate_keys_for_manual_group_filters(
                    category=str(payload.get("category") or ""),
                    subject=str(payload.get("subject") or ""),
                    year=str(payload.get("year") or ""),
                    ordinal=str(payload.get("ordinal") or ""),
                    range_text=str(payload.get("range") or ""),
                )
            if not keys:
                self.send_json({"ok": False, "error": "candidate_keys or a valid seed_candidate_key/range is required"}, status=400)
                return
            result = self.state.confirm_group(
                [str(key) for key in keys],
                reviewer=payload.get("reviewer") or "local",
                notes=payload.get("notes") or "",
                group_ref=payload.get("group_ref") or "",
                group_type=payload.get("group_type") or "shared_stem",
                shared_stem=payload.get("shared_stem") or "",
                group_sheet_key=payload.get("group_sheet_key") or "",
            )
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "saved_count": len(result["saved"]),
                    "skipped_count": len(result["skipped"]),
                    "skipped": result["skipped"][:50],
                    "events": result["saved"],
                    "group": result.get("group") or {},
                }
            )
            return
        if parsed.path == "/api/manual-asset":
            candidate_key = str(payload.get("candidate_key") or "")
            data_url = str(payload.get("data_url") or "")
            if not candidate_key:
                self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
                return
            if not data_url:
                self.send_json({"ok": False, "error": "data_url is required"}, status=400)
                return
            try:
                result = self.state.save_manual_image_asset(
                    candidate_key,
                    data_url,
                    reviewer=payload.get("reviewer") or "local",
                    notes=payload.get("notes") or "",
                    caption=payload.get("caption") or "",
                    asset_role=payload.get("asset_role") or "manual_question_image",
                    placement=payload.get("placement") or "stem",
                    target_option=payload.get("target_option") or "",
                    replace_existing=bool(payload.get("replace_existing")),
                )
            except KeyError:
                self.send_json({"ok": False, "error": "candidate_key not found"}, status=404)
                return
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except Exception as exc:
                self.send_json({"ok": False, "error": f"manual asset save failed: {exc}"}, status=500)
                return
            self.send_json({"ok": True, **result})
            return
        if parsed.path == "/api/ai-question-audit":
            candidate_key = str(payload.get("candidate_key") or "")
            if not candidate_key:
                self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
                return
            try:
                event = self.state.run_question_ai_audit(
                    candidate_key,
                    reviewer=payload.get("reviewer") or "local",
                    notes=payload.get("notes") or "",
                )
            except KeyError:
                self.send_json({"ok": False, "error": "candidate_key not found"}, status=404)
                return
            except Exception as exc:
                self.send_json({"ok": False, "error": f"AI audit failed: {exc}"}, status=502)
                return
            self.send_json({"ok": True, "ai_review_log": str(self.state.ai_review_log), "event": event})
            return
        if parsed.path == "/api/ai-question-audit-reset":
            candidate_key = str(payload.get("candidate_key") or "")
            if not candidate_key:
                self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
                return
            try:
                event = self.state.reset_ai_review(
                    candidate_key,
                    reviewer=payload.get("reviewer") or "local",
                    notes=payload.get("notes") or "",
                )
            except KeyError:
                self.send_json({"ok": False, "error": "candidate_key not found"}, status=404)
                return
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            self.send_json({"ok": True, "ai_review_log": str(self.state.ai_review_log), "event": event})
            return
        if parsed.path == "/api/ai-feedback":
            try:
                event = self.state.append_ai_feedback(payload)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=409)
                return
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            self.send_json({"ok": True, "ai_feedback_log": str(self.state.ai_feedback_log), "event": event})
            return
        if parsed.path == "/api/ai-learning":
            try:
                event = self.state.append_ai_learning(payload)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=409)
                return
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            self.send_json({"ok": True, "ai_learning_log": str(self.state.ai_learning_log), "event": event})
            return
        if parsed.path == "/api/principles":
            try:
                event = self.state.append_principle(payload)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self.send_json({
                "ok": True,
                "principles_log": str(self.state.principles_log),
                "event": event,
                **principles_projection(self.state.principles_events),
            })
            return
        if parsed.path == "/api/repair-question":
            try:
                event = self.state.append_repair_question(payload)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self.send_json({
                "ok": True,
                "repair_questions_log": str(self.state.repair_questions_log),
                "event": event,
                **repair_questions_projection(self.state.repair_questions_events),
            })
            return
        if parsed.path == "/api/answer-review":
            action = payload.get("action")
            if action not in ANSWER_REVIEW_ACTIONS:
                self.send_json({"ok": False, "error": "Invalid action"}, status=400)
                return
            if not payload.get("candidate_key"):
                self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
                return
            if action in {"accept", "unblock"} and not self.state.question_is_answer_eligible(payload.get("candidate_key")):
                self.send_json(
                    {
                        "ok": False,
                        "error": "Question review is not accepted; answer review cannot pass this item.",
                        "question_review_action": self.state.question_review_action(payload.get("candidate_key")),
                    },
                    status=409,
                )
                return
            if "corrected_answer" in payload:
                payload["corrected_answer"] = "" if payload["corrected_answer"] is None else str(payload["corrected_answer"])
            try:
                saved_event = self.state.append_answer_review(payload)
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            self.send_json({"ok": True, "answer_review_log": str(self.state.answer_review_log), "event": saved_event})
            return
        if parsed.path == "/api/answer-review-batch":
            action = payload.get("action")
            entries = payload.get("entries")
            if action not in ANSWER_REVIEW_ACTIONS:
                self.send_json({"ok": False, "error": "Invalid action"}, status=400)
                return
            if not isinstance(entries, list) or not entries:
                self.send_json({"ok": False, "error": "entries must be a non-empty list"}, status=400)
                return
            entry_keys = [
                str(entry.get("candidate_key") or "")
                for entry in entries
                if isinstance(entry, dict) and entry.get("candidate_key")
            ]
            eligibility = self.state.question_answer_eligibility_map(entry_keys)
            ineligible = [
                {
                    "candidate_key": entry.get("candidate_key"),
                    "question_review_action": eligibility.get(str(entry.get("candidate_key") or ""), ""),
                }
                for entry in entries
                if isinstance(entry, dict)
                and entry.get("candidate_key")
                and eligibility.get(str(entry.get("candidate_key") or ""), "") not in {"accept", "unblock"}
            ]
            if action in {"accept", "unblock"} and ineligible:
                self.send_json(
                    {
                        "ok": False,
                        "error": "Some questions are not accepted in question review; answer review batch was not saved.",
                        "ineligible": ineligible,
                    },
                    status=409,
                )
                return
            unresolved_mod_entries = [
                {
                    "candidate_key": entry.get("candidate_key"),
                    "corrected_answer": entry.get("corrected_answer"),
                }
                for entry in entries
                if isinstance(entry, dict)
                and entry.get("needs_manual_answer_review")
                and str(entry.get("corrected_answer") or "").strip() in {"", "#"}
            ]
            if action in {"accept", "unblock"} and unresolved_mod_entries:
                self.send_json(
                    {
                        "ok": False,
                        "error": "MOD answers with # or blank values must be resolved before the answer sheet can pass.",
                        "unresolved_mod_entries": unresolved_mod_entries[:50],
                    },
                    status=409,
                )
                return
            events = []
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("candidate_key"):
                    continue
                event = {
                    "candidate_key": entry.get("candidate_key"),
                    "answer_source_registry_key": entry.get("answer_source_registry_key") or "",
                    "action": action,
                    "notes": payload.get("notes") or entry.get("notes") or "",
                    "reviewer": payload.get("reviewer") or "local",
                    "reviewed_answer": entry.get("reviewed_answer") or {"answer": entry.get("answer")},
                    "corrected_answer": "" if entry.get("corrected_answer") is None else str(entry.get("corrected_answer")),
                    "sheet_key": payload.get("sheet_key") or "",
                    "sheet_action": payload.get("sheet_action") or action,
                }
                if payload.get("ai_requested"):
                    event["ai_requested"] = True
                events.append(event)
            try:
                saved_events = self.state.append_answer_reviews_batch(events)
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}", "saved_count": 0}, status=500)
                return
            self.send_json(
                {
                    "ok": True,
                    "answer_review_log": str(self.state.answer_review_log),
                    "saved_count": len(saved_events),
                    "events": saved_events,
                }
            )
            return
        action = payload.get("action")
        if action not in QUESTION_REVIEW_ACTIONS:
            self.send_json({"ok": False, "error": "Invalid action"}, status=400)
            return
        if not payload.get("candidate_key"):
            self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
            return
        if "correction" in payload:
            correction = normalized_correction(payload.get("correction"))
            if not correction:
                payload.pop("correction", None)
            else:
                payload["correction"] = correction
        if payload.get("source") == "ai_suggestion" and not self.state.ai_suggestion_apply_allowed(str(payload.get("candidate_key") or "")):
            self.send_json(
                {
                    "ok": False,
                    "error": "AI 建議不能覆寫已完成人工審核的題目；請先由核准的抓漏清單退回未審。",
                },
                status=409,
            )
            return
        try:
            saved_event = self.state.append_review(payload)
        except SqlWriteError as exc:
            self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
            return
        self.send_json({"ok": True, "review_log": str(self.state.review_log), "event": saved_event})


class MobileHandler(Handler):
    """Constrained listener for the phone workflow on its own port."""

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.path = "/mobile/workflow/"
            super().do_HEAD()
            return
        if parsed.path.startswith("/mobile"):
            super().do_HEAD()
            return
        self.send_error(404, "Not found")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.path = "/mobile/workflow/"
            super().do_GET()
            return
        if parsed.path.startswith("/mobile") or parsed.path in {"/api/candidates", "/api/workflow", "/api/reload-status", "/api/correction-feedback", "/evidence-file", "/file", "/legacy", "/legacy/"}:
            super().do_GET()
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/api/mobile-review", "/api/review"}:
            super().do_POST()
            return
        self.send_error(404, "Not found")
