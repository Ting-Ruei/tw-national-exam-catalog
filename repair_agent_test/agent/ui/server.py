#!/usr/bin/env python3
"""給設計者用的互動判讀介面：一次一題、整題完整、右邊配 PDF、判讀寫進檔案。

**這個檔是 UI 的後端，不是 agent。** agent 的本體是 `agent.mjs`（Pi SDK）；
本檔只做兩件事：把 `bridge.py` 已經算好的東西端給瀏覽器，以及把設計者的判讀
**寫進同一條學習流**（`store/agent_feedback.jsonl`）。

## 為什麼不是靜態頁

`a2/runs/*.html` 是靜態頁、零 fetch、判讀只存 localStorage（`grep fetch` = 0）。
設計者第八輪要的是**互動**：每一題獨立顯示、右邊配 PDF、判讀**寫入檔案**當學習語料。
localStorage 是「寫在這台瀏覽器裡」，換一台機器就不見了，也不會進到 agent 的記憶。

## 與 v2 的關係（為什麼不直接改 v2）

v2 是**審題介面**（人做 accept／block 決定，寫 `question_review_events.jsonl`）。
這一頁是**學習介面**（人下指導，寫 agent 的學習語料）。兩者的資料流不同、
**權威也不同**：v2 的決定是人工審核紀錄，這一頁的指導是模型的前驗。
照 Q15 的裁決「實驗階段只加註記」，這一頁**另開一支**，不動 v2。

## 紀律

- **只寫 `store/agent_feedback.jsonl`**（append-only，schema 對齊
  `review_state.append_ai_feedback`）。**永不碰 `question_review_events.jsonl`**。
- 讀取一律走 `bridge.py` 的函式，**不重寫一份**。路徑解析走
  `qbr.review_ui.paths.project_path`（v2 用的同一支），所以瀏覽器安全變體
  （JPEG 2000 的掃描頁）自動生效。

    python3 server.py --port 8790          # 然後開 http://127.0.0.1:8790
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parent
SANDBOX = AGENT_DIR.parent
CATALOG = SANDBOX.parent
QBR = CATALOG / "qbr"

for path in (QBR / "src", QBR / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# `bridge.py` is imported as a module so the UI shows exactly what the agent sees. A second
# implementation of "the complete question" here would be a second answer to the same question,
# and the first thing to drift would be the figure boxes.
_spec = importlib.util.spec_from_file_location("repair_agent_bridge", AGENT_DIR / "bridge.py")
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

from qbr.review_ui.paths import content_type_of, safe_file_path  # noqa: E402

STORE = Path(bridge.STORE)
STORE_DIR = Path(bridge.STORE_DIR)
LESSONS = STORE_DIR / "lessons.jsonl"
CROPS = STORE_DIR / "crops"

_write_lock = threading.Lock()


def _read_jsonl(path: Path) -> list[dict]:
    """Tolerant read: a half-written last line must not blank the whole page."""
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def judgements_for(key: str) -> list[dict]:
    """What has already been said about this question, by the agent and by the designer.

    Both are in the same stream on purpose: the designer's guidance and the agent's judgement are
    both "evidence about this question", and separating them by file would make "who said this"
    a property of the path rather than of the record. `source` carries that, explicitly.
    """
    return [row for row in _read_jsonl(STORE) if row.get("candidate_key") == key]


def lessons_for(key: str) -> list[dict]:
    return [row for row in _read_jsonl(LESSONS) if row.get("key") == key]


def append_judgement(record: dict) -> dict:
    """Append one record. The only write this server performs.

    Mirrors `review_state.append_ai_feedback`'s shape so that promoting this stream into the real
    one later is a change of destination, not of format. `candidate_key` is required because a
    judgement with no question is a judgement nobody can act on.
    """
    if not record.get("candidate_key"):
        raise ValueError("candidate_key is required")
    if not str(record.get("reason") or "").strip():
        raise ValueError("reason is required: guidance with no basis cannot be learned from")
    record["at"] = record.get("at") or _utc_now()
    with _write_lock:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        with STORE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def question_payload(key: str) -> dict:
    question = bridge.load_question(key)
    view = bridge.question_view(question)
    pdf = bridge.pdf_path_of(question)
    view["pdf_absolute"] = pdf
    view["pdf_relative"] = question.get("metadata", {}).get("question_pdf_relative")
    view["page"] = question.get("metadata", {}).get("question_page")
    view["crop_png"] = None
    # Matched on the paper's own file name, not just the number: a crop is named after the paper it
    # came from (`1152_醫事檢驗師_..._q068.png`), and two different papers both have a q068. Globbing
    # on the number alone would show one paper's crop beside another paper's question — a picture
    # that looks authoritative and is of the wrong page.
    paper = Path(pdf).name.replace(".pdf", "") if pdf else ""
    candidates = sorted(CROPS.glob("%s_q%03d*.png" % (paper, view.get("question_number") or 0)))
    if candidates:
        view["crop_png"] = str(candidates[-1])
    view["judgements"] = judgements_for(key)
    view["lessons"] = lessons_for(key)
    return view


class Handler(BaseHTTPRequestHandler):
    server_version = "RepairAgentUI/0.1"

    def log_message(self, fmt, *args):  # noqa: A003 - quieter than the default
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in ("/", "/index.html"):
                self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/api/search":
                self._search(query)
            elif parsed.path == "/api/question":
                self._question(query)
            elif parsed.path == "/api/queue":
                self._queue(query)
            elif parsed.path == "/file":
                self._file(query)
            elif parsed.path == "/crop":
                self._crop(query)
            else:
                self._json({"error": "not found: %s" % parsed.path}, 404)
        except bridge.QuestionNotFound as error:
            self._json({"error": str(error)}, 404)
        except Exception as error:  # noqa: BLE001 - the UI must show why, not die
            self._json({"error": "%s: %s" % (type(error).__name__, error)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "body is not JSON"}, 400)
            return
        try:
            if parsed.path == "/api/judgement":
                record = {
                    "candidate_key": payload.get("key"),
                    "rating": payload.get("rating") or "up",
                    "reason": payload.get("reason"),
                    "engine": payload.get("engine") or "human:designer",
                    "source": "designer",
                    "scope": "question",
                    "question_number": payload.get("question_number"),
                }
                self._json({"appended": append_judgement(record), "store": str(STORE)})
            else:
                self._json({"error": "not found: %s" % parsed.path}, 404)
        except ValueError as error:
            self._json({"error": str(error)}, 400)

    # -- endpoints ---------------------------------------------------------------------------
    def _search(self, query: dict) -> None:
        # `number` must be **None** when absent, not 0: `do_find` filters on
        # `args.number is not None`, so a 0 here silently turns every search into "question number
        # zero", which matches nothing and looks like an empty corpus rather than a bad default.
        raw_number = (query.get("number") or [""])[0]
        args = argparse.Namespace(
            subject=(query.get("subject") or [""])[0],
            number=int(raw_number) if raw_number.strip().isdigit() else None,
            contains=(query.get("contains") or [""])[0],
            with_figures=(query.get("with_figures") or ["0"])[0] in ("1", "true"),
            limit=int((query.get("limit") or ["40"])[0] or 40),
        )
        self._json(bridge.do_find(args))

    def _question(self, query: dict) -> None:
        key = (query.get("key") or [""])[0]
        if not key:
            self._json({"error": "key is required"}, 400)
            return
        self._json(question_payload(key))

    def _queue(self, query: dict) -> None:
        """A paper's questions in order, with the machine's own signal per question.

        This is what makes grouping possible without inventing a score: `quality_status` and the
        finding population are already on the record (2,858 of 3,471 figure questions were judged
        `OK` by a text-only pass that could not see the picture — see qa-log Q21). The UI shows the
        existing signal and lets the designer decide which group deserves attention.
        """
        key = (query.get("key") or [""])[0]
        question = bridge.load_question(key)
        limit = int((query.get("limit") or ["0"])[0] or 0)
        rows = []
        for peer in bridge.questions_of_paper(question, limit=limit):
            rows.append({
                "candidate_key": peer.get("candidate_key"),
                "question_number": peer.get("question_number"),
                "quality_status": peer.get("quality_status"),
                "figures": len(peer.get("image_refs") or []),
                "human_touched": bool(bridge.human_events(peer.get("candidate_key") or "")),
            })
        self._json({"paper": question.get("candidate_key"), "questions": rows})

    def _file(self, query: dict) -> None:
        """Serve a corpus file (the question PDF) the same way v2 does.

        Delegated to `qbr.review_ui.paths.safe_file_path` rather than reimplemented: it applies the
        allowed-roots check and prefers the browser-safe variant that makes JPEG 2000 scans
        renderable in Chrome. A second path resolver here would miss exactly that fix.
        """
        raw = (query.get("path") or [""])[0]
        resolved = safe_file_path(raw)
        if resolved is None or not resolved.is_file():
            self._json({"error": "file not available: %s" % raw}, 404)
            return
        data = resolved.read_bytes()
        self._send(200, data, content_type_of(resolved.name, data))

    def _crop(self, query: dict) -> None:
        path = (query.get("path") or [""])[0]
        resolved = Path(path).resolve()
        # Confined to the sandbox crop directory: this route serves pictures the agent itself
        # produced, and an unconfined reader here would be a path-traversal in front of the whole
        # repository.
        if not str(resolved).startswith(str(CROPS.resolve())) or not resolved.is_file():
            self._json({"error": "crop not available"}, 404)
            return
        data = resolved.read_bytes()
        self._send(200, data, content_type_of(resolved.name, data))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("判讀介面： http://%s:%d" % (args.host, args.port), file=sys.stderr)
    print("學習語料： %s" % STORE, file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
