# -*- coding: utf-8 -*-
"""What the model said was wrong with a question, and how it said to fix it.

Why this is not the triage that was already rejected
----------------------------------------------------
Task 21 asked a model to *find* which questions were broken, and it measured 12-16% precision -
one useful question in 6.3. That finding stands, and this module does not revisit it. The question
here is a different one, asked after a **person** has already said a question is wrong: *what does
the model think is wrong, and how would it be fixed?*

The difference is what makes the record worth keeping. A triage list is a guess about the future; a
finding is a note about a case that is already known to be bad. Even when the note is wrong it is
evidence of what was believed at the time, and the cases that need it most are the ones a person
cannot classify - "國考題的意外", the one-off accidents where the defect is real and the shape of it
is strange.

Why the record is append-only
-----------------------------
The same reason the human review log is. The value of the note is that it says what the model
answered *on the reading that was in front of it*, and a record that can be edited later cannot be
trusted to say that. `append` is the only writer; there is deliberately no `rewrite`, no `update`
and no `save`, so the module cannot express a change to a finding - the shape of the code is the
guarantee.

Nothing here decides anything (`GOV-05`)
---------------------------------------
A finding is advisory. It never writes a review event, never edits a question, never becomes a
rule. It is a note a person reads afterwards. The rule this project keeps relearning is that a
model's answer is worth having only when it is recorded next to the evidence, named with the model
that gave it, and checkable by whoever reads it next - which is why the record carries the exact
text the model was shown rather than a summary of it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from . import canon

#: Bumped when the record's shape changes, so an old log stays readable as an old log rather than
#: being silently reinterpreted under a new schema.
SCHEMA = "qbr_ai_finding_v0.1"

#: The vocabulary of what can be wrong. Single-sourced here rather than written again in the
#: asking script, because two copies of a code list is two places for a code to mean two things,
#: and a code the reader cannot act on is worse than no code.
CODES = {
    "SUPERSCRIPT_FLATTENED": "上下標被壓平成普通字元，公式或化學式因此讀不出層次（如 PO4 3- 應為 PO₄³⁻）",
    "OPTION_TRUNCATED": "選項被截斷，看起來只有半截（如 [Na、[K 這種沒有結尾的括號）",
    "SPACE_INSIDE_WORD": "中文詞中間多了不該有的空格（中文書寫不用空格斷詞）",
    "OPTION_MERGED_INTO_STEM": "選項內容跑進題幹，題幹結尾出現公式或選項編號",
    "MISSING_OPTION": "選項數不足四個，或選項編號跳號",
    "STEM_TRUNCATED": "題幹在句中斷掉，語意不完整",
    "GLYPH_DAMAGE": "出現不該出現的字元（亂碼、罕用異體字、簡體字、別國字母）",
    "DROP_OUT": "紙本有內容，抽取結果裡沒有（掉字、掉符號、掉整行），且位置可以指出來",
    "ANSWER_DISAGREES": "答案卷與題目對不上（答案少一題、答案與選項不符）",
    "FIGURE_MISSING": "題目要讀者看的圖不在這一題身上（圖沒被抽出來，或跨頁續接）",
    "NONE": "沒有發現異常",
}

#: Verdicts. `NOT_EXTRACTION` is first-class and is not a failure: some blocked questions are
#: disputes about the paper's own content, and saying so is more useful than inventing a defect.
#: This was measured - the last 7 unexplained blocks were pharmacokinetic calculations whose
#: extraction is correct while the PDF text layer is the truncated thing.
VERDICTS = ("DEFECT", "NOT_EXTRACTION", "OK")

#: How the question arrived in front of the model. This is **part of the measurement**, not decoration.
#:
#: The prompt was written for the loop, where every question really had been blocked by a person, so
#: it said so. Then `--all` sent the whole corpus through the same prompt and that sentence became a
#: **lie**: questions nobody had judged were described as "a human already flagged this". That is
#: worse than inaccurate, it is leading - it asserts a defect exists and asks for it to be named,
#: which is precisely how a corpus-wide pass manufactures findings, 79,090 questions at a time.
#:
#: A question that arrives because a person rejected it and one that arrives because it is next in the
#: file are different questions, and the answer means something different in each case. The prompt
#: has to say which one this is, and the version hash has to change when it does.
POPULATIONS = {
    "blocked": {
        "arrival": "使用者給你一題「已經被人類標記為有問題」的題目文字。",
        "ask": "這個題目已經被人類標記為「有問題」。請說出你認為哪裡錯了、以及該怎麼修。",
    },
    "corpus": {
        "arrival": "使用者依序給你題庫裡的題目，請你檢查抽取結果有沒有問題。",
        "ask": "這一題**沒有**被人類標記過——它是整份題庫依序送來的其中一題。"
               "請自己判斷抽取結果是否正確。**沒有發現異常是正常的答案**：這種情況 verdict 用 OK，"
               "`what` 用 NONE，不要為了有東西交而把正常的題目說成缺陷。",
    },
}

SYSTEM = """你是國考題庫抽取品管員。{arrival}
你的工作只有兩件：

1. `where`：**哪裡錯了**。指出具體位置（題幹，還是哪一個選項 A/B/C/D），並引用出問題的原文片段。
   不要說「整題看起來怪怪的」，要指出是哪幾個字。
2. `fix`：**怎麼修**。具體到「把 X 改成 Y」。若你認為不該自動修、需要人回去看紙本，
   就明說要看紙本的哪個位置，以及你預期會看到什麼。

不要回答題目的正確答案，也不要重述題目。不要客套。

【重要】有些題目的問題**不是抽取造成的**：可能是紙本本身的意外、答案爭議、或題目本身的錯。
這種情況 verdict 用 NOT_EXTRACTION，並在 `where`/`fix` 說明你的理由。
**誠實說「這不是抽取缺陷」比硬找一個缺陷有價值**，因為那會讓下一個人去修一個不存在的東西。

【重要】有些問題是**單一個案**（只出現一次的意外），不是一類。這種情況 `rule_worthy` 用 false。
這正是我們要的區分：**一類問題才值得寫規則，個案要單獨討論**，寫規則只會讓它變成誤傷別人的規則。

可能的性質代碼（`what` 只能用這些）：
{codes}

只輸出這個 JSON，不要有其他文字：
{{"verdict":"DEFECT"或"NOT_EXTRACTION"或"OK","what":"上面其中一個代碼","where":"具體位置與原文片段","fix":"具體修法，或說明為何不能自動修","rule_worthy":true或false,"confidence":0.0到1.0}}

若 verdict 是 DEFECT，`what` 不可以是 NONE。若不確定，把 confidence 設低，不要猜。"""

#: What has already been learned from earlier runs, added to the prompt when the caller asks for it.
#:
#: This is the loop's fourth step made real. The loop is: a person blocks, a model says what is wrong
#: and how to fix it, a person confirms, a rule is written - and then the *next* batch should start
#: from what was learned rather than from scratch. Without this the model rediscovers the same shapes
#: every run and the only place the knowledge lives is a human's memory.
#:
#: It is stated as things already **known or ruled out**, not as instructions to look for them, for two
#: reasons. A prompt that says "look for X" invites the model to find X whether or not it is there -
#: measured here: telling the model that empty options were picture options made it stop reporting
#: them as defects, which was right, but the same trick aimed at a shape that is *not* settled would
#: manufacture findings. And a settled shape does not need finding again: `disputes.py` already
#: detects it deterministically, which is the project's rule (detection is a script, meaning is a
#: prompt). What the model is asked to do with a settled shape is **not** report it, so the notes
#: stay about things nobody has classified yet.
LEARNED = """
【已經知道的事】以下是先前批次已經確認過、或已經被判定不是缺陷的形狀：
{learned}
若這一題的異常屬於上面任何一種，請在 `where` 簡短註明「已列為既知形狀：<代碼>」，
`rule_worthy` 用 false（已經有規則或已被排除，不需要再寫一次），
並把注意力放在**其他**異常上。若沒有其他異常，verdict 用 NOT_EXTRACTION。
"""

USER = """科目：{subject}
試卷：{paper}
題號：第 {number} 題

題幹：
{stem}

選項：
{options}

{figures}答案：{answer}

{ask}"""

#: What to tell the model about images. It matters because an option that is a picture is stored with
#: **empty text and an image reference**, and a model shown four empty options reports `MISSING_OPTION`
#: or `FIGURE_MISSING` every time - correctly, from what it can see, and uselessly, because this
#: project already decided those are figure-option questions and not defects. Measured: on
#: `105020:305:11 q052`/`q053` Splash called the empty options a defect and marked it rule-worthy.
#: The model is not being told the answer; it is being shown the same evidence a human reviewer gets,
#: which is that the option is an image.
FIGURE_NOTE = ("這一題的圖片：{count} 張（{parts}）。\n"
               "若某個選項的文字是空的，但上面說它有對應的圖片，那就是**圖片選項**，"
               "不是選項遺失——請不要把它當成缺陷。\n"
               "若題幹說「如下圖」、但這一題沒有任何圖片，那才是缺陷。\n\n")


def figures_note(question) -> str:
    """The image evidence, in the same vocabulary the reviewer's screen uses (`image_refs`)."""
    refs = question.get("image_refs") or []
    if not refs:
        return FIGURE_NOTE.format(count=0, parts="沒有任何圖片") if _mentions_figure(question) else ""
    parts = []
    for ref in refs:
        role = ref.get("asset_role") or "image"
        key = ref.get("option_key")
        parts.append("選項 %s 的圖" % key if role == "option-image" and key else role)
    return FIGURE_NOTE.format(count=len(refs), parts="、".join(parts))


def _mentions_figure(question) -> bool:
    """Whether the text refers to a figure at all.

    Only used to decide whether to say "there are no images". Saying it on every question would
    invite the model to look for a missing figure on a question that never had one.
    """
    text = question.get("stem") or ""
    return bool(re.search(r"圖|表|figure|shown below|下図", text))


def options_block(options) -> str:
    lines = []
    for option in options or []:
        letter = str(option.get("key") or "").upper()
        lines.append("%s. %s" % (letter, option.get("text") or ""))
    return "\n".join(lines)


#: How a voided answer is rendered, kept as a named thing because `prompt_version` has to be able to
#: see it change. `answer_of` is behaviour, not text, so the hash below would not notice it being
#: rewritten - and a record written before the fix would claim the same prompt generation as one
#: written after, which is exactly the confusion the version field exists to prevent. The 15
#: false-positive `ANSWER_DISAGREES` findings are what a version that could not see this change
#: cost. Bump the wording here whenever how the answer is read changes.
ANSWER_READING = "送分／不計分的題目要以「送分」呈現，不是 A、B、C、D"


def answer_of(question) -> str:
    """The answer as a person should read it, which is not always the accepted values.

    A voided question is stored with `accepted_values` of every option so that an answer comparison
    does not fail - that is the machine's view, and it is deliberately not the reader's. The
    corrections sheet said "第10題一律給分", and the reader's view is `送分`. This function used to
    prefer `accepted_values` unconditionally, so the model was shown `答案：A、B、C、D` for a
    question whose answer was voided and no indication that the list meant "everyone scores".

    Measured cost: 15 of 28 `ANSWER_DISAGREES` findings in the first corpus sweep were this - the
    model correctly observed that a single-choice question cannot have four answers, and reported a
    defect in the paper when the defect was in the prompt. The same trap `review_queue.py` documents
    on the display side, arriving on the model's side.

    `is_special_correction` is the field that says which reading applies, so it decides.

    There is a second, quieter version of the same trap: the corrections sheet writes `答Ｂ或Ｃ者均給分`
    as `B或C`, and `accepted_values` holds both letters. Shown as `答案：B、C` that reads as a
    multi-select answer, and on a single-choice paper the model correctly objects - measured at 4 of
    the 28 `ANSWER_DISAGREES` findings, and 769 questions in the corpus carry an `或` answer. The
    stored display string already has the sheet's own wording, so it is used and the ambiguity is
    spelled out.
    """
    payload = question.get("answer_payload") or {}
    accepted = [str(value) for value in (payload.get("accepted_values") or [])]
    if payload.get("is_special_correction"):
        display = str(payload.get("answer") or question.get("answer") or "送分")
        if accepted:
            return "%s（這一題不計分／全部給分；選項 %s 都算對，這不是有四個答案）" % (
                display, "、".join(accepted))
        return display
    stored = str(payload.get("answer") or question.get("answer") or "")
    # A display string carrying `或` is the sheet saying "any of these is accepted". The letters are
    # not several answers to be marked at once.
    if len(accepted) > 1 and "或" in stored:
        return "%s（更正答案：這幾個選項**任一**都算對，不是要同時選）" % stored
    if accepted:
        return "、".join(accepted)
    return stored


def subject_of(question) -> str:
    metadata = question.get("metadata") or {}
    return (metadata.get("normalized_subject_name") or metadata.get("official_subject_name")
            or "")


def category_of(question) -> str:
    """The 考別 (which profession the paper is for), the unit a rule may be scoped to.

    Recorded on every finding because the requirement is that an error and its remedy be attributed
    to where it came from: a defect confined to one 考科 (藥學(一) formulas, 臨床生理學 tables) may be
    real and frequent there while being absent elsewhere, and a fix that helps one paper's shape may
    be wrong to apply globally. Without the field, "this happens 9 times" and "this happens 9 times
    in the same paper" look the same, and those call for different rules - a scoped one and a global
    one. `disputes.py` already decides scope per rule; this is the evidence that tells it which.

    Deliberately the *normalized* name, i.e. the same string the reviewer's screen and the queue
    index use, so a report can be joined back to the queue by name rather than by a code nobody reads.
    """
    metadata = question.get("metadata") or {}
    return (metadata.get("normalized_category_name") or metadata.get("official_category_name")
            or metadata.get("group_name") or "")


def paper_of(question) -> str:
    """The paper a candidate key points at, for the record's own grouping."""
    parts = str(question.get("candidate_key") or "").split(":")
    return ":".join(parts[1:3]) if len(parts) >= 3 else ""


def reading_fingerprint(question) -> str:
    """A hash of exactly the reading the model was shown.

    This is what makes the record checkable later. A finding is a statement about one particular
    reading; if the queue is rebuilt and the reading changes, the old finding is not false, it is
    about different text, and this hash is how a reader can tell which case they are looking at.

    The stored text is hashed *unfolded*. `canon.fold` is for comparing two readings; using it here
    would erase the very differences (a Kangxi radical, a full-width letter) that a finding may be
    about.
    """
    payload = {
        "stem": question.get("stem") or "",
        "options": [[str(o.get("key") or ""), o.get("text") or ""]
                    for o in (question.get("options") or [])],
        "answer": answer_of(question),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_prompt(question, *, learned=None, population="blocked") -> tuple:
    """The system prompt and the user turn. Returned together so a record can store both.

    `learned` is optional on purpose. Omitting it is not the same as passing an empty one: a pass
    given prior findings and one that was not are different passes, and the record keeps the prompt
    verbatim so the difference survives.

    `population` says how the question got here, and it is not cosmetic - see `POPULATIONS`. The
    default is the loop's case (a person blocked it), because that is what the record format was
    built around; the corpus pass must pass `"corpus"` explicitly so it cannot inherit a claim that
    a human flagged a question nobody has looked at.
    """
    framing = POPULATIONS[population]
    codes = "\n".join("- %s：%s" % (code, desc) for code, desc in CODES.items())
    number = question.get("question_number")
    user = USER.format(subject=subject_of(question) or "（未知）",
                       paper=paper_of(question) or "（未知）",
                       number=number if number is not None else "?",
                       stem=question.get("stem") or "（空白）",
                       options=options_block(question.get("options")) or "（無）",
                       figures=figures_note(question),
                       answer=answer_of(question) or "（無）",
                       ask=framing["ask"])
    return SYSTEM.format(codes=codes, arrival=framing["arrival"]) + learned_note(learned), user


def prompt_version(population="blocked") -> str:
    """A short hash of the prompt itself, so a change to it is visible in every record.

    The loop's whole point is that the prompt is adjusted between batches, which means two records
    written an hour apart may answer slightly different questions. Storing the prompt text verbatim
    already makes that *checkable*; this makes it *visible* - a record says which prompt generation
    it belongs to, so notes from before and after a change can be compared by a field rather than by
    a diff. `disputes.py`'s detectors have the same problem and the same answer: a rule change is
    identified by a name, not by remembering when it happened.

    Deliberately hashes `LEARNED` too. The learned block changes what the model is asked to ignore,
    so two runs with different learned blocks are different prompts even though the instructions are
    identical.

    And `population`, because a pass that tells the model "a human flagged this" is not the same
    measurement as one that tells it "nobody has looked at this yet" - they even ask for different
    things. Two records that differ only in that sentence must not hash to the same version, or the
    records most likely to be compared (blocked questions vs the corpus sweep) would look like one
    consistent measurement.
    """
    framing = POPULATIONS[population]
    body = "\x00".join([SYSTEM, USER, LEARNED, framing["arrival"], framing["ask"],
                         ANSWER_READING, "\x00".join(sorted(CODES))])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def learned_note(learned) -> str:
    """The "already known" block, or nothing when there is nothing to say.

    Kept out of the prompt entirely when empty: an empty 【已經知道的事】 section would read as
    "nothing is known", which is a different claim from "this run was not given prior findings".
    """
    if not learned:
        return ""
    if isinstance(learned, str):
        lines = [learned]
    else:
        lines = ["- %s：%s" % (str(item).strip(), str(known or "").strip())
                 for item, known in sorted(learned.items())]
    return LEARNED.format(learned="\n".join(lines))


def make_record(*, question, finding, model, endpoint, prompt_system, prompt_user,
                raw="", usage=None, seconds=0.0, error=None, created_at=None, learned=None,
                population="blocked") -> dict:
    """One finding, with everything needed to check it later.

    `prompt_user` is stored verbatim and not regenerated at read time: the point of the record is
    what the model was actually shown, and a prompt rebuilt from the current code would answer a
    different question.
    """
    return {
        "schema": SCHEMA,
        "candidate_key": question.get("candidate_key"),
        "question_number": question.get("question_number"),
        "paper": paper_of(question),
        "subject": subject_of(question),
        # 考別. Part of the record because a rule that is right for 藥學(一) may be wrong globally, and
        # "this shape occurs 9 times" must be readable as "9 times in the same paper".
        "category": category_of(question),
        "reading_sha256": reading_fingerprint(question),
        "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "endpoint": endpoint,
        # Which generation of the prompt this note belongs to. The prompt is adjusted between batches
        # - that is the loop - so without this field two notes taken before and after a change look
        # like the same measurement and cannot be compared without diffing the stored text by hand.
        "prompt_version": prompt_version(population),
        # How the question reached the model: a person blocked it, or it was next in the file. A
        # finding about a blocked question and one about a random corpus question are different
        # claims with different error rates, so which population it came from is part of the record.
        "population": population,
        "learned": learned or None,
        "prompt_system": prompt_system,
        "prompt_user": prompt_user,
        # The exact text the model was shown. A reader who cannot see the evidence is trusting a
        # summary, and this project's rule is that unseen evidence is not evidence.
        "evidence": {"stem": question.get("stem") or "",
                     "options": options_block(question.get("options")),
                     "answer": answer_of(question)},
        "finding": finding,
        "raw": raw,
        "usage": usage or {},
        "seconds": seconds,
        "error": error,
    }


#: The name of the stream, inside the queue's `review-ui/` directory.
#:
#: It lives there, next to the human decision log, because that is the only place the rest of the
#: pipeline already protects. I first put it *beside* `review-ui/` so a model's note could never sit
#: next to a person's decision - and then found that the rebuild carries only named streams inside
#: `review-ui/`, and `deploy_station.sh` protects only their names, so a rebuild into a new directory
#: would have discarded every note silently. Durability beats tidiness here, and the separation that
#: actually matters (a finding is not a decision) is guaranteed by this record having no `action`
#: and no `reviewer`, not by which directory it sits in.
STREAM = "question_ai_findings.jsonl"


def store_path(queue_root: str) -> str:
    """Where findings for one queue live.

    `queue_root` may be either the queue root (holding `review-ui/`) or that directory itself, for
    the same reason `repair_loop.review_ui_dir` tolerates both: the flag is named after the queue,
    and a script that quietly wrote to the wrong directory would look exactly like "no findings yet".
    """
    if os.path.basename(os.path.normpath(queue_root)) == "review-ui":
        return os.path.join(queue_root, STREAM)
    if os.path.exists(os.path.join(queue_root, "review-ui")):
        return os.path.join(queue_root, "review-ui", STREAM)
    return os.path.join(queue_root, STREAM)


def append(path: str, record: dict) -> None:
    """Append one finding. The only writer in this module - there is no rewrite by design."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load(path: str) -> list:
    """Every finding in order. Unparseable lines are kept as `{"_unparseable": <line>}`.

    Kept rather than dropped because a corrupt line is a fact about the file; silently skipping it
    would make "the log is damaged" and "there is no finding" look identical.
    """
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"_unparseable": line})
    return records


def latest_by_question(path: str) -> dict:
    """The most recent finding per candidate key, keeping the order of first appearance."""
    latest = {}
    for record in load(path):
        key = record.get("candidate_key")
        if key:
            latest[key] = record
    return latest


def stale_questions(path: str, current_version: str, population=None) -> set:
    """Keys whose *latest* finding was written under a different prompt generation.

    Deliberately not "every record whose version differs": a question re-asked later would otherwise
    still look stale from its older record, and would be re-asked forever. What matters is which
    generation the question's **current** answer belongs to, which is what `latest_by_question` means.

    `population` restricts it to one framing: a corpus sweep in flight is not stale just because a
    fix landed, it is a different measurement on purpose. Without this, `--restale` during a running
    pass would try to re-ask all 79,090 questions.
    """
    stale = set()
    for key, record in latest_by_question(path).items():
        if population is not None and record.get("population") != population:
            continue
        if record.get("prompt_version") != current_version:
            stale.add(key)
    return stale


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, small enough to write out. Used only to recover a near-miss code."""
    if abs(len(a) - len(b)) > 1:
        return 2
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def normalize_code(value):
    """Map a reported code onto the vocabulary, tolerating case, separators and one typo.

    Measured need: the engine answered `GLYPDAMAGE` for `GLYPH_DAMAGE`, and the first version of
    `parse_finding` discarded the whole note over one missing letter. But the `what` code is only an
    index into the note - the value is `where` and `fix`.

    The typo recovery is deliberately narrow: the normalised form must be within **one** edit of
    **exactly one** vocabulary entry. If two codes are both one edit away, nothing is chosen - a
    coin flip between `STEM_TRUNCATED` and `OPTION_TRUNCATED` would label a finding wrongly, and a
    wrong label is worse than a missing one because it groups with the wrong class.
    """
    if not isinstance(value, str):
        return None
    def flatten(text):
        return "".join(ch for ch in text.upper() if ch.isalnum())
    wanted = flatten(value)
    for code in CODES:
        if flatten(code) == wanted:
            return code
    near = [code for code in CODES if _edit_distance(flatten(code), wanted) <= 1]
    return near[0] if len(near) == 1 else None


def parse_finding(content: str):
    """Pull a finding out of whatever the model wrapped its JSON in.

    The note is kept even when the code is not in the vocabulary, because the note is the point.
    What is *not* kept is a wrong code: an unrecognised one is reported as `what: None` with the
    original in `what_reported`, so nobody can mistake it for data the pipeline can act on. The
    record still says where the model thought the error was and how it would fix it - which is what
    a person reads later - and it also says that the code could not be trusted.

    Only a response with no usable JSON object at all comes back as `None`, because then there is no
    note to keep.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        # `lstrip("json")` would strip any leading j/s/o/n characters, not the word - so the
        # language tag is removed as a prefix.
        stripped = text.lstrip()
        text = stripped[4:] if stripped.startswith("json") else stripped
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        finding = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(finding, dict) or not finding:
        return None
    verdict = str(finding.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        # A code where a verdict belongs. Measured on the Splash engine (`reasoning_effort=none`):
        # it answered `"verdict":"FIGURE_MISSING","what":"FIGURE_MISSING"` and, on another question,
        # `"verdict":"FIGURE_MISSING"` with the code left out of `what` - so requiring the verdict to
        # be one of three words discarded two of three correct findings. The code's own meaning
        # supplies the verdict: `NONE` is not a defect, every other code is.
        code_from_verdict = normalize_code(finding.get("verdict"))
        if code_from_verdict is not None:
            finding["verdict_reported"] = finding.get("verdict")
            finding["verdict"] = "OK" if code_from_verdict == "NONE" else "DEFECT"
            if not finding.get("what"):
                finding["what"] = code_from_verdict
            verdict = finding["verdict"]
        else:
            # Genuinely unrecognised. It decides whether the note is a defect report at all, so the
            # note is kept but marked unclassified rather than guessed at.
            finding["verdict_reported"] = finding.get("verdict")
            finding["verdict"] = None
            verdict = None
    reported = finding.get("what")
    code = normalize_code(reported)
    if code is None and reported is not None:
        finding["what_reported"] = reported
    if code is not None:
        finding.pop("what_reported", None)
    finding["what"] = code
    # A DEFECT that says NONE (or says nothing) contradicts itself; it is treated as unclassified
    # rather than as a defect, but the note survives.
    if finding.get("verdict") == "DEFECT" and finding.get("what") in (None, "NONE"):
        finding["verdict"] = None
    return finding


def is_rule_worthy(record: dict) -> bool:
    """Whether the model judged this a class rather than a one-off.

    Read defensively: an unparsed or absent finding is not rule-worthy, because the default when
    the answer is unreadable must be "a person looks at it", not "write a rule".
    """
    finding = record.get("finding") or {}
    return finding.get("rule_worthy") is True and finding.get("verdict") == "DEFECT"


def summarize(records) -> dict:
    """Group findings so a person can see classes and one-offs apart.

    This is the shape the requirement actually needs: a rule is built for a class and a one-off is
    discussed on its own, and the two must not be counted together. A note whose verdict or code
    could not be classified is listed separately rather than dropped, because it still says where
    the model thought the problem was, and that is worth reading even when the label is missing.
    """
    classes = {}
    one_offs = []
    unclassified = []
    for record in records:
        if record.get("_unparseable"):
            continue
        finding = record.get("finding") or {}
        verdict = finding.get("verdict")
        if verdict not in VERDICTS:
            unclassified.append({"candidate_key": record.get("candidate_key"),
                                 "where": finding.get("where"), "fix": finding.get("fix"),
                                 "what_reported": finding.get("what_reported"),
                                 "verdict_reported": finding.get("verdict_reported")})
            continue
        if is_rule_worthy(record):
            entry = classes.setdefault(finding.get("what") or "UNKNOWN", [])
            entry.append(record.get("candidate_key"))
        elif verdict == "DEFECT":
            one_offs.append({"candidate_key": record.get("candidate_key"),
                             "what": finding.get("what"),
                             "where": finding.get("where"),
                             "fix": finding.get("fix")})
    return {"classes": classes, "one_offs": one_offs, "unclassified": unclassified,
            "not_extraction": [r.get("candidate_key") for r in records
                               if (r.get("finding") or {}).get("verdict") == "NOT_EXTRACTION"],
            "failed": [r.get("candidate_key") for r in records if r.get("error")]}
