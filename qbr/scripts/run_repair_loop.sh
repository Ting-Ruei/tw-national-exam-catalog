#!/bin/bash
# 題目修正迴圈的調試腳本（one-shot、有收執）。埠是參數、不寫死。
# 對照：`docs/skills/run-question-review-loop/SKILL.md`（操作程序）與 `qbr/AGENTS.md`（契約）。
# 本機 3.14：`os.stat().st_mtime_ns` 為整數、`json.load` 為 `load`，故本檔不用 `1e9` 類整數寫法。
set -u

QBR="$(cd "$(dirname "$0")/.." && pwd)"        # .../qbr
ROOT="$(dirname "$QBR")"                       # 倉庫根
QUEUE="${QUEUE:-$QBR/data/review-queues/live}"
PY="$QBR/.venv/bin/python"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
WINDOW="${WINDOW:-5}"
LANE="${LANE:-splash}"
BASE="http://$HOST:$PORT"

step() { printf '\n== %s\n' "$1"; }

step "0) 端點可達（port 為參數）"
curl -s -m 8 -o /dev/null -w "  /v2  HTTP %{http_code}  %{size_download}B  %{time_total}s\n" "$BASE/v2"

step "1) 拉回常駐機決策（append-only JSONL 以 event 為單位）"
( cd "$ROOT" && bash scripts/pull_station_reviews.sh )

step "2) 本地計數（script 判，限純前置）與 12 項對照（QA 判，限後置）"
"$PY" - "$QUEUE" <<'PY'
import json, os, sys, collections
q = os.path.join(sys.argv[1], "review-ui")
def n(p):
    with open(q + "/" + p, encoding="utf-8") as h:
        return sum(1 for x in h if x.strip())
print("  candidates          :", n("/candidates.jsonl"))
print("  question events     :", n("/question_review_events.jsonl"))
print("  answer  events      :", n("/answer_review_events.jsonl"))
print("  ai findings         :", n("/question_ai_findings.jsonl"))
print("  correction feedback :", n("/question_correction_feedback_events.jsonl"))
idx = json.load(open(os.path.join(q, "queue_index.json"), encoding="utf-8"))
print("  index.papers        :", idx.get("papers"))
print("  index.questions     :", idx.get("questions"))
print("  index.issue_rows    :", idx.get("issues"))
last = {}
with open(os.path.join(q, "question_review_events.jsonl"), encoding="utf-8") as h:
    for line in h:
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("candidate_key"):
            last[d["candidate_key"]] = d
c = collections.Counter(v.get("action") for v in last.values())
print("  distinct key        :", len(last), "| action 計:", dict(c))
print("  null finding        :", sum(1 for v in last.values() if not v.get("finding")))
kinds = collections.Counter()
with open(os.path.join(q, "candidates.jsonl"), encoding="utf-8") as h:
    for line in h:
        if not line.strip():
            continue
        for d in (json.loads(line).get("disputes") or []):
            kinds[d.get("kind")] += 1
print("  kind 計:", {k: kinds[k] for k in sorted(kinds)})
miss = [k for k in sorted(last) if not last[k].get("finding")]
print("  缺 finding 的 key 數:", len(miss), "| 例:", miss[:3])
PY

step "3) 幂等對照（script 判 ⑨ 與 ⑩ 同批、note 異）"
"$PY" - "$QUEUE" <<'PY'
import json, os, sys
q = os.path.join(sys.argv[1], "review-ui")
rows = [json.loads(l) for l in open(os.path.join(q, "candidates.jsonl"), encoding="utf-8") if l.strip()]
for probe in (0, 2, 3):
    row = rows[probe] if probe < len(rows) else {}
    one = __import__("qbr.disputes", fromlist=[]).summary_row(row) if False else None
import qbr.disputes as D
for probe in (0, 2, 3):
    row = rows[probe] if probe < len(rows) else {}
    a = D.of_question(row); b = D.of_question(row)
    print("  row%d 冪等:%s" % (probe, a == b), "| kinds:", sorted({d["kind"] for d in a}))
PY

step "4) 巡迴：本機 agent（thinking ON；並發）讀 5 題 → 寫 → 刷新"
( cd "$QBR" && "$PY" scripts/repair_agent.py --queue "$QUEUE" --window "$WINDOW" --lane "$LANE" --every 1 2>&1 | tail -14 )

step "5) 就地刷新佇列（Q9 幂等；寫入後由第 4 步完成）"
( cd "$QBR" && "$PY" scripts/refresh_queue_text.py --queue "$QUEUE" 2>&1 | tail -6 )

step "6) 推回常駐機（append-only，同 event 即新）"
( cd "$ROOT" && bash scripts/push_reviews_to_station.sh )

step "7) 終局計數（與 2) 同欄對照；QA 判 12 項）"
"$PY" - "$QUEUE" <<'PY'
import json, os, sys
q = os.path.join(sys.argv[1], "review-ui")
def n(p):
    with open(q + "/" + p, encoding="utf-8") as h:
        return sum(1 for x in h if x.strip())
print("  candidates          :", n("/candidates.jsonl"))
print("  question events     :", n("/question_review_events.jsonl"))
print("  ai findings         :", n("/question_ai_findings.jsonl"))
print("  correction feedback :", n("/question_correction_feedback_events.jsonl"))
last = {}
with open(os.path.join(q, "question_review_events.jsonl"), encoding="utf-8") as h:
    for line in h:
        if line.strip():
            d = json.loads(line)
            if d.get("candidate_key"):
                last[d["candidate_key"]] = d
print("  distinct key        :", len(last))
for k in sorted(last)[:5]:
    f = last[k].get("finding") or {}
    print("  %-44s %-9s %s" % (k[:44], (last[k].get("action") or "-"), (f.get("what") or "-")))
PY

step "8) v2 三面（CDP＋CDP note；QA 判 4 項對照）"
printf '  note_typing:%s\n' "$(node "$ROOT/scripts/test_v2_note_typing.mjs" "$BASE" 2>&1 | tail -1)"
printf '  areas:%s\n' "$(node "$ROOT/scripts/test_v2_areas_browser.mjs" "$BASE" 2>&1 | tail -1)"
printf '  note:%s\n' "$(node "$ROOT/scripts/test_v2_note_browser.mjs" "$BASE" 2>&1 | tail -1)"

step "9) 離線導覽（本機）＋ 本機 QA（4 項）"
( cd "$ROOT" && node scripts/test_v2_navigation.mjs review_ui/v2.html \
    "$QUEUE/review-ui/candidates.jsonl" 2>&1 | tail -1 )
printf '  本機 QA(4 項): %s\n' "$(node "$ROOT/scripts/test_v2_note_typing.mjs" "$BASE" 2>&1 | tail -1)"

printf '\n完成。\n'
