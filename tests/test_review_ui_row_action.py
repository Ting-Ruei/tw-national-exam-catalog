"""清單那一格要說「我對這一題做過什麼」，不是「這一題的字被改過」。

owner 2026-09-29 的原文：「我當時的決定（accept 就是通過，block 就是阻擋）」。這一條契約的
差別不是措辭，是**哪幾列顯示什麼**：

    這一列                                  correction 優先（2026-09-29 之前）  人的決定優先（現在）
    action=accept ＋ 有 correction          已修正                              確認正常
    action=block  ＋ 有 correction          已修正                              阻擋
    action=needs_review ＋ 有 correction    已修正                              需重看

量到的差別（站上快照 `question_review_events.jsonl`，20,329 事件／13,780 題，2026-09-29 13:48；
先跑伺服器的投影組成真正送進瀏覽器的那份 `review`，再把兩版規則逐列跑一次）：**635 列搬家**
——accept 560、block 74、needs_review 1；「已修正」那一格從 648 列縮到 13 列。

`correct` 不會因此消失：它是同一列的另一件事，`review.correction` 與詳情區的 `applied_kind` 都還留著；
沒有那三個人的決定時（例如 2026-09-25 之前的修正事件被寫成 `reviewed`），這一列讀成已修正。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_review_ui_v2_scope import run_node  # noqa: E402  （同一個目錄的真 script harness）


def row_action(review: dict) -> str:
    """把一份 `review` 交給真的 `01-core.js` 裡的 `rowReviewAction`。"""
    return run_node(f"rowReviewAction({{ review: {_js(review)} }})")


def _js(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


#: 那三個人的決定：有 `correction` 也必須答那個人自己的決定。
#: （`comment`＝只加註記**不**在內：它不落在 owner 指名的那三個動作裡，所以這一列剩下的、讀得出來的事實
#: 是「字被改過」——與 B 版一致，見下面 `test_a_note_only_row_with_a_correction_reads_as_corrected`。）
HUMAN_DECISIONS = ("accept", "block", "needs_review")


class RowActionPrecedenceTests(unittest.TestCase):
    def test_a_human_decision_beats_a_later_correction_on_the_same_row(self):
        for action in HUMAN_DECISIONS:
            got = row_action({"action": action, "correction": {"options": [{"key": "A", "text": "字"}]}})
            self.assertEqual(got, action, f"action={action} ＋ correction")

    def test_the_three_decisions_the_owner_named_keep_their_own_words(self):
        # 「accept 就是通過，block 就是阻擋」——這三格的字必須是那三個動作本身。
        self.assertEqual(row_action({"action": "accept", "correction": {"changes": [{}]}}), "accept")
        self.assertEqual(row_action({"action": "block", "correction": {"changes": [{}]}}), "block")
        self.assertEqual(row_action({"action": "needs_review", "correction": {"changes": [{}]}}), "needs_review")

    def test_a_correction_nobody_decided_on_is_still_findable_as_corrected(self):
        """2026-09-25 之前，修正事件被寫成 `reviewed`（`correct` 被前一個決定覆寫）。

        那些列的行動不是那三個之一，所以「字被改過」仍是這一列唯一讀得出來的事實——只讀 action 的話
        它們會變成「已看過」，而「已修正」那一格是 0（使用者回報的「修正完之後找不到那一題」）。
        """
        legacy = {"action": "reviewed", "correction": {"options": [{"key": "B", "text": "字"}]}}
        self.assertEqual(row_action(legacy), "correct")

    def test_a_note_only_row_with_a_correction_reads_as_corrected(self):
        """只加註記（`comment`）不是 owner 指名的那三個動作，所以這一列說的是「字被改過」。

        與 B 版一致（`comment` 不在它的早退清單裡）。註記本身由 `S.notes` 畫，不靠這一格。
        """
        got = row_action({"action": "comment", "correction": {"changes": [{}]}})
        self.assertEqual(got, "correct")

    def test_a_machine_reset_still_asks_the_reviewer_to_look_again(self):
        # 兩版都一樣：`is_reset_unreviewed` 最優先（機器改過、等人複核）。
        got = row_action({"is_reset_unreviewed": True, "action": "accept",
                          "correction": {"changes": [{}]}})
        self.assertEqual(got, "reset_review")

    def test_a_withdrawal_never_reads_as_corrected(self):
        # 撤回之後沒有人再看過 → 未看（空字串）；撤回前有人 block 過 → 那個 block。
        self.assertEqual(row_action({"action": "reset_review", "applied": "withdrawn",
                                     "correction": {"changes": [{}]}}), "")
        self.assertEqual(row_action({"action": "block", "applied_kind": "withdrawn",
                                     "correction": {"changes": [{}]}}), "block")

    def test_a_row_with_no_event_at_all_reads_as_unseen(self):
        self.assertEqual(row_action({}), "")
        self.assertEqual(row_action({"action": None}), "")

    def test_the_negative_control_is_the_old_correction_first_rule(self):
        """舊規則：`correction` 先問，人的決定被它蓋掉。

        這個負對照證明上面的案例抓得到它——舊規則在這三列上答「已修正」。
        """
        def old_rule(review: dict) -> str:
            if review.get("is_reset_unreviewed"):
                return "reset_review"
            withdrawn = str(review.get("applied_kind") or review.get("applied") or "").lower() == "withdrawn"
            if not withdrawn and isinstance(review.get("correction"), dict):
                return "correct"
            action = str(review.get("action") or "")
            if withdrawn:
                return "" if action == "reset_review" else action
            return action

        for action in ("accept", "block", "needs_review"):
            row = {"action": action, "correction": {"changes": [{}]}}
            self.assertEqual(old_rule(row), "correct", "舊規則應該把這一列畫成已修正")
            self.assertNotEqual(old_rule(row), row_action(row), "新規則必須與舊規則不同")


if __name__ == "__main__":
    unittest.main()
