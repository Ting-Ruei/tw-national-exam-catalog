# -*- coding: utf-8 -*-
"""Ask the paper itself about a question a dispute was raised on, and write down the difference.

The gap this fills
------------------

The loop up to here had two halves that did not meet. `disputes.py` measures the *stored text* and
says, for example, `⻑ 應為 長` - it knows the codepoint is in the CJK Radicals Supplement and knows
which ideograph the paper means, but it has never looked at the page. `reread.py` can crop the page
and read it back, and was written for exactly this - but it had **no caller**, so a dispute was only
ever a label a person had to go and check by hand.

That is (b) and (c) of the request: a disputed question should be **screenshotted and sent to
27B-splash to confirm**, and it should be **repairable, not merely reported**. Both fall out of
calling the module that already existed, with one rule kept:

    the model **transcribes**, this script **subtracts**.

The model is never asked whether the question is good (`reread.py` documents why: on 978 option
crops the model called 56 clipped that were byte-identical to the PDF's own objects, and the same
crop asked 131 times produced 39 self-contradictions). It is asked what the crop says, which is a
closed question with a checkable answer. The difference between that and the stored text is computed
here, character by character, by `reread.compare`.

What is written, and what is not
--------------------------------

A finding, in the same append-only stream as every other model note - same schema, same loader, same
panel in the review UI. Two extra fields carry the parts that were missing:

    crop      the queue-relative path of the screenshot the model was shown, so the reviewer can
              look at the same picture the note is about. Unseen evidence is not evidence.
    changes   `[{field, from, to}]`, the mechanical difference. A prose `fix` describes a repair;
              this is the repair.

Nothing here writes a review event and nothing here edits a question (`GOV-05`). A confirmed
difference is exactly that: confirmed. Turning it into a `correct` event is a person's action, in
the review UI, where the record of who decided it lives.

Usage
-----
    # every question carrying a dispute, confirmed against its own paper
    .venv/bin/python scripts/confirm_dispute.py --queue data/review-queues/live

    # one question, and keep the crop so it can be looked at
    .venv/bin/python scripts/confirm_dispute.py --queue data/review-queues/live \\
        --only q004 --keep-crop data/review-queues/live/review-ui/crops

    # only the disputes a specific detector raised
    .venv/bin/python scripts/confirm_dispute.py --queue ... --kind substituted-ideograph
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import ai_findings, discuss, extract, paths, reread  # noqa: E402
import ask_about_blocks  # noqa: E402
import repair_loop  # noqa: E402
from qbr.dispute_apply.withdrawals import MAX_ATTEMPTS  # noqa: E402

#: The disputes worth spending a render and an inference on.
#:
#: Deliberately NOT every kind. Where a defect can be *measured* - a dangling answer, an option count
#: that disagrees with the paper's own declaration - there is nothing for a transcription to settle,
#: and asking would replace a certainty with an impression. These kinds are the ones whose whole
#: question is "what does the page actually carry": the reader is being shown one character and the
#: dispute claims the paper prints another. That is what a crop answers.
CONFIRMABLE_KINDS = ("substituted-ideograph", "substituted-script", "lost-glyph",
                     "flattened-offset")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; candidates and crops live under its review-ui/")
    parser.add_argument("--model", default="splash", choices=sorted(ask_about_blocks.ENDPOINTS))
    parser.add_argument("--only", nargs="*", default=None,
                        help="question numbers (q004 or 4); default is every disputed question")
    parser.add_argument("--kind", nargs="*", default=None,
                        help="restrict to these dispute kinds (default: the confirmable kinds)")
    parser.add_argument("--limit", type=int, default=0, help="ask at most this many (0 = all)")
    parser.add_argument("--out", help="findings path; default <queue>/review-ui/"
                                        + ai_findings.STREAM)
    parser.add_argument("--keep-crop", metavar="DIR",
                        help="also write each screenshot here, so a person can look at the same "
                             "picture the model was shown (the queue's own crops/ is the default "
                             "place for a served queue)")
    parser.add_argument("--dpi", type=int, default=200, help="render resolution for the crop")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-tokens", type=int, default=2000,
                        help="a transcription is a few hundred tokens; the budget is headroom "
                             "against a truncated JSON, which loses the whole answer")
    parser.add_argument("--report", action="store_true",
                        help="read back the confirmations recorded so far instead of asking")
    parser.add_argument("--blocked-only", action="store_true",
                        help="only questions a person has marked `block`; the resident loop reaches "
                             "this set through --pending-only (`pending ∩ these`), so this flag is a "
                             "manual sweep over every standing block")
    parser.add_argument("--skip-confirmed", action="store_true",
                        help="skip a question whose current reading already has a confirmation, "
                             "so a 30-minute loop does not re-render what it already read")
    parser.add_argument("--principles", metavar="PATH",
                        help="the reviewer's 基本原則 stream (default: the queue's own "
                             + discuss.PRINCIPLES_STREAM + "); each active principle is added to "
                             "the transcription prompt as a constraint")
    parser.add_argument("--escalate", action="store_true",
                        help="when the page cannot settle a blocked question, ask the person back "
                             "(append an `ask` to " + discuss.REPAIR_QUESTIONS_STREAM + ")")
    parser.add_argument("--pending-only", action="store_true",
                        help="process only the questions `scan_for_repairs.py` put in pending AND a "
                             "person has marked `block` (`pending ∩ blocked`), and take each one out "
                             "of pending once it is done. This is what keeps 「掃描到」 and 「做完了」 "
                             "from being the same state: a round that finds 100 questions and "
                             "finishes 5 leaves 95 pending for the next round instead of reporting "
                             "them done. Pending keys that are not the human's blocks are drained in "
                             "the same pass - they were queued by an older gate, and a later `block` "
                             "brings a question back on its own.")
    parser.add_argument("--orchestrate", metavar="ENGINE", default=None,
                        choices=sorted(ask_about_blocks.ENDPOINTS),
                        help="optionally ask a second, explicitly selected local engine to check "
                             "the page reading; advisory only, never an accept/block decision")
    return parser.parse_args()


def paper_pdf_of(question):
    """The question paper's path on disk, from the row's own relative reference.

    Read from `question_pdf_relative` rather than searched for by name: the row already carries the
    corpus-relative path, and a second way of finding the paper is a second opinion about which paper
    a question came from - the one thing that must not differ from the queue.
    """
    metadata = question.get("metadata") or {}
    relative = metadata.get("question_pdf_relative") or metadata.get("question_pdf") or ""
    if not relative:
        return None
    asset_root = paths.asset_root()
    name = paths.CORPUS_DIR_NAME
    # The stored path is corpus-relative (`國考題資料夾/10_official_pdf/...`), and `asset_root()`
    # already ends in `國考題資料夾` in the workspace layout - so the leading component is dropped
    # when it is there, which is the same normalisation `paths` performs for its own callers.
    tail = relative[len(name) + 1:] if relative.startswith(name + "/") else relative
    candidate = os.path.join(asset_root, tail)
    return candidate if os.path.isfile(candidate) else None


def dispute_kinds(question):
    kinds = []
    for dispute in question.get("disputes") or []:
        if isinstance(dispute, dict) and dispute.get("kind"):
            kinds.append(dispute["kind"])
    return kinds


def dispute_reasons(question, wanted):
    """The dispute details the model is told about, so the transcription is aimed at the field.

    The crop shows the whole question, and the dispute already computed *where* inside it the doubt
    is (`field`, `position`, `context`). Passing that to the transcription would risk the model
    reading the answer off the hint - so it is deliberately not sent to the model. It is kept in the
    record's `evidence` instead, where a person reads it beside the note.
    """
    out = []
    for dispute in question.get("disputes") or []:
        if not isinstance(dispute, dict):
            continue
        if wanted and dispute.get("kind") not in wanted:
            continue
        out.append({key: dispute.get(key) for key in ("kind", "severity", "note", "detail")
                    if dispute.get(key) is not None})
        for extra in ("substitutions", "marks", "offsets"):
            if dispute.get(extra):
                out[-1][extra] = dispute[extra]
    return out


def blocked_keys(queue_dir):
    """The candidate keys a person has marked `block`, latest decision wins.

    The loop's work list used to be "every blocked question that no detector explains", which is
    empty by construction once a detector has fired on all of them - measured 2026-09-23: after the
    three new dispute kinds landed, all 304 blocked questions had a detector and the resident loop
    selected **nothing** on every round (`本批沒有待問的 candidate_key`). A loop whose work list is
    "what nobody can classify" empties itself the moment classification succeeds, and then reports
    silence as if it were completion.

    What remains for a *model* to do is narrower and does not vanish: read the page for the questions
    a person blocked. So the work list is "blocked", and since 2026-09-24 **the kind is not part of
    it**: 174 of the 279 blocked questions on the local mirror carry no dispute kind at all, and those
    are precisely the ones only a page read can say anything about (`disputed_questions`).

    The traversal is `repair_loop.fold_review_events`, **not** a copy of it. This function used to
    walk the file itself while `repair_loop` walked it too, and the copy is where the reviewer's
    note was lost: it kept the latest *event*, so a line like `block` with `notes: ""` - which is
    what every decision written with the note box closed looks like - hid the `comment「檢查上下標」`
    above it. One stream, one fold rule, so the work list and the note cannot disagree about what
    the person said.
    """
    standing = repair_loop.fold_review_events(
        os.path.join(queue_dir, "question_review_events.jsonl"))
    return {key for key, row in standing.items() if row["action"] == "block"}


def confirmed_keys(store_path):
    """Successful dispute reads, with the prompt input that was confirmed.

    Reading identity alone is insufficient: after a human rejects a machine repair, the candidate
    text and screenshot can be unchanged while the next prompt must include the refused change.
    Keep only the fields the selector needs, not the full finding record.

    Failed reads do not count as confirmed; a transcription must actually exist.
    """
    if not store_path or not os.path.isfile(store_path):
        return {}
    current = ai_findings.latest_by_question(store_path)
    confirmed = {}
    for key, record in current.items():
        if record.get("population") != "dispute":
            continue
        if record.get("error") or (record.get("finding") or {}).get("error"):
            continue
        if (record.get("finding") or {}).get("transcription") is None:
            continue
        confirmed[key] = {
            "reading_sha256": record.get("reading_sha256"),
            "rejected": record.get("rejected"),
            "review_context_sha256": record.get("review_context_sha256"),
        }
    return confirmed


def prompt_context_fingerprint(question, *, principles=None, answers=None, notes=None, rejected=None):
    """Hash the page-read prompt inputs that can change without changing question text.

    `prompt_version` covers the template, reviewer feedback, and rejection history. Figure ownership
    is question-specific and is added separately because the shared prompt-version hash cannot see it.
    """
    kinds = sorted(set(dispute_kinds(question)))
    learned = {"confirm_dispute": "；".join(kinds)} if kinds else None
    version = ai_findings.prompt_version(
        population="dispute", learned=learned, principles=principles, answers=answers,
        notes=notes, rejected=rejected)
    payload = {"prompt_version": version, "figures": ai_findings.figures_note(question)}
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()

def confirmation_matches_prompt(question, previous, *, principles, answers=None, notes=None,
                                rejected=None):
    """Whether a successful read still matches this question and its current prompt context."""
    return bool(
        isinstance(previous, dict)
        and previous.get("reading_sha256") == ai_findings.reading_fingerprint(question)
        and previous.get("review_context_sha256") == prompt_context_fingerprint(
            question, principles=principles, answers=answers, notes=notes, rejected=rejected)
    )


def load_prompt_inputs(queue_dir, principles_path=None):
    """Take one round snapshot of reviewer context for selection and transcription."""
    events_path = os.path.join(queue_dir, "question_review_events.jsonl")
    questions_path = os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM)
    principles_path = principles_path or os.path.join(queue_dir, discuss.PRINCIPLES_STREAM)
    principle_events = discuss.load_events(principles_path)
    return {
        "events_path": events_path,
        "questions_path": questions_path,
        "principles_path": principles_path,
        "principle_projection": discuss.principles_projection(principle_events),
        "principles": ai_findings.principles_for_prompt(principle_events),
        "answers_by_key": repair_loop.human_answers_by_key(questions_path),
        "notes_by_key": repair_loop.notes_by_key(events_path),
        "rejections_by_key": repair_loop.rejections_by_key(events_path),
    }

def disputed_questions(queue_dir, only, kinds, limit, *, blocked=None, already=None,
                       rejected_by_key=None, principles=None, answers_by_key=None,
                       notes_by_key=None, completed=None):
    """Which questions this round reads against their own paper.

    Three gates, in this order: the question number (`--only`), the caller's work list (`blocked`),
    and whether the current reading already has a confirmation (`already`).

    The **dispute kind** is not one of them when a work list was given. It used to be, and that is
    what made the loop blind to the reviewer: on the local mirror (2026-09-24) 279 questions carry a
    standing `block`, and **174 of them have no dispute kind at all**, so `wanted_kinds ∩
    dispute_kinds` was empty for exactly the questions a person had flagged by hand and no detector
    had explained. A block with no kind is not a question to skip - it is the one where the page read
    is the only evidence there is. The kind stays a gate when nobody handed us a work list (`--only`,
    `--kind`: an ad-hoc run that must not widen itself) or when the caller named kinds explicitly.
    """
    candidates = os.path.join(queue_dir, "candidates.jsonl")
    if not os.path.isfile(candidates):
        print("找不到 candidates.jsonl：%s" % queue_dir, file=sys.stderr)
        return []
    wanted_kinds = set(kinds) if kinds else set(CONFIRMABLE_KINDS)
    wanted_numbers = None
    if only:
        wanted_numbers = {str(item).lower().lstrip("q").lstrip("0") or "0" for item in only}
    # **`blocked=None` 是「不篩」，`blocked=set()` 是「一題都沒有」。**（2026-09-24 修正）
    #
    # 舊版把兩者混成同一件事：`blocked = set() if blocked is None else set(blocked)`，於是接著的
    # `if blocked and key not in blocked` 在**空集合**時為 False——不 continue，所以
    # 「只處理人已 block 的題」這條契約反過來被違反：讀不到任何 block 時，它會把**所有**爭議題
    # 當成可掃。無人值守時這是最危險的方向（它會動你沒標的題），而它只在「剛好一題 block 都沒有」
    # 時發生，所以有人看著時永遠看不到。
    #
    # 現在 `None` 與空集合分開：`None` = 呼叫者沒要篩（例如 `--only` 指名），空集合 = 篩出來
    # 真的沒有，那就回空清單。契約是 `--blocked-only`，遵守它的方式是不做事，不是做全部。
    block_filter = None if blocked is None else set(blocked)
    already = {} if already is None else already
    rows = []
    for question in repair_loop.load_candidates(candidates):
        number = str(question.get("question_number")).lstrip("0") or "0"
        if wanted_numbers is not None and number not in wanted_numbers:
            continue
        key = question.get("candidate_key")
        if block_filter is not None and key not in block_filter:
            continue
        found = wanted_kinds.intersection(dispute_kinds(question))
        # **一個被 block 的題目不需要有「可確認的爭議種類」。**（owner 2026-09-24）
        #
        # 當呼叫者給了 `blocked`（`--blocked-only` 或 `--pending-only`），那個集合**就是**工作清單：
        previous = already.get(key)
        previous_reading = previous.get("reading_sha256") if isinstance(previous, dict) else previous
        same_context = True
        if isinstance(previous, dict) and principles is not None:
            same_context = confirmation_matches_prompt(
                question, previous, principles=principles,
                answers=(answers_by_key or {}).get(key),
                notes=(notes_by_key or {}).get(key),
                rejected=(rejected_by_key or {}).get(key))
        elif isinstance(previous, dict) and rejected_by_key is not None:
            same_context = previous.get("rejected") == rejected_by_key.get(key)
        if key in already and previous_reading == ai_findings.reading_fingerprint(question) \
                and same_context:
            if completed is not None:
                completed.add(key)
            continue
        # **The judgement payload reads `question["kinds"]`, and this is where it is set.**
        #
        # Measured 2026-09-25 while moving the driver onto the on-prem engine: the judged question was
        # the raw candidate row, which carries `disputes` but no `kinds` key, so
        # `orchestrator.judgement_payload`'s `dispute_kinds` was **empty on every call this loop has
        # ever made** - and its own docstring says the dispute kinds are part of the smallest set the
        # judge needs. The kinds are computed here anyway (`dispute_kinds`, above, is the same call the
        # finding records), so the question carries them from here on rather than the judge being told
        # there were none.
        rows.append(dict(question, kinds=sorted(set(dispute_kinds(question)))))
    if limit:
        rows = rows[:limit]
    return rows


def crop_for(pdf_path, number, *, dpi, out_png=None, boxes=()):
    """The page's own view of one question: the rows its number owns, cropped and stitched.

    Uses `reread.band_rows`, which is the tested definition of "the rows this question owns" - its
    number down to the next number, across page breaks. Rendering the *rows* rather than a fixed
    region is what keeps a question whose stem continues onto the next page whole.

    `boxes` are this question's own picture boxes (`image_refs`). **They belong in the crop**, and
    leaving them out was a measured defect: on `1152_藥師(一)_藥學(一)` q42 the band's text rows on
    page 9 are the three markers `B.`/`C.`/`D.`, which sit in a 39.2→50.3 column while the option
    pictures beside them reach x 158.4 - so the model was shown an 11-point sliver, reported the
    options as blank, and answered `▢` for the fields it could not see. The owner reported the
    symptom as 「有些題目的圖片沒有截圖正確」 (2026-09-25).
    """
    kept_rows = extract.extract_cells_a(pdf_path)
    rows = reread.band_rows(kept_rows, int(number))
    if not rows:
        return None, 0, ("no-rows",)
    png = reread.crop_rows(pdf_path, rows, dpi=dpi, boxes=boxes)
    if not png:
        return None, len(rows), ("no-crop",)
    if out_png:
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        with open(out_png, "wb") as handle:
            handle.write(png)
    return png, len(rows), None


def neighbour_crop_for(pdf_path, number, *, dpi, out_png=None, kept_rows=None):
    """The bands of the question **before and after** this one, stitched, as a reference image.

    Owner 2026-09-25: 「如果截該題沒辦法找出問題，應該截上下題來參考」. A question's own band can be
    unable to answer what is wrong with it in two measured ways: the band's rows are the wrong rows
    (a number the extractor placed oddly, so the question's text is in a neighbour's band), or the
    body is a picture/table that belongs to the area shared with the neighbouring question. In both
    cases the neighbouring bands carry the missing evidence, and the alternative - which is what
    happened - was for the reading to write `▢` (「讀不出來」) or to agree, and for the machine to then
    ask the person about a crop neither of them could read.

    `band_rows(number-1)` stops at the row that starts this question and `band_rows(number+1)` starts
    at the next number, so the union cannot contain this question's own band: it is exactly the
    surroundings.
    """
    kept = extract.extract_cells_a(pdf_path) if kept_rows is None else kept_rows
    rows = []
    for neighbour in neighbour_numbers(number):
        rows.extend(reread.band_rows(kept, neighbour))
    if not rows:
        return None, 0, ("no-neighbour-rows",)
    png = reread.crop_rows(pdf_path, rows, dpi=dpi)
    if not png:
        return None, len(rows), ("no-crop",)
    if out_png:
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        with open(out_png, "wb") as handle:
            handle.write(png)
    return png, len(rows), None


def neighbour_system_note(neighbours):
    """What the second image is, said before the model reads it.

    The reference image holds the **bands of the neighbouring questions**. Two sentences carry the
    weight, and the first was corrected against the real crop: the crop is a **rectangle of the
    page**, so when the previous and next question sit above and below this one, this question is in
    the picture too - measured 2026-09-25 on `1011_醫事檢驗師_微生物學及臨床微生物學` q27, whose
    stitched q26+q28 crop rendered a region a model read as 26, 27 *and* 28. The first version said
    「這張圖不是被問的那一題本身」, which is a false claim about the input, and a model that believes
    it can talk itself out of a picture that is the best evidence it has. What has to be said instead
    is what to do with it, plus the failure that would be worse than not looking at all: counting a
    neighbour's text as this question's (the subtraction is by field, so a neighbour's line pasted
    into `stem` would look like a very confident repair).
    """
    return ("\n\n[第二張圖是參考] 這張圖是同一份紙本上第 %s 題的**前一題與後一題**的區塊。"
            "裁切是紙本上的一個矩形，所以被問的那一題自己也可能一起落在這張圖裡。"
            "它們是參考：如果被問的那一題的題幹、選項、圖或表格有一部分落在這裡，"
            "請把它補齊後再轉錄被問的那一題；**不要把其他題目的文字算進被問的那一題**"
            "（別的題目出現在圖裡，不代表它的文字屬於被問的那一題）。"
            "如果這裡沒有被問的那一題的內容，就照你原本看到的回答。"
            % ", ".join(str(value) for value in (neighbours or [])))


def transcribe_system(principles=None, answers=None, notes=None, rejected=None, figures=None,
                      neighbours=None):
    """The system prompt a page read is actually sent.

    A separate function because two places need the *same* text: `transcribe` sends it, and
    `_append_finding` stores it. The record's whole value is that a reader can see what the model was
    shown; regenerating a different prompt at record time (which is what happens when the record is
    built through `ai_findings.build_prompt` while the send used `reread.SYSTEM`) would put a prompt
    in the record that was never sent. The principles are appended here, once, so the send and the
    record cannot disagree about whether the reviewer's constraints were in force.

    `answers` is the same idea for a different stream: the reviewer's replies to this agent's own
    earlier `ask`s. It is kept separate from `principles` because they are different kinds of thing
    (a general rule vs. a statement about one question), but it is appended here for the identical
    reason — so the send and the record cannot disagree about whether the reviewer had replied.

    `notes` is the reviewer's 註解 on **this** question — the sentence they typed while blocking it,
    which used to reach nobody: this file had no `notes` reference at all, so a person could write
    「檢查上下標」 next to a blocked question and the round would ask the model to read the same crop
    with no idea what to look for. It is the one channel that says *where* the person thinks the
    problem is, so it is appended last, nearest the question.

    `rejected` is the loop's memory of this question being repaired and refused
    (`repair_loop.rejections_by_key`'s row) and is appended last of all, because it is the newest fact
    about the question: the pass is being asked to read a crop on which its own earlier reading was
    already thrown away. Without it a second round re-invents the same change — measured 2026-09-25,
    62 questions were refused twice in one day and no prompt had ever been told which change was
    refused.

    `figures` is 「這一題的圖長什麼樣子」, which the owner named as one of the five inputs that were
    not integrated (2026-09-25): 「原始題目的整個狀態(文字有沒有完整/這題的圖長什麼樣子)」. The facts
    are already measured per picture by step ⑤ (`image_refs`' `ownership`/`clipped`, written into a
    sentence by `ai_findings.figure_ref_note`) and **this** call is the one that had never read them —
    the reading is told to transcribe a crop of a question whose pictures may be clipped out of it,
    with no hint that a picture is expected there. It is appended before the reviewer's sentence so
    the person's own words stay nearest the question.

    `neighbours` switches the read to the reference image (`neighbour_crop_for`) and says so (see
    `neighbour_system_note`).
    """
    system = reread.SYSTEM
    if principles:
        system = system + ai_findings.principles_note(principles)
    if answers:
        system = system + ai_findings.answers_note(answers)
    if figures:
        system = system + figures
    if notes:
        system = system + ai_findings.notes_note(notes)
    if rejected:
        system = system + ai_findings.rejected_note(rejected)
    if neighbours:
        system = system + neighbour_system_note(neighbours)
    return system


def neighbour_instruction(number):
    """The user turn for the reference read: what to do with a picture of the neighbours."""
    return ("這是第 %s 題**上下相鄰題目**的紙本區塊（同一個矩形裁切，可能也包含第 %s 題自己）。"
            "請只轉錄第 %s 題：如果它的題幹、選項、圖或表格有一部分落在這些區塊裡（跨頁續行、"
            "圖被擺在下一題上方、表格屬於這一題），請把它補齊；如果這裡沒有第 %s 題的內容，"
            "就照你原本看到的回答。" % (number, number, number, number))


def transcribe(png, *, endpoint, max_tokens, timeout, principles=None, answers=None, notes=None,
               rejected=None, figures=None, neighbours=None, number=None):
    """Ask the model what the crop says. Returns `(seen_or_None, raw, error, usage, seconds)`.

    The engine controls come from `ask_about_blocks` rather than being written again here, because
    the thinking switch is engine-specific and a spelling the engine ignores is silent: measured on
    Splash, `chat_template_kwargs.enable_thinking` is accepted with HTTP 200 and takes 31.5s with
    612 characters of reasoning, while `reasoning_effort: none` takes 6.7s with none. Two copies of
    that table is two places for a "thinking off" run to keep thinking.

    `principles` are the reviewer's 基本原則, appended to the system prompt verbatim. They go on the
    **system** turn, not the user turn, because they are a standing constraint on how to read rather
    than part of the question - the same reason the transcription instructions are there. This is the
    integration point requirement (4) asks for: a person writes one line in the 錯題討論區, and every
    later page read by the resident loop obeys it, with no rebuild.

    `neighbours`/`number` switch this call to the reference read: the image is the neighbouring
    questions' bands and both turns say so (see `neighbour_instruction`). The question asked is the
    same one, so the answer is comparable with the first read's - which is what lets the caller keep
    whichever reading explained more of this question (`confirm_one`).
    """
    import base64

    messages = [
        {"role": "system",
         "content": transcribe_system(principles, answers, notes, rejected, figures, neighbours)},
        {"role": "user", "content": [
            {"type": "text",
             "text": neighbour_instruction(number) if neighbours else "請轉錄這張截圖。"},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(png).decode()}}]},
    ]
    _parsed, raw, complaint, usage, seconds = ask_about_blocks.ask(
        messages, endpoint=endpoint, max_tokens=max_tokens, timeout=timeout)
    seen = reread.parse(raw)
    if seen is None:
        return None, raw, complaint or "unparsed", usage, seconds
    return seen, raw, None, usage, seconds


def changes_between(question, seen):
    """The stored text vs the page, as a list of concrete substitutions.

    `reread.compare` reports *where* the two readings differ, but in the **comparison form** (NFKC,
    whitespace removed) - which is right for deciding they differ and wrong for editing, because a
    sentence that was folded cannot be pasted back over the field. So each differing field carries
    both the folded pair (what `compare` measured, and what the record's evidence shows) and the
    **raw** pair, which is what the editor needs. Only fields that differ appear, so an empty list is
    the honest answer for "the paper agrees with the extraction".
    """
    report = reread.compare({"stem": question.get("stem") or "",
                             "options": reread.options_of(question)}, seen or {})
    seen_options = reread.options_of(seen or {})
    changes = []
    stem = report.get("stem_differs")
    if stem:
        changes.append({"field": "stem", "from": stem.get("pipeline") or "",
                        "to": stem.get("page") or "",
                        "stored": question.get("stem") or "",
                        "page": (seen or {}).get("stem") or ""})
    for key in sorted((report.get("option_differs") or {})):
        differ = report["option_differs"][key]
        changes.append({"field": "option %s" % key, "from": differ.get("pipeline") or "",
                        "to": differ.get("page") or "",
                        "stored": reread.options_of(question).get(key) or "",
                        "page": seen_options.get(str(key)) or ""})
    return report, changes


def unreadable_change(change) -> bool:
    """True when the page's side of this difference is the model saying it could not read it.

    A transcription model that cannot see a field answers with `ai_findings.UNREADABLE_MARK` (「▢」)
    rather than inventing text, so a difference whose page side carries that mark is **not** a claim
    that the extraction is wrong. Measured through the served stream on 2026-09-25: **127 changes
    carried the mark and 96 of them had an empty extraction side** — the machine was proposing to
    write 「讀不出來」 into a blank field, and one of those reached the owner as an ask
    (「把 option A 從「」改成「▢」」). The applier already refuses those
    (`dispute_apply.page_read.whole_field_refusals`), but refusing them at the end means the reading
    is still reported as a defect and the person is still asked about it, which is what cost the owner
    a turn.
    """
    return ai_findings.UNREADABLE_MARK in str((change or {}).get("page") or "")


def readable_changes(changes):
    """`(kept, unreadable)`: the differences that are a claim about the page, and the ones that are
    the model saying it could not read the region.

    The unreadable ones are **kept in the record** (they are the model's own statement about what it
    saw) and taken out of the repair proposal, so the run can say 「紙本那一段讀不出來」 instead of
    「紙本是「▢」」. That state is what triggers the reference read over the neighbouring bands
    (`neighbour_crop_for`) — the owner's rule, 2026-09-25: 「如果截該題沒辦法找出問題，應該截上下題來
    參考」.
    """
    kept, unreadable = [], []
    for change in changes or ():
        (unreadable if unreadable_change(change) else kept).append(change)
    return kept, unreadable


def finding_from(changes, *, seen, error, caveat="", unreadable=()):
    """The `finding` dict, built from the mechanical diff rather than from the model's prose.

    A transcription model is not asked to judge, so it has no `verdict` to give. Synthesising one
    from the diff is the honest translation: if the page and the extraction differ, the extraction
    is defective **with respect to the page**, and the `where`/`fix` are the diff's own two sides.
    That keeps this record in the same schema as every other finding - one loader, one panel - while
    the value in it comes from a subtraction, not from an opinion.

    `caveat` is `ai_findings.figure_caveat(question)`: what this question's pictures do **not** let
    this reading conclude. It is appended because the subtraction compared **text** (stem and
    options), and the owner's 2026-09-25 report is precisely the cost of not saying so - a reading
    that said 「紙本與抽取一致」 was read as 「圖片沒問題」 for questions whose pictures had never been
    attributed to them, so the check answered a question it had not looked at the answer to.

    `unreadable` are the differences dropped by `readable_changes` (the page side was 「▢」). When
    **every** difference was one of those, the honest finding is neither 「一致」 nor 「有缺陷」: the
    page read did not cover the field. It gets its own `what` (`UNREADABLE`) so the run report counts
    it as its own class - the owner reads those counts, and 「一致」 there would be a lie.
    """
    if error is not None:
        return {"verdict": None, "what": None, "where": None, "fix": None,
                "rule_worthy": False, "confidence": None, "error": error}
    unreadable_fields = "、".join(sorted({str(change.get("field") or "?") for change in unreadable}))
    if not changes and unreadable:
        return {"verdict": None, "what": "UNREADABLE",
                "where": "紙本那一段讀不出來（截圖沒涵蓋到那一格？）：%s" % unreadable_fields,
                "fix": "不要改字：這一格要看紙本本身（或換一張含上下題的截圖）",
                "rule_worthy": False, "confidence": None, "transcription": seen}
    if not changes:
        return {"verdict": "OK", "what": "NONE", "where": "紙本與抽取一致" + caveat,
                "fix": "不需要修", "rule_worthy": False, "confidence": 0.9,
                # The reading is kept on the *agreeing* path too, and that is not decoration. Without
                # it, "the page agreed" and "the page could not be read" produce the same record
                # shape (`changes == []`, no transcription), and the resident loop - which skips a
                # question whose reading already has a confirmation - cannot tell them apart. It
                # then either re-asks forever or, worse, counts an outage as agreement (both were
                # measured on 2026-09-23). The transcription is what makes "someone looked"
                # checkable, so it travels on both paths.
                "transcription": seen}
    where = "；".join("%s：紙本是「%s」，抽取成「%s」" % (c["field"], c["to"], c["from"])
                      for c in changes)
    fix = "；".join("把 %s 從「%s」改成「%s」" % (c["field"], c["from"], c["to"])
                    for c in changes)
    if unreadable:
        # Said out loud rather than silently dropped: the run's own count of what it could not read
        # is the number that tells the owner whether the crops are covering the questions at all.
        where = where + "；另有 %d 格紙本讀不出來（▢，沒有當成改動）：%s" % (
            len(unreadable), unreadable_fields)
    return {"verdict": "DEFECT", "what": None, "where": where + caveat, "fix": fix,
            # Not rule-worthy by construction: this was raised by a detector that already exists,
            # so the class is known and what is left is one reading to fix. Saying `true` here would
            # propose writing a second rule for a shape that already has one.
            "rule_worthy": False, "confidence": 0.8,
            # The raw transcription is kept whole, because the diff is a claim *about* it.
            "transcription": seen}


def crop_output_path(crops_root, pdf_path, number, suffix="dispute"):
    """Where a question's screenshot is kept, under the crops root.

    The paper's own file name identifies the directory, the same name `_adopt_crops` uses, so a later
    rebuild finds these beside the figure crops instead of learning a second directory scheme.

    `suffix` names which screenshot it is: `dispute` is the question's own band (what the reading is
    judged on) and `neighbours` is the surrounding area it may be re-read with (`neighbour_crop_for`).
    Two files rather than one overwritten name, because the record cites both and a reader has to be
    able to open the one the conclusion came from.
    """
    paper = os.path.basename(pdf_path)[:-4]  # strip .pdf
    return os.path.join(crops_root, paper, "q%s-%s.png" % (str(number).zfill(3), suffix))


def queue_relative(path, queue_root):
    """`path` as the queue spells it, so it resolves on the laptop and in the container alike.

    The container mounts the queue at `/queue` and the laptop at wherever the queue lives; only a
    queue-relative path (`review-ui/crops/...`) resolves against both, which is the spelling figure
    crops already use. A path that cannot be expressed relative to the root is returned unchanged, so
    a caller who passed an absolute crop outside the queue still gets a path rather than an exception.
    """
    if not path or not queue_root:
        return None
    try:
        return os.path.relpath(path, queue_root)
    except ValueError:
        return path


def explained_fields(question, seen):
    """The fields of `question` a reading accounted for, by name.

    Used to compare two readings of the same question: the direct crop's and the reference crop's.
    Comparing **field names** rather than prose is the only comparison that means anything here,
    because the two answers are different transcriptions of different pictures; the question is which
    of them explains this question's fields.
    """
    if not seen:
        return set()
    _report, changes = changes_between(question, seen)
    return {str(change.get("field")) for change in changes}


def needs_reference_read(kept, error):
    """Whether this question's own read left the question unsettled.

    Three measured shapes count as unsettled (see `reference_read`): the read agreed (`kept` empty),
    it answered `UNREADABLE_MARK` for a field (also `kept` empty once `readable_changes` has taken
    those out), or its crop could not be made at all (`no-rows`/`no-crop` - the question's content is
    not where its number says it is, which is the strongest case for looking at its neighbours).

    A transport or parse failure is **not** one of them: the picture was never the problem there, and
    re-sending a different picture to an engine that just failed would spend a second call to learn
    the same thing.
    """
    if error in ("no-rows", "no-crop"):
        return True
    if error is not None:
        return False
    return not kept


def neighbour_numbers(number):
    """The questions either side of this one - the ones `neighbour_crop_for` puts in the picture.

    One definition because two readers need the same pair: `reference_read` tells the model which
    questions the second picture is of, and the round's own log says which pair was used. Two
    spellings of 「前一題與後一題」 is how the prompt and the log would come to disagree about what was
    looked at.
    """
    try:
        value = int(number)
    except (TypeError, ValueError):
        return ()
    return tuple(candidate for candidate in (value - 1, value + 1) if candidate >= 1)


def reference_read(question, *, number, pdf_path, crops_root, dpi, send, first_seen,
                   kept_rows=None):
    """Re-read this question with its **neighbouring questions' bands** in the picture.

    Owner 2026-09-25: 「有些題目的圖片沒有截圖正確，有些題目沒有圖片但是卻截別題的來貼上，AI的審核是
    截圖來找錯誤我可以理解，但是我認為如果截該題沒辦法找出問題，應該截上下題來參考」. The resident
    loop's read is one picture of one question's band; when that picture cannot settle the question -
    the band's rows are the wrong rows, the option pictures are beside text markers in a sliver the
    model cannot read (`crop_for`'s docstring has the measured 11pt case), or the content belongs to
    the area the question shares with its neighbours - then the neighbouring bands are the only other
    evidence the paper has.

    `send` is the payload the direct read was sent with (everything `transcribe` takes except the
    image and the neighbour switch), so the two reads differ in **one** thing: the picture. That is
    what makes their answers comparable.

    The reference reading **replaces** the direct one only when it explains more of this question's
    fields (`explained_fields`) - the case the owner described, where the direct crop could not find
    what the neighbours could. When both found something, the direct read is kept: it is a picture of
    the question itself, and the reference picture is context.
    """
    out_png = crop_output_path(crops_root, pdf_path, number, suffix="neighbours")
    png, rows, failure = neighbour_crop_for(pdf_path, number, dpi=dpi, out_png=out_png,
                                           kept_rows=kept_rows)
    if failure:
        return {"used": False, "failure": failure[0], "rows": rows, "crop_png": None}
    neighbours = neighbour_numbers(number)
    seen, raw, error, usage, seconds = transcribe(png, neighbours=neighbours, number=number, **send)
    explained = explained_fields(question, seen)
    first = explained_fields(question, first_seen)
    return {"used": bool(error is None and explained and not first), "seen": seen, "raw": raw,
            "error": error, "usage": usage, "seconds": seconds, "crop_png": out_png, "rows": rows,
            "system": transcribe_system(send.get("principles"), send.get("answers"),
                                        send.get("notes"), send.get("rejected"),
                                        send.get("figures"), neighbours),
            "explained": sorted(explained), "first_explained": sorted(first)}


def confirm_one(question, *, endpoint, args, crops_root, queue_root, principles=None,
                answers=None, notes=None, rejected=None):
    key = question.get("candidate_key")
    number = question.get("question_number")
    pdf_path = paper_pdf_of(question)
    record_base = {
        "candidate_key": key, "question_number": number,
        "kinds": sorted(set(dispute_kinds(question))),
        # Carried so `_append_finding` records the constraints that were actually sent, and so a
        # failed read still records under which constraints it failed.
        "principles": principles or None,
        "answers": answers or None,
        # The reviewer's 註解 on this question, on the same footing as the other two: it is part of
        # what the model was shown, so a record that cannot say whether it was there is a record
        # nobody can audit.
        "notes": notes or None,
        # 這一題的機器修復史（被退幾次、上一次被退的改動），第四個通道，理由同前三個：沒交代它
        # 是「這次沒有把被退過的事交給模型」，交代它是「模型知道不要重貼同一個改動」。失敗的讀取
        # 也要記下它是在哪一種交代下失敗的（同 `principles`／`answers`／`notes`）。
        "rejected": rejected or None,
    }
    if not pdf_path:
        return {**record_base, "error": "no-paper", "seconds": 0.0}
    figures = ai_findings.figures_note(question)
    out_png = crop_output_path(crops_root, pdf_path, number) if crops_root else None
    png, rows, failure = crop_for(pdf_path, number, dpi=args.dpi, out_png=out_png,
                                  boxes=question.get("image_refs") or ())
    send = {"endpoint": endpoint, "max_tokens": args.max_tokens, "timeout": args.timeout,
            "principles": principles, "answers": answers, "notes": notes, "rejected": rejected,
            "figures": figures}
    if failure:
        seen, raw, error, usage, seconds = None, "", failure[0], {}, 0.0
    else:
        seen, raw, error, usage, seconds = transcribe(png, **send)
    # 送出什麼，就記什麼（`transcribe_system` 的 docstring 是這條規則的根據）。參考讀取若真的取代了
    # 第一次判讀，`system` 換成它自己送出的那一段——否則紀錄會說模型看過一段它沒看過的提示詞。
    system = transcribe_system(principles, answers, notes, rejected, figures)
    report, changes = changes_between(question, seen)
    kept, unreadable = readable_changes(changes)
    reference = None
    if needs_reference_read(kept, error):
        reference = reference_read(question, number=number, pdf_path=pdf_path,
                                   crops_root=crops_root, dpi=args.dpi, send=send, first_seen=seen)
        if reference and reference.get("used"):
            seen, raw, error, usage, seconds = (reference["seen"], reference["raw"],
                                               reference["error"], reference["usage"],
                                               reference["seconds"])
            system = reference["system"]
            report, changes = changes_between(question, seen)
            kept, unreadable = readable_changes(changes)
    # 這一題的圖**有沒有量到歸屬**：機械相減只比文字（題幹與選項），所以當圖的歸屬沒量到時，
    # 「紙本與抽取一致」不可以被讀成「圖片沒問題」（業主 2026-09-25 回報的正是這個讀法）。
    finding = finding_from(kept, seen=seen, error=error,
                           caveat=ai_findings.figure_caveat(question), unreadable=unreadable)
    return {**record_base, "pdf": pdf_path, "rows": rows, "crop_png": out_png,
            "crop": queue_relative(out_png, queue_root), "seen": seen, "raw": raw,
            "usage": usage, "seconds": round(seconds, 1), "changes": changes,
            "unreadable": unreadable, "system": system, "figure_facts": figures or None,
            "report": report, "finding": finding, "error": error,
            # 參考讀取的收據：切了哪一張（`neighbour_crop`）、有沒有取代第一次判讀
            # （`read_with_neighbours`）、它多解釋了哪些欄位、以及沒切成的原因。
            "neighbour_crop_png": (reference or {}).get("crop_png"),
            # 結論是從哪一張圖來的：參考讀取取代了第一次判讀時，是鄰題那一張；否則是被問那一題
            # 自己的。指揮者要看的就是這一張（它要判斷的是同一個結論）。
            "deciding_crop_png": ((reference or {}).get("crop_png")
                                  if reference and reference.get("used") else out_png),
            "neighbour_crop": queue_relative((reference or {}).get("crop_png"), queue_root),
            "neighbour_rows": (reference or {}).get("rows"),
            "neighbour_failure": (reference or {}).get("failure"),
            "neighbour_explained": (reference or {}).get("explained"),
            "read_with_neighbours": bool(reference and reference.get("used"))}


def main() -> int:
    args = parse_args()
    queue = args.queue
    queue_dir = repair_loop.review_ui_dir(queue)
    out = args.out or ai_findings.store_path(queue)

    if args.report:
        return report(out)

    prompt_inputs = load_prompt_inputs(queue_dir, args.principles)
    events_path = prompt_inputs["events_path"]
    questions_path = prompt_inputs["questions_path"]
    principles_path = prompt_inputs["principles_path"]
    projection = prompt_inputs["principle_projection"]
    principles = prompt_inputs["principles"]
    answers_by_key = prompt_inputs["answers_by_key"]
    answer_count = sum(len(value["questions"]) for value in answers_by_key.values())
    notes_by_key = prompt_inputs["notes_by_key"]
    rejections_by_key = prompt_inputs["rejections_by_key"]
    blocked = blocked_keys(queue_dir) if args.blocked_only else None
    already = confirmed_keys(out) if args.skip_confirmed else None
    # `--pending-only` narrows to what the scan step queued, **intersected with the human's blocks**.
    #
    # It used to be `blocked = pending`, on the reasoning that pending was computed by the scan from
    # the same standing actions so intersecting would just read the same set twice. That reasoning
    # stopped being true the moment the scan's gate changed (2026-09-24): the station's state file
    # held **488 pending keys queued by the old kind-based gate**, and if the round only trusted that
    # cache, those 488 questions nobody had blocked would be sent to the model - the exact opposite of
    # 「僅針對block去看」. A cache written by a previous version of the rule is not the rule.
    #
    # So: `blocked = pending ∩ blocked_keys(queue_dir)`. The human's standing `block` is the truth,
    # pending is only a hint about what is owed. The `None` vs empty-set rule is preserved at the
    # point that matters: an empty intersection means **do nothing**, never "no filter".
    if args.pending_only:
        from qbr import scan_state

        # `queue_dir` here is the `review-ui/` subdirectory; the fingerprints and pending list live at
        # the **queue root**, one level up. Resolving that with the same function the scan uses is the
        # point: two answers for "where is this queue" is what made a scan queue 813 questions and the
        # repair pass then report an empty list.
        root = os.path.dirname(queue_dir)
        pending = set(scan_state.pending_keys(root))
        if not pending:
            print("pending 是空的——先跑 scripts/scan_for_repairs.py，或這一輪沒有新工作。")
            return 0
        blocked = pending & blocked_keys(queue_dir)
        exhausted_pending = {
            key for key in blocked
            if int((rejections_by_key.get(key) or {}).get("count") or 0) >= MAX_ATTEMPTS
        }
        blocked -= exhausted_pending
        drained = pending - blocked
        if drained:
            # **不是人 block 的 pending 在這一輪就離開 pending。**（owner 2026-09-24）
            #
            # 它們是舊閘門留下的（掃描當時把「有爭議種類」當工作），而掃描已經存過它們的指紋：
            # 留著不動的話，每一輪都會被再讀一次 pending、再被判一次「不是這個迴圈的工」，而且
            # 只要有人手動看一眼 pending 就會以為那是工作。
            #
            # 移除不等於遺失：指紋裡有**人的決定**這一項，所以人之後把其中一題標成 block 就是
            # 內容變了，那一題下一輪自己會回來。
            #
            # 一次寫完（`mark_pending`）而不是逐題 `mark_processed`：同樣的結果，一次原子寫入，
            # 不是 480 次讀-改-寫。
            scan_state.mark_pending(root, blocked)
        print("pending %d 題：人已 block 的 %d 題要讀；其餘 %d 題（非 block 或已達重試上限）移出 pending。"
              % (len(pending), len(blocked), len(drained)))
        if exhausted_pending:
            print("  已退滿 %d 次、交給人處理：%d 題" % (MAX_ATTEMPTS, len(exhausted_pending)))
        if not blocked:
            print("pending 裡沒有仍可自動處理的 block：這一輪不讀。")
            return 0
    already_confirmed_pending = set()
    questions = disputed_questions(
        queue_dir, args.only, args.kind, args.limit, blocked=blocked, already=already,
        rejected_by_key=rejections_by_key, principles=principles, answers_by_key=answers_by_key,
        notes_by_key=notes_by_key, completed=already_confirmed_pending)
    if args.pending_only and already_confirmed_pending:
        blocked -= already_confirmed_pending
        scan_state.mark_pending(root, blocked)
        print("  相同讀法與提示輸入已確認，移出 pending：%d 題" % len(already_confirmed_pending))
    if not questions:
        print("沒有符合條件的爭議題（沒有 dispute、--only 沒對上，或 --kind 不對）。")
        return 0
    endpoint = ask_about_blocks.ENDPOINTS[args.model]
    # The crops live in the queue's own `review-ui/crops/`, so a screenshot the model was shown is
    # served by the same route as every figure crop and a rebuild carries it with the queue. A caller
    # may override the place with `--keep-crop`, and then the stored path is relative to the queue
    # root so it still resolves when the queue is mounted elsewhere.
    crops_root = args.keep_crop or os.path.join(queue_dir, "crops")
    queue_root = os.path.dirname(queue_dir) if os.path.basename(queue_dir) == "review-ui" \
        else queue_dir
    print("對 %d 題看紙本並轉錄（%s @ %s）"
          % (len(questions), endpoint["name"], endpoint["url"]))
    print("寫到：%s" % out)
    print("截圖留存：%s" % crops_root)
    # The reviewer's principles are read **once per round**, not once per question: they are a
    # standing constraint, and re-reading the file per question would let a principle added mid-round
    # apply to some of the batch and not others - the same measurement claiming two prompt versions.
    # **只有人核准過的原則會進提示詞**（2026-09-24，owner 原文：「改標籤送到『AI已解決』，我才能
    # 知道有沒有改過」）。核准是原則區上的一顆按鈕，事件寫進同一條 append-only 流；兩個存取器
    # 分工：`active_principles` 是畫面要畫的（含待核准，人才知道有東西等他點頭），
    # `ai_findings.principles_for_prompt` 是提示詞唯一該讀的。兩個數字都印出來，因為
    # 「寫了 N 條」與「模型看到 N 條」現在是兩個不同的量——只印前者的話，一條剛寫好、還沒被
    # 核准的原則會**安靜地**從提示詞裡消失。
    if principles:
        print("基本原則 %d 條（已核准 %d／待核准 %d）（%s）"
              % (len(principles), projection["approved_count"], projection["pending_count"],
                 principles_path))
    else:
        print("基本原則：無已核准的（待核准 %d 條；%s）"
              % (projection["pending_count"], principles_path))
    # **The reviewer's answers to this agent's own questions, on the same footing as the principles.**
    #
    # Read once per round for the same reason the principles are: a reply written mid-round must not
    # apply to some of the batch and not others, or one measurement claims two prompt versions.
    #
    # This is the other half of the loop `repair_daemon.sh:55-59` describes ("人對 ask 的回答下一輪讀得到").
    # It was never true: only `PRINCIPLES_STREAM` was read, so an answer went into the UI and stopped
    # there. Measured 2026-09-24: the stream is empty (0 bytes), so nothing was lost — the wire was
    # just not connected. It is connected now, before the first answer makes the loss real.
    if answer_count:
        print("審題者的回答 %d 筆（%s）" % (answer_count, questions_path))
    else:
        print("審題者的回答：無（%s）" % questions_path)
    # **審題者寫在這些題目上的註解**，與基本原則、回答同一個位置、同一種讀法：一輪讀一次。
    #
    # 為什麼一輪讀一次而不是每題讀一次：與原則同一個理由——這一輪的中途寫下的註解，不該只對
    # 後面幾題生效，否則同一批量測會聲稱兩個提示詞版本。
    #
    # 為什麼是 `repair_loop.notes_by_key` 而不是自己再折一次事件流：這個檔案原本**完全沒有**讀過
    # 註解（`grep notes` 找不到任何引用），而工作清單 `blocked_keys` 自己折了一次——那次折疊只看
    # 最後一筆事件，所以 `block` 那一行帶的 `notes: ""` 蓋掉了它上面那句「檢查上下標」。一份事件流
    # 只有一個折疊規則，這裡用它第二次（第一次是工作清單），不是第二個實作。
    annotated = [question for question in questions
                 if notes_by_key.get(question.get("candidate_key"))]
    print("審題者的註解 %d 題（%s）" % (len(annotated), events_path))
    # **這些題目被機器改過、又被退過的次數與那筆被退的改動**，同一份事件流、第三個折疊讀者。
    # 一輪讀一次（同原則、回答、註解的理由：同一批量測不可以聲稱兩個提示詞版本）。
    #
    # 「被退過」是這一輪的模型最需要知道、卻從來沒被告知的一件事：第一輪它照著紙本讀、提出改動，
    # 人打回；第二輪它拿到的是**同一張截圖**與同一份材料，若不知道上次改的是什麼、為什麼被退，
    # 它只能再發明一次同一個改動（2026-09-25 量到：62 題當天被退第二次，0 題帶著這段資訊）。
    refused = [question for question in questions
               if rejections_by_key.get(question.get("candidate_key"))]
    print("機器改過又被退的題 %d 題（%s）" % (len(refused), events_path))
    print()

    confirmed = 0
    agreed = 0
    failures = 0
    unreadable = 0
    reference_looked = 0
    reference_used = 0
    escalated = 0
    degraded = 0
    triaged = {}
    for index, question in enumerate(questions, 1):
        # 這一題自己的註解。一輪一份對照表（上面讀的），每題只拿自己的那一份：模型手上是這一題的
        # 截圖，別題的註解只會讓它去找不存在的東西。
        key = question.get("candidate_key")
        note = notes_by_key.get(key)
        refused_here = rejections_by_key.get(key)
        answers_here = answers_by_key.get(key)
        result = confirm_one(question, endpoint=endpoint, args=args, crops_root=crops_root,
                             queue_root=queue_root, principles=principles, answers=answers_here,
                             notes=note, rejected=refused_here)
        kinds = ",".join(result["kinds"])
        number = str(result["question_number"])
        if result.get("error"):
            failures += 1
            print("[%d/%d] q%-4s %-28s 讀不到（%s）"
                  % (index, len(questions), number, kinds, result["error"]))
        elif result["changes"]:
            confirmed += 1
            print("[%d/%d] q%-4s %-28s 不一致 %d 處"
                  % (index, len(questions), number, kinds, len(result["changes"])))
            for change in result["changes"]:
                print("        %s：紙本「%s」→ 抽取「%s」"
                      % (change["field"], (change["to"] or "")[:40], (change["from"] or "")[:40]))
        elif result.get("unreadable"):
            unreadable += 1
            print("[%d/%d] q%-4s %-28s 紙本讀不出來 %d 格（▢，沒有當成改動）"
                  % (index, len(questions), number, kinds, len(result["unreadable"])))
        else:
            agreed += 1
            print("[%d/%d] q%-4s %-28s 紙本與抽取一致"
                  % (index, len(questions), number, kinds))
        # 「截該題沒辦法找出問題，應該截上下題來參考」（業主 2026-09-25）。這一輪有沒有用到、以及
        # 它有沒有真的改變結論，都要印出來：一個只在紀錄裡的機制，讀者不會知道它跑過。
        if result.get("neighbour_failure"):
            print("        參考讀取沒切成（%s）" % result["neighbour_failure"])
        elif result.get("neighbour_crop"):
            reference_looked += 1
            reference_used += 1 if result.get("read_with_neighbours") else 0
            print("        參考讀取（上下題 %s）：%s"
                  % (", ".join(str(v) for v in neighbour_numbers(result["question_number"])),
                     "取代了第一次判讀（多解釋 %s）" % "、".join(result.get("neighbour_explained") or [])
                     if result.get("read_with_neighbours")
                     else "沒有多解釋，維持原判讀"))

        # The orchestrator judges the reading we just made. It runs **before** `_append_finding`
        # because the finding is what gets written, and a judgement appended afterwards would be a
        # second record for one question - the exact shape this project keeps removing.
        #
        # It only runs when the local read produced something to judge: a `讀不到` has no reading,
        # and asking a second model to adjudicate a failure is not a judgement, it is a retry.
        if args.orchestrate and not result.get("error"):
            from qbr import orchestrator

            orchestration, note = orchestrator.review_with_orchestrator(
                question, result.get("finding"), endpoint=ask_about_blocks.ENDPOINTS[args.orchestrate],
                audit_log=os.path.join(queue_dir, "external_llm_calls.jsonl"),
                # 指揮者拿到的是**同一輪已經摺好的**那一份（原則／人的註解／被退過的改動）與**結論
                # 來自的那一張圖**：不在 orchestrator 裡重算，也不讓它看一張沒被採用的截圖。
                crop_path=result.get("deciding_crop_png"), principles=principles, notes=note,
                rejected=refused_here, figures=result.get("figure_facts"))
            result["orchestration"] = orchestration
            if orchestration.get("degraded"):
                degraded += 1
                print("        指揮者沒有判斷（%s）——這題的紀錄會標示未分流" % note)
            else:
                triaged[orchestration["verdict"]] = triaged.get(orchestration["verdict"], 0) + 1
                print("        指揮者：%s（%s）%s"
                      % (orchestration["verdict"], orchestration["why"][:60],
                         "→ 自己再看一眼" if orchestration.get("self_look") else "→ 放行"))
                if orchestration.get("second_look", {}).get("disagrees_with_local"):
                    print("        ⚠ 指揮者與地端不一致：%s"
                          % (orchestration["second_look"].get("why") or "")[:80])

        _append_finding(out, question, result, endpoint, args)
        # **A question leaves pending only when it is actually done.**
        #
        # `讀不到`（no paper, no crop, an engine that did not answer）is not done: the next round
        # should retry it, and taking it out of pending here would be exactly the silent loss the
        # owner described (「掃描到跟做完了是兩回事」). An advisory finding either way — the
        # difference is whether the question still owes a pass.
        if args.pending_only and not result.get("error"):
            from qbr import scan_state

            # Same resolver as the read above: `queue_dir` is `review-ui/`, the state is one level up.
            scan_state.mark_processed(os.path.dirname(queue_dir), question.get("candidate_key"))
        # The page read settled the question or it did not. When it did not - the crop could not be
        # read, or the diff is empty while the person still blocked it - the honest next move is to
        # ask the person, not to try a second opinion from the same model. This is the answer to
        # "what does the loop do with what it cannot resolve": it stops and says so, in the place the
        # person is already looking.
        if args.escalate and _needs_person(result):
            if _escalate(queue_dir, question, result, endpoint):
                escalated += 1

    print()
    print("已記錄 %d 筆：不一致 %d、一致 %d、紙本讀不出來 %d、讀不到 %d。"
          % (len(questions), confirmed, agreed, unreadable, failures))
    # 「參考讀取」跑過幾題、其中幾題真的改變了結論。兩個數字都印，因為「看了」與「看出東西」是
    # 兩件不同的事——只印前者會讓一輪看起來很有進展。
    if reference_looked:
        print("參考讀取（上下題）：切了 %d 題，其中 %d 題取代了第一次判讀。"
              % (reference_looked, reference_used))
    if args.orchestrate:
        parts = "、".join("%s %d" % (name, triaged[name]) for name in ("TRUST", "CARE", "DOUBT")
                          if triaged.get(name))
        print("指揮者（%s）：%s%s"
              % (args.orchestrate, parts or "沒有判斷",
                 "；未分流 %d" % degraded if degraded else ""))
        print("第二判讀請求完成；分流結果保留在每題 finding 中。")
    if args.escalate:
        print("反問人 %d 筆（寫進 %s）。" % (escalated, discuss.REPAIR_QUESTIONS_STREAM))
    print("以上是**讀**的結果，advisory：讀不改題目文字，也不寫人為判決"
          "（套用修復是後面獨立的一步 ③；那一步由 `apply_dispute_repairs.py` 以機器身分"
          "寫 append-only 的重審紀錄）。")
    return 1 if failures else 0


#: Why a page read that came back "一致" can still leave the question stuck. The distinction is
#: between a dispute the page can settle ("does the text match the picture") and one it cannot (a
#: question a person rejected for a reason the picture does not show - an answer they disagree with,
#: a defect in the exam itself). `confirm_dispute`'s header records why the model must not be asked
#: the open question; this recognises the moment the loop has reached it.
def _needs_person(result):
    """Whether this read leaves something only a person can settle."""
    if result.get("error"):
        return True
    # Empty diff on a blocked question: the page and the extraction agree, so the block cannot be
    # about extraction. That is exactly `NOT_EXTRACTION`, and it is the human-judgment case.
    return not result.get("changes")


def _escalate(queue_dir, question, result, endpoint):
    """Append one `ask` to the repair-question stream, unless this reading is already asked.

    Idempotent by reading, like `confirmed_keys`: the loop runs every 30 minutes forever, and the
    same unanswerable question must not spawn a new `ask` each round - the person would open the
    discussion area to 200 identical questions. The `question_id` is therefore derived from the
    candidate key and the reading, so the same reading cannot open a second question and a repaired
    question (new reading) gets asked afresh.
    """
    path = os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM)
    key = question.get("candidate_key")
    fingerprint = ai_findings.reading_fingerprint(question)
    question_id = "rq-%s-%s" % (str(key).replace("moex:", "").replace(":", "-"), fingerprint[:8])
    events = discuss.load_events(path)
    if any(str(event.get("question_id")) == question_id for event in events):
        return False
    if result.get("error"):
        reason = "紙本讀不到（%s）" % result["error"]
        ask = ("這一題我拍到了紙本，但讀不出來（%s）。請確認：這題還需要修嗎？"
               "若需要，請在討論區直接改文字；若只是紙本本身沒問題，請按重新審核。" % result["error"])
    elif result.get("unreadable"):
        # 「讀不出來」**不可以**被說成「看過是一樣的」。`▢` 現在不進 `changes`（那是判讀的修正），
        # 但它也不能等於一致——這一段就是那個分別：模型看不到那一格，所以這一題要人。
        fields = "、".join(sorted({str(change.get("field")) for change in result["unreadable"]}))
        reason = "紙本那一段讀不出來（▢）：%s" % fields
        ask = ("這一題的截圖我看不出 %s 的內容（▢ 是「我讀不出來」，不是我看到空白）。"
               "紙本上那一格如果有字或有圖，請直接在討論區告訴我它應該是什麼；"
               "如果那一格本來就是空的，請按重新審核。" % fields)
    else:
        reason = "紙本與抽取一致，人仍阻擋"
        ask = ("這一題的紙本與抽取文字看過是一樣的（我已核對過截圖），"
               "但你之前把它阻擋了。這表示問題不在抽取——請告訴我是什麼："
               "答案有爭議、題目本身有錯、還是紙本以外的原因？你的回答會在下一輪讀到。")
    discuss.append_event(path, {
        "action": "ask", "question_id": question_id, "candidate_key": key,
        "question": ask, "reason": reason,
        "model": endpoint["name"], "endpoint": endpoint["url"],
        "reviewer": "repair_agent",
    })
    print("        → 反問人：%s" % reason)
    return True


def _file_size(path):
    """The size of an artefact the record cites, or `None` when it is not on disk.

    `None` rather than `0`: a missing crop and a zero-length crop are different facts, and the
    record's `crop_bytes` is read as 「模型看過的那張圖有多大」 — a 0 there would claim a picture
    existed.
    """
    return os.path.getsize(path) if path and os.path.exists(path) else None


def _append_finding(out, question, result, endpoint, args):
    """One finding, in the shared schema, carrying the crop and the mechanical diff.

    A result with no `finding` is still recorded. `讀不到` (no rows, no paper, an engine that did not
    answer) is the most useful kind of record there is — it is the set of questions the loop cannot
    finish, and the round's own pending logic keeps them queued precisely because of it. Crashing here
    would take the whole round down on the first unreadable question, which is worse than the gap it
    was hiding: measured on `lost-glyph`, the first such question aborted the run with `KeyError`.
    """
    kinds = result.get("kinds") or []
    learned = {"confirm_dispute": "；".join(kinds)} if kinds else None
    finding = result.get("finding") or {}
    principles = result.get("principles") or None
    answers = result.get("answers") or None
    notes = result.get("notes") or None
    rejected = result.get("rejected") or None
    # The stored prompt is the one that was **sent** (`transcribe_system`), not one rebuilt from the
    # finding template. Storing a prompt the model never saw is exactly the kind of record this
    # project treats as no record at all. `confirm_one` hands back the system prompt of the read the
    # conclusion came from (the direct one, or the reference read over the neighbouring bands when
    # that replaced it), so the two cannot disagree.
    system = result.get("system") or transcribe_system(principles, answers, notes, rejected,
                                                       result.get("figure_facts"))
    _template_system, user = ai_findings.build_prompt(question, learned=learned,
                                                      population="dispute", principles=principles,
                                                      notes=notes, rejected=rejected)
    record = ai_findings.make_record(
        question=question, finding=finding, model=endpoint["name"], endpoint=endpoint["url"],
        prompt_system=system, prompt_user=user, raw=result.get("raw") or "",
        usage=result.get("usage") or {}, seconds=result.get("seconds") or 0.0,
        error=None if result.get("error") is None else result["error"],
        learned=learned, population="dispute", crop=result.get("crop"),
        changes=result.get("changes"), principles=principles, answers=answers, notes=notes,
        rejected=rejected)
    record["review_context_sha256"] = prompt_context_fingerprint(
        question, principles=principles, answers=answers, notes=notes, rejected=rejected)
    # The dispute itself, and the crop's size, are the evidence this note is about. Kept beside the
    # note rather than inside it, because a reader has to be able to see the claim and the picture.
    record["evidence"] = {**(record.get("evidence") or {}),
                          "disputes": dispute_reasons(question, []),
                          "rows": result.get("rows"),
                          "unreadable": result.get("unreadable") or [],
                          "read_with_neighbours": bool(result.get("read_with_neighbours")),
                          "neighbour_crop": result.get("neighbour_crop"),
                          "neighbour_bytes": _file_size(result.get("neighbour_crop_png")),
                          "crop_bytes": _file_size(result.get("crop_png"))}
    # The orchestrator's judgement, when there is one. It is stored beside the local finding rather
    # than folded into it: the two disagreeing is the information a reviewer needs, and collapsing
    # them would destroy which model said what (charter §3 — provenance).
    #
    # A degraded judgement (`degraded: True`) is stored too. "The orchestrator was unreachable" is a
    # fact about this record, and a record that cannot say whether it was triaged is a record whose
    # trustworthiness nobody can audit.
    orchestration = result.get("orchestration")
    if orchestration:
        record["orchestration"] = orchestration
    ai_findings.append(out, record)


def report(path):
    records = ai_findings.load(path)
    confirms = [r for r in records if r.get("population") == "dispute"]
    if not confirms:
        print("這個紀錄裡還沒有 dispute 確認（population=dispute）。")
        return 0
    by_kind = {}
    agreed = 0
    changed = 0
    failed = 0
    unreadable = 0
    for record in confirms:
        finding = record.get("finding") or {}
        for kind in (record.get("evidence") or {}).get("disputes", [{}]):
            by_kind.setdefault(kind.get("kind") or "?", 0)
            by_kind[kind.get("kind") or "?"] += 1
        if record.get("error"):
            failed += 1
        elif record.get("changes"):
            changed += 1
        elif finding.get("what") == "UNREADABLE":
            # 紙本那一格讀不出來（▢）：既不是「不一致」，也不是「一致」。舊紀錄不會落到這一類
            # （那時候 `▢` 還留在 `changes` 裡，會被算成不一致），所以這個數字只會從現在起算。
            unreadable += 1
        else:
            agreed += 1
    print("爭議確認 %d 筆：不一致 %d、一致 %d、紙本讀不出來 %d、讀不到 %d"
          % (len(confirms), changed, agreed, unreadable, failed))
    print()
    for kind, count in sorted(by_kind.items(), key=lambda item: -item[1]):
        rows = [r for r in confirms
                if any((d or {}).get("kind") == kind
                       for d in (r.get("evidence") or {}).get("disputes", []))]
        differ = sum(1 for r in rows if r.get("changes"))
        print("  %-24s %4d 題，其中紙本與抽取不一致 %d 題" % (kind, count, differ))
    print()
    for record in confirms:
        finding = record.get("finding") or {}
        if finding.get("verdict") != "DEFECT":
            continue
        print("  %s  [%s]" % (record.get("candidate_key"), record.get("model")))
        print("      哪裡：%s" % (finding.get("where") or "（沒說）"))
        print("      怎麼修：%s" % (finding.get("fix") or "（沒說）"))
        if record.get("crop"):
            print("      截圖：%s" % record["crop"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
