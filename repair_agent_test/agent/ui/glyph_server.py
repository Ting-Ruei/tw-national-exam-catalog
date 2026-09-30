#!/usr/bin/env python3
"""字形決策表的**點選 UI**（workplan 4.1，設計者 2026-09-30：table approval，不是逐題看）。

一次看完 170 個字形映射、每個映射給「同意／退回」，決定 append 進
`store/scans/20260930-4.1/glyph-approvals.jsonl`（schema `glyph_approvals v1`）：

    {"at": "…", "by": "…", "decisions": [{"no": 1, "from": "ᵐ", "to": "m", "approved": true}, …]}

紀律：
- **只寫 `glyph-approvals.jsonl`**。不碰 review events、不碰 candidates、不開模型批次——
  runner 之後批次要吃這張表時，讀取以「同一個 no **最後一次**送出的決定」為準（append-only，
  最新紀錄蓋過前次）。
- 決定權在設計者。這一頁沒有「全部同意後自動開跑」的按鈕，也不該有。

    ../../qbr/.venv/bin/python ui/glyph_server.py --port 8791 --host 0.0.0.0
    #   然後：http://100.96.207.80:8791/   （設計者走 Tailscale 進來；127.0.0.1 自己測用）
"""
from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parent
TABLE = AGENT_DIR / "store" / "scans" / "20260930-4.1" / "glyph-decision-table.jsonl"
APPROVALS = AGENT_DIR / "store" / "scans" / "20260930-4.1" / "glyph-approvals.jsonl"

PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>字形決策表 · 點選核准</title>
<style>
  :root { --ink:#1a1a1a; --muted:#6b6b6b; --line:#d8d8d8; --ok:#1a7f37; --bad:#b42318; --bg:#faf9f7; }
  * { box-sizing:border-box; }
  body { font-family:"Noto Sans TC","PingFang TC",system-ui,sans-serif; margin:0; background:var(--bg); color:var(--ink); }
  header { position:sticky; top:0; z-index:2; background:#fff; border-bottom:1px solid var(--line); padding:10px 18px; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header .count { color:var(--muted); font-size:12px; }
  header .totals b { margin-right:8px; }
  header .ok { color:var(--ok); } header .bad { color:var(--bad); }
  header input { font-size:13px; padding:4px 8px; border:1px solid var(--line); border-radius:6px; width:180px; }
  header button { font-size:13px; padding:5px 12px; border-radius:6px; border:1px solid var(--line); background:#fff; cursor:pointer; }
  header button.primary { background:#17457c; color:#fff; border-color:#17457c; }
  header button:disabled { opacity:.45; cursor:not-allowed; }
  main { max-width:980px; margin:0 auto; padding:14px 16px 80px; }
  .card { display:flex; align-items:baseline; gap:12px; background:#fff; border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin:8px 0; cursor:pointer; }
  .card.approved { border-color:var(--ok); box-shadow:inset 3px 0 0 var(--ok); }
  .card.rejected { border-color:var(--bad); box-shadow:inset 3px 0 0 var(--bad); }
  .no { color:var(--muted); font-size:12px; width:40px; flex:none; }
  .glyph { font-size:22px; width:70px; text-align:center; flex:none; font-family:serif; }
  .glyph.to { color:var(--ok); font-weight:600; }
  .arrow { color:var(--muted); width:14px; flex:none; }
  .meta { flex:1; min-width:0; font-size:12px; color:var(--muted); }
  .meta .cls { display:inline-block; background:#f1efe9; border-radius:4px; padding:1px 6px; margin-right:6px; color:var(--ink); }
  .verdict { flex:none; display:flex; gap:6px; }
  .verdict button { font-size:12px; padding:4px 10px; border-radius:6px; border:1px solid var(--line); background:#fff; cursor:pointer; }
  .verdict .yes[aria-pressed="true"] { background:var(--ok); color:#fff; border-color:var(--ok); }
  .verdict .no[aria-pressed="true"] { background:var(--bad); color:#fff; border-color:var(--bad); }
  .keys { font-size:11px; color:var(--muted); margin-top:3px; font-family:ui-monospace,monospace; }
  #bar { position:fixed; bottom:0; left:0; right:0; background:#fff; border-top:1px solid var(--line); padding:10px 16px; display:flex; gap:12px; align-items:center; justify-content:center; }
  #bar .note { color:var(--muted); font-size:12px; }
  .hint { font-size:12px; color:var(--muted); margin:6px 0 14px; line-height:1.6; }
  #flash { margin-left:8px; font-size:12px; color:var(--ok); }
</style>
</head>
<body>
<header>
  <h1>字形決策表 <span id="count"></span></h1>
  <span class="count" id="tally"></span>
  <input id="q" placeholder="搜尋：字形／分類" oninput="if(this.timer)clearTimeout(this.timer);this.timer=setTimeout(draw,180)">
  <button onclick="bulk(1)">全部同意</button>
  <button onclick="bulk(-1)">全部退回</button>
  <button onclick="bulk(0)">清空</button>
  <button class="primary" id="submitBtn" onclick="submit()">送出決定</button>
  <span id="flash"></span>
</header>
<main>
  <div class="hint">
    這是 workplan 4.1 的字形映射核准面：一列＝一個「從字形 → 到字形」的映射（含受影響題數與欄位數）。
    按一列＝同意；再按一次＝取消。決定會 append 進 <code>glyph-approvals.jsonl</code>，
    **同一個編號以最後一次送出為準**。runner 不會自動開工——批次要等你另外點頭才吃這張表。
    已送出紀錄：<span id="records">…</span>
  </div>
  <div id="rows"></div>
</main>
<div id="bar">
  <button class="primary" onclick="submit()">送出決定</button>
  <span class="note">送出＝append 一筆 <code>glyph_approvals v1</code> 紀錄</span>
</div>
<script>
let rows = [], decided = {}, pending = false;
function hex(c){ return [...c].map(x => 'U+' + x.codePointAt(0).toString(16).toUpperCase().padStart(4,'0')).join(' '); }
function tally(){
  const yes = Object.values(decided).filter(v => v===1).length,
        no = Object.values(decided).filter(v => v===-1).length,
        none = rows.length - yes - no;
  document.getElementById('tally').innerHTML =
    `<b class="ok">同意 ${yes}</b><b class="bad">退回 ${no}</b><b>未決 ${none}</b>`;
  document.getElementById('submitBtn').disabled = pending || (yes+no)===0;
}
function draw(){
  const q = document.getElementById('q').value.trim().toLowerCase();
  const host = document.getElementById('rows');
  host.textContent = '';
  for (const r of rows){
    const sample = r.sample_keys.join(' ');
    if (q && !(r.from.toLowerCase().includes(q) || r.to.toLowerCase().includes(q)
               || r.class.toLowerCase().includes(q) || sample.toLowerCase().includes(q)
               || String(r.no)==q)) continue;
    const card = document.createElement('div');
    card.className = 'card' + (decided[r.no]===1?' approved':decided[r.no]===-1?' rejected':'');
    card.innerHTML = `<span class="no">#${r.no}</span>`
      + `<span class="glyph" title="${hex(r.from)}">${esc(r.from)}</span>`
      + `<span class="arrow">→</span>`
      + `<span class="glyph to" title="${hex(r.to)}">${esc(r.to)}</span>`
      + `<span class="meta"><span class="cls">${esc(r.class||'?')}</span>`
      + `${r.questions} 題 · ${r.codepoint_changes} 位置（fields ${r.fields}）`
      + `<div class="keys">${esc(sample)}</div></span>`
      + `<span class="verdict"><button class="yes" onclick="event.stopPropagation();dec(${r.no},1,${r.questions})">同意</button>`
      + `<button class="no" onclick="event.stopPropagation();dec(${r.no},-1,${r.questions})">退回</button></span>`;
    card.onclick = () => dec(r.no, decided[r.no] ? 0 : 1);
    host.appendChild(card);
  }
  tally();
}
function esc(s){ const d=document.createElement('div'); d.textContent=s??''; return d.innerHTML; }
function dec(no, v){ decided[no] = v || undefined; if (!decided[no]) delete decided[no]; draw(); fetchRecords(); }
function bulk(v){
  if (v===0) decided = {}; else rows.forEach(r => decided[r.no] = v);
  draw();
}
async function fetchRecords(){
  const res = await fetch('/api/records');
  const j = await res.json();
  document.getElementById('records').textContent = j.records + ' 筆';
}
async function submit(){
  const decisions = rows.filter(r => decided[r.no]!==undefined)
    .map(r => ({no:r.no, from:r.from, to:r.to, approved: decided[r.no]===1}));
  if (!decisions.length) return;
  pending = true; tally();
  const res = await fetch('/api/decisions', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ by: document.querySelector('meta[name=owner]')?.content || 'owner-click-ui', decisions }) });
  const j = await res.json();
  pending = false; draw();
  document.getElementById('flash').textContent = `已送出 ${decisions.length} 決定（紀錄 #${j.record}）`;
  setTimeout(()=>{ document.getElementById('flash').textContent=''; }, 6000);
  fetchRecords();
}
(async () => {
  rows = await (await fetch('/api/table')).json();
  rows.forEach(r => r.sample_keys = (r.question_keys||[]).slice(0,4));
  document.getElementById('count').textContent = `${rows.length} 個映射 · ${rows.reduce((a,r)=>a+r.codepoint_changes,0)} 個位置`;
  await fetchRecords();
  draw();
})();
</script>
</body>
</html>
"""


class GlyphHandler(BaseHTTPRequestHandler):
    lock = threading.Lock()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
        sys_log = f"{self.address_string()} {fmt % args}"
        print(sys_log)

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        if path == "/":
            body = PAGE.encode("utf-8")
            self._send(body, "text/html; charset=utf-8")
            return
        if path == "/api/table":
            rows = [json.loads(line) for line in TABLE.read_text(encoding="utf-8").splitlines() if line.strip()]
            body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
            return
        if path == "/api/records":
            count = 0
            if APPROVALS.exists():
                with APPROVALS.open(encoding="utf-8") as fh:
                    count = sum(1 for line in fh if line.strip())
            self._send(json.dumps({"records": count}).encode("utf-8"), "application/json; charset=utf-8")
            return
        self.send_error(404, "only /, /api/table, /api/records, /api/decisions")

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if self.path.split("?", 1)[0] != "/api/decisions":
            self.send_error(404, "only POST /api/decisions")
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        decisions = payload.get("decisions")
        if not isinstance(decisions, list) or not decisions:
            self.send_error(400, "decisions must be a nonempty list of {no, from, to, approved}")
            return
        table = {row["no"]: row for row in
                 (json.loads(line) for line in TABLE.read_text(encoding="utf-8").splitlines() if line.strip())}
        cleaned = []
        for item in decisions:
            try:
                row = table[int(item["no"])]
            except (KeyError, TypeError, ValueError):
                raise SystemExit
            if item["from"] != row["from"] or item["to"] != row["to"]:
                self.send_error(409, f"decision #{item['no']} does not match the table (from/to drifted)")
                return
            cleaned.append({"no": row["no"], "from": row["from"], "to": row["to"],
                            "approved": bool(item["approved"])})
        record = {
            "schema": "glyph_approvals v1",
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "by": str(payload.get("by") or "owner-click-ui"),
            "decisions": cleaned,
        }
        APPROVALS.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, APPROVALS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._send(json.dumps({"ok": True, "record": record["at"],
                               "decisions": len(cleaned)}, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="字形決策表的點選 UI。")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), GlyphHandler)
    print(f"字形決策表 UI: http://{args.host}:{args.port}/  → {APPROVALS}")
    server.serve_forever()


if __name__ == "__main__":
    main()