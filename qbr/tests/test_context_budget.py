# -*- coding: utf-8 -*-
"""配額與視窗上限：它們的失效模式都是靜默的。

owner 的兩條規則（2026-09-24）是這裡的骨幹：
  「你要給予合理的 token 預算，不然 AI 會偷懶都丟給人做」
  「不要認為地端模型可以容納很大的上下文（512K），我認為設限 256K」

**負控制**：`test_a_cheap_uncertain_answer_is_flagged` 在 `is_suspicious` 永遠回 False 時必須
失敗——否則這條規則只是文字，不是行為。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import context_budget as cb  # noqa: E402


# ------------------------------------------------------------------ the window

def test_the_budget_is_256k_not_the_engines_advertised_window():
    """524,288 是引擎的標稱值；我們計畫時用的是 owner 給的 256K。"""
    assert cb.CONTEXT_BUDGET_TOKENS == 256_000
    assert not cb.within_budget(524_288), "標稱視窗被當成可用預算了"
    assert cb.within_budget(256_000)
    assert cb.within_budget(255_999)


def test_the_budget_is_configurable_without_editing_code():
    """換引擎或換人決定時，不該要改程式——但預設必須是 256K。"""
    assert cb.within_budget(300_000, budget=400_000)
    assert not cb.within_budget(300_000, budget=200_000)


# ------------------------------------------------------------------ the round size

def test_a_round_cannot_exceed_the_limit():
    assert cb.round_size(50) == cb.QUESTIONS_PER_ROUND
    assert cb.round_size(5) == 5
    # 0／負數是呼叫者的「用預設」，不是「送無限多」
    assert cb.round_size(0) == cb.QUESTIONS_PER_ROUND
    assert cb.round_size(-1) == cb.QUESTIONS_PER_ROUND


# ------------------------------------------------------------------ the quota

def test_a_confident_answer_is_never_suspicious_whatever_it_cost():
    """便宜的**正確**答案是目標，不是問題。這條規則只標記「沒花錢就宣告不確定」。"""
    assert cb.is_suspicious(uncertain=False, tokens_used=10) is False
    assert cb.is_suspicious(uncertain=False, tokens_used=999_999) is False


def test_a_cheap_uncertain_answer_is_flagged():
    """核心：用不到配額的四分之一就說「看不出來」——那正是 owner 預測的偷懶。"""
    assert cb.is_suspicious(uncertain=True, tokens_used=1_000) is True
    assert cb.is_suspicious(uncertain=True, tokens_used=cb.QUESTION_QUOTA_TOKENS // 4 - 1) is True


def test_an_uncertain_answer_that_spent_the_quota_is_taken_at_face_value():
    """花完配額才說不確定，是一個量測結果，不是偷懶。它會走「升級問人」那條路。"""
    assert cb.is_suspicious(uncertain=True, tokens_used=cb.QUESTION_QUOTA_TOKENS) is False
    assert cb.is_suspicious(uncertain=True, tokens_used=cb.QUESTION_QUOTA_TOKENS // 4) is False


def test_the_record_says_what_the_answer_cost():
    """讀者要能自己判斷一個「不確定」值不值得信，所以成本要寫進紀錄而不只是 log。"""
    note = cb.budget_note(used=2_000)
    assert "2000" in note or "2,000" in note
    assert "可疑" in note, "用 2K 就宣告不確定，紀錄裡沒有標記"
    # 花完配額的那一種不該被標成可疑——否則標記沒有分辨力
    assert "可疑" not in cb.budget_note(used=cb.QUESTION_QUOTA_TOKENS)
    # 沒有用量資訊時不要編一句話出來
    assert cb.budget_note(used=0) == ""
