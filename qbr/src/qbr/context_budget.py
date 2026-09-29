# -*- coding: utf-8 -*-
"""每次送出多少，以及「認真做」和「回報不確定」哪個比較划算。

**為什麼這是一個模組，而不是幾個常數。** 這兩件事是同一件事的兩面，而且它們的失敗模式都是
靜默的：

1. **視窗上限。** 引擎標稱的 `max_model_len`（DGX 上是 524,288）不等於可用的預算。owner 的
   規則（2026-09-24）是把可用上限設在 **256K**，理由是標稱視窗不是有效視窗，而且越長的
   context 越不穩定。把上限寫成常數並在送出前檢查，比相信「模型說它可以」可靠。

2. **配額，不是上限。** 這一條是從一個真實的失敗推出來的：如果只給「上限」，模型面對一題
   難題時的理性選擇是**宣告不確定**——回答「我看不出來」只要幾百 token，認真比對要幾萬。
   兩者都不會被懲罰，所以它會選便宜的。owner 的原話是「不然 AI 會偷懶都丟給人做」。

   所以送出的是一個**配額**（「這一題你有這麼多可以用」），而不是一個天花板；而且用完配額
   得到的答案與草率宣告不確定得到的答案，要能被區分開來。做法不是懲罰，是**標記**：
   一題在配額的一小部分之內就宣告不確定，是一筆「可疑」的紀錄，留給人抽查。它可能是模型
   偷懶，也可能是它真的看不出來——這個模組不猜哪一種，它只負責讓兩者**可分辨**。

**這裡不做的事：** 不關掉思考。owner 的規則是「約束不是單純關閉思考，而是要給予合適的提示詞，
以及給予工具調用的功能」——思考要能被引導到「去看截圖確認」，那是提示詞與工具的事
（`confirm_dispute.py`），不是一個 `reasoning_effort: none` 就能代替的。
"""
from __future__ import annotations

#: The usable context budget, in tokens, regardless of what the engine advertises.
#:
#: 524,288 is what the DGX model reports. It is deliberately not what we plan against: a nominal
#: window is not an effective one. 256K is the owner's number (2026-09-24) and it is the value a
#: caller should check against, not the engine's.
CONTEXT_BUDGET_TOKENS = 256_000

#: How many questions one round may send. Owner's starting number (2026-09-24): five, adjustable.
#:
#: It is adjustable because it trades against the two limits above rather than being a fact about the
#: task: fewer questions per call when a question needs a whole page in view (a 變通手法 that needs
#: the layout), more when the job is a line-by-line transcription. The binding constraint is the
#: token budget, not this number.
QUESTIONS_PER_ROUND = 5

#: Per-question quota, in tokens. **A quota, not a ceiling.** A model that has 80K available and uses
#: 2K to declare "cannot tell" has made a different decision from one that spent the 80K and then said
#: so, and only the second one is a measurement of the model.
QUESTION_QUOTA_TOKENS = 80_000

#: Below this fraction of the quota, an "uncertain" answer is marked suspicious rather than accepted
#: at face value. Chosen so that a genuine single-image transcription (measured at a few thousand
#: tokens including the prompt) is not flagged, while a bare "看不清楚" is.
SUSPICIOUS_FRACTION = 0.25


def within_budget(token_count: int, *, budget: int = CONTEXT_BUDGET_TOKENS) -> bool:
    """Whether a request of this size may be sent.

    Checked by the caller **before** the request, on the estimate, because the failure this prevents
    is an engine that accepts an over-long request and then silently truncates the *front* of it —
    which is where the system prompt and the reviewer's principles live.
    """
    return token_count <= budget


def round_size(requested: int, *, limit: int = QUESTIONS_PER_ROUND) -> int:
    """How many questions to actually send this round.

    A caller may ask for fewer; it may not ask for more. The limit exists so that one round cannot
    spend the whole day's budget on a single batch, which would leave the reviewer's own interactive
    requests queued behind it (the owner's other constraint: 「設備有限，有別的任務要做」).
    """
    if requested <= 0:
        return limit
    return min(requested, limit)


def is_suspicious(*, uncertain: bool, tokens_used: int,
                  quota: int = QUESTION_QUOTA_TOKENS) -> bool:
    """Whether an "I cannot tell" answer was reached **too cheaply** to be taken at face value.

    Returns False for a confident answer whatever it cost: a cheap correct answer is the goal, not a
    problem. This only marks the combination that the owner predicted — declaring uncertainty without
    spending the budget that would justify it.
    """
    if not uncertain:
        return False
    return tokens_used < quota * SUSPICIOUS_FRACTION


def budget_note(*, used: int, quota: int = QUESTION_QUOTA_TOKENS) -> str:
    """The sentence appended to a finding so a reader can see what the answer cost.

    Written into the record rather than only into a log, because the reader who has to decide whether
    to trust an "uncertain" is looking at the record, not at the daemon's stdout.
    """
    if used <= 0:
        return ""
    share = used / float(quota) if quota else 0.0
    flag = "（可疑：用不到配額的四分之一就宣告不確定）" if share < SUSPICIOUS_FRACTION else ""
    return "這一題用了約 %d token，配額 %d（%.0f%%）%s" % (used, quota, share * 100, flag)
