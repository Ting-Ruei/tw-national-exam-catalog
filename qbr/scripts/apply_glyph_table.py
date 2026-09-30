# -*- coding: utf-8 -*-
"""把**設計者核可的字形決策表**（156 列）落到佇列的文字上（字形 → 抽取檔）。

這是 2026-09-30 owner 拍板的 4.1 落地步（workplan 4.1；open-items §2.3）：
「核可後 4.1 deterministic 批次依表放行 → 落地（G3，單獨窗口，
`apply_text_corrections` ＋ append-only `reset_review`）」。

它只做「事件生產者」那一半，文字落地**永遠**交給 `apply_text_corrections.py`
（它的三道閘會逐欄驗：磁碟文字與 `correction` 的差必須**剛好**等於宣稱的替換對）：

    apply_glyph_table.py --queue <queue> --table <table> --approvals <approvals> --out <events>
    apply_text_corrections.py --queue <queue> --events <events> --dry-run / --apply

決定的權威與界線
----------------
- 決定只讀 `glyph-approvals.jsonl`（append-only；同一個 `no` 以**最後一筆**為準——UI 的同一條規則）。
- **未決列不動**：`approvals` 沒有的編號＝不產生任何事件。
- **修飾字母列（class=modifier-letter）即使被寫進決定也拒收**：它們是另案的記法轉換
  （折＋`<sup>/<sub>` 補位），不是這張「折碼放行」表的對象；折了會把上標感抹掉。
- 影響範圍是表上的 `question_keys`（表建置時逐列量出來的）；佇列裡若又冒出表外也含
  該字形碼位的列，那是表／佇列漂移——**列出來，拒絕寫入**。
- 佇列已有同源落的（本工具 `source`＋同一組替換）→ 跳過，重跑＝冪等。

事件形狀沿用 2026-09-23 dispute landing（`⻑→長`）那筆的契約：
`reset_review` ＋ `correction`（只帶被改的欄）＋ `changes`（逐碼位 from/to，`applied:"substitution"`）
＋ `previous_*`（承接這一題**上一筆人工**決定；機器審查者前綴取自
`review_ui/constants.py::REPAIR_REVIEWER_PREFIXES`，不抄第二份）。

安全：dry run 預設；`--apply` 才寫**新的事件檔**（不直接追加帳本——先讓
`apply_text_corrections --dry-run` 對這份事件檔驗整批，全過才追加）。任何一列被折疊的工具拒絕，
這支就整批停：落地是 deterministic 的，出現拒絕代表表與現實分岔，需要人看。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(PKG, "src"))

from scripts.serve_question_review_ui import REPAIR_REVIEWER_PREFIXES  # noqa: E402

QUESTION_REVIEWER = "repair_dispute_apply"
TOOL_SOURCE = "qbr_glyph_landing"

#: The fold classes this tool is chartered to land. Modifier letters are
#: **deliberately out** (recorded decision, 2026-09-30): folding ᵐ→m would erase the superscript
#: look that the glyph carries; that batch needs fold + `sup` positional markup from its own
#: approved surface.
#: （中英並陳：這個閘是治理界線，值得讓兩種語言的讀者都撞得到。）
LANDABLE_CLASSES = frozenset({"kangxi-radical", "compat-ideograph"})

QUESTION_ACTIONS = {"accept", "correct", "needs_review", "block", "exclude", "unblock",
                    "reset_review", "reviewed", "unreviewed", "comment"}

TEXT_FIELDS = ("stem", "options")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approved_mappings(table_path: Path, approvals_path: Path) -> list[dict]:
    """The approved subset of the decision table: latest record wins per `no`."""
    table = [json.loads(line) for line in table_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_no = {row["no"]: row for row in table}
    approved: dict[int, bool] = {}
    for line in approvals_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        for decision in record.get("decisions") or []:
            no = int(decision["no"])
            if no not in by_no:
                raise SystemExit(f"決定引用了不存在的表編號 #{no}（表或已換版）")
            if decision.get("from") != by_no[no]["from"] or decision.get("to") != by_no[no]["to"]:
                raise SystemExit(f"決定 #{no} 的 from/to 與表漂移，拒收（附表時間戳對照再核）。")
            approved[no] = bool(decision.get("approved"))
    rows = []
    for no, is_approved in sorted(approved.items()):
        if not is_approved:
            continue
        row = by_no[no]
        if row.get("class") not in LANDABLE_CLASSES:
            raise SystemExit(f"#{no}（{row['from']}→{row['to']}，class={row.get('class')}）"
                             "不是可折碼放行的列：修飾字母＝另案記法轉換，拒收。")
        rows.append(row)
    return rows


def human_previous(events_path: Path) -> dict[str, dict]:
    """`candidate_key -> 上一筆人工決定`（行動、註解、時間）；機器審查者不算人。"""
    out: dict[str, dict] = {}
    if not events_path.exists():
        return out
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        reviewer = str(event.get("reviewer") or "")
        if reviewer.startswith(REPAIR_REVIEWER_PREFIXES):
            continue
        action = event.get("action")
        if action not in QUESTION_ACTIONS:
            continue
        out[event.get("candidate_key")] = {
            "previous_action": action,
            "previous_notes": str(event.get("notes") or ""),
            "previous_reviewed_at": event.get("created_at"),
        }
    return out


def plan(queue_dir: Path, table_path: Path, approvals_path: Path, events_path: Path):
    """`{events, mappings}` — the whole batch as one deterministic plan (idempotent by text state:
    a mapping's `from` that is already folded away produces no hit and no event)."""
    mappings = approved_mappings(table_path, approvals_path)
    wanted_keys = {key for row in mappings for key in row.get("question_keys") or []}
    fold = human_previous(events_path)

    events: list[dict] = []
    with (queue_dir / "review-ui" / "candidates.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            key = row.get("candidate_key")
            if key not in wanted_keys:
                continue
            fields: dict[str, str] = {"stem": str(row.get("stem") or "")}
            for option in row.get("options") or []:
                fields["option %s" % option.get("key")] = str(option.get("text") or "")
            changes: list[dict] = []
            touched: dict[str, str] = {}
            for field, before in fields.items():
                after = before
                for mapping in mappings:
                    if mapping["from"] in before:
                        after = after.replace(mapping["from"], mapping["to"])
                        changes.append({"field": field, "from": mapping["from"],
                                        "to": mapping["to"]})
                if after != before:
                    touched[field] = after
            if not touched:
                continue
            correction: dict = {}
            if "stem" in touched:
                correction["stem"] = touched["stem"]
            if any(f.startswith("option ") for f in touched):
                for option in row.get("options") or []:
                    name = "option %s" % option.get("key")
                    entry = {"key": option.get("key"), "text": str(option.get("text") or "")}
                    if name in touched:
                        entry["text"] = touched[name]
                    correction.setdefault("options", []).append(entry)
            pairs = sorted({(c["from"], c["to"]) for c in changes})
            notes = "字形決策表落地：" + "；".join("%s→%s" % pair for pair in pairs)
            previous = fold.get(key, {})
            events.append({
                "action": "reset_review",
                "candidate_key": key,
                "changes": changes,
                "correction": correction,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "notes": notes,
                "repair_kind": "content_change",
                "reset_notes": notes,
                "reviewer": QUESTION_REVIEWER,
                "source": TOOL_SOURCE,
                "applied": "substitution",
                "previous_action": previous.get("previous_action"),
                "previous_notes": previous.get("previous_notes"),
                "previous_reviewed_at": previous.get("previous_reviewed_at"),
            })
    return {"events": events, "mappings": mappings}


def manifest(queue_dir: Path, plan_result: dict, table_path: Path, approvals_path: Path) -> dict:
    return {
        "schema": "glyph_landing_manifest v1",
        "table": str(table_path),
        "table_sha256": sha256_file(table_path),
        "approvals": str(approvals_path),
        "approvals_sha256": sha256_file(approvals_path),
        "candidate_rows": len(plan_result["events"]),
        "field_writes": sum(len(e.get("correction", {})) for e in plan_result["events"]),
        "codepoint_pairs": sum(len(e["changes"]) for e in plan_result["events"]),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path,
                        help="review queue 目錄（內含 review-ui/candidates.jsonl）")
    parser.add_argument("--table", required=True, type=Path,
                        help="glyph-decision-table.jsonl")
    parser.add_argument("--approvals", required=True, type=Path,
                        help="glyph-approvals.jsonl（append-only 決定紀錄；同一編號以最後一筆為準）")
    parser.add_argument("--out", required=True, type=Path,
                        help="新的事件檔路徑（--apply 才會寫；之後交給 apply_text_corrections 折疊）")
    parser.add_argument("--events", type=Path, default=None,
                        help="帳本（取 previous_* 承接與表外漂移檢查）；預設 <queue>/review-ui/question_review_events.jsonl")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="真的寫事件檔；預設 dry-run")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    queue_dir = Path(args.queue)
    events_path = args.events or (queue_dir / "review-ui" / "question_review_events.jsonl")
    plan_result = plan(queue_dir, Path(args.table), Path(args.approvals), events_path)

    # 表外漂移：佇列裡還有**表內編號影響的碼位**落在表沒點名的列上 → 表或佇列曾變動，整批停。
    mappings = {row["from"]: row for row in plan_result["mappings"]}
    named = {key for row in plan_result["mappings"] for key in row.get("question_keys") or []}
    outside = 0
    with (queue_dir / "review-ui" / "candidates.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("candidate_key") in named:
                continue
            text = str(row.get("stem") or "") + "".join(
                str(o.get("text") or "") for o in row.get("options") or [])
            if any(src in text for src in mappings):
                outside += 1
    if outside:
        print(f"表外仍有 {outside} 個帶表內碼位的列（表／佇列有漂移）——先查清再落地，不寫任何東西。")
        return 2

    m = manifest(queue_dir, plan_result, Path(args.table), Path(args.approvals))
    print(json.dumps(m, ensure_ascii=False, indent=1))
    for row in plan_result["mappings"]:
        n = sum(1 for pair in [(c["from"], c["to"]) for e in plan_result["events"]
                               for c in e["changes"]] if pair == (row["from"], row["to"]))
        print(f"  #{row['no']} {row['from']}→{row['to']}（{row['class']}）：計畫 {n} 個碼位"
              f"/ 表上 {row['codepoint_changes']}"
              f"{' ✓' if n == row['codepoint_changes'] else ' ⚠ 數目不符'}")

    if not args.apply:
        print("（dry-run；--apply 才寫事件檔。折疊請再走 apply_text_corrections --events <out>。）")
        return 0
    mismatches = []
    for row in plan_result["mappings"]:
        n = sum(1 for e in plan_result["events"] for c in e["changes"]
                if (c["from"], c["to"]) == (row["from"], row["to"]))
        if n != row["codepoint_changes"]:
            mismatches.append(row["no"])
    if mismatches:
        print(f"計畫碼位數與表不符：{mismatches}；不寫。")
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for event in plan_result["events"]:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"已寫 {len(plan_result['events'])} 筆事件 → {args.out}（尚未落帳本；先 dry-run apply_text_corrections）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())