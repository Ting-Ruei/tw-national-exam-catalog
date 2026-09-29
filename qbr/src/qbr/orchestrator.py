# -*- coding: utf-8 -*-
"""指揮者：決定地端模型的讀法可不可信，不可信時才自己看。

**角色（owner 2026-09-24）**：
  「因為是指揮者，必要時候可以看然後決策，而不是每一題都看，DGX處理過覺得合理就放行，
    覺得不合理就自己來看輔助修正 DGX 的地端模型」

所以這是一個**分流器**，不是第二個審查員：

    DGX 讀完 → 指揮者看它的讀法與差異 → TRUST   ：放行，這題結束
                                    → CARE    ：地端讀法可疑，指揮者自己看那張圖
                                    → DOUBT   ：地端明顯錯，指揮者自己看並提出修正

**為什麼不是「每一題都送出去」。** 兩個理由，第二個比第一個重要：

1. **成本。** 實測一次判斷 ~240 tokens/1.4s；一輪 5 題就是 5 次外網呼叫。
2. **資料邊界。** 只有被指揮者判定需要看的題目，其**內容**才離開內網。放行的題目只送出
   結構化欄位（見 `judgement_payload`）——這是 owner 同意的界線，而它是可以量測的：
   `external_llm_calls.jsonl` 記每一次的 bytes。

**它不做決定。** 它不 accept、不 block、不改題目文字。它產生的是一個 advisory 判斷，
人仍然是最後一關（charter 第 3 節，GOV-05）。這一條有負控制：
`test_the_orchestrator_cannot_change_a_review_state`。
"""
from __future__ import annotations

import json
import os

from . import ai_findings, engines

#: What the orchestrator may say about the local model's reading.
VERDICTS = ("TRUST", "CARE", "DOUBT")

ORCHESTRATOR_SYSTEM = """你是考題審核流程的指揮者。

有一個地端模型負責「看考卷截圖、逐字轉錄紙本內容」。它的讀法不總是對的：它會把標記誤判成
罕見字元、會把上下標壓平、會截斷、偶爾會自己「修正」它認為是錯的字。

你的工作只有一件：**判斷它這一次的讀法可不可信**，並在不可信時決定你自己要不要看那張圖。

回一個 JSON，不要有其他文字：
{"verdict":"TRUST|CARE|DOUBT","why":"一句話，說明你憑什麼這樣判","self_look":true|false}

判準：
- TRUST：差異是機械性的、方向一致、不改變題意（例如序號被誤判成罕見字元後又被還原）。
  這種放行，人只需抽查。
- CARE：差異可能改變答案、或地端讀法本身內部矛盾（同一符號在不同選項讀法不一致）。
  這種你要自己看，且 `self_look` 必須是 true。
- DOUBT：地端明顯讀錯，或有編造痕跡（讀出紙本上不可能存在的東西）。`self_look` 必須是 true。

不確定時選 CARE 而不是 TRUST：放行一個錯的讀法，比多看一眼貴得多。
"""


def judgement_payload(question, finding, *, principles=None, notes=None, rejected=None) -> dict:
    """What the orchestrator is shown when it is deciding whether to look.

    **This is the data-boundary decision, written down.** The triage payload is the *smallest* set
    that lets a judging model answer "can I trust this reading": the dispute kinds, the numbers, and
    the local model's own statement of where it differs.

    That last one is why `where` is here and not only in `look_payload`. The first version omitted it
    and the consequence was measured immediately: with `verdict` but no `where`, every question came
    back `CARE` for the same reason — 「地端回報 DEFECT 但未說明缺陷內容」 — which is not a judgement,
    it is the model correctly saying it was given nothing to judge. On this population `what` is
    `null` and **all** the substance lives in `where`, so sending the verdict without it sends a label
    with no predicate.

    The owner's instruction was 「指揮者看得到題目文字（含截圖判讀結果）」, and this function is where
    that permission is scoped: the model sees the diff it must adjudicate, and the full extracted
    text plus the page reading only when it has said it must look (`look_payload`).

    `principles`/`notes`/`rejected` are the three channels the local reader was sent and the judge was
    **not** (owner 2026-09-25: 「你應該參考原則 ＋ 原始題目的整個狀態 ＋ 截圖PDF找線索 ＋ 之前自己寫的
    怎麼修 ＋ 人的註記或建議 統合這些資料來解決問題，感覺目前沒有整合好」). Measured before this
    change: `judgement_payload` carried question number/subject/paper/dispute kinds/`local_model` and
    nothing else, so the only external basis for step ③'s whole-field rewrites was a verdict with no
    idea what the person had said or what had already been refused. They are rendered with the **same**
    functions the local prompt uses (`ai_findings.notes_note` / `rejected_note` /
    `principles_note`), so the judge and the reader cannot be shown two different versions of the same
    fact - and the caller passes the fold it already computed for the reader, so there is no second
    computation to drift.
    """
    local = finding if isinstance(finding, dict) else {}
    payload = {
        "question_number": question.get("question_number"),
        "subject": question.get("subject"),
        "paper": question.get("paper"),
        "dispute_kinds": sorted(question.get("kinds") or []),
        "local_model": {
            "verdict": local.get("verdict"),
            "what": local.get("what"),
            # The substance: which fields differ and how. Without this the judgement is blind.
            "where": local.get("where"),
            "confidence": local.get("confidence"),
        } if finding else None,
        "local_error": local.get("error"),
    }
    facts = {"reviewer_note": ai_findings.notes_note(notes) if notes else None,
             "previous_repairs_refused": ai_findings.rejected_note(rejected) if rejected else None,
             "reviewer_principles": ai_findings.principles_note(principles) if principles else None}
    for key, value in facts.items():
        # Bounded the same way the rest of the payload is: the triage call is the small one, and a
        # note that grew into an essay must not turn it into a second reading of the question.
        if value:
            payload[key] = value[:800]
    return payload


def look_payload(question, finding, *, figures=None) -> dict:
    """What it is shown when it has decided to look: the content itself.

    The extracted text, the page transcription, and the model's diff — the three things needed to
    adjudicate. Sent only after a non-TRUST verdict.

    `figures` is 「這一題的圖長什麼樣子」 (`ai_findings.figures_note`: per picture, what step ⑤ measured
    about it - `clipped {above, below}` / `ownership: unverified`). It is the fact that decides a
    question the text diff cannot: when the local reading's `where` is empty because the options are
    pictures, the pictures are the only thing left to look at. Measured before this change: the
    judge's payload had no picture fact and the second look had no picture at all, while
    `ORCHESTRATOR_SYSTEM` promised 「自己看那張圖」.
    """
    payload = judgement_payload(question, finding)
    payload["extracted"] = {
        "stem": question.get("stem") or "",
        "options": [str((option or {}).get("text") or "") for option in (question.get("options") or [])],
    }
    if isinstance(finding, dict):
        payload["page_read"] = finding.get("transcription") or {}
        payload["local_where"] = finding.get("where")
        payload["local_fix"] = finding.get("fix")
    if figures:
        payload["figure_facts"] = figures[:800]
    return payload


def parse_judgement(raw) -> dict | None:
    """The orchestrator's answer, or `None` when it did not give a usable one.

    Tolerant about the envelope (some gateways wrap JSON in a sentence or a fence), strict about the
    verdict: an unrecognised verdict is a failure, not a default. Defaulting to TRUST would silently
    let everything through; defaulting to DOUBT would make the loop ask the person about everything.
    Neither is a decision this function is entitled to make.
    """
    text = engines.content_of(raw) or ""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json").strip() if text.startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    verdict = str(parsed.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        return None
    self_look = bool(parsed.get("self_look"))
    # A verdict that says "the local reading is suspect" and then declines to look is a contradiction:
    # the only reason to escalate is to look. Made consistent here rather than trusted, so the
    # downstream rule ("CARE/DOUBT ⇒ the orchestrator reads the page") cannot be violated by a model
    # that answered sloppily.
    if verdict in ("CARE", "DOUBT"):
        self_look = True
    return {"verdict": verdict, "why": str(parsed.get("why") or "")[:400], "self_look": self_look}


def judge(question, finding, *, endpoint, timeout=120, max_tokens=400, audit_log=None,
          principles=None, notes=None, rejected=None):
    """Ask the orchestrator whether the local reading can be trusted.

    Returns `(judgement_or_None, note)`. `None` means the orchestrator did not answer — and that is a
    **degradation, not a failure**: the caller keeps the local finding and says so. An orchestrator
    that is down must not stop the repair loop, or the platform acquires a hard dependency on an
    external service, which is the thing charter §6 forbids outright
    (「production runtime 依賴未經現行 Charter 指定的外部 AI 節點才能完成一般作答」).

    `principles`/`notes`/`rejected` go straight into `judgement_payload`, from the caller's own fold
    (see that docstring).
    """
    refusal = engines.egress_refusal(endpoint)
    if refusal:
        return None, refusal
    if audit_log:
        os.environ["QBR_EXTERNAL_AUDIT_LOG"] = audit_log
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": json.dumps(
            judgement_payload(question, finding, principles=principles, notes=notes,
                              rejected=rejected), ensure_ascii=False)},
    ]
    try:
        raw, seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens, timeout=timeout)
    except Exception as exc:  # transport shapes vary by gateway; a judging failure is never fatal
        return None, "指揮者呼叫失敗：%s" % exc
    if raw is None:
        return None, "指揮者沒有回答（%s）" % endpoint.get("name")
    judgement = parse_judgement(raw)
    if judgement is None:
        return None, "指揮者的回答不是可用的 JSON（沒有預設 verdict）"
    judgement["seconds"] = round(seconds, 3)
    judgement["model"] = endpoint.get("name")
    return judgement, ""


def crop_image_part(path):
    """The screenshot as the message part an engine accepts, or `None` when there is none.

    Base64 in a data URL, the same spelling `reread.transcribe` sends, because both go through
    `engines.ask`/`engines.body_for`: two ways to send a picture is two places for an engine to accept
    one and reject the other. A missing or unreadable file is `None` — not an exception, and not an
    empty image — so a round with no crop still gets its judgement, and the record says whether the
    second look had a picture (`look_image`).
    """
    if not path or not os.path.exists(path):
        return None
    import base64

    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode()
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}}


def review_with_orchestrator(question, finding, *, endpoint, audit_log=None,
                             timeout=180, max_tokens=1200, crop_path=None, principles=None,
                             notes=None, rejected=None, figures=None):
    """The full hand-off: triage, then look, then say what the local model got wrong.

    Returns `(record, note)`. `record` is the advisory block that goes into the finding; it always
    carries the triage verdict, and carries a second opinion when the orchestrator decided to look.

    **The second look is not a replacement.** The record keeps both readings side by side, because
    the two disagreeing is itself the information a reviewer needs — collapsing them into one
    "corrected" answer would destroy the evidence of which model said what, which is exactly the
    provenance charter §3 requires (「LLM 產生的詳解不是知識來源」；兩人說法不同時，人要能看到).

    `crop_path` is the screenshot the conclusion came from (`confirm_dispute` returns the deciding
    one, which is the neighbouring-bands picture when that read replaced the direct one), and it is
    sent as part of the second look. The system prompt has always told the orchestrator it may
    「自己看那張圖」; until this change the message it received was **text only** and `look_payload`
    had no field for a picture, so `CARE`/`DOUBT`'s 「自己看」 was a promise the code did not keep —
    measured 2026-09-25 by reading the payload it was sent.
    """
    if audit_log:
        os.environ["QBR_EXTERNAL_AUDIT_LOG"] = audit_log
    judgement, note = judge(question, finding, endpoint=endpoint, timeout=timeout,
                            max_tokens=400, principles=principles, notes=notes, rejected=rejected)
    if judgement is None:
        return {"degraded": True, "why": note}, note

    record = {"verdict": judgement["verdict"], "why": judgement["why"],
              "self_look": judgement["self_look"], "model": judgement["model"],
              "seconds": judgement["seconds"]}

    if not judgement["self_look"]:
        return record, ""

    image = crop_image_part(crop_path)
    record["look_image"] = image is not None
    text = (json.dumps(look_payload(question, finding, figures=figures), ensure_ascii=False)
            + "\n\n你已經決定自己看。請回第二個 JSON："
              '{"page_read":{"stem":"...","options":{"A":"...","B":"..."}},'
              '"disagrees_with_local":true|false,"why":"哪裡不同，一句話"}'
            + ("" if image is not None else "\n\n（這一題沒有截圖可送，請只憑上面的文字判斷。）"))
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": text if image is None else
         [{"type": "text", "text": text}, image]},
    ]
    try:
        raw, seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens,
                                   timeout=timeout)
    except Exception as exc:
        record["second_look_error"] = str(exc)
        return record, "指揮者第二眼看不出來：%s" % exc
    if raw is None:
        record["second_look_error"] = "no response"
        return record, "指揮者第二眼沒有回答"
    record["second_look_seconds"] = round(seconds, 3)
    second = engines.content_of(raw) or ""
    start, end = second.find("{"), second.rfind("}")
    if start != -1 and end != -1:
        try:
            parsed = json.loads(second[start:end + 1])
            if isinstance(parsed, dict):
                record["second_look"] = {
                    "page_read": parsed.get("page_read"),
                    "disagrees_with_local": bool(parsed.get("disagrees_with_local")),
                    "why": str(parsed.get("why") or "")[:400],
                }
        except json.JSONDecodeError:
            record["second_look_raw"] = second[:400]
    else:
        record["second_look_raw"] = second[:400]
    return record, ""
