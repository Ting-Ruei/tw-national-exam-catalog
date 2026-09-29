"""The acceptance standard's instrument, tested against the defect it was built from.

`continuation.py` says a field that stops where the paper continues is the thing worth escalating.
These tests are written so that the instrument cannot pass by measuring nothing: the first one
fails unless the real paper's text is reproduced exactly, and the second one is the negative
control the charter requires - it must FAIL on the shipped fields as they are today.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qbr import continuation  # noqa: E402


#: Engine A's reading of `1002_物理治療師_骨科疾病物理治療學` question 10. The paper's option A
#: continues on the next line with `異常`.
LINES_A = [
    "10.下列有關薦髂關節（sacroiliac joint）病變的敘述，何者正確？",
    "A.薦髂關節疼痛可能對臀中肌（gluteus medius）造成反射性抑制（reflex inhibition），導致步態",
    "異常",
    "B.當發現左邊的前上腸骨棘和後上腸骨棘均較右邊的位置偏高，則表示左邊的薦骨有向上移位（upslip）問題",
    "C.發生在恥骨聯合（symphysis pubis）的問題不會影響到薦髂關節",
    "D.此關節有多條肌肉經過，所以常發生病變",
]

#: Engine B's reading of the same page. B puts each marker on a row of its own, which is what makes
#: it the more reliable instrument for geometry - and why a B-*led* re-segmentation is not the test.
LINES_B = [
    "10.",
    "A. 薦髂關節疼痛可能對臀中肌（gluteus",
    "medius）造成反射性抑制（reflex inhibition），導致步態",
    "異常",
    "當發現左邊的前上腸骨棘（anterior",
    "B. superior iliac spine）和後上腸骨棘（posterior superior",
    "iliac spine）均較右邊的位置偏高，則表示左邊的薦骨有向上移位（upslip）問題",
]

#: As packaged. Option A stops at `步態`; `異常` was swept into the stem.
SHIPPED = {10: {"A": "薦髂關節疼痛可能對臀中肌（gluteus medius）造成反射性抑制（reflex inhibition），導致步態",
                "B": "當發現左邊的前上腸骨棘和後上腸骨棘均較右邊的位置偏高，則表示左邊的薦骨有向上移位問題",
                "C": "發生在恥骨聯合（symphysis pubis）的問題不會影響到薦髂關節",
                "D": "此關節有多條肌肉經過，所以常發生病變"}}

#: As the paper printed it. The fix must make the shipped reading equal this.
CORRECT = {10: dict(SHIPPED[10],
                    A="薦髂關節疼痛可能對臀中肌（gluteus medius）造成反射性抑制（reflex inhibition），"
                      "導致步態異常")}


def test_the_fixture_carries_the_wrap_it_claims():
    """Guards the fixture itself: without this, every test below could pass on nothing."""
    assert "導致步態" in LINES_A[1] and LINES_A[2] == "異常"
    assert SHIPPED[10]["A"].endswith("導致步態")
    assert CORRECT[10]["A"].endswith("導致步態異常")


def test_both_engines_see_the_loss_that_is_there():
    """The measurement the standard rests on: the intersection, not either engine alone."""
    result = continuation.verify_paper(LINES_A, LINES_B, SHIPPED)
    assert result["loss_both"] == 1
    assert result["both"][0]["option"] == "A"
    assert result["both"][0]["dropped"] == "異常"


def test_the_control_fails_on_the_shipped_fields():
    """Negative control (charter): the check must FAIL on the text as it is today.

    If this ever passes, the defect is gone and this file's fixtures must be rebuilt from a real
    loss - not relaxed, because a control that passes proves the instrument measures nothing.
    """
    result = continuation.verify_paper(LINES_A, LINES_B, SHIPPED)
    assert result["both"], "the control no longer fails: the shipped option is no longer truncated"


def test_a_complete_option_raises_nothing():
    """The other half of the control: the same instrument is silent when the field is complete."""
    result = continuation.verify_paper(LINES_A, LINES_B, CORRECT)
    assert result["loss_both"] == 0
    assert result["both"] == []


def test_a_marker_or_number_after_the_option_is_not_a_continuation():
    """A line that opens a question or an option continues nothing. Otherwise every option
    would be 'lossy', which is the over-firing that made the first three versions of this
    detector useless."""
    lines = ["1.題幹？", "A.選項甲的完整敘述夠長了吧",
             "B.選項乙的完整敘述也夠長了", "2.下一題的題幹夠長了嗎？",
             "A.下一題的選項敘述夠長了嗎"]
    shipped = {1: {"A": "選項甲的完整敘述夠長了吧", "B": "選項乙的完整敘述也夠長了"}}
    assert continuation.continuation_losses(lines, shipped) == []


def test_a_short_fragment_is_not_evidence():
    """A one-character line cannot be told from a stray glyph of the other engine."""
    lines = ["1.題幹夠長了嗎？", "A.選項甲的敘述夠長了", "、",
             "B.選項乙的敘述夠長了"]
    shipped = {1: {"A": "選項甲的敘述夠長了", "B": "選項乙的敘述夠長了"}}
    assert continuation.continuation_losses(lines, shipped) == []


@pytest.mark.parametrize("engine_a,engine_b,expected", [
    # A's reading carries the continuation; B's reading does not have that text at all (it placed
    # the tail somewhere else on the page). B therefore computes no loss: in its own reading the
    # field is complete. The intersection is empty - and that is the point of intersecting.
    # Reporting A's count alone would put this in front of a reviewer on one engine's word.
    (["1.題幹夠長了嗎？", "A.選項甲的敘述已經夠長了", "然後繼續往下寫", "B.選項乙的敘述也夠長了"],
     ["1.", "A.選項甲的敘述已經夠長了", "B.選項乙的敘述也夠長了"],
     0),
])
def test_intersection_is_the_threshold(engine_a, engine_b, expected):
    shipped = {1: {"A": "選項甲的敘述已經夠長了", "B": "選項乙的敘述也夠長了"}}
    result = continuation.verify_paper(engine_a, engine_b, shipped)
    assert result["loss_a"] >= 1, "engine A's reading continues the option, so A must see the loss"
    assert result["loss_b"] == 0, "engine B's reading does not carry that text"
    assert result["loss_both"] == expected


def test_summarise_counts_questions_not_fields():
    """Two lost options on one question is one question to look at."""
    papers = [{"loss_a": 3, "loss_b": 5, "loss_both": 2,
               "both": [{"question_number": 4, "option": "A"},
                        {"question_number": 4, "option": "C"}]}]
    totals = continuation.summarise(papers)
    assert totals == {"papers": 1, "loss_a": 3, "loss_b": 5, "loss_both": 2,
                      "questions_with_loss": 1}
