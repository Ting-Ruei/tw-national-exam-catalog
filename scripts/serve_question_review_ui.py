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
import sys
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


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


def safe_file_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        if value.startswith("國考題資料夾/"):
            path = PROJECT_ROOT / value
        else:
            path = ASSET_ROOT / value
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return None
    allowed_roots = [PROJECT_ROOT.resolve(), ASSET_ROOT.resolve()]
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return resolved
    return None


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

    def candidate_payloads(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for item in self.candidates:
            key = item["candidate_key"]
            copy = dict(item)
            copy["issues"] = self.issues.get(key, [])
            payloads.append(copy)
        return payloads

    def append_review(self, event: dict[str, Any]) -> None:
        event = dict(event)
        event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        with self.review_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


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
        if parsed.path != "/api/review":
            self.send_error(404, "Not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
            return
        action = payload.get("action")
        if action not in {"accept", "correct", "needs_review", "block", "unblock", "comment"}:
            self.send_json({"ok": False, "error": "Invalid action"}, status=400)
            return
        if not payload.get("candidate_key"):
            self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
            return
        self.state.append_review(payload)
        self.send_json({"ok": True, "review_log": str(self.state.review_log)})


PAGE_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>國考題候選審核</title>
  <style>
    :root { color-scheme: light; --line:#d9dee8; --muted:#687385; --bg:#f7f8fb; --ink:#172033; --ok:#0b7a4b; --warn:#9a5b00; --bad:#b42318; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Noto Sans TC", "Segoe UI", sans-serif; color:var(--ink); background:var(--bg); }
    header { height:52px; display:flex; align-items:center; gap:12px; padding:0 16px; border-bottom:1px solid var(--line); background:white; }
    header strong { font-size:16px; }
    header input, header select { height:32px; border:1px solid var(--line); border-radius:6px; padding:0 8px; background:white; }
    main { display:grid; grid-template-columns: 320px minmax(360px, 1fr) minmax(420px, 1.2fr); height:calc(100vh - 52px); }
    aside { border-right:1px solid var(--line); overflow:auto; background:white; }
    .list-item { width:100%; text-align:left; border:0; border-bottom:1px solid var(--line); background:white; padding:10px 12px; cursor:pointer; }
    .list-item:hover, .list-item.active { background:#eef4ff; }
    .meta { color:var(--muted); font-size:12px; line-height:1.35; }
    .badge { display:inline-block; min-width:44px; text-align:center; padding:2px 6px; border-radius:999px; font-size:12px; background:#edf0f5; color:#334155; }
    .badge.pass { background:#dff7ea; color:var(--ok); }
    .badge.needs_review { background:#fff1cf; color:var(--warn); }
    .badge.blocked { background:#fee4e2; color:var(--bad); }
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
    textarea { width:100%; min-height:72px; resize:vertical; border:1px solid var(--line); border-radius:6px; padding:8px; }
    iframe { width:100%; height:calc(100vh - 92px); border:1px solid var(--line); border-radius:8px; background:white; }
    .asset-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap:8px; }
    .asset-grid img { width:100%; max-height:160px; object-fit:contain; border:1px solid var(--line); border-radius:6px; background:white; }
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
    <span id="count" class="meta"></span>
  </header>
  <main>
    <aside id="list"></aside>
    <section id="detail"></section>
    <section><iframe id="pdf"></iframe></section>
  </main>
<script>
let candidates = [];
let filtered = [];
let current = null;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[m]));
const fileUrl = (path) => path ? `/file?path=${encodeURIComponent(path)}` : '';

async function load() {
  const res = await fetch('/api/candidates');
  const data = await res.json();
  candidates = data.candidates;
  applyFilter();
}

function applyFilter() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const status = document.getElementById('status').value;
  filtered = candidates.filter(item => {
    const meta = item.metadata || {};
    const text = [
      item.candidate_key, item.question_number, item.stem,
      meta.group_name, meta.normalized_category_name, meta.normalized_subject_name,
      ...(item.issues || []).map(i => `${i.issue_code} ${i.message}`)
    ].join(' ').toLowerCase();
    return (!status || item.quality_status === status) && (!q || text.includes(q));
  });
  document.getElementById('count').textContent = `${filtered.length} / ${candidates.length}`;
  renderList();
  if (!current && filtered.length) selectCandidate(filtered[0].candidate_key);
}

function renderList() {
  const list = document.getElementById('list');
  list.innerHTML = filtered.map(item => {
    const meta = item.metadata || {};
    return `<button class="list-item ${current && current.candidate_key === item.candidate_key ? 'active' : ''}" onclick="selectCandidate('${esc(item.candidate_key)}')">
      <div><span class="badge ${esc(item.quality_status)}">${esc(item.quality_status)}</span> 第 ${esc(item.question_number)} 題</div>
      <div class="meta">${esc(meta.group_name)} ${esc(meta.year)}-${esc(meta.exam_ordinal)} ${esc(meta.normalized_subject_name)}</div>
      <div class="meta">${esc(item.issue_count || 0)} issues</div>
    </button>`;
  }).join('');
}

function selectCandidate(key) {
  current = candidates.find(item => item.candidate_key === key);
  renderList();
  renderDetail();
}

function renderDetail() {
  if (!current) return;
  const meta = current.metadata || {};
  const pdf = meta.question_pdf || meta.question_pdf_relative || '';
  document.getElementById('pdf').src = fileUrl(pdf);
  const images = (current.image_refs || []).filter(ref => ref.exists).map(ref =>
    `<a href="${fileUrl(ref.path)}" target="_blank"><img src="${fileUrl(ref.path)}" alt="${esc(ref.raw_ref)}"></a>`
  ).join('');
  const issues = (current.issues || []).map(issue =>
    `<div class="issue ${esc(issue.severity)}"><b>${esc(issue.severity)} / ${esc(issue.issue_code)}</b><br>${esc(issue.message)}</div>`
  ).join('') || '<div class="meta">目前沒有 QA flag。</div>';
  const options = (current.options || []).map(opt =>
    `<div class="option"><b>(${esc(opt.key)})</b><div>${esc(opt.text)}</div></div>`
  ).join('');
  document.getElementById('detail').innerHTML = `
    <div class="panel"><h2>題目</h2><div class="body">
      <div class="meta"><code>${esc(current.candidate_key)}</code></div>
      <p class="meta">${esc(meta.normalized_category_name)} / ${esc(meta.normalized_subject_name)} / ${esc(meta.year)} 年第 ${esc(meta.exam_ordinal)} 次</p>
      <div class="stem">${esc(current.stem)}</div>
      <hr>${options}
      <p><b>答案：</b>${esc(current.answer ?? '未配對')}</p>
      <p><b>題組：</b>${esc(current.group_ref ?? '無')}</p>
    </div></div>
    <div class="panel"><h2>疑點</h2><div class="body">${issues}</div></div>
    <div class="panel"><h2>圖片</h2><div class="body"><div class="asset-grid">${images || '<span class="meta">未偵測到圖片引用。</span>'}</div></div></div>
    <div class="panel"><h2>人工審核</h2><div class="body">
      <textarea id="notes" placeholder="審核註記或修正摘要"></textarea>
      <div class="toolbar">
        <button class="action accept" onclick="review('accept')">通過</button>
        <button class="action" onclick="review('needs_review')">保留疑問</button>
        <button class="action block" onclick="review('block')">阻擋入庫</button>
        <button class="action" onclick="review('comment')">只加註記</button>
      </div>
      <p id="saved" class="meta"></p>
    </div></div>
    <div class="panel"><h2>來源</h2><div class="body">
      <p class="meta">PDF: <code>${esc(pdf)}</code></p>
      <p class="meta">Markdown: <code>${esc(meta.question_markdown || '')}</code></p>
    </div></div>`;
}

async function review(action) {
  if (!current) return;
  const notes = document.getElementById('notes').value;
  const res = await fetch('/api/review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({candidate_key: current.candidate_key, action, notes, reviewer: 'local'})
  });
  const data = await res.json();
  document.getElementById('saved').textContent = data.ok ? `已寫入 ${data.review_log}` : `寫入失敗：${data.error}`;
}

document.getElementById('search').addEventListener('input', applyFilter);
document.getElementById('status').addEventListener('change', applyFilter);
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
