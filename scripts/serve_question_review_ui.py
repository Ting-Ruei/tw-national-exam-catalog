#!/usr/bin/env python3
"""
Serve a minimal local review UI for question candidates.

The UI is intentionally dependency-free. It reads candidate JSONL and issue CSV
files, displays the source PDF beside parsed content, and appends human review
events to a JSONL log. It does not write to PostgreSQL.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import mimetypes
import os
import sys
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover - optional runtime dependency in Docker
    psycopg = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
DEFAULT_CANDIDATE_ROOT = ASSET_ROOT / "30_normalized_items" / "question_candidates"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a local human review UI for question candidates.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def latest_path(pattern: str) -> Path:
    paths = sorted(DEFAULT_CANDIDATE_ROOT.glob(pattern))
    if not paths:
        raise SystemExit(f"No candidate output found: {DEFAULT_CANDIDATE_ROOT}/{pattern}")
    return paths[-1]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_issues(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}
    issues: dict[str, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("candidate_key") or ""
            if row.get("issue_json"):
                try:
                    row["issue_json"] = json.loads(row["issue_json"])
                except json.JSONDecodeError:
                    pass
            issues.setdefault(key, []).append(row)
    return issues


def project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        parts = path.parts
        if "tw-national-exam-catalog" in parts:
            index = parts.index("tw-national-exam-catalog")
            return PROJECT_ROOT.joinpath(*parts[index + 1 :])
        return path
    if value.startswith("國考題資料夾/"):
        return PROJECT_ROOT / value
    return ASSET_ROOT / value


def safe_file_path(value: str) -> Path | None:
    if not value:
        return None
    path = project_path(value)
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return None
    allowed_roots = [PROJECT_ROOT.resolve(), ASSET_ROOT.resolve()]
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return resolved
    return None


def display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def sibling_pdf(markdown_value: str, suffix: str) -> str | None:
    if not markdown_value:
        return None
    markdown_path = project_path(markdown_value)
    candidate = markdown_path.with_name(f"{markdown_path.stem}{suffix}.pdf")
    if candidate.exists():
        return display_path(candidate)
    return None


def load_review_events(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    if not path.exists():
        return latest, counts
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            latest[key] = event
    return latest, counts


def html_page() -> bytes:
    return PAGE_HTML.encode("utf-8")


class ReviewState:
    def __init__(self, candidate_path: Path, issue_path: Path | None, review_log: Path) -> None:
        self.candidate_path = candidate_path
        self.issue_path = issue_path
        self.review_log = review_log
        self.candidates = load_jsonl(candidate_path)
        self.issues = load_issues(issue_path)
        self.review_log.parent.mkdir(parents=True, exist_ok=True)
        self.preference_path = self.review_log.parent / "review_ui_preferences.json"
        self.database_url = os.environ.get("DATABASE_URL")
        self.latest_reviews, self.review_counts = load_review_events(review_log)

    def candidate_payloads(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for item in self.candidates:
            key = item["candidate_key"]
            copy = dict(item)
            metadata = copy.get("metadata") or {}
            copy["issues"] = self.issues.get(key, [])
            latest_review = self.latest_reviews.get(key)
            copy["review"] = {
                "status": "reviewed" if latest_review else "unreviewed",
                "action": latest_review.get("action") if latest_review else None,
                "notes": latest_review.get("notes") if latest_review else None,
                "updated_at": latest_review.get("created_at") if latest_review else None,
                "event_count": self.review_counts.get(key, 0),
            }
            copy["source_files"] = {
                "official_pdf": metadata.get("question_pdf_relative") or metadata.get("question_pdf"),
                "mineru_layout_pdf": sibling_pdf(metadata.get("question_markdown_relative") or metadata.get("question_markdown") or "", "_layout"),
                "mineru_origin_pdf": sibling_pdf(metadata.get("question_markdown_relative") or metadata.get("question_markdown") or "", "_origin"),
                "question_markdown": metadata.get("question_markdown_relative") or metadata.get("question_markdown"),
            }
            payloads.append(copy)
        return payloads

    def load_preferences(self, reviewer: str) -> dict[str, Any]:
        preferences = self._load_file_preferences().get(reviewer, {})
        db_preferences = self._load_db_preferences(reviewer)
        if db_preferences:
            preferences.update(db_preferences)
        return preferences

    def save_preferences(self, reviewer: str, preferences: dict[str, Any]) -> None:
        preferences = dict(preferences)
        file_preferences = self._load_file_preferences()
        file_preferences[reviewer] = preferences
        with self.preference_path.open("w", encoding="utf-8") as f:
            json.dump(file_preferences, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        self._save_db_preferences(reviewer, preferences)

    def _load_file_preferences(self) -> dict[str, dict[str, Any]]:
        if not self.preference_path.exists():
            return {}
        try:
            with self.preference_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        return {}

    def _load_db_preferences(self, reviewer: str) -> dict[str, Any]:
        if psycopg is None or not self.database_url:
            return {}
        try:
            with psycopg.connect(self.database_url, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT preferences_json FROM exam.review_ui_preferences WHERE reviewer = %s",
                        (reviewer,),
                    )
                    row = cur.fetchone()
                    if row and isinstance(row[0], dict):
                        return row[0]
        except Exception:
            return {}
        return {}

    def _save_db_preferences(self, reviewer: str, preferences: dict[str, Any]) -> None:
        if psycopg is None or not self.database_url:
            return
        try:
            with psycopg.connect(self.database_url, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO exam.review_ui_preferences (reviewer, preferences_json, updated_at)
                        VALUES (%s, %s::jsonb, now())
                        ON CONFLICT (reviewer) DO UPDATE
                        SET preferences_json = EXCLUDED.preferences_json,
                            updated_at = now()
                        """,
                        (reviewer, json.dumps(preferences, ensure_ascii=False)),
                    )
                conn.commit()
        except Exception:
            return

    def append_review(self, event: dict[str, Any]) -> None:
        event = dict(event)
        event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        with self.review_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        key = event.get("candidate_key")
        if key:
            self.latest_reviews[key] = event
            self.review_counts[key] = self.review_counts.get(key, 0) + 1

    def pipeline_payload(self) -> dict[str, Any]:
        candidates = self.candidate_payloads()
        reviewed = [item for item in candidates if item.get("review", {}).get("status") == "reviewed"]
        accepted = [item for item in candidates if item.get("review", {}).get("action") == "accept"]
        blocked = [item for item in candidates if item.get("review", {}).get("action") == "block"]
        needs_review = [item for item in candidates if item.get("review", {}).get("action") == "needs_review"]
        issue_count = sum(len(item.get("issues", [])) for item in candidates)
        answer_ready = [item for item in candidates if item.get("answer") not in (None, "")]
        question_accepted_answer_pending = [item for item in accepted if item.get("answer") not in (None, "")]
        return {
            "candidate_jsonl": str(self.candidate_path),
            "issue_csv": str(self.issue_path) if self.issue_path else None,
            "review_log": str(self.review_log),
            "layers": [
                {
                    "name": "官方 PDF / MinerU raw",
                    "tables": ["exam.official_documents", "exam.assets", "exam.document_assets", "exam.mineru_runs"],
                    "status": "source",
                    "count": len({item.get("source_registry_key") for item in candidates}),
                    "description": "官方 PDF、MinerU markdown、圖片與 layout PDF。這一層只追溯來源，不代表題目已可入庫。",
                },
                {
                    "name": "題目 candidate",
                    "tables": ["exam.question_candidates"],
                    "status": "pre_ingestion",
                    "count": len(candidates),
                    "description": "parser 從 MinerU markdown 切出的候選題目，目前仍需人工審核。",
                },
                {
                    "name": "QA flags",
                    "tables": ["exam.question_parse_issues"],
                    "status": "pre_ingestion",
                    "count": issue_count,
                    "description": "機械檢查疑點，例如題號重複、選項不足、圖片提示但未偵測圖片。",
                },
                {
                    "name": "題目人工審核",
                    "tables": ["exam.question_review_events"],
                    "status": "human_review",
                    "count": len(reviewed),
                    "description": "你在 Review UI 按下通過、保留疑問、阻擋入庫、註記後產生的事件。",
                    "breakdown": {
                        "accepted": len(accepted),
                        "needs_review": len(needs_review),
                        "blocked": len(blocked),
                    },
                },
                {
                    "name": "答案核對",
                    "tables": ["exam.answer_review_events"],
                    "status": "planned",
                    "count": len(question_accepted_answer_pending),
                    "description": "獨立於題目結構審核。題目通過後，再集中核對答案、MOD/ANS 優先序與答案表解析。",
                    "breakdown": {
                        "candidates_with_answer": len(answer_ready),
                        "question_accepted_answer_pending": len(question_accepted_answer_pending),
                    },
                },
                {
                    "name": "正式題庫",
                    "tables": ["exam.question_groups", "exam.questions", "exam.question_options", "exam.answers", "exam.question_assets"],
                    "status": "not_bulk_ingested",
                    "count": 0,
                    "description": "目前不做大量自動寫入。只有題目審核與答案核對都通過後，才升級到正式表。",
                },
            ],
        }


class Handler(BaseHTTPRequestHandler):
    state: ReviewState

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), format % args))

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            data = html_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
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
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            data = html_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/candidates":
            self.send_json(
                {
                    "candidate_jsonl": str(self.state.candidate_path),
                    "issue_csv": str(self.state.issue_path) if self.state.issue_path else None,
                    "review_log": str(self.state.review_log),
                    "candidates": self.state.candidate_payloads(),
                }
            )
            return
        if parsed.path == "/api/pipeline":
            self.send_json(self.state.pipeline_payload())
            return
        if parsed.path == "/api/preferences":
            query = urllib.parse.parse_qs(parsed.query)
            reviewer = query.get("reviewer", ["local"])[0] or "local"
            self.send_json({"ok": True, "reviewer": reviewer, "preferences": self.state.load_preferences(reviewer)})
            return
        if parsed.path == "/file":
            query = urllib.parse.parse_qs(parsed.query)
            path = safe_file_path(query.get("path", [""])[0])
            if path is None or not path.exists() or not path.is_file():
                self.send_error(404, "File not found or not allowed")
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/api/review", "/api/preferences"}:
            self.send_error(404, "Not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
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
        action = payload.get("action")
        if action not in {"accept", "correct", "needs_review", "block", "unblock", "comment", "reviewed"}:
            self.send_json({"ok": False, "error": "Invalid action"}, status=400)
            return
        if not payload.get("candidate_key"):
            self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
            return
        self.state.append_review(payload)
        self.send_json({"ok": True, "review_log": str(self.state.review_log), "event": payload})


PAGE_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>國考題候選審核</title>
  <style>
    :root { color-scheme: light; --line:#d9dee8; --muted:#687385; --bg:#f7f8fb; --ink:#172033; --ok:#0b7a4b; --warn:#9a5b00; --bad:#b42318; --blue:#175cd3; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Noto Sans TC", "Segoe UI", sans-serif; color:var(--ink); background:var(--bg); }
    header { min-height:76px; display:flex; align-items:center; gap:10px; padding:10px 16px; border-bottom:1px solid var(--line); background:white; flex-wrap:wrap; }
    header strong { font-size:16px; }
    header input, header select { height:32px; border:1px solid var(--line); border-radius:6px; padding:0 8px; background:white; max-width:180px; }
    main { display:grid; grid-template-columns: 320px minmax(360px, 1fr) minmax(420px, 1.2fr); height:calc(100vh - 76px); }
    aside { border-right:1px solid var(--line); overflow:auto; background:white; }
    .list-item { width:100%; text-align:left; border:0; border-bottom:1px solid var(--line); background:white; padding:10px 12px; cursor:pointer; }
    .list-item:hover, .list-item.active { background:#eef4ff; }
    .meta { color:var(--muted); font-size:12px; line-height:1.35; }
    .badge { display:inline-block; min-width:44px; text-align:center; padding:2px 6px; border-radius:999px; font-size:12px; background:#edf0f5; color:#334155; }
    .badge.pass { background:#dff7ea; color:var(--ok); }
    .badge.needs_review { background:#fff1cf; color:var(--warn); }
    .badge.blocked { background:#fee4e2; color:var(--bad); }
    .badge.reviewed { background:#dbeafe; color:var(--blue); }
    .badge.unreviewed { background:#edf0f5; color:#475467; }
    section { overflow:auto; padding:14px; }
    .panel { background:white; border:1px solid var(--line); border-radius:8px; margin-bottom:12px; overflow:hidden; }
    .panel h2 { margin:0; padding:10px 12px; font-size:14px; border-bottom:1px solid var(--line); background:#fbfcff; }
    .panel .body { padding:12px; }
    .stem { white-space:pre-wrap; line-height:1.55; }
    .option { display:grid; grid-template-columns:34px 1fr; gap:8px; margin:8px 0; line-height:1.5; }
    .option b { color:#243b64; }
    .issue { border-left:4px solid #b8c1d1; padding:7px 9px; margin:7px 0; background:#f8fafc; }
    .issue.warning { border-color:#e2a100; }
    .issue.error, .issue.blocked { border-color:#d92d20; }
    .toolbar { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
    button.action { border:1px solid var(--line); border-radius:6px; padding:8px 10px; background:white; cursor:pointer; }
    button.action.accept { border-color:#8bd9b1; color:var(--ok); }
    button.action.block { border-color:#f2a19b; color:var(--bad); }
    button.action.active { background:#eef4ff; border-color:#9ab8ff; color:var(--blue); }
    button.nav { border:1px solid var(--line); border-radius:6px; height:32px; padding:0 10px; background:white; cursor:pointer; }
    textarea { width:100%; min-height:72px; resize:vertical; border:1px solid var(--line); border-radius:6px; padding:8px; }
    iframe { width:100%; height:calc(100vh - 162px); border:1px solid var(--line); border-radius:8px; background:white; }
    .viewer-toolbar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
    .viewer-toolbar button { border:1px solid var(--line); border-radius:6px; padding:7px 9px; background:white; cursor:pointer; }
    .viewer-toolbar button.active { background:#eef4ff; border-color:#9ab8ff; color:var(--blue); }
    .asset-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap:8px; }
    .asset-grid img { width:100%; max-height:160px; object-fit:contain; border:1px solid var(--line); border-radius:6px; background:white; }
    .inline-images { display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:10px; margin:12px 0; }
    .inline-images figure { margin:0; border:1px solid var(--line); border-radius:8px; background:#fbfcff; padding:8px; }
    .inline-images img { width:100%; max-height:280px; object-fit:contain; display:block; background:white; }
    .inline-images figcaption { margin-top:6px; color:var(--muted); font-size:12px; word-break:break-all; }
    .layer { border-left:4px solid #b8c1d1; background:#f8fafc; margin:8px 0; padding:9px 10px; }
    .layer.human_review { border-color:#175cd3; }
    .layer.planned { border-color:#9a5b00; }
    .layer.not_bulk_ingested { border-color:#b42318; }
    .kv { display:grid; grid-template-columns:120px 1fr; gap:6px 10px; }
    .question-number { display:inline-flex; align-items:baseline; gap:6px; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:#f8fafc; margin:8px 0; }
    .question-number b { font-size:16px; color:#0f2f5f; }
    .filter-label { display:flex; align-items:center; gap:4px; }
    code { word-break:break-all; }
  </style>
</head>
<body>
  <header>
    <strong>國考題候選審核</strong>
    <input id="search" placeholder="搜尋類科、科目、題號、疑點">
    <select id="status">
      <option value="">全部狀態</option>
      <option value="blocked">blocked</option>
      <option value="needs_review">needs_review</option>
      <option value="pass">pass</option>
    </select>
    <select id="reviewStatus">
      <option value="">全部審核</option>
      <option value="unreviewed" selected>未看過</option>
      <option value="reviewed">已看過</option>
      <option value="accept">已通過</option>
      <option value="block">阻擋入庫</option>
      <option value="needs_review">保留疑問</option>
      <option value="comment">有註記</option>
    </select>
    <label class="filter-label meta">考別<select id="categoryFilter"><option value="">全部</option></select></label>
    <label class="filter-label meta">科目<select id="subjectFilter"><option value="">全部</option></select></label>
    <label class="filter-label meta">年份<select id="yearFilter"><option value="">全部</option></select></label>
    <label class="filter-label meta">考次<select id="ordinalFilter"><option value="">全部</option></select></label>
    <span id="count" class="meta"></span>
    <span id="progress" class="meta"></span>
    <button class="nav" onclick="showPipeline()">資料庫層級</button>
  </header>
  <main>
    <aside id="list"></aside>
    <section id="detail"></section>
    <section>
      <div class="viewer-toolbar">
        <button id="pdfOfficial" onclick="setPdfKind('official_pdf')">官方 PDF</button>
        <button id="pdfLayout" onclick="setPdfKind('mineru_layout_pdf')">MinerU layout</button>
        <button id="pdfOrigin" onclick="setPdfKind('mineru_origin_pdf')">MinerU origin</button>
        <a id="pdfOpen" class="meta" target="_blank">另開</a>
      </div>
      <iframe id="pdf"></iframe>
      <p id="pdfPath" class="meta"></p>
    </section>
  </main>
<script>
let candidates = [];
let filtered = [];
let current = null;
let currentPdfKind = 'mineru_layout_pdf';
let lastPdfUrl = '';
let preferences = {};
let savePreferenceTimer = null;
const reviewer = 'local';

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[m]));
const fileUrl = (path) => path ? `/file?path=${encodeURIComponent(path)}` : '';
const compactJson = (value) => {
  if (!value || (typeof value === 'object' && Object.keys(value).length === 0)) return '';
  try { return JSON.stringify(value); } catch { return String(value); }
};

async function load() {
  const [candidateRes, preferenceRes] = await Promise.all([
    fetch('/api/candidates'),
    fetch(`/api/preferences?reviewer=${encodeURIComponent(reviewer)}`)
  ]);
  const data = await candidateRes.json();
  const preferenceData = await preferenceRes.json();
  candidates = data.candidates;
  preferences = preferenceData.preferences || {};
  populateFilters();
  restorePreferences();
  applyFilter(preferences.currentKey || null);
}

function uniqueSorted(values, numeric = false) {
  const items = [...new Set(values.filter(v => v !== undefined && v !== null && String(v).trim() !== '').map(String))];
  return items.sort((a, b) => numeric ? Number(a) - Number(b) : a.localeCompare(b, 'zh-Hant'));
}

function populateSelect(id, values, numeric = false) {
  const select = document.getElementById(id);
  const currentValue = select.value;
  select.innerHTML = '<option value="">全部</option>' + uniqueSorted(values, numeric).map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
  if ([...select.options].some(option => option.value === currentValue)) select.value = currentValue;
}

function populateFilters() {
  const metas = candidates.map(item => item.metadata || {});
  populateSelect('categoryFilter', metas.map(meta => meta.normalized_category_name || meta.group_name));
  populateSelect('subjectFilter', metas.map(meta => meta.normalized_subject_name));
  populateSelect('yearFilter', metas.map(meta => meta.year), true);
  populateSelect('ordinalFilter', metas.map(meta => meta.exam_ordinal), true);
}

function restorePreferences() {
  const filters = preferences.filters || {};
  for (const [id, value] of Object.entries(filters)) {
    const element = document.getElementById(id);
    if (element && Array.from(element.options || []).some(option => option.value === value)) {
      element.value = value;
    } else if (element && element.tagName === 'INPUT') {
      element.value = value || '';
    }
  }
  currentPdfKind = preferences.pdfKind || currentPdfKind;
}

function collectPreferences() {
  return {
    filters: {
      search: document.getElementById('search').value,
      status: document.getElementById('status').value,
      reviewStatus: document.getElementById('reviewStatus').value,
      categoryFilter: document.getElementById('categoryFilter').value,
      subjectFilter: document.getElementById('subjectFilter').value,
      yearFilter: document.getElementById('yearFilter').value,
      ordinalFilter: document.getElementById('ordinalFilter').value
    },
    currentKey: current ? current.candidate_key : preferences.currentKey || '',
    pdfKind: currentPdfKind,
    updatedAt: new Date().toISOString()
  };
}

function savePreferencesSoon() {
  preferences = collectPreferences();
  clearTimeout(savePreferenceTimer);
  savePreferenceTimer = setTimeout(() => {
    fetch('/api/preferences', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({reviewer, preferences})
    }).catch(() => {});
  }, 250);
}

async function showPipeline() {
  const res = await fetch('/api/pipeline');
  const data = await res.json();
  current = null;
  renderList();
  document.getElementById('pdf').src = '';
  lastPdfUrl = '';
  document.getElementById('pdfOpen').removeAttribute('href');
  document.getElementById('pdfPath').textContent = '';
  const layers = data.layers.map(layer => `
    <div class="layer ${esc(layer.status)}">
      <h3>${esc(layer.name)} <span class="badge">${esc(layer.count)}</span></h3>
      <p>${esc(layer.description)}</p>
      <div class="kv">
        <span class="meta">狀態</span><code>${esc(layer.status)}</code>
        <span class="meta">表格</span><code>${esc((layer.tables || []).join(', '))}</code>
        ${layer.breakdown ? `<span class="meta">細項</span><code>${esc(JSON.stringify(layer.breakdown))}</code>` : ''}
      </div>
    </div>
  `).join('');
  document.getElementById('detail').innerHTML = `
    <div class="panel"><h2>資料庫入庫層級</h2><div class="body">
      <p class="meta">Candidate: <code>${esc(data.candidate_jsonl)}</code></p>
      <p class="meta">Issues: <code>${esc(data.issue_csv)}</code></p>
      <p class="meta">Review log: <code>${esc(data.review_log)}</code></p>
      ${layers}
    </div></div>`;
}

function applyFilter(preferredKey = null, preferredIndex = null, skipKey = null) {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const status = document.getElementById('status').value;
  const reviewStatus = document.getElementById('reviewStatus').value;
  const categoryFilter = document.getElementById('categoryFilter').value;
  const subjectFilter = document.getElementById('subjectFilter').value;
  const yearFilter = document.getElementById('yearFilter').value;
  const ordinalFilter = document.getElementById('ordinalFilter').value;
  filtered = candidates.filter(item => {
    const meta = item.metadata || {};
    const review = item.review || {};
    const text = [
      item.candidate_key, item.question_number, item.stem,
      meta.group_name, meta.normalized_category_name, meta.normalized_subject_name,
      review.action, review.notes,
      ...(item.issues || []).map(i => `${i.issue_code} ${i.message}`)
    ].join(' ').toLowerCase();
    const reviewMatch = !reviewStatus
      || review.status === reviewStatus
      || review.action === reviewStatus;
    const category = meta.normalized_category_name || meta.group_name || '';
    const subject = meta.normalized_subject_name || '';
    return (!status || item.quality_status === status)
      && reviewMatch
      && (!categoryFilter || category === categoryFilter)
      && (!subjectFilter || subject === subjectFilter)
      && (!yearFilter || String(meta.year || '') === yearFilter)
      && (!ordinalFilter || String(meta.exam_ordinal || '') === ordinalFilter)
      && (!q || text.includes(q));
  });
  const reviewedCount = candidates.filter(item => (item.review || {}).status === 'reviewed').length;
  document.getElementById('count').textContent = `${filtered.length} / ${candidates.length}`;
  document.getElementById('progress').textContent = `已看 ${reviewedCount}，未看 ${candidates.length - reviewedCount}`;

  let next = null;
  if (preferredKey) {
    next = filtered.find(item => item.candidate_key === preferredKey) || null;
  }
  if (!next && Number.isInteger(preferredIndex) && preferredIndex !== null && filtered.length) {
    const forward = filtered[Math.min(preferredIndex, filtered.length - 1)] || null;
    if (forward && forward.candidate_key !== skipKey) {
      next = forward;
    } else {
      next = filtered.find((item, index) => index > preferredIndex && item.candidate_key !== skipKey) || null;
      next = next || [...filtered].reverse().find((item, index) => filtered.length - 1 - index < preferredIndex && item.candidate_key !== skipKey) || null;
    }
  }
  if (!next && current && current.candidate_key !== skipKey) {
    next = filtered.find(item => item.candidate_key === current.candidate_key) || null;
  }
  if (!next && filtered.length) {
    next = filtered.find(item => item.candidate_key !== skipKey) || null;
  }
  current = next;
  renderList();
  renderDetail();
  savePreferencesSoon();
}

function renderList() {
  const list = document.getElementById('list');
  list.innerHTML = filtered.map(item => {
    const meta = item.metadata || {};
    const review = item.review || {};
    return `<button class="list-item ${current && current.candidate_key === item.candidate_key ? 'active' : ''}" onclick="selectCandidate('${esc(item.candidate_key)}')">
      <div><span class="badge ${esc(item.quality_status)}">${esc(item.quality_status)}</span> <span class="badge ${esc(review.status || 'unreviewed')}">${esc(review.action || review.status || 'unreviewed')}</span> 第 ${esc(item.question_number)} 題</div>
      <div class="meta">${esc(meta.group_name)} ${esc(meta.year)}-${esc(meta.exam_ordinal)} ${esc(meta.normalized_subject_name)}</div>
      <div class="meta">${esc(item.issue_count || 0)} issues</div>
    </button>`;
  }).join('');
}

function selectCandidate(key) {
  current = candidates.find(item => item.candidate_key === key);
  renderList();
  renderDetail();
  savePreferencesSoon();
}

function pdfPathFor(kind) {
  const files = current && current.source_files ? current.source_files : {};
  return files[kind] || files.mineru_layout_pdf || files.official_pdf || files.mineru_origin_pdf || '';
}

function setPdfKind(kind) {
  currentPdfKind = kind;
  updatePdfViewer();
  savePreferencesSoon();
}

function updatePdfViewer() {
  if (!current) return;
  const path = pdfPathFor(currentPdfKind);
  const url = fileUrl(path);
  if (url !== lastPdfUrl) {
    document.getElementById('pdf').src = url;
    lastPdfUrl = url;
  }
  document.getElementById('pdfOpen').href = url;
  document.getElementById('pdfPath').innerHTML = path ? `<code>${esc(path)}</code>` : '找不到可顯示的 PDF';
  for (const [kind, id] of [['official_pdf', 'pdfOfficial'], ['mineru_layout_pdf', 'pdfLayout'], ['mineru_origin_pdf', 'pdfOrigin']]) {
    const btn = document.getElementById(id);
    const exists = Boolean(pdfPathFor(kind));
    btn.disabled = !exists;
    btn.className = currentPdfKind === kind ? 'active' : '';
  }
}

function renderDetail() {
  if (!current) {
    document.getElementById('detail').innerHTML = `<div class="panel"><h2>目前沒有符合條件的題目</h2><div class="body"><p class="meta">可以切換審核篩選，或開始產生下一批 candidate。</p></div></div>`;
    document.getElementById('pdf').src = '';
    lastPdfUrl = '';
    document.getElementById('pdfOpen').removeAttribute('href');
    document.getElementById('pdfPath').textContent = '';
    return;
  }
  const meta = current.metadata || {};
  const reviewState = current.review || {};
  updatePdfViewer();
  const images = (current.image_refs || []).filter(ref => ref.exists).map(ref =>
    `<a href="${fileUrl(ref.path)}" target="_blank"><img src="${fileUrl(ref.path)}" alt="${esc(ref.raw_ref)}"></a>`
  ).join('');
  const inlineImages = (current.image_refs || []).filter(ref => ref.exists).map((ref, index) =>
    `<figure><a href="${fileUrl(ref.path)}" target="_blank"><img src="${fileUrl(ref.path)}" alt="${esc(ref.raw_ref || `image ${index + 1}`)}"></a><figcaption>圖 ${index + 1}: ${esc(ref.raw_ref || ref.path)}</figcaption></figure>`
  ).join('');
  const issues = (current.issues || []).map(issue => {
    const detail = compactJson(issue.issue_json);
    return `<div class="issue ${esc(issue.severity)}"><b>${esc(issue.severity)} / ${esc(issue.issue_code)}</b><br>${esc(issue.message)}${detail ? `<br><code>${esc(detail)}</code>` : ''}</div>`;
  }).join('') || '<div class="meta">目前沒有 QA flag。</div>';
  const options = (current.options || []).map(opt =>
    `<div class="option"><b>(${esc(opt.key)})</b><div>${esc(opt.text)}</div></div>`
  ).join('');
  const answerText = current.answer !== undefined && current.answer !== null && String(current.answer).trim() !== ''
    ? esc(current.answer)
    : '<span class="meta">目前 candidate 未抓到答案，後續答案核對關卡會集中排查。</span>';
  document.getElementById('detail').innerHTML = `
    <div class="panel"><h2>題目</h2><div class="body">
      <div class="meta"><code>${esc(current.candidate_key)}</code></div>
      <p class="meta">Canonical: <code>${esc(current.canonical_question_key || current.candidate_key)}</code> / occurrence ${esc(current.question_number_occurrence || 1)}</p>
      <p class="meta">${esc(meta.normalized_category_name)} / ${esc(meta.normalized_subject_name)} / ${esc(meta.year)} 年第 ${esc(meta.exam_ordinal)} 次</p>
      <div class="question-number"><span>資料庫題號</span><b>第 ${esc(current.question_number)} 題</b><span class="meta">occurrence ${esc(current.question_number_occurrence || 1)}</span></div>
      <p><span class="badge ${esc(reviewState.status || 'unreviewed')}">${esc(reviewState.action || reviewState.status || 'unreviewed')}</span> <span class="meta">${esc(reviewState.updated_at || '')}</span></p>
      <div class="stem">${esc(current.stem)}</div>
      ${inlineImages ? `<div class="inline-images">${inlineImages}</div>` : ''}
      <hr>${options}
      <p><b>答案：</b>${answerText} <span class="meta">此處顯示目前解析結果；正式判定會在下一個「答案核對」關卡統一檢查。</span></p>
      <p><b>題組：</b>${esc(current.group_ref ?? '無')}</p>
    </div></div>
    <div class="panel"><h2>疑點</h2><div class="body">${issues}</div></div>
    <div class="panel"><h2>圖片</h2><div class="body"><div class="asset-grid">${images || '<span class="meta">未偵測到圖片引用。</span>'}</div></div></div>
    <div class="panel"><h2>人工審核</h2><div class="body">
      <textarea id="notes" placeholder="審核註記或修正摘要">${esc(reviewState.notes || '')}</textarea>
      <div class="toolbar">
        <button class="action accept" onclick="review('accept')">通過</button>
        <button class="action" onclick="review('reviewed')">標記已看過</button>
        <button class="action" onclick="review('needs_review')">保留疑問</button>
        <button class="action block" onclick="review('block')">阻擋入庫</button>
        <button class="action" onclick="review('comment')">只加註記</button>
      </div>
      <p id="saved" class="meta"></p>
    </div></div>
    <div class="panel"><h2>來源</h2><div class="body">
      <p class="meta">官方 PDF: <code>${esc((current.source_files || {}).official_pdf || '')}</code></p>
      <p class="meta">MinerU layout: <code>${esc((current.source_files || {}).mineru_layout_pdf || '')}</code></p>
      <p class="meta">MinerU origin: <code>${esc((current.source_files || {}).mineru_origin_pdf || '')}</code></p>
      <p class="meta">Markdown: <code>${esc((current.source_files || {}).question_markdown || '')}</code></p>
    </div></div>`;
}

async function review(action) {
  if (!current) return;
  const reviewedKey = current.candidate_key;
  const currentIndex = filtered.findIndex(item => item.candidate_key === reviewedKey);
  const notes = document.getElementById('notes').value;
  const res = await fetch('/api/review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({candidate_key: current.candidate_key, action, notes, reviewer: 'local'})
  });
  const data = await res.json();
  if (data.ok) {
    current.review = {
      status: 'reviewed',
      action,
      notes,
      updated_at: data.event.created_at,
      event_count: (current.review?.event_count || 0) + 1
    };
    applyFilter(null, currentIndex >= 0 ? currentIndex : null, reviewedKey);
  } else {
    document.getElementById('saved').textContent = `寫入失敗：${data.error}`;
  }
}

document.getElementById('search').addEventListener('input', applyFilter);
document.getElementById('status').addEventListener('change', applyFilter);
document.getElementById('reviewStatus').addEventListener('change', applyFilter);
document.getElementById('categoryFilter').addEventListener('change', applyFilter);
document.getElementById('subjectFilter').addEventListener('change', applyFilter);
document.getElementById('yearFilter').addEventListener('change', applyFilter);
document.getElementById('ordinalFilter').addEventListener('change', applyFilter);
load();
</script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(candidate_path, issue_path, review_log)
    Handler.state = state
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Review UI: http://{args.host}:{args.port}/")
    print(f"Candidate JSONL: {candidate_path}")
    print(f"Issue CSV: {issue_path}")
    print(f"Review log: {review_log}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
