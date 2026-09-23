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


class NoteKeepsTheQuestionInTheStuckQueueTests(unittest.TestCase):
    """A note on a stuck question must not lift it out of the 錯題討論區.

    The defect: the 錯題討論區 is populated by `reviewStatus=discuss`, which matches a question when
    its **latest** state is a pending reset (`repair_pending` / `accepted_reaudit` / `reset_review`)
    or a human `block`. A note written with `action=comment` becomes the latest event unless it is
    reaffirmed. `_reaffirm_standing_action` only re-states an action in `STANDING_ACTIONS`,
    and `reset_review` is not one of them - so the note cleared the pending reset and the question
    left the stuck list. Writing the 註解 that explains why a question is stuck **removed it from the
    list of stuck questions**. Measured 2026-09-23 through the real `append_review` + fold path.

    What is pinned: the note merges into the pending reset (both stay), a real verdict still clears
    it, and the person's note stays visible (`review_projection` reads `reset_notes` first, so the
    repair marker is preserved under that name while the note becomes `notes`).
    """

    @classmethod
    def setUpClass(cls):
        cls.ui = import_review_ui()

    def _replay(self, events: list[dict]) -> tuple[str, str, dict]:
        """Write the events through the real append path, then fold them from the log again.

        Both halves matter: the in-memory maps drive the request that wrote the note, and the
        reloaded maps drive every later request. The defect was present in both.
        """
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            key = "moex:115090:311:0704:1:question:q001"
            (tmp / "candidates.jsonl").write_text(json.dumps({
                "candidate_key": key, "question_number": 1, "stem": "題幹",
                "options": [{"key": "A", "text": "甲"}], "answer": "A",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            log = tmp / "events.jsonl"
            log.write_text("", encoding="utf-8")
            state = self.ui.ReviewState(tmp / "candidates.jsonl", None, log, review_backend="jsonl")
            for event in events:
                state.append_review({"candidate_key": key, **event})
            in_memory = self.ui.review_projection(
                state.latest_reviews.get(key), state.latest_reset_reviews.get(key))["queue_bucket"]
            latest, _counts, latest_reset = self.ui.load_review_events(log)
            folded = self.ui.review_projection(latest.get(key), latest_reset.get(key))["queue_bucket"]
            shown = (state.latest_reset_reviews.get(key) or latest_reset.get(key) or {}).get("notes")
            return in_memory, folded, {"notes": shown, "reset": state.latest_reset_reviews.get(key)}

    #: The three ways a question gets into the 錯題討論區, plus a human block.
    RESETS = {
        "repair_pending": {"action": "reset_review", "reviewer": "repair",
                           "repair_kind": "backfill_repair", "notes": "修復後待複核"},
        "accepted_reaudit": {"action": "reset_review", "reviewer": "accepted-reaudit",
                             "previous_action": "accept", "approval_ref": "accepted-reaudit"},
        "reset_review": {"action": "reset_review", "reviewer": "pipeline", "notes": "退回"},
    }

    def test_a_note_does_not_lift_a_stuck_question_out_of_the_stuck_queue(self):
        for bucket, reset in self.RESETS.items():
            with self.subTest(bucket=bucket):
                in_memory, folded, info = self._replay(
                    [reset, {"action": "comment", "notes": "我把 C 改成莢膜", "reviewer": "local"}])
                self.assertIn(in_memory, self.ui.DISCUSS_BUCKETS,
                              "寫完註解後，這題就從記憶體的討論區清單消失了")
                self.assertIn(folded, self.ui.DISCUSS_BUCKETS,
                              "寫完註解後，重載的討論區清單也少了這題")
                self.assertEqual(bucket, in_memory)
                # The person's note is what the UI shows.
                self.assertEqual(info["notes"], "我把 C 改成莢膜")

    def test_the_repair_marker_is_preserved_under_its_own_key(self):
        # `review_projection` reads `reset_notes` before `notes`, so moving the original note there
        # keeps the repair reason classifying as repair_pending after the person's note lands.
        for bucket, reset in self.RESETS.items():
            with self.subTest(bucket=bucket):
                _in_memory, _folded, info = self._replay(
                    [reset, {"action": "comment", "notes": "人的註解", "reviewer": "local"}])
                self.assertEqual(info["reset"].get("notes"), "人的註解")
                if reset.get("notes") in ("修復後待複核", "退回"):
                    self.assertEqual(info["reset"].get("reset_notes"), reset["notes"])

    def test_a_verdict_still_clears_the_pending_reset(self):
        # The fix must not make a question un-leavable: a real decision is still a decision.
        for action in ("accept", "block", "needs_review"):
            with self.subTest(action=action):
                in_memory, folded, _info = self._replay(
                    [self.RESETS["repair_pending"], {"action": action, "reviewer": "local"}])
                self.assertNotEqual("repair_pending", in_memory)
                self.assertNotEqual("repair_pending", folded)

    def test_a_note_after_a_verdict_is_unaffected(self):
        # The note-after-accept case the note store already handled must keep working.
        in_memory, folded, _info = self._replay(
            [{"action": "accept", "reviewer": "local"},
             {"action": "comment", "notes": "補註", "reviewer": "local"}])
        self.assertEqual("reviewed", in_memory)
        self.assertEqual("reviewed", folded)

    def test_the_sql_fold_uses_the_same_rule_as_the_jsonl_fold(self):
        """The SQL load is its own copy of the fold, so it needs its own check.

        The JSONL tests above pass even if the SQL half is wrong - and on the SQL backend
        (`sql_primary`) the JSONL half is not the one answering. A stub connection drives the real
        `_sql_question_review_maps`, so the assertion is about the fold, not about a string.
        """
        import json

        ui = self.ui

        class Stub:
            def __init__(self, rows):
                self._rows = rows
                self._used = False

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def execute(self, _sql, _params):
                pass

            def fetchall(self):
                if self._used:
                    return []
                self._used = True
                return self._rows

        class Conn:
            def __init__(self, cur):
                self._cur = cur

            def cursor(self):
                return self._cur

        def fold(rows):
            import contextlib
            state = object.__new__(ui.ReviewState)
            cur = Stub(rows)

            @contextlib.contextmanager
            def fake_connect():
                yield Conn(cur)

            state._sql_connect = fake_connect
            return state._sql_question_review_maps(["k"])

        reset_row = ("k", "reset_review", None,
                     {"action": "reset_review", "reviewer": "repair",
                      "repair_kind": "backfill_repair", "notes": "修復後待複核"}, None, "repair", None)
        note_row = ("k", "comment", None,
                    {"action": "comment", "notes": "我把 C 改成莢膜", "note_action": "note"},
                    "我把 C 改成莢膜", "local", None)
        latest, _counts, latest_reset = fold([reset_row, note_row])
        self.assertNotIn("k", latest, "SQL 折疊把待複核的重置事件換成了註解")
        self.assertIn("k", latest_reset)
        self.assertEqual("我把 C 改成莢膜", latest_reset["k"].get("notes"))
        self.assertEqual("修復後待複核", latest_reset["k"].get("reset_notes"))
        bucket = ui.review_projection(latest.get("k"), latest_reset.get("k"))["queue_bucket"]
        self.assertIn(bucket, ui.DISCUSS_BUCKETS)

        # Negative control: a real verdict after the reset must still clear it on this path too.
        accept_row = ("k", "accept", None,
                      {"action": "accept", "reviewer": "local"}, None, "local", None)
        latest2, _c2, latest_reset2 = fold([reset_row, note_row, accept_row])
        self.assertNotIn("k", latest_reset2)
        self.assertIn("k", latest2)

    def test_the_condition_lives_in_one_place(self):
        # Six copies of "is this a note on a pending reset" is six chances for the discuss list and
        # the projection to disagree. It must be one function, used by every fold.
        source = (ROOT / "scripts" / "serve_question_review_ui.py").read_text(encoding="utf-8")
        self.assertEqual(1, source.count("def _note_annotates_pending_reset("))
        self.assertGreaterEqual(source.count("_note_annotates_pending_reset("), 5,
                                "每個折疊點都要用同一個判斷")
        self.assertIn("def _merge_note_into_reset(", source)


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
