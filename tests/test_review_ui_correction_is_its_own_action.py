"""修正要留在日誌裡自己的名字下，而且永遠不等於通過。

業主 2026-09-25 回報：「AI 給建議 → 我按帶入修正 → 儲存修正 → 它就通過了，我就找不到了。」
量到的事實：全站 465 筆帶 `correction` 的事件裡，464 筆是機器的 `reset_review`，**唯一一筆人工修正**
（`moex:107100:305:33:1:question:q076`，2026-09-25T02:24:43）被寫成 `action="reviewed"`。

原因是寫入端 `_reaffirm_standing_action` 把 `correct` 改寫成「底下那個決定」：底下有 `accept` 就記成
`accept`（＝畫面上通過）、有 `block` 就記成 `block`、什麼都沒有就記成泛用的 `reviewed`。於是

  * 日誌答不出「哪一題的文字被人改過」——`correction` 欄位是唯一線索，`action` 在說謊；
  * 清單依 `action` 畫那一列，`reviewed` 沒有任何標籤、也不是任何一個 chip，人就**找不到自己剛改的
    那一題**（`review_ui/v2` 的 `stateOf` 把所有 truthy 的 verdict 畫成 done）。

這個檔案釘住的是**日誌層的契約**，走真實的 `ReviewState.append_review` 與真實的事件檔（不是 helper
單獨呼叫），因為缺陷正是「append 下去的那一刻寫了什麼」。每個檢查都有負對照：把改寫規則放回去，
對應的斷言必須失敗。

畫面上的那一半（存完停在原題、畫面等於存下來的那一份、有「已修正」chip 找得回來）由真 Chrome 的
`scripts/test_v2_correction_keeps_question.mjs` 驗，這裡不重複讀原始碼。

第二個缺陷也在這裡（`CorrectedTextStaysOnScreenTests`）：修正下來的文字只活在那一筆事件上，所以
只要後面再寫一筆註解或一個決定，伺服器送出來的列就退回抽取器的讀法——檔案那一份折疊會帶著走，
寫入路徑的記憶體那一份不會，而寫完之後檔案簽章已被更新，`refresh_event_logs` 因此不再折疊。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_review_ui_discuss import run_node  # noqa: E402

KEY = "moex:107100:305:33:1:question:q076"
CORRECTED_STEM = "下列何者為 5-HT<sub>1A</sub> 受體致效劑？"


def import_review_ui():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "serve_question_review_ui_correction_state_test",
        ROOT / "scripts" / "serve_question_review_ui.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CorrectionActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = import_review_ui()

    def _append(self, *events: dict) -> list[dict]:
        """把事件依序寫進一個隔離的事件檔，回傳真正寫下去的那幾筆。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            candidates = tmp / "candidates.jsonl"
            candidates.write_text(json.dumps({
                "candidate_key": KEY, "question_number": 76, "stem": "下列何者為 5-HT1A 受體致效劑？",
                "options": [{"key": "A", "text": "甲"}], "answer": "A",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            log = tmp / "question_review_events.jsonl"
            log.write_text("", encoding="utf-8")
            state = self.ui.ReviewState(candidates, None, log, review_backend="jsonl")
            for event in events:
                state.append_review(dict(event))
            return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]

    def _correction(self, **extra) -> dict:
        return {
            "candidate_key": KEY, "action": "correct", "reviewer": "local", "source": "linear_v2",
            "correction": {"stem": CORRECTED_STEM, "options": [{"key": "A", "text": "甲"}], "answer": "A"},
            "notes": "人工依紙本修正抽出文字。",
            **extra,
        }

    def test_a_correction_is_stored_under_its_own_name(self):
        # 第一筆事件就是修正（沒有底下的決定）：它必須叫 `correct`。
        # 負對照：舊規則在這一條路寫的是 `reviewed`（站上那唯一一筆人工修正的實際值）。
        events = self._append(self._correction())
        self.assertEqual(1, len(events))
        self.assertEqual("correct", events[0]["action"])
        self.assertNotEqual("reviewed", events[0]["action"])
        self.assertEqual("save", events[0]["correction_action"])
        self.assertEqual(CORRECTED_STEM, events[0]["correction"]["stem"])

    def test_a_correction_after_a_pass_is_not_recorded_as_a_pass(self):
        # 「修正≠通過」在日誌層的樣子：底下是 `accept`，修正不能被記成第二筆 `accept`。
        # 負對照：舊規則改寫成 `previous_action`，這一條就會變成 accept（而畫面上「通過」）。
        events = self._append(
            {"candidate_key": KEY, "action": "accept", "reviewer": "local"},
            self._correction(),
        )
        self.assertEqual(["accept", "correct"], [event["action"] for event in events])
        self.assertNotIn("correct", self.ui.QUESTION_READY_ACTIONS,
                         "修正永遠不能讓一題變成「可以出貨」")

    def test_a_correction_after_a_block_does_not_become_the_block(self):
        # 底下是 block：修正不是那個 block 的重申，它是人對文字的另一個處置。這一條也是機器的爭議
        # 迴圈停下來的地方（`repair_loop.BLOCKING_ACTIONS` 只看最新的 action）。
        events = self._append(
            {"candidate_key": KEY, "action": "block", "notes": "字錯了", "reviewer": "local"},
            self._correction(),
        )
        self.assertEqual(["block", "correct"], [event["action"] for event in events])
        self.assertEqual("字錯了", events[0]["notes"])

    def test_a_note_after_a_correction_keeps_the_correction(self):
        # 註解不是決定：它重申它底下那個決定。修正現在是「站著的」決定之一，所以一筆註解之後，
        # 這一題仍然是「已修正」，而不是掉回未看（`comment` 不進 `S.verdict`，也不進正式題庫）。
        events = self._append(
            self._correction(),
            {"candidate_key": KEY, "action": "comment", "notes": "這一欄照紙本重打", "reviewer": "local"},
        )
        self.assertEqual(["correct", "correct"], [event["action"] for event in events])
        self.assertEqual("note", events[1]["note_action"])
        self.assertEqual("這一欄照紙本重打", events[1]["notes"])


class CorrectedTextStaysOnScreenTests(unittest.TestCase):
    """修正下來的文字是**題目**的屬性：後面再寫什麼，畫面都不能退回抽取器那一版。

    量到的缺陷（2026-09-25）：`correct` 之後再寫一筆註解或一個決定，伺服器送出來的列就變回抽取器
    的讀法。原因不是折疊寫錯，而是**兩份狀態不一致**：檔案那一份折疊（`events.load_review_events`）
    會把 correction 帶下去，寫入路徑的記憶體那一份不會——而這台伺服器在寫完之後就把檔案簽章更新
    了，所以 `refresh_event_logs` 看到「沒變」而不再折疊。審題者看到的就是「改動與畫面不同」。

    這裡走的是真正的 payload 路徑（`filtered_candidate_payloads`），因為那正是畫面讀的那一份。
    """

    @classmethod
    def setUpClass(cls):
        cls.ui = import_review_ui()

    def _served_stem(self, *events: dict) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            candidates = tmp / "candidates.jsonl"
            candidates.write_text(json.dumps({
                "candidate_key": KEY, "question_number": 76, "stem": "下列何者為 5-HT1A 受體致效劑？",
                "options": [{"key": "A", "text": "甲"}], "answer": "A", "metadata": {},
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            log = tmp / "question_review_events.jsonl"
            log.write_text("", encoding="utf-8")
            state = self.ui.ReviewState(candidates, None, log, review_backend="jsonl")
            for event in events:
                state.append_review(dict(event))
                state.refresh_event_logs()   # 伺服器每個 request 都做這件事
            payload = state.filtered_candidate_payloads({"focusKey": KEY, "limit": "5"})
            row = next(r for r in payload["candidates"] if r["candidate_key"] == KEY)
            return str(row.get("stem") or "")

    def _correction(self, **extra) -> dict:
        return {
            "candidate_key": KEY, "action": "correct", "reviewer": "local",
            "correction": {"stem": CORRECTED_STEM, "options": [{"key": "A", "text": "甲"}], "answer": "A"},
            **extra,
        }

    def test_nothing_written_shows_the_parsers_reading(self):
        # 負對照：沒有人改過的字，畫面就是抽取器那一版（證明下面幾條不是恆真）。
        self.assertNotIn("<sub>", self._served_stem())

    def test_a_correction_alone_is_what_the_screen_shows(self):
        self.assertEqual(CORRECTED_STEM, self._served_stem(self._correction()))

    def test_a_later_note_keeps_the_corrected_text(self):
        served = self._served_stem(
            self._correction(),
            {"candidate_key": KEY, "action": "comment", "notes": "這一欄照紙本重打", "reviewer": "local"},
        )
        self.assertEqual(CORRECTED_STEM, served)

    def test_a_later_decision_keeps_the_corrected_text(self):
        for action in ("accept", "block", "needs_review"):
            with self.subTest(action=action):
                served = self._served_stem(
                    self._correction(),
                    {"candidate_key": KEY, "action": action, "reviewer": "local"},
                )
                self.assertEqual(CORRECTED_STEM, served)

    def test_a_withdrawal_still_puts_the_parsers_reading_back(self):
        # 機器把自己的字收回（`applied: withdrawn`）時，畫面必須回到抽取器那一版——上面那條「帶著
        # 修正走」的規則不能把它蓋掉。這是 2026-09-24 訂下來的規則，這裡是它的負對照。
        served = self._served_stem(
            self._correction(),
            {"candidate_key": KEY, "action": "reset_review", "reviewer": "qbr_dispute_apply",
             "repair_kind": "withdrawn", "applied": "withdrawn"},
        )
        self.assertNotIn("<sub>", served)


class CorrectedRowIsFindableTests(unittest.TestCase):
    """清單那一列：修正過的題目要能被找回來，**連舊事件也一樣**。

    站上唯一一筆人工修正（2026-09-25T02:24:43）在 append-only 的日誌裡是 `action="reviewed"`
    ——被舊的寫入端改寫掉了，那一筆補不回來。清單若只讀 `review.action`，那一題在畫面上就是
    「已看過」、`已修正` chip 是 0 題，而業主的原話正是「修正完之後我就找不到了」。

    所以清單改讀**這一列自己的事實**：`review.correction` 在，就代表這一題的文字被人改過。
    這是讀得出來的事實，而且與 `action` 無關——舊事件的錯名字蓋不掉它。

    走真檔案的 JS（`01-core.js` 的 `rowReviewAction` ＋ `02-area-question.js` 的 `stateOf`），
    因為要釘住的正是「這一列畫成哪一格」。負對照：沒有 correction 的列不准被改寫。
    """

    def test_a_legacy_correction_row_reads_as_corrected(self):
        action = run_node("rowReviewAction({review:{action:'reviewed', correction:{stem:'x'}}})")
        self.assertEqual("correct", action)

    def test_the_negative_control_a_row_without_a_correction_is_untouched(self):
        # v1 的「已看過」也是 `reviewed`：沒有 correction 的那一種不能被這條規則吃掉。
        self.assertEqual("reviewed", run_node("rowReviewAction({review:{action:'reviewed'}})"))
        self.assertEqual("block", run_node("rowReviewAction({review:{action:'block'}})"))

    def test_a_pending_machine_return_still_outranks_it(self):
        # 機器退回來說的是「再看一次」，那比「文字被改過」更急——順序不能顛倒。
        action = run_node(
            "rowReviewAction({review:{is_reset_unreviewed:true, action:'reviewed', correction:{stem:'x'}}})"
        )
        self.assertEqual("reset_review", action)

    def test_the_row_lands_in_the_corrected_grid_not_in_done(self):
        # 這一格就是業主找不到那一題的地方：`stateOf` 決定 chip，`correct` 與 `done` 不同格。
        corrected = run_node(
            "(() => { S.verdict.set('k1',"
            " rowReviewAction({review:{action:'reviewed', correction:{stem:'x'}}}));"
            " return stateOf({candidate_key:'k1'}); })()"
        )
        self.assertEqual("corrected", corrected)
        plain = run_node(
            "(() => { S.verdict.set('k2', rowReviewAction({review:{action:'reviewed'}}));"
            " return stateOf({candidate_key:'k2'}); })()"
        )
        self.assertEqual("done", plain)

    def test_a_withdrawn_machine_repair_is_not_corrected(self):
        """機器把字收回之後，那一列不是「已修正」。

        量到的事實（2026-09-25，撤回 56 筆之後的站上）：`藥師(一)/103/2/藥劑學（包括生物藥劑學）`
        的三題撤回讓「已修正」chip 顯示 **3**，而畫面上那三題的字是紙本那一版。撤回的事件仍然帶著
        `correction`（那是紀錄），伺服器也把 `applied_kind` 標成 `withdrawn`——只讀 correction 的
        那一條會把「機器改錯又收回」畫成「機器修好了」。
        """
        row = ("{action:'block', correction:{stem:'x'}, applied:'withdrawn',"
               " applied_kind:'withdrawn'}")
        self.assertEqual("block", run_node(f"rowReviewAction({{review:{row}}})"))
        state = run_node(
            "(() => { S.verdict.set('k9',"
            f" rowReviewAction({{review:{row}}}));"
            " return stateOf({candidate_key:'k9'}); })()"
        )
        self.assertEqual("flagged", state)

    def test_the_negative_control_a_standing_machine_repair_is_still_corrected(self):
        # 同一條規則的另一半：沒有被收回的機器修復（`applied_kind: field`）仍然是已修正，
        # 所以上面那一條不是把機器修復整類關掉。
        row = "{action:'reset_review', correction:{stem:'x'}, applied:'field', applied_kind:'field'}"
        self.assertEqual("correct", run_node(f"rowReviewAction({{review:{row}}})"))

    def test_the_negative_control_the_action_alone_is_what_missed_it(self):
        # 負對照的來源是**那一列自己的資料**：同一列若只讀 `review.action`（＝修好之前那一版
        # 讀法），答案就是 `reviewed`，也就是「已看過」那一格。這證明上面幾條不是恆真。
        row = "{action:'reviewed', correction:{stem:'x'}}"
        self.assertEqual("reviewed", run_node(f"(() => {{ const review = {row}; return review.action; }})()"))
        self.assertEqual("correct", run_node(f"rowReviewAction({{review:{row}}})"))


if __name__ == "__main__":
    unittest.main()
