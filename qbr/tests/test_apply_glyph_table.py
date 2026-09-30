# -*- coding: utf-8 -*-
"""`apply_glyph_table.py` 的閘門與負控制（2026-09-30，4.1 落地步的合約測試）。

每個契約都帶一個「必須失敗」的負案例：
- 未核可（不在 approvals 裡）的編號 → 不產生事件（未決列不動）；
- 修飾字母列（另案記法轉換）即使被點頭也**拒收**（exit 2）；
- 決定的 from/to 與表漂移 → 拒收；
- 表點名的列沒有計畫、表外的碼位不擴張；
- 冪等：`from` 已折掉的列 → 不再出事件；
- 上筆人工決定的 `previous_*` 承接；機器審查者前綴不算人；
- 全鏈路：本工具的事件交給 `apply_text_corrections --apply`，磁碟真的變、原文進 `parser_original`。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

QBR = Path(__file__).resolve().parents[1]
QBR_PY = QBR / ".venv" / "bin" / "python"
SCRIPT = QBR / "scripts" / "apply_glyph_table.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("apply_glyph_table", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.qdir = self.tmp
        (self.qdir / "review-ui").mkdir(parents=True, exist_ok=True)
        self.tool = load_tool()

    def enqueue(self, rows, ledger=()):
        candidates = self.qdir / "review-ui" / "candidates.jsonl"
        write_jsonl(candidates, rows)
        events = self.qdir / "review-ui" / "question_review_events.jsonl"
        if ledger:
            write_jsonl(events, ledger)
        else:
            events.write_text("", encoding="utf-8")
        return events

    @staticmethod
    def fixtures():
        row = {
            "candidate_key": "moex:107100:305:33:1:question:q051",
            "stem": "使用較⼤的有效焦斑，⻑端會得到較⼤的模糊",
            "options": [
                {"key": "A", "text": "說法正確"},
                {"key": "B", "text": "⽤較長的OID"},
                {"key": "C", "text": "溫度以℃為單位"},
                {"key": "D", "text": "⾎壓正常"},
            ],
        }
        table = [
            {"no": 2, "from": "⽤", "to": "用", "class": "kangxi-radical",
             "codepoint_changes": 1, "questions": 1, "fields": 1,
             "question_keys": [row["candidate_key"]]},
            {"no": 5, "from": "⻑", "to": "長", "class": "kangxi-radical",
             "codepoint_changes": 1, "questions": 1, "fields": 1,
             "question_keys": [row["candidate_key"]]},
            {"no": 17, "from": "⾦", "to": "金", "class": "kangxi-radical",
             "codepoint_changes": 1, "questions": 1, "fields": 1,
             "question_keys": ["moex:100020:308:33:1:question:q074"]},
            {"no": 1, "from": "ᵐ", "to": "m", "class": "modifier-letter",
             "codepoint_changes": 1626, "questions": 861, "fields": 1626,
             "question_keys": []},
        ]
        return row, table

    def approvals(self, decisions, at="2026-09-30T08:29:38.400+00:00"):
        path = self.tmp / "approvals.jsonl"
        write_jsonl(path, [{"schema": "glyph_approvals v1", "at": at, "by": "owner-click-ui",
                            "decisions": decisions}])
        return path


class GateTests(Harness):
    def test_unapproved_number_stays_unfolded_and_approved_folds(self):
        row, table = self.fixtures()
        table_path = self.tmp / "table.jsonl"
        write_jsonl(table_path, table)
        approvals = self.tmp / "approvals.jsonl"
        write_jsonl(approvals, [{"schema": "glyph_approvals v1", "at": "2026-09-30T08:29:38.400+00:00",
                                 "by": "owner-click-ui",
                                 "decisions": [{"no": 2, "from": "⽤", "to": "用", "approved": True}]}])
        self.enqueue([row])
        plan_result = self.tool.plan(
            self.tmp, table_path, approvals,
            self.qdir / "review-ui" / "question_review_events.jsonl")
        self.assertEqual(len(plan_result["events"]), 1, "只有核可且表點名的列該有事件")
        event = plan_result["events"][0]
        self.assertEqual({(c["field"], c["from"], c["to"]) for c in event["changes"]},
                         {("option B", "⽤", "用")}, "未決的 ⻑/⼤ 不得出任何替換")
        self.assertEqual(event["correction"].get("stem", row["stem"]), row["stem"],
                         "stem 未核可的碼位原樣（未被改就不得出現在 correction）")
        self.assertEqual(
            [o["text"] for o in event["correction"]["options"] if o["key"] == "B"],
            ["用較長的OID"])
        self.assertNotIn("⻑", str(event["changes"]))

    def test_modifier_letter_row_is_refused_even_if_clicked(self):
        row, table = self.fixtures()
        table_path = self.tmp / "table.jsonl"
        write_jsonl(table_path, table)
        approvals = self.tmp / "approvals.jsonl"
        write_jsonl(approvals, [{"schema": "glyph_approvals v1", "at": "2026-09-30T09:00:00.000+00:00",
                                 "by": "x",
                                 "decisions": [{"no": 1, "from": "ᵐ", "to": "m", "approved": True}]}])
        self.enqueue([row])
        with self.assertRaises(SystemExit) as caught:
            self.tool.approved_mappings(table_path, approvals)
        self.assertIn("ᵐ", str(caught.exception), "修飾字母＝另案，這張表拒收")

    def test_from_to_drift_is_refused(self):
        row, table = self.fixtures()
        table_path = self.tmp / "table.jsonl"
        write_jsonl(table_path, table)
        approvals = self.tmp / "approvals.jsonl"
        write_jsonl(approvals, [{"schema": "glyph_approvals v1", "at": "2026-09-30T09:00:00.000+00:00",
                                 "by": "x",
                                 "decisions": [{"no": 2, "from": "⽤", "to": "⺠", "approved": True}]}])
        self.enqueue([row])
        with self.assertRaises(SystemExit) as caught:
            self.tool.approved_mappings(table_path, approvals)
        self.assertIn("漂移", str(caught.exception))

    def test_unnamed_row_is_never_touched(self):
        # 表點名 q051，但另一列也含 ⽤——映射不得擴張到表外；表外檢查會讓整批停。
        row, table = self.fixtures()
        stranger = {"candidate_key": "moex:999:1:1:1:question:q001",
                    "stem": "本題也⽤到字形", "options": []}
        table_path = self.tmp / "table.jsonl"
        write_jsonl(table_path, table)
        approvals = self.tmp / "approvals.jsonl"
        write_jsonl(approvals, [{"schema": "glyph_approvals v1", "at": "2026-09-30T09:00:00.000+00:00",
                                 "by": "x",
                                 "decisions": [{"no": 2, "from": "⽤", "to": "用", "approved": True}]}])
        self.enqueue([row, stranger])
        plan_result = self.tool.plan(
            self.tmp, table_path, approvals,
            self.qdir / "review-ui" / "question_review_events.jsonl")
        self.assertNotIn(stranger["candidate_key"], {e["candidate_key"] for e in plan_result["events"]},
                         "表沒點名的列不得出事件（範圍＝表的 question_keys）")

    def test_previous_human_decision_carried_machines_skipped(self):
        row, table = self.fixtures()
        table_path = self.tmp / "table.jsonl"
        write_jsonl(table_path, table)
        approvals = self.tmp / "approvals.jsonl"
        write_jsonl(approvals, [{"schema": "glyph_approvals v1", "at": "2026-09-30T09:00:00.000+00:00",
                                 "by": "x",
                                 "decisions": [{"no": 2, "from": "⽤", "to": "用", "approved": True}]}])
        ledger = [
            {"action": "block", "candidate_key": row["candidate_key"], "reviewer": "local",
             "notes": "MAOB 下標問題", "created_at": "2026-09-24T10:00:00"},
            {"action": "reset_review", "candidate_key": row["candidate_key"],
             "reviewer": "repair_dispute_apply", "notes": "x", "created_at": "2026-09-25T10:00:00"},
        ]
        self.enqueue([row], ledger=ledger)
        plan_result = self.tool.plan(
            self.tmp, table_path, approvals,
            self.qdir / "review-ui" / "question_review_events.jsonl")
        event = plan_result["events"][0]
        self.assertEqual(event["previous_action"], "block")
        self.assertEqual(event["previous_notes"], "MAOB 下標問題")
        self.assertEqual(event["previous_reviewed_at"], "2026-09-24T10:00:00")

    def test_already_folded_field_idempotent(self):
        row, table = self.fixtures()
        folded = json.loads(json.dumps(row, ensure_ascii=False))
        for option in folded["options"]:
            if option["key"] == "B":
                option["text"] = option["text"].replace("⽤", "用")
        table_path = self.tmp / "table.jsonl"
        write_jsonl(table_path, table)
        approvals = self.tmp / "approvals.jsonl"
        write_jsonl(approvals, [{"schema": "glyph_approvals v1", "at": "2026-09-30T09:00:00.000+00:00",
                                 "by": "x",
                                 "decisions": [{"no": 2, "from": "⽤", "to": "用", "approved": True}]}])
        self.enqueue([folded])
        plan_result = self.tool.plan(
            self.tmp, table_path, approvals,
            self.qdir / "review-ui" / "question_review_events.jsonl")
        self.assertEqual(plan_result["events"], [], "⽤ 已折掉＝冪等重跑不出事件")


class FullChainTests(Harness):
    """本工具 → `apply_text_corrections.py --apply` → 磁碟真的變。"""

    def test_event_folds_into_candidates(self):
        row, table = self.fixtures()
        table_path = self.tmp / "table.jsonl"
        write_jsonl(table_path, table)
        approvals = self.tmp / "approvals.jsonl"
        write_jsonl(approvals, [{"schema": "glyph_approvals v1", "at": "2026-09-30T09:00:00.000+00:00",
                                 "by": "x",
                                 "decisions": [{"no": 2, "from": "⽤", "to": "用", "approved": True}]}])
        ledger = self.enqueue([row], ledger=[])
        out = self.tmp / "glyph-events.jsonl"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--queue", str(self.tmp), "--table", str(table_path),
             "--approvals", str(approvals), "--out", str(out), "--apply"],
            capture_output=True, text=True, check=True)
        self.assertTrue(out.exists(), result.stdout + result.stderr)
        folded = subprocess.run(
            [sys.executable, str(QBR / "scripts" / "apply_text_corrections.py"),
             "--queue", str(self.tmp), "--events", str(out), "--apply"],
            capture_output=True, text=True, check=True)
        self.assertIn("1", folded.stdout, "應有 1 列被折疊：" + folded.stdout)
        rows = [json.loads(line) for line in
                (self.tmp / "review-ui" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        option_b = next(o["text"] for o in rows[0]["options"] if o["key"] == "B")
        self.assertEqual(option_b, "用較長的OID")
        stem = rows[0]["stem"]
        self.assertEqual(stem, row["stem"], "未核可的碼位（⼤/⻑）不得動")
        # 原文進 parser_original（option B），且 events 由 --events 新檔折疊（帳本未被本工具碰到）
        original = rows[0].get("parser_original")
        self.assertIsNotNone(original)
        original_b = next(o["text"] for o in original["options"] if o["key"] == "B")
        self.assertEqual(original_b, "⽤較長的OID",
                         "原文（折前的抽取字）留在 parser_original")

    def test_dry_run_writes_nothing(self):
        row, table = self.fixtures()
        table_path = self.tmp / "table.jsonl"
        write_jsonl(table_path, table)
        approvals = self.tmp / "approvals.jsonl"
        write_jsonl(approvals, [{"schema": "glyph_approvals v1", "at": "2026-09-30T09:00:00.000+00:00",
                                 "by": "x",
                                 "decisions": [{"no": 2, "from": "⽤", "to": "用", "approved": True}]}])
        self.enqueue([row])
        out = self.tmp / "noop.jsonl"
        subprocess.run([sys.executable, str(SCRIPT), "--queue", str(self.tmp),
                        "--table", str(table_path), "--approvals", str(approvals),
                        "--out", str(out)], capture_output=True, text=True, check=True)
        self.assertFalse(out.exists(), "dry-run 不寫事件檔")


if __name__ == "__main__":
    unittest.main()