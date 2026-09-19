# -*- coding: utf-8 -*-
"""The corrections sheet, and the two chrome rules that were overfitted.

Every test here is a regression test for a defect found by measuring the corpus, and each
one names the real file it was found on. A test written from imagination would have passed
against the broken code in all four cases, so the samples are the actual text.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import canon, corrections, repair  # noqa: E402


class VoidWording(unittest.TestCase):
    """`除未作答者不給分外，其餘均給分` means the same as `一律給分`."""

    def test_the_fifth_wording_is_read_as_a_void(self):
        # Found on 1031_中醫師(一)_中醫基礎醫學(一). The `＃` for question 22 had no explanation
        # at all until this wording was accepted, because the regex only knew `一律給分`.
        note = "備註：第3題答Ｂ或Ｄ或BD者均給分，第22題除未作答者不給分外，其餘均給分，第71題一律給分"
        parsed = corrections.parse_corrections(note)
        self.assertEqual(parsed[22].kind, "void")
        self.assertEqual(parsed[22].as_answer(), "送分")
        self.assertEqual(parsed[71].kind, "void")

    def test_the_nfkc_comma_is_accepted(self):
        # `，` becomes `,` under NFKC, which `parse_corrections` performs before matching. A
        # pattern written only for the full-width comma therefore matched nothing at all.
        self.assertEqual(
            corrections.parse_corrections("備註：第5題除未作答者不給分外,其餘均給分")[5].kind,
            "void")


class WhichRemark(unittest.TestCase):
    """The notes are read from the last `備註`, never the first."""

    def test_a_table_headed_第N題_does_not_swallow_the_next_note(self):
        # Found on 1042_中醫師(一)_中醫基礎醫學(一). Its answer table is headed `第1題第2題…`, so
        # the text contains `…第10題答案…`, and the *first* `備註` is inside the table's own
        # label (`備註。題號第1題…`). Searching from there let `第10題` start a match that ran
        # through the whole table and ended at `第14題答B給分`, inventing a correction for 10
        # and swallowing the real one for 14.
        table = ("題號第1題第2題第3題第4題第5題第6題第7題第8題第9題第10題"
                 "答案 C C B C D C A C C B "
                 "備註。題號第11題第12題第13題第14題第15題第16題第17題第18題第19題第20題"
                 "答案 A D B # A C D A D D "
                 "備註：第14題答Ｂ給分，第45題答Ｂ或Ｃ或BC者均給分。")
        parsed = corrections.parse_corrections(table)
        self.assertIn(14, parsed)
        self.assertEqual(parsed[14].accepted, frozenset({"B"}))
        self.assertNotIn(10, parsed)


class CombinationsAreNotLabels(unittest.TestCase):
    """`BC` names two boxes ticked together, not an option called `BC`."""

    def test_labels_and_combinations_are_kept_apart(self):
        parsed = corrections.parse_corrections("備註：第16題答Ｂ或Ｃ或BC者均給分")
        correction = parsed[16]
        self.assertEqual(correction.labels, frozenset({"B", "C"}))
        self.assertEqual(correction.combinations, frozenset({"BC"}))

    def test_a_combination_never_reaches_the_answer_table(self):
        # The table is what every consumer compares against the paper's options. Putting `BC`
        # in it made the gate's `answer-not-on-sheet` check fire on 6 of 6 papers of
        # 1152 醫事檢驗師, naming `BC` and `BD` as answers the paper did not offer - a correct
        # complaint about the data it was handed and a wrong one about the paper.
        answer_text = "題號 01 02\n答案 A B"
        table, _ = corrections.authoritative_answers(
            [answer_text], ["備註：第2題答Ｂ或Ｃ或BC者均給分"],
            options_by_number={2: ["A", "B", "C", "D"]})
        self.assertEqual(table[2], ("B", "C"))
        for label in table[2]:
            self.assertIn(label, ["A", "B", "C", "D"])

    def test_a_candidate_marking_both_boxes_is_accepted(self):
        parsed = corrections.parse_corrections("備註：第16題答Ｂ或Ｃ或BC者均給分")
        self.assertTrue(parsed[16].accepts("BC"))
        self.assertTrue(parsed[16].accepts("B"))
        self.assertFalse(parsed[16].accepts("A"))


class VoidedAnswersUseTheQuestionsOwnLabels(unittest.TestCase):
    def test_a_void_accepts_every_option_the_paper_printed(self):
        # A voided question accepts whatever the candidate chose, which is the options *that
        # question offered*. Filling in A-H instead makes the gate fire on a four-option
        # question - measured on 115090 questions 10 and 41.
        table, parsed = corrections.authoritative_answers(
            ["題號 01\n答案 A"], ["備註：第1題一律給分"],
            options_by_number={1: ["A", "B", "C", "D"]})
        self.assertEqual(table[1], ("A", "B", "C", "D"))
        self.assertEqual(parsed[1].as_answer(), "送分")


class RepeatedDrawsAreNotCollapsed(unittest.TestCase):
    """An option row that legitimately repeats must survive."""

    def test_a_repeated_option_row_is_not_dropped(self):
        # Found on 1001_醫事檢驗師_生物化學與臨床生化學: Q11 at the foot of page 3 and Q59 at the
        # head of page 4 have the identical option set, nine lines apart. The removed
        # `collapse_repeated_draws` deleted the second and left Q59 with no options.
        options = "\ue18cApo A-I  \ue18dApo B-100  \ue18eApo B-48  \ue18fApo C-II"
        text = "\n".join([
            "11  下列那一種酵素催化的反應不會產生CO₂？",
            options,
            "59  將膽固醇由肝外組織運送至肝臟加以排除的脂蛋白元為：",
            options,
        ])
        repaired, dropped = repair.normalize_pretty(text)
        self.assertEqual(repaired.count("\ue18cApo A-I"), 2)
        self.assertFalse([d for d in dropped if d.get("rule") == "repeated-draw"])

    def test_the_collapse_helper_is_gone(self):
        self.assertFalse(hasattr(repair, "collapse_repeated_draws"))


class ChromeIsPrintedOnEveryPage(unittest.TestCase):
    """A footer repeats on all pages; a truncated option label does not."""

    @staticmethod
    def _row(page, y, text):
        return {"page": page, "x0": 35.0, "y0": float(y), "y1": float(y) + 10,
                "text": text, "size": 9.0, "engine": "a"}

    def test_a_footer_on_every_body_page_is_chrome(self):
        rows = [self._row(page, 805.6, "代號：5104") for page in (1, 2, 3, 4)]
        rows += [self._row(page, 300.0, "%d  下列何者正確？" % page) for page in (2, 3, 4)]
        kept, dropped = repair.mask_chrome(rows)
        self.assertEqual(len([r for r in kept if "代號" in r["text"]]), 0)
        self.assertTrue(all(d.get("rule") == "recurring-at-fixed-position"
                            for d in dropped if "代號" in (d.get("text") or "")))

    def test_a_truncated_option_label_is_not_chrome(self):
        # Found on 1062_醫事檢驗師_生物化學與臨床生化學, whose questions wrap at the foot of the
        # page. The leftover `A.` `C.` `D.` of three truncated option sets all landed on
        # y=805.6 - exactly where the form prints its footer - so three of twelve pages was
        # enough under the old count rule and the labels were deleted as chrome.
        #
        # The page numbers are the ones measured on that file: `A.` on 2/6/9, `C.` on 3/7/10,
        # `D.` on 4/8. Each appears on exactly three of twelve pages, which is the count the
        # old rule asked for - so this test fails against the code that had that rule.
        rows = []
        for label, pages in (("A.", (2, 6, 9)), ("C.", (3, 7, 10)), ("D.", (4, 8))):
            for page in pages:
                rows.append(self._row(page, 805.6, label))
        for page in range(2, 13):
            rows.append(self._row(page, 100.0, "%d  題幹" % page))
        kept, _ = repair.mask_chrome(rows)
        self.assertEqual(sorted(r["text"] for r in kept if r["y0"] == 805.6),
                         ["A.", "A.", "A.", "C.", "C.", "C.", "D.", "D."])

    def test_a_one_page_document_keeps_its_rows(self):
        # A subset test against the empty body-page set is vacuously true, so a single-page
        # corrections sheet was called chrome from end to end and the `備註` was lost with it.
        # The count condition is what keeps the two rules from cancelling each other.
        rows = [self._row(1, 100.0, "題號 01"), self._row(1, 110.0, "答案 A"),
                self._row(1, 700.0, "備註：第1題一律給分")]
        kept, _ = repair.mask_chrome(rows)
        self.assertEqual(len(kept), 3)


if __name__ == "__main__":
    unittest.main()
