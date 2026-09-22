"""A note is something written *about* a question; it is not a verdict on it.

The defect this pins down: v2.html had a note box that was in the DOM and never visible - `reasonBox`
only ever had its `on` class **removed**, never added - so `reasonText.value` was empty on every
decision and every note the reviewer typed was silently dropped. The first half of this file is that
the box can now be opened and a note can be saved at all. The second half is the part that would be
easy to get wrong while adding it: the event log keeps the *latest* event as the question's state, so
a note saved after `確認正常` would withdraw the acceptance unless the note reaffirms it.

The rules, stated so they can be checked:
  * a note never promotes a question (only `accept`/`unblock` are "ready"),
  * a note never demotes one either - it keeps the decision it is attached to,
  * a note on an undecided question leaves it undecided.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "review_ui" / "v2.html"


def js_of(html: str) -> str:
    """v2 拆檔後的單字串視圖：HTML 骨架 ＋ 依 `<script src>` 序串起的 JS（＝執行序）。
    `src` 以 `review_ui/` 為基準（本層三檔对此一致：`V2.parent` 即 `review_ui/`）。"""
    parts = [html]
    for src in re.findall(r'<script src="([^"]+)"></script>', html):
        parts.append((V2.parent / src).read_text(encoding="utf-8"))
    for inline in re.findall(r"<script>(.*?)</script>", html, re.S):
        if inline.strip():
            parts.append(inline)
    return "\n".join(parts)


def import_review_ui():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "serve_question_review_ui_note_test",
        ROOT / "scripts" / "serve_question_review_ui.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NoteActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = import_review_ui()

    def test_a_note_is_not_a_verdict(self):
        # If `comment` were "ready", saving a note would be indistinguishable from passing the
        # question, and the fast pass would skip questions nobody had decided.
        self.assertNotIn("comment", self.ui.QUESTION_READY_ACTIONS)
        self.assertIn("comment", self.ui.NOTE_ACTIONS)
        self.assertIn("comment", self.ui.QUESTION_REVIEW_ACTIONS)

    def test_a_note_after_an_accept_keeps_the_accept(self):
        # The defect: the latest event is the question's state, so an unreaffirmed `comment` would
        # flip an accepted question to "needs another look" and drop it out of the formal set.
        event = {"action": "comment", "candidate_key": "k", "notes": "紙本第 3 頁為莢膜"}
        self.ui._reaffirm_standing_action(event, {"action": "accept"})
        self.assertEqual(event["action"], "accept")
        self.assertEqual(event["note_action"], "note")
        self.assertEqual(event["notes"], "紙本第 3 頁為莢膜")

    def test_a_note_after_each_standing_action_keeps_that_action(self):
        # Every decision a reviewer can have already made must survive a later note.
        for standing in ("accept", "needs_review", "block", "exclude", "unblock", "reviewed"):
            with self.subTest(standing=standing):
                event = {"action": "comment", "candidate_key": "k"}
                self.ui._reaffirm_standing_action(event, {"action": standing})
                self.assertEqual(event["action"], standing)

    def test_a_note_on_an_undecided_question_leaves_it_undecided(self):
        # No decision to keep, so the event stays a note - and a note promotes nothing.
        event = {"action": "comment", "candidate_key": "k"}
        self.ui._reaffirm_standing_action(event, None)
        self.assertEqual(event["action"], "comment")
        self.assertNotIn(event["action"], self.ui.QUESTION_READY_ACTIONS)

    def test_a_verdict_is_left_alone(self):
        # The helper must not touch an actual decision.
        event = {"action": "accept", "candidate_key": "k"}
        self.ui._reaffirm_standing_action(event, {"action": "block"})
        self.assertEqual(event["action"], "accept")
        self.assertNotIn("note_action", event)

    def test_a_note_is_reaffirmed_through_the_real_append_path(self):
        # Not just the helper: the real store must apply it on the real path, because the whole bug
        # is that `append_review` writes whatever action it is handed as the question's latest state.
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            key = "moex:115090:311:0704:1:question:q001"
            candidates = tmp / "candidates.jsonl"
            candidates.write_text(json.dumps({
                "candidate_key": key, "question_number": 1, "stem": "題幹",
                "options": [{"key": "A", "text": "甲"}], "answer": "A",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            log = tmp / "question_review_events.jsonl"
            log.write_text("", encoding="utf-8")
            state = self.ui.ReviewState(candidates, None, log, review_backend="jsonl")
            state.append_review({"candidate_key": key, "action": "accept", "reviewer": "local"})
            state.append_review({"candidate_key": key, "action": "comment", "notes": "莢膜",
                                 "reviewer": "local"})
            events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual([e["action"] for e in events], ["accept", "accept"])
        self.assertEqual(events[1]["notes"], "莢膜")
        self.assertEqual(events[1]["note_action"], "note")
        # And the projection still calls it decided, which is what the note must not break.
        self.assertEqual(events[-1]["action"], "accept")

    def test_no_path_still_treats_only_correct_as_needing_reaffirming(self):
        # A note must be reaffirmed on every path, so the old `correct`-only checks must not survive
        # anywhere - a note reaffirmed on one path and not another is two behaviours for one rule.
        source = (ROOT / "scripts" / "serve_question_review_ui.py").read_text(encoding="utf-8")
        self.assertNotIn('if event.get("action") == "correct":\n            previous =', source)
        self.assertNotIn('if event.get("action") == "correct":\n                previous =', source)
        self.assertIn('if event.get("action") in (NOTE_ACTIONS | {"correct"}):', source)


class NoteUiTests(unittest.TestCase):
    """The note box has to be *reachable*, which is the half that was missing."""

    @classmethod
    def setUpClass(cls):
        cls.html = js_of(V2.read_text(encoding="utf-8"))

    def test_the_note_box_can_be_opened(self):
        # The defect: `.on` was only ever removed. Asserted as the class being added somewhere.
        self.assertIn("$('reasonBox').classList.add('on')", self.html)
        self.assertIn("$('reasonBox').classList.remove('on')", self.html)

    def test_there_is_a_control_that_opens_the_note(self):
        self.assertIn('id="actNote"', self.html)
        self.assertIn("$('actNote').onclick", self.html)
        # A keyboard route, because the point is to annotate without breaking the pass.
        self.assertIn("c: 'note'", self.html)

    def test_saving_a_note_sends_a_comment_action(self):
        self.assertIn("action: 'comment'", self.html)

    def test_a_note_does_not_count_as_reviewed_in_the_ui(self):
        # The client's own copy of the rule. If `comment` reached `S.verdict`, annotating during the
        # first pass would mark questions read and the walk would skip them.
        self.assertIn("NOTE_ACTIONS", self.html)
        self.assertIn("if (action && !NOTE_ACTIONS.has(action)) S.verdict.set", self.html)

    def test_the_note_is_shown_with_the_question_it_is_about(self):
        # A note the reviewer cannot see again is a note they will write twice.
        self.assertIn("noteOf(item.candidate_key)", self.html)
        self.assertIn("noteShown", self.html)

    def test_a_note_can_only_be_attached_to_the_question_it_was_typed_on(self):
        """註記框是同一個,但註記是屬於寫它的那一題。

        缺陷(2026-09-22 瀏覽器重現):框沒有被清空,而 `decide()` 無條件讀 `reasonText.value`。
        在第 1 題寫下 `第一題的註記ABCXYZ`、按「確認正常」前進到第 2 題、不開框直接按 B
        阻擋 —— 第 1 題的註記就成了第 2 題的阻擋理由。真人不會知道自己被安上了別題的註記。

        負對照:把決定時的 `S.noteKey === item.candidate_key` 條件拿掉(回到「無條件讀框」),
        這個測試會失敗 —— 因為它驗的是「誰擁有它」,不是「框在不在」。
        """
        # 關框時要清空並忘記主人。
        self.assertIn("$('reasonText').value = ''", self.html)
        self.assertIn("S.noteKey = null", self.html)
        # 開框時要記住是誰的。
        self.assertIn("S.noteKey = item ? item.candidate_key : null", self.html)
        # 決定時只讀「開著且是這一題的」那個框。
        self.assertIn("S.noteKey === item.candidate_key", self.html)
        self.assertIn(
            "const noteHere = $('reasonBox').classList.contains('on') && S.noteKey === item.candidate_key;",
            self.html,
        )
        # 負對照要針對 `decide()` 本身：它的區塊裡不可以再有「無條件讀框」那一行。
        # （`saveNote()` 留著無條件讀是對的：它只在框開著時被呼叫。）
        decide_body = self.html.split("async function decide(action) {", 1)[1].split("}\n", 1)[0]
        self.assertNotIn("const notes = $('reasonText').value.trim();", decide_body)
        self.assertIn("const notes = noteHere ? $('reasonText').value.trim() : '';", decide_body)


if __name__ == "__main__":
    unittest.main()
