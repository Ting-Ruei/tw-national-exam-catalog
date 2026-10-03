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
import re

from . import ai_findings, engines

#: What the orchestrator may say about the local model's reading.
VERDICTS = ("TRUST", "CARE", "DOUBT")

#: Which engine leads. The designer assigned the A 層 lane on 2026-10-01 (`dgx-flash`, serving
#: GLM-5.3-Flash-EXL3 on the DGX — LAN, not an external provider, so the egress gate stays closed
#: and no key is needed). `QBR_CONDUCTOR_ENGINE` moves it; resolved at call time, like every engine
#: address, so a resident process can be repointed without a restart.
CONDUCTOR_ENGINE = "dgx-flash"


def conductor_endpoint() -> dict:
    """The engine the second opinion is asked of, resolved now."""
    return engines.named(os.environ.get("QBR_CONDUCTOR_ENGINE", "").strip() or CONDUCTOR_ENGINE)

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
    for key, value in _fact_channels(principles, notes, rejected).items():
        # Bounded the same way the rest of the payload is: the triage call is the small one, and a
        # note that grew into an essay must not turn it into a second reading of the question.
        if value:
            payload[key] = value[:800]
    return payload


def _fact_channels(principles=None, notes=None, rejected=None) -> dict:
    """The three channels the local reader was sent and the judge must also see (owner 2026-09-25:
    「你應該參考原則 ＋ 原始題目的整個狀態 ＋ 截圖PDF找線索 ＋ 之前自己寫的 怎麼修 ＋ 人的註記或建議
    統合這些資料來解決問題」). Rendered with the same functions the local prompt uses, so the judge
    and the reader cannot be shown two different versions of the same fact."""
    return {"reviewer_note": ai_findings.notes_note(notes) if notes else None,
            "previous_repairs_refused": ai_findings.rejected_note(rejected) if rejected else None,
            "reviewer_principles": ai_findings.principles_note(principles) if principles else None}


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


EVIDENCE_INSTRUCTION = ("以下是完整證據包：抽取文字、那一題的頁面轉錄、與一張截圖（若有）。"
                        "請依同一判準回一個 JSON，不要其他文字：\n"
                        '{"verdict":"TRUST|CARE|DOUBT","why":"一句話","self_look":true|false}\n'
                        "self_look 恒 false：你已經看到證據包了，判斷就在這一條回覆裡完成。")


def judge_with_evidence(question, finding, *, endpoint, timeout=120, max_tokens=400, crop_path=None,
                        principles=None, notes=None, rejected=None, figures=None, audit_log=None):
    """The verdict contract judged **with** the evidence pack, not the diff summary alone.

    The triage-only form routes a *blocked dispute* ("can this reading be trusted — should I look?");
    its measured 2026-10-01 behaviour on the category-audit population is a rubber stamp there:
    43/43 DEFECT diffs came back TRUST, three of them justified with a made-up repair (「地端已自行
    還原為正確的化學式」 — no repair exists in the record). An audit diff is small enough to describe
    mechanically precisely when the leader never sees the actual text. So this form sends the full
    payload (`look_payload`: extracted text + page transcription + the crop when there is one) and
    keeps the same verdict vocabulary. Same parse as triage — an unusable answer degrades, never
    defaults to TRUST.
    """
    refusal = engines.egress_refusal(endpoint)
    if refusal:
        return None, refusal
    if audit_log:
        os.environ["QBR_EXTERNAL_AUDIT_LOG"] = audit_log
    image = crop_image_part(crop_path)
    payload = look_payload(question, finding, figures=figures)
    for key, value in _fact_channels(principles, notes, rejected).items():
        if value:
            payload[key] = value[:800]
    text = json.dumps(payload, ensure_ascii=False) + "\n\n" + EVIDENCE_INSTRUCTION
    if not image:
        text += "\n\n（這一題沒有找到截圖檔；判斷請基於上面的文字。）"
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": text if image is None else
         [{"type": "text", "text": text}, image]},
    ]
    try:
        raw, seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens, timeout=timeout)
    except Exception as exc:
        return None, "指揮者呼叫失敗：%s" % exc
    if raw is None:
        return None, "指揮者沒有回答（%s）" % endpoint.get("name")
    judgement = parse_judgement(raw)
    if judgement is None:
        return None, "指揮者的回答不是可用的 JSON（沒有預設 verdict）"
    judgement["seconds"] = round(seconds, 3)
    judgement["model"] = endpoint.get("name")
    judgement["evidence_look"] = True
    return judgement, ""


#: The figure-missing instruction. Same discipline `parse_judgement` states: the answer is a strict
#: JSON object; anything the pack did not show is not available to cite — here that means the refs
#: must be filenames the pack listed, and the runner re-checks that instead of trusting the text.
FIGURE_INSTRUCTION = (
    "這一題的 image_refs 為空，但磁碟上有它的裁片檔（清單在 available_crops；影像也在附件）。\n"
    "只回一個 JSON 物件：{\"decision\": \"insert\"|\"none\"|\"uncertain\", "
    "\"refs\": [檔名…], \"where\": \"要插進哪一格（一句話）\", \"basis\": \"依據（一句話）\"}。\n"
    "判定：那張圖確實屬於這一題 ⇒ insert，refs 填 **清單裡的檔名**（一或多個）；"
    "這一題沒有需要引用的圖 ⇒ none，refs 空陣列；看圖仍無法決定 ⇒ uncertain，refs 空陣列。"
    "refs 只能使用 available_crops 列出的檔名，不得自行拼造。")


def _figure_payload(question, *, crop_names, current_refs):
    """The evidence pack for a figure-ref decision, all of it deterministic except the reading."""
    payload = {
        "task": "figure_ref_missing",
        "candidate_key": question.get("candidate_key") or "",
        "question_number": question.get("question_number") or "",
        "stem": question.get("stem") or "",
        "options": ["%s：%s" % (opt.get("key"), str(opt.get("text") or "")[:200])
                    for opt in (question.get("options") or []) if isinstance(opt, dict)],
        "current_image_refs": (current_refs or [])[:400],
        "available_crops": crop_names,
    }
    return payload


def parse_figure_decision(raw) -> dict | None:
    """The figure decision, or `None`; same tolerance/strictness trade as `parse_judgement`.

    Strict where it matters: `decision` must be one of the three named states, and a decision that
    is neither insert nor none is `uncertain` — never defaulted to insert (a made-up ref that lands
    corrupts the question row; "no decision" only costs a human moment).
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
    decision = str(parsed.get("decision") or "").strip().lower()
    if decision not in ("insert", "none", "uncertain"):
        return None
    refs = parsed.get("refs") or []
    if not isinstance(refs, list):
        return None
    return {"decision": decision,
            "refs": [str(r) for r in refs if str(r).strip()],
            "where": str(parsed.get("where") or "")[:200],
            "basis": str(parsed.get("basis") or "")[:400]}


def propose_figure_refs(question, crop_names, crop_paths, *, endpoint, timeout=120,
                        max_tokens=800):
    """Decide `image_refs` for one question, with the crops themselves attached.

    The workorder-shaped agent session measured 2026-10-01 at 5–9 min/item on occamy and hung >15 min
    with a dgx brain; the deterministic scan already names the crop pattern, so the missing judgement is
    one evidence pack away. Returns `(record_or_None, note)`; `None` degrades — an unusable answer is
    recorded as such, never defaulted to insert. The runner re-checks `refs ⊆ crop_names` because the
    pack shows the model the names; a model may still invent one.
    """
    refusal = engines.egress_refusal(endpoint)
    if refusal:
        return None, refusal
    payload = _figure_payload(question, crop_names=crop_names, current_refs=[])
    text = json.dumps(payload, ensure_ascii=False) + "\n\n" + FIGURE_INSTRUCTION
    images = [part for part in (crop_image_part(path) for path in crop_paths) if part]
    content = [{"type": "text", "text": text}] + images if images else text
    if not images:
        content += "\n\n（這一題沒有可附上的裁片影像；決策請基於文字與清單。）"
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": content},
    ]
    try:
        raw, seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens, timeout=timeout)
    except Exception as exc:
        return None, "指揮者呼叫失敗：%s" % exc
    if raw is None:
        return None, "指揮者沒有回答（%s）" % endpoint.get("name")
    decision = parse_figure_decision(raw)
    if decision is None:
        return None, "指揮者的回答不是可用的 JSON（沒有預設 decision）"
    decision["seconds"] = round(seconds, 3)
    decision["model"] = endpoint.get("name")
    return decision, ""


#: 修題生產者（L2）的提示詞。鐵律：你的封鎖理由是**第一優先**、歷次退回理由依序、經驗隨行；
#: fix 是「改完後的完整文字」不是建議；依據必須指到真的看得到的證據；證據不足就明說 degraded，
#: 不硬湊——一份硬湊的草案比沒有草案貴（設計者要多按一次 ↩）。
REPAIR_INSTRUCTION = (
    "你是修題生產者。這一題被設計者封鎖（block）；`designer_block_reason` 是設計者說的錯在哪裡，"
    "**第一優先**；`previous_return_reasons` 是之前被退回的理由（依時序，不要重犯）；"
    "`subject_lessons` 是這個科目累積的經驗。\n"
    "只回一個 JSON 物件，不要有其他文字：\n"
    "{\"fix\": \"改完後的完整文字\", \"insert\": \"題幹\"|\"選項 A\"..\"選項 D\"|\"答案\", "
    "\"basis\": \"依據（紙本哪個字、哪一行或哪張圖）\"}\n"
    "規則：\n"
    "- fix 是**整欄替換後的完整內容**：insert=題幹 ⇒ fix＝改完的整句題幹（不是「把X改成Y」"
    "這種建議）；insert=選項 X ⇒ fix＝該選項改完的完整文字；insert=答案 ⇒ fix＝正確選項字母。\n"
    "- 依據必須指到**這次真的看得到的**證據（附的裁片影像或題面文字），不得編造頁碼或字。\n"
    "- 證據不足以提出整欄替換 ⇒ 回 {\"degraded\": true, \"note\": \"一句話原因\"}，不要硬湊。")


def _repair_payload(question, *, block_reason, return_reasons, lessons, crop_names):
    """The evidence pack for a text repair, all of it deterministic except the reading."""
    return {
        "task": "repair_proposal",
        "candidate_key": question.get("candidate_key") or "",
        "question_number": question.get("question_number") or "",
        "stem": question.get("stem") or "",
        "options": ["%s：%s" % (opt.get("key"), str(opt.get("text") or "")[:200])
                    for opt in (question.get("options") or []) if isinstance(opt, dict)],
        "answer": question.get("answer"),
        "current_image_refs": (question.get("image_refs") or [])[:400],
        "available_crops": crop_names,
        "designer_block_reason": block_reason or "",
        "previous_return_reasons": list(return_reasons or []),
        "subject_lessons": list(lessons or []),
    }


def _parse_json_object(raw) -> dict | None:
    """The first JSON object in the completion, or `None` (same tolerance as `parse_judgement`)."""
    text = (engines.content_of(raw) or "").strip()
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
    return parsed if isinstance(parsed, dict) else None


def parse_text_fix(raw) -> tuple[dict | None, str]:
    """`(fix/insert/basis record, "")` 或 `(None, 原因)`——沒有預設 fix，缺一欄就不可用。

    strict 的點：`insert` 必須是落點欄位（題幹／選項 A–D／答案），`fix`/`basis` 必須非空；
    模型自己說 degraded 也記為不可用——原因原樣帶回，不硬湊。
    """
    parsed = _parse_json_object(raw)
    if parsed is None:
        return None, "回答不是可用的 JSON"
    if parsed.get("degraded"):
        return None, "生產者自報 degraded：%s" % (str(parsed.get("note") or "")[:200] or "未說明")
    fix = str(parsed.get("fix") or "").strip()
    insert = str(parsed.get("insert") or "").strip()
    basis = str(parsed.get("basis") or "").strip()
    if not fix:
        return None, "fix 是空的"
    if not basis:
        return None, "basis（依據）是空的"
    if "題幹" not in insert and "stem" not in insert.lower() \
            and "答案" not in insert and not insert.lower().startswith("answer") \
            and not re.search(r"(?:選項|options?)\s*[A-D]", insert, re.IGNORECASE):
        return None, "insert 認不得（要 題幹／選項 A–D／答案）：%r" % insert[:60]
    return {"fix": fix, "insert": insert, "basis": basis[:400]}, ""


def propose_text_fix(question, *, block_reason, return_reasons, lessons, crop_paths,
                     endpoint, timeout=180, max_tokens=800):
    """Propose one whole-field repair for a blocked question — the L2 one-call producer.

    Same shape as `propose_figure_refs`: one evidence pack (question fields + the paper crop
    itself + the designer's words), one completion, `(record_or_None, note)`; `None` degrades.
    `refs ⊆ 磁碟` 的對應物是 **crop 檔必須真的存在**——由呼叫者先行過濾，這裡只收存在的路徑。
    """
    refusal = engines.egress_refusal(endpoint)
    if refusal:
        return None, refusal
    crop_names = [os.path.basename(p) for p in (crop_paths or [])]
    payload = _repair_payload(question, block_reason=block_reason, return_reasons=return_reasons,
                              lessons=lessons, crop_names=crop_names)
    text = json.dumps(payload, ensure_ascii=False) + "\n\n" + REPAIR_INSTRUCTION
    images = [part for part in (crop_image_part(path) for path in crop_paths) if part]
    content = [{"type": "text", "text": text}] + images if images else text
    if not images:
        content += "\n\n（這一題沒有可附上的裁片影像；依據請基於題面文字與清單。）"
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": content},
    ]
    try:
        raw, seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens, timeout=timeout)
    except Exception as exc:
        return None, "做事模型呼叫失敗：%s" % exc
    if raw is None:
        return None, "做事模型沒有回答（%s）" % endpoint.get("name")
    record, note = parse_text_fix(raw)
    if record is None:
        return None, note
    record["seconds"] = round(seconds, 3)
    record["model"] = endpoint.get("name")
    return record, ""
