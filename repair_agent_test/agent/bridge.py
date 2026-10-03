#!/usr/bin/env python3
"""Pi agent 的判讀器橋接：把「一題」完整取出來，交給地端模型看紙本。

**這個檔是 Pi 的工具，不是 agent 本身。** agent 的本體是
`repair_agent_test/agent/agent.mjs`（Pi SDK）；本檔只提供 Pi 用 `bash` 呼叫的四個動作。
（詳見 `skills/design-repair-agent/references/pi-agent-design.md` §1。）

為什麼要有這一支：Pi 的 `read` 工具只能讀文字檔，**看不到 PDF、也不會裁圖**。
判讀一題需要三件事——題目本身的完整內容、這一題自己的紙本裁切、把裁切送給地端模型——
三件都在既有的 Python 裡，只是散在不同檔。本檔把它們收成一個有 JSON 輸出的入口，
讓 Pi 不必自己拼路徑、不必自己猜端點。

    question  --key KEY              一題的完整內容（題幹／ABCD／答案／圖／人做過什麼）
    crop      --key KEY [--out PNG]  這一題自己的紙本裁切（含它自己的圖框）
    read      --key KEY [--engine E] [--out PNG] [--no-image]  看紙本並回傳逐字判讀
    feedback  --key KEY --rating up|down --reason TEXT          把判讀寫進學習語料

**沙盒紀律**：`feedback` 寫的是 `agent/store/agent_feedback.jsonl`（本層自己的檔），
**永不碰 `question_review_events.jsonl`**，也不冒充人類審核者。schema 刻意與
`review_state.append_ai_feedback` 相同，所以將來要「取代」成正式流時是換一行，不是改格式。

**量測紀律**：`crop` 一定把這一題自己的 `image_refs` box 一起交給 `crop_for`。
不交的話模型會看到一條細縫，然後很誠實地說「選項是空的」——那個「讀不出來」是裁切的錯，
不是模型的錯（`confirm_dispute.crop_for` 的 docstring 記了同一個缺陷，2026-09-25，`1152_藥師(一)` q42）。
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.dirname(HERE)
CATALOG = os.path.dirname(SANDBOX)
QBR = os.path.join(CATALOG, "qbr")

for path in (os.path.join(QBR, "src"), os.path.join(QBR, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

# `lib/` holds this sandbox's own helpers — `platform_view` renders a question the way the platform
# will, reading the platform's allowlist from its source. Imported by path so the bridge can be run
# from any directory (it is invoked as a subprocess by `lib/tools.mjs`).
if os.path.join(HERE, "lib") not in sys.path:
    sys.path.insert(0, os.path.join(HERE, "lib"))

import platform_view  # noqa: E402
import queue_index  # noqa: E402

# The closed set naming "a machine" — imported from **review_ui's own constants**, never copied:
# two copies of the prefix list is two places they can drift.
from qbr.review_ui.constants import REPAIR_REVIEWER_PREFIXES  # noqa: E402

# `image_refs[].path` is relative to the **live queue root** — `review-ui/crops/...` — which is the
# parent of the directory holding `candidates.jsonl`, not that directory itself. Measured: joining
# against `QUEUE` gives `.../review-ui/review-ui/crops/...` and matches nothing.
#
# The root follows the **station's own layout contract**: the laptop keeps the queue under
# `qbr/data/review-queues/live` in the repo; the station keeps it at `~/qbr-review/queue` and its
# code tree deliberately carries **no** `qbr/data/` (deploy excludes it). The sandbox is deployed
# to the station like the review UI is, so it needs the same one-variable handoff: start it with
# `REPAIR_AGENT_QUEUE=~/qbr-review/queue` and everything here — candidates, ledger, crops allowlist
# (server reads `bridge.QUEUE_ROOT`) — resolves against the station's queue, not a missing path.
QUEUE_ROOT = os.environ.get("REPAIR_AGENT_QUEUE") \
    or os.path.join(QBR, "data", "review-queues", "live")
QUEUE = os.path.join(QUEUE_ROOT, "review-ui")
CANDIDATES = os.path.join(QUEUE, "candidates.jsonl")
STORE_DIR = os.environ.get("REPAIR_AGENT_STORE") or os.path.join(SANDBOX, "agent", "store")
# `REPAIR_AGENT_STORE` redirects the whole sandbox store, for tests. Nothing else should set it.
# `STORE` is derived from `STORE_DIR`, not spelled out again: it used to be a second, independent
# path, so a test that set the variable still appended its rows to the **real** learning stream
# (measured 2026-09-29: a redirected smoke run grew `store/agent_feedback.jsonl` by one line).
STORE = os.path.join(STORE_DIR, "agent_feedback.jsonl")
#: A repair **draft** is not a judgement and not a change: it is the agent's own proposal, written
#: to its own file so the judge (a person, 2.5) reads proposals from one append-only place whose
#: every row keeps its provenance — and so no batch of drafts pretends to be an approval.
DRAFTS = os.path.join(STORE_DIR, "repair_drafts.jsonl")
DEFAULT_ENGINE = "occamy-6bit"

# --- the byte-offset index ---------------------------------------------------------------
#
# Measured before it was written (2026-09-29, this laptop's queue): `/api/question` 0.86 s and
# `/api/browse` 3.38 s. `lib/queue_index.py` records where the time went — decoding 708 MB of
# findings into `str` once per request, and one 15 KB file open per candidate row in the browse
# list. The index answers both from offsets instead.
#
# It is an accelerator, never an authority: any key it does not hold falls back to the streaming
# read below, so a partial tail line or a reshaped record costs time and cannot change an answer.
# `REPAIR_AGENT_NO_INDEX=1` turns it off, and `test_agent.mjs` compares the two paths on the real
# files rather than trusting that claim.
USE_INDEX = os.environ.get("REPAIR_AGENT_NO_INDEX") not in ("1", "true")
_INDEXED: dict[tuple, queue_index.Index] = {}


def indexed(path: str, field: str = "candidate_key",
            limit: int | None = queue_index.PREFIX_BYTES) -> queue_index.Index | None:
    """The index for `path`, held in memory and rebuilt when the file moves under it.

    The in-memory memo is the point, not a nicety: re-reading the cache file would parse a 7 MB
    JSON document on every request, which measured at 0.06 s — more than the lookup it saves. The
    guard is checked on every call (`stat`, microseconds), so a rewritten queue is noticed rather
    than served from a stale copy.
    """
    if not USE_INDEX or not os.path.exists(path):
        return None
    memo = (path, field, limit)
    index = _INDEXED.get(memo)
    try:
        if index is not None and index.guard == queue_index.guard_of(path):
            return index
        index = queue_index.load_or_build(path, STORE_DIR, field, limit)
    except OSError:
        return None  # an unwritable cache directory is not a reason to answer slowly *or* wrongly
    _INDEXED[memo] = index
    return index


# The three dispositions the designer can write, and the agent can write.
#
# `up` / `down` are the production ratings (`review_ui.constants.AI_FEEDBACK_RATINGS`).
# **`hold` is the sandbox's third one, and it is not a rating**: it means "keep this note, I am not
# deciding yet". It exists because the designer's judgement is usually an *annotation* — a sentence
# about where the problem is — and forcing that into up/down made him choose between saying
# something untrue and saying nothing (designer, 2026-09-28).
#
# Declared here, not in `server.py`, because both the bridge CLI and the UI server write this
# stream; two lists of allowed values is two places they can disagree.
#
# Promoting `hold` into production is a **change of destination, not of format** — the same promise
# the record shape makes below. It costs one entry in `AI_FEEDBACK_RATINGS`, and that entry is
# deliberately not added yet: production ratings feed a reviewed pipeline, and widening it is a
# separate, reviewed decision.
JUDGEMENT_RATINGS = ("up", "down", "hold")


class QuestionNotFound(LookupError):
    """The requested candidate key is not in the queue.

    A real exception rather than `SystemExit`, because the same functions are called by the CLI
    **and** by the UI server. `SystemExit` in a server request thread kills the thread and the
    browser sees a dropped connection instead of "no question with key X" — the failure mode where
    a wrong key looks like a broken server.
    """


def _die(message: str, code: int = 1):
    json.dump({"error": message}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    raise SystemExit(code)


def load_question(key: str) -> dict:
    """One candidate by key. Streams the JSONL: 199 MB does not belong in memory.

    The streaming read stays, and it is the fallback for every key the byte-offset index does not
    hold — including a `canonical_question_key` (the index is built on `candidate_key`), a line
    whose key sits past the indexed prefix, and a key written into the file since the index was
    cached. Falling back is how "the index is not the authority" is true in code rather than in a
    comment.
    """
    if not os.path.exists(CANDIDATES):
        raise QuestionNotFound(
            "candidates.jsonl not found at %s; run scripts/sync_from_station.sh first" % CANDIDATES)
    index = indexed(CANDIDATES)
    if index is not None:
        for row in index.rows(key):
            if row.get("candidate_key") == key or row.get("canonical_question_key") == key:
                return row
    with open(CANDIDATES, encoding="utf-8") as handle:
        for line in handle:
            if key not in line:
                continue
            row = json.loads(line)
            if row.get("candidate_key") == key or row.get("canonical_question_key") == key:
                return row
    raise QuestionNotFound("no question with key %r" % key)


def questions_of_paper(question: dict, limit: int = 0) -> list:
    """Every question in the same paper, in order. Used by the agent to see neighbours.

    Keyed on the paper rather than the question because a defect is often only visible
    next door: a stem that continues onto the next page, or a figure that was assigned
    to the neighbouring question. Measuring one question in isolation cannot see either.
    """
    metadata = question.get("metadata") or {}
    paper = metadata.get("question_pdf_relative")
    if not paper:
        return []
    found = []
    index = indexed(CANDIDATES, "question_pdf_relative", limit=None)
    if index is not None and index.unkeyed == 0:
        # `unkeyed == 0` is required, not cosmetic: the paper index is the one that can *drop a
        # question* rather than merely be slow, and a line it could not read is a question the paper
        # view would not show. If even one line is unreadable the whole index is abandoned for this
        # call and the streaming read below is used instead.
        found = [row for row in index.rows(paper)
                 if (row.get("metadata") or {}).get("question_pdf_relative") == paper]
        found.sort(key=lambda r: (r.get("question_number") or 0,
                                  r.get("question_number_occurrence") or 0))
        return found[:limit] if limit else found
    with open(CANDIDATES, encoding="utf-8") as handle:
        for line in handle:
            if paper not in line:
                continue
            row = json.loads(line)
            if (row.get("metadata") or {}).get("question_pdf_relative") == paper:
                found.append(row)
    found.sort(key=lambda r: (r.get("question_number") or 0, r.get("question_number_occurrence") or 0))
    return found[:limit] if limit else found


def human_events(key: str) -> list:
    """This question's human review events, if the queue has them.

    Read-only and never written back. Kept in the view because the agent is asked to
    explain a *disagreement*: a question the person blocked, corrected, or commented on
    is where the agent's judgement is worth the most, and the person's own sentence is
    the only channel that says where they thought the problem was.
    """
    path = os.path.join(QUEUE, "question_review_events.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if key not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("candidate_key") == key or row.get("canonical_question_key") == key:
                # The ledger carries two authorships (human `local` rows + machine repair rows).
                # Label at the boundary with **v2's own closed prefix set**, so the UI never keeps
                # a second copy of the list. Mislabel measured 2026-09-30: the designer's own
                # screenshot showed a machine `reset_review` row wearing his name (設計者（v2）) —
                # the display form of the "機器冒充人類審核者" failure the governance rule forbids.
                reviewer = str(row.get("reviewer") or "")
                row = dict(row)
                row["machine_reviewer"] = any(
                    reviewer.startswith(prefix) for prefix in REPAIR_REVIEWER_PREFIXES)
                out.append(row)
    return out


def human_event_keys() -> set:
    """Every key that carries a human review event — one read for a whole paper.

    `human_events(key)` is the right call for one question (14 MB, 20 ms). The paper view asked it
    for every question in the paper and paid the file read again each time, measured 2026-09-29 at
    1.86 s for `/api/queue`. The membership test is built from **both** key fields, exactly as
    `human_events` matches them, so "touched" means the same thing either way.
    """
    path = os.path.join(QUEUE, "question_review_events.jsonl")
    if not os.path.exists(path):
        return set()
    keys = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("candidate_key"):
                keys.add(row["candidate_key"])
            if row.get("canonical_question_key"):
                keys.add(row["canonical_question_key"])
    return keys


# The pipeline's own AI-findings stream — 707 MB, 108,181 lines, written by the repair loop.
#
# This is the file that actually holds **what the model was asked and what it produced**: each record
# carries `prompt_system`, `prompt_user`, the parsed `finding`, the `raw` completion and the model
# name. The designer asked for exactly this (2026-09-28: 「我可以看到 jsonl 讓 AI 看到或是產生的
# 內容到底是什麼，才有比較的依據」) and **neither console shows it** — v2 reads only the parsed
# `finding` (verdict/where/fix), and this sandbox reads only its own `agent_feedback.jsonl`.
#
# Read by substring pre-filter, the same shape as `prior_judgements`: the file is 707 MB and parsing
# every line per request would make the page unusable. A candidate_key is long enough that a false
# positive needs the key to appear inside another question's prompt text, which cannot happen — the
# key is this question's own identifier.
AI_FINDINGS = os.path.join(QUEUE, "question_ai_findings.jsonl")


def ai_findings(key: str, limit: int = 20) -> list:
    """The pipeline's records for one question, oldest first.

    **All** of them, not the newest: the loop runs a question more than once when a repair comes
    back, and the sequence of readings is the evidence. The designer's words: 「才有比較的依據」 —
    one reading has nothing to compare against.
    """
    if not key or not os.path.exists(AI_FINDINGS):
        return []
    out = []
    index = indexed(AI_FINDINGS)
    if index is not None:
        for row in index.rows(key):
            if row.get("candidate_key") != key:
                continue
            out.append(row)
            if len(out) >= limit:
                break
        if out or index.has(key):
            return out
    with open(AI_FINDINGS, encoding="utf-8") as handle:
        for line in handle:
            if key not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("candidate_key") != key:
                continue
            out.append(row)
            if len(out) >= limit:
                break
    return out


def prior_judgements(key: str) -> list:
    """Judgements already made on this question, by the designer or by an earlier agent run.

    **This is the channel the designer's guidance travels on.** The UI writes to
    `agent_feedback.jsonl`, and without reading it back here the design (第八輪) would be broken in
    the one place that matters: the designer types why a question is wrong, the record lands in a
    file, and the next agent run — the whole point of the loop — never sees the sentence.

    Read from the sandbox store, not from the human review stream. Those are different authorities:
    `question_review_events.jsonl` is a reviewer's decision and is read-only to this agent;
    `agent_feedback.jsonl` is guidance about the model's output. Mixing them would make "who
    decided this question is acceptable" unanswerable.
    """
    if not os.path.exists(STORE):
        return []
    out = []
    with open(STORE, encoding="utf-8") as handle:
        for line in handle:
            if key not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("candidate_key") == key:
                out.append(row)
    return out


def question_answer_display(question: dict) -> str:
    """The answer as a reader should see it, via the pipeline's own normaliser.

    Delegation, not reimplementation. `ai_findings.answer_of` is the function that already knows
    the three traps: a **voided** question (`送分`) whose `accepted_values` is every option and
    which must not be shown as four answers; a **correction** answer (`B或C`); and the ordinary
    letter. Its docstring records the measured cost of getting this wrong on the model's side
    (15 of 28 `ANSWER_DISAGREES` findings were the prompt's fault, not the paper's).

    Falls back only if the import is unavailable, because a UI that shows nothing is worse than one
    that shows the raw string — but the fallback is deliberately the *stored* string, which is
    already the sheet's own wording, never an attempt to parse it here.
    """
    try:
        from qbr import ai_findings
    except Exception:  # noqa: BLE001 - the UI must still render without the pipeline
        return str(question.get("answer") or "")
    return ai_findings.answer_of(question) or str(question.get("answer") or "")


def question_answer_keys(question: dict) -> list:
    """The option letters to highlight.

    `answer_payload.accepted_values` is the machine's list and the authority for this: it is a
    real list on all 79,090 questions (measured), it holds both letters for `B或C`, and for a
    voided question it holds every option — which is correct, since all of them score. Using the
    parsed *display* string instead would highlight nothing for `送分`, and the green mark is the
    one thing the reader checks first.
    """
    payload = question.get("answer_payload") or {}
    accepted = payload.get("accepted_values")
    if isinstance(accepted, list):
        values = [str(value).strip().upper() for value in accepted if str(value).strip()]
        if values:
            return values
    # No payload (older queue builds): fall back to the letters present in the stored answer. This
    # is a fallback for one shape, not a second parser for the normal case.
    return [ch for ch in str(question.get("answer") or "").upper() if ch in "ABCD"]


def figure_asset_path(ref: dict) -> str | None:
    """Where this question's own crop file actually is, or `None` if it is not on disk.

    `image_refs[].path` is **relative to the queue root**, not to the repository and not to
    `國考題資料夾/` (which is what `qbr.review_ui.paths.safe_file_path` resolves against — measured:
    it returns `國考題資料夾/review-ui/crops/...`, which does not exist). Handing the relative
    string to a reader is not enough either: on 2026-09-28 the agent was given the path, could not
    open it as written, and spent two `bash` calls running `find /` to locate its own crop. The run
    still finished correctly, which is exactly why this is worth fixing: the failure was invisible
    in the answer and visible only in the tool trace.

    `exists` in the reference is a claim recorded when the queue was built; this checks the file
    now, because the crop directory can be re-synced between build and read.
    """
    relative = ref.get("path")
    if not relative:
        return None
    candidate = relative if os.path.isabs(relative) else os.path.join(QUEUE_ROOT, relative)
    return candidate if os.path.exists(candidate) else None


def question_view(question: dict) -> dict:
    """The complete question, not the fields that happen to have been touched.

    The design (designer, 2026-09-27) is explicit that each question must be read in full
    —「每一題都要讀取完整，不能只讀這個不讀那個」— because a view that only expands the
    changed fields cannot show a defect in an untouched field.
    """
    metadata = question.get("metadata") or {}
    refs = question.get("image_refs") or []
    return {
        "candidate_key": question.get("candidate_key"),
        "question_number": question.get("question_number"),
        "question_type": question.get("question_type"),
        "group_ref": question.get("group_ref"),
        "group_size": question.get("group_size"),
        "quality_status": question.get("quality_status"),
        "subject": metadata.get("normalized_subject_name") or metadata.get("official_subject_name"),
        "category": metadata.get("official_category_name"),
        "year": metadata.get("year"),
        "exam_ordinal": metadata.get("exam_ordinal"),
        "question_pdf_relative": metadata.get("question_pdf_relative"),
        "answer_pdf_relative": metadata.get("answer_pdf_relative"),
        "stem": question.get("stem"),
        "shared_stem": question.get("shared_stem"),
        "stem_markup": question.get("stem_markup"),
        "stem_image": question.get("stem_image"),
        "subitem_legend": question.get("subitem_legend"),
        "options": question.get("options") or [],
        # **How the platform will show this question to a learner.**
        #
        # The designer's requirement (2026-09-28): 「這題在考題平台會是以什麼方式被我看到，
        # 所以斜體、上下標都應該是直接呈現出來（我不應該看到 <sup> 這種東西）」.
        #
        # `stem`/`options` above are the storage form. These are the **rendered** form, produced by
        # `lib/platform_view.py`, which reads the platform's own allowlist out of
        # `platform-app/frontend-next/lib/sanitize.ts` rather than keeping a copy. A reviewer has to
        # see the question that ships; showing `<sub>` is showing the storage format, which is why
        # the rendered pair is carried alongside rather than replacing the raw one — the raw one is
        # what the machine actually recorded, and the designer asked for both views.
        "stem_html": platform_view.as_platform_html(question.get("stem")),
        "shared_stem_html": platform_view.as_platform_html(question.get("shared_stem")),
        "options_html": [
            {
                "key": option.get("key") if isinstance(option, dict) else None,
                "text_html": platform_view.as_platform_html(
                    option.get("text") if isinstance(option, dict) else option),
            }
            for option in (question.get("options") or [])
        ],
        "answer": question.get("answer"),
        "answer_payload": question.get("answer_payload"),
        # The answer as a person reads it, plus the letters to highlight, both computed by the
        # pipeline's own tested normaliser.
        #
        # `answer` alone is a **string** on every one of the 79,090 questions (measured
        # 2026-09-28: 100% `str`), and it is not always a letter — 1,205 carry `送分`, `B或C`,
        # `A或B或C或D`. A UI that treats it as a list throws on every question, which is exactly
        # what happened («(question.answer || []).map is not a function» → the page showed no
        # question at all). `ai_findings.answer_of` already documents all three traps and is
        # covered by tests, so it is called rather than reimplemented: a second parser here would
        # be a second answer to「正確答案是什麼」, which is the one thing this project cannot have
        # two of.
        "answer_display": question_answer_display(question),
        # `accepted_values` is the machine's list and is what decides which option letters go
        # green. For a voided question it is every option, which is the truth: all of them score.
        "answer_keys": question_answer_keys(question),
        "answer_is_void": bool((question.get("answer_payload") or {}).get("is_special_correction")),
        "losses": {
            "lost_glyphs": question.get("lost_glyphs"),
            "lost_glyph_note": question.get("lost_glyph_note"),
            "issue_count": question.get("issue_count"),
            "dispute_severity": question.get("dispute_severity"),
        },
        "figures": [
            {
                "label": ref.get("label"),
                "description": ref.get("description"),
                "page": ref.get("page"),
                "pages": ref.get("pages"),
                "box": ref.get("box"),
                "clipped": ref.get("clipped"),
                "ownership_note": ref.get("ownership_note"),
                "exists": ref.get("exists"),
                # **What this asset is, and which option it belongs to.** v2 binds a crop to an
                # option row with `asset_role == 'option-image'` + `option_key`
                # (`review_ui/v2/02-area-question.js::optionCropHtml`), and this view used to drop
                # both fields — so the UI could not place a picture in its option and an
                # image-only question (four chemical structures, empty option text) rendered as
                # four blank rows. That is the designer's report: 「過去很多管線的截圖是對的，
                # 但在這裡完全沒有截圖（因為題目本身有圖）」. Measured 2026-09-28: 1,320 refs carry
                # `option-image`, 3,209 carry `figure-crop`.
                "asset_role": ref.get("asset_role"),
                "option_key": ref.get("option_key"),
                "placement": ref.get("placement"),
                "ownership": ref.get("ownership"),
                "source": ref.get("source"),
                # The resolved path, so a reader opens the file instead of searching for it.
                # `path` keeps the key the pipeline already uses; `relative_path` is what the
                # queue recorded, kept because it is stable across machines and the absolute one
                # is not. The absolute form must come **after** no other `path` assignment, or a
                # later duplicate silently wins (that happened: the fix looked applied and the
                # agent still could not open the file).
                "relative_path": ref.get("path"),
                "path": figure_asset_path(ref),
            }
            for ref in refs
        ],
        "human_events": human_events(question.get("candidate_key") or ""),
        # The designer's own sentences, newest last, straight off the stream the UI writes.
        # The agent is told to read these before judging: they are the one signal that says
        # what a person already found wrong, which is exactly what the model is being asked
        # to reproduce.
        "prior_judgements": prior_judgements(question.get("candidate_key") or ""),
        # What the pipeline's own repair loop asked a model and what it answered. Carried here so the
        # UI can show 「AI 實際讀到／產生什麼」; without it the only readable record of a model's
        # reading was its parsed verdict (`where`/`fix`), which is the conclusion, not the evidence.
        #
        # **But the prompt archaeology is not evidence.** `prompt_system`/`prompt_user` are copies
        # of the *prompts* past runs used (measured 2026-10-03 on q051: 11.7k of 21.1k finding
        # chars). The agent already has the current ROLE; shipping four generations of old prompts
        # again is ~4k input tokens **per tool call**, and the conductor's latency scales with it
        # (measured: a turn with one get_question went 15.6k → 19.6k input). What the agent compares
        # is readings and verdicts: `finding`, `evidence`, `raw`, `model`, `created_at`. The full
        # rows stay in the UI's own pipeline-findings panel and in the jsonl on disk.
        "ai_findings": [
            {k: v for k, v in row.items()
             if k not in ("prompt_system", "prompt_user", "orchestration", "principles")}
            for row in ai_findings(question.get("candidate_key") or "")
        ],
    }


def pdf_path_of(question: dict) -> str:
    metadata = question.get("metadata") or {}
    relative = metadata.get("question_pdf_relative")
    if not relative:
        _die("question has no question_pdf_relative; cannot crop a page without a PDF")
    # `question_pdf_relative` is relative to the corpus root, not to qbr/. The laptop keeps the
    # corpus inside the repo; the station keeps it in `~/qbr-review/assets/` and points
    # `REPAIR_AGENT_CORPUS_ROOT` there — the code tree's `國考題資料夾` is only the container's
    # mount point, and carrying the corpus inside `code/` is exactly what deploy excludes.
    corpus_root = os.environ.get("REPAIR_AGENT_CORPUS_ROOT") or CATALOG
    path = relative if os.path.isabs(relative) else os.path.join(corpus_root, relative)
    if not os.path.exists(path):
        _die("question PDF not found: %s" % path)
    return path


def figure_boxes(question: dict) -> list:
    """This question's own picture boxes, in the shape `reread.page_extents` reads.

    **Each entry is a dict with `box` and `page`, not a bare tuple.** `page_extents` looks for
    `entry.get("box")`, and a tuple has no `.get` — so a list of tuples is skipped by the
    `if not isinstance(entry, dict): continue` guard, silently. Measured on
    `1152_藥師(一)_藥學(一)` q42 (2026-09-28): passing tuples kept the page-9 region at x1 50.3 (the
    `B.`/`C.`/`D.` marker column) instead of 158.4 (where the option structures end), which is
    exactly the 11-point sliver the 2026-09-25 defect was about. The pictures were in the data the
    whole time; the shape was wrong, and a wrong shape here costs nothing visible: no exception,
    just a crop missing its figures.

    The page matters as much as the box: a question whose options are structures on the *following*
    page only gets that page into the crop through these boxes, because the band's rows for it are
    the marker labels alone.
    """
    boxes = []
    for ref in question.get("image_refs") or []:
        box = ref.get("box")
        page = ref.get("page")
        if not box or len(box) != 4 or not page:
            continue
        boxes.append({"box": [float(value) for value in box], "page": int(page)})
    return boxes


def do_crop(args) -> dict:
    import confirm_dispute

    question = load_question(args.key)
    # `--out` is optional, so the file has to be named here when it is absent.
    #
    # It used to be reported as `"png": args.out`, i.e. `None` on the normal call. The crop really
    # happened (134,227 bytes for q042) and the payload said `png: null`, so the model read that as
    # "no picture exists" and went looking for one — measured 2026-09-28: four `read` calls on the
    # option PNGs plus a `bash ls`, all returning nothing useful, in a trace that otherwise looked
    # healthy. `do_read` already named its default this way; `do_crop` did not, and the difference
    # was invisible because `rows`/`bytes` were correct.
    out_png = args.out or os.path.join(
        STORE_DIR, "crops",
        "%s_q%03d.png" % (os.path.basename(pdf_path_of(question)).replace(".pdf", ""),
                          question.get("question_number") or 0),
    )
    png, rows, error = confirm_dispute.crop_for(
        pdf_path_of(question),
        question.get("question_number"),
        dpi=args.dpi,
        out_png=out_png,
        boxes=figure_boxes(question),
    )
    if png is None:
        return {"error": error, "rows": rows, "candidate_key": args.key}
    return {
        "candidate_key": args.key,
        "question_number": question.get("question_number"),
        "rows": rows,
        "bytes": len(png),
        "png": out_png,
        "boxes_supplied": len(figure_boxes(question)),
    }


def experience_for(key, *, queue="", principles=""):
    """這一題的「人寫的經驗」四條通道 ＋ 這一題圖的事實，用**正式的載入器**取。

    四個通道來自 `confirm_dispute.load_prompt_inputs`——與 `confirm_dispute.confirm_one`（正式那一條
    判讀路徑）**同一個載入器**。沙盒的判讀原本只送 `endpoint/max_tokens/timeout/number`，
    所以人寫的「基本原則」「對反問的回答」「這一題的註解」「上一次被退掉的改動」到不了模型眼前，
    而正式路徑四個都送：同一件事兩份讀法，就是兩份會不一致的東西。

    回傳的是**原形**（`transcribe` 要什麼給什麼），選題由 `key` 決定。
    """
    import confirm_dispute

    inputs = confirm_dispute.load_prompt_inputs(queue or QUEUE, principles or None)
    return {
        "queue": queue or QUEUE,
        "principles": inputs["principles"],
        "answers": (inputs["answers_by_key"] or {}).get(key),
        "notes": (inputs["notes_by_key"] or {}).get(key),
        "rejected": (inputs["rejections_by_key"] or {}).get(key),
        # 第五個輸入（圖的事實）。`transcribe_system` 的 docstring 把它列在同一個「送出什麼就記什麼」
        # 規則裡，所以它跟四條通道一起進出。
        "figures": _figures_note_of(key),
    }


def _figures_note_of(key):
    from qbr import ai_findings

    try:
        return ai_findings.figures_note(load_question(key)) or None
    except SystemExit:
        # `load_question` 找不到題目時會 `_die`；這裡只是要一段描述，不值得讓整個動作失敗。
        return None


def experience_summary(exp):
    """把原形收成「這一題用了哪幾條經驗」——給紀錄與畫面用的那一份。

    每個欄位都用**既有的正規化器**（`ai_findings.latest_note`／`answers_note`），不自己走一次列：
    `load_prompt_inputs` 給的形狀是 `answers_by_key[key] = {"questions": [...]}`、`notes_by_key[key]`
    是事件列，手寫的走訪會在形狀改變時靜默給出空的摘要。畫面要回答的是「模型這次被交代了什麼」，
    所以文字取原文，不做摘要。
    """
    from qbr import ai_findings

    rejected = exp.get("rejected") or None
    return {
        "principles": [str(p).strip() for p in (exp.get("principles") or [])],
        "note": ai_findings.latest_note(exp.get("notes")),
        "answers": ai_findings.answers_note(exp.get("answers")) or None,
        "rejected": ({"count": rejected.get("count"), "fields": rejected.get("fields")}
                     if isinstance(rejected, dict) else None),
        "figures": exp.get("figures") or None,
    }


def do_read(args) -> dict:
    import confirm_dispute
    from qbr import engines

    question = load_question(args.key)
    pdf = pdf_path_of(question)
    number = question.get("question_number")

    exp = experience_for(args.key, queue=args.queue, principles=args.principles)
    principles, answers, notes = exp["principles"], exp["answers"], exp["notes"]
    rejected, figures = exp["rejected"], exp["figures"]

    out_png = args.out or os.path.join(
        STORE_DIR, "crops",
        "%s_q%03d.png" % (os.path.basename(pdf).replace(".pdf", ""), number or 0),
    )
    png, rows, error = confirm_dispute.crop_for(
        pdf, number, dpi=args.dpi, out_png=out_png, boxes=figure_boxes(question)
    )
    if png is None:
        return {"error": "crop failed: %s" % (error,), "rows": rows, "candidate_key": args.key}

    # `engines.named` returns the whole endpoint record, not a URL. Passing just the URL
    # is what `engines.ask` crashes on (`endpoint.get` on a str) — the record is what carries
    # the egress flag, the thinking switch and the model name that `body_for` needs.
    endpoint = engines.named(args.engine)
    # `--no-image` is the negative control: same prompt, no picture. A2.1 measured
    # 47.8% -> 18.9% on ornith with exactly this switch, which is what proves the score
    # comes from the picture and not from the prompt.
    if args.no_image:
        seen, raw, complaint, usage, seconds = (None, "", "image withheld (negative control)", {}, 0.0)
    else:
        seen, raw, complaint, usage, seconds = confirm_dispute.transcribe(
            png,
            endpoint=endpoint,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            number=number,
            principles=principles,
            answers=answers,
            notes=notes,
            rejected=rejected,
            figures=figures,
        )

    # `compare` compares two `{key: text}` maps, and `options_of` is the tested normaliser for the
    # two shapes the pipeline produces (a dict of records, or a list of them). Building the map by
    # hand here would be a second, untested reading of "what this question's options are".
    from qbr import reread
    stored = {
        "stem": question.get("stem") or "",
        "options": reread.options_of(question),
    }
    diff = None
    if seen:
        diff = reread.compare(stored, seen)

    return {
        "candidate_key": args.key,
        "question_number": number,
        "subject": (question.get("metadata") or {}).get("normalized_subject_name"),
        "engine": args.engine,
        "endpoint": endpoint["url"],
        "png": out_png,
        "png_bytes": len(png),
        "rows": rows,
        "seconds": seconds,
        "usage": usage,
        "image_attached": not args.no_image,
        "stored": {"stem": stored["stem"], "options": stored["options"]},
        "seen": seen,
        "raw": raw[:4000],
        "complaint": complaint,
        "diff": diff,
        # 送出什麼就記什麼：`transcribe_system` 是 `transcribe` 內部實際組出來的那一段，所以這個
        # 欄位不可能與送出的提示詞不一致（自己再拼一次就會）。`experience` 是同一批輸入的摘要，
        # 讓畫面回答「這一題用了哪幾條經驗」而不必自己從提示詞裡再讀一次。
        "review_context": {
            "queue": exp["queue"],
            "experience": experience_summary(exp),
            "system": confirm_dispute.transcribe_system(
                principles, answers, notes, rejected, figures
            ),
        },
    }


#: One string names the sandbox judgement stream's shape, read by **both** writers (the bridge and
#: the UI's `append_judgement`) — a schema named twice is two schemas that can drift.
AI_FEEDBACK_SCHEMA = "repair_agent_test/agent_feedback v2"


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def judgement_envelope() -> dict:
    """The provenance stamp every judgement row carries, whoever wrote it.

    One producer of the four columns (`at`, `run_id`, `session_id`, `prompt_version`) so an agent
    row and a designer row share their shape and the two writers cannot drift — measured
    2026-09-30 (workplan 2.1): the agent's rows had **no `at` at all** and neither writer carried a
    run/session id, so no row could answer「這一判是何時、在哪個 run、哪段對話脈絡下」. The ids
    come from the caller's environment (`REPAIR_AGENT_RUN_ID`, `REPAIR_AGENT_SESSION_ID`,
    `REPAIR_AGENT_PROMPT_VERSION`) and stay the honest empty string where a writer genuinely has
    none: a direct designer rating is not an agent run, and its `engine` field still says who
    wrote it.
    """
    return {
        "at": _utc_now(),
        "run_id": os.environ.get("REPAIR_AGENT_RUN_ID", ""),
        "session_id": os.environ.get("REPAIR_AGENT_SESSION_ID", ""),
        "prompt_version": os.environ.get("REPAIR_AGENT_PROMPT_VERSION", ""),
    }


def do_feedback(args) -> dict:
    """Append the agent's or the person's judgement to the sandbox learning stream.

    Append-only by construction (`open(..., "a")`), so a second run cannot overwrite a
    first. The record copies `review_state.append_ai_feedback`'s field names on purpose:
    promoting this into the production stream later is then a change of path, not of shape
    — and a shape that has already been written is a shape that has already been tested.
    Provenance is stamped by **`judgement_envelope()`**, the one producer of the four columns.

    The two authorships carry different burdens (2026-09-30, workplan 2.2): the designer's rating
    is a verdict and may go without a note, but **an agent rating must say why** — a machine
    writing a bare rating is exactly what cannot be audited later.
    """
    if args.source == "agent" and not (args.reason or "").strip():
        _die("an agent judgement must carry --reason: a rating with no basis teaches nothing")
    question = load_question(args.key)
    record = {
        "action": "ai_feedback",
        "schema": AI_FEEDBACK_SCHEMA,
        "candidate_key": question.get("candidate_key"),
        "question_number": question.get("question_number"),
        "subject": (question.get("metadata") or {}).get("normalized_subject_name"),
        "rating": args.rating,
        "audit_scope": args.audit_scope,
        "reason": (args.reason or "")[:2000],
        "engine": args.engine,
        "source": args.source,
        # 「這一題用了哪幾條經驗」記在**判讀這一側的紀錄**裡，不在評語裡：評語是結論，經驗是
        # 這一次判讀的輸入。少了它，下一輪就無法回答「同一題為什麼換了答案」——那是提示詞變了，
        # 還是題目變了。用同一個載入器取（`experience_for`），所以它與 `read` 送出去的一致。
        "experience": experience_summary(
            experience_for(question.get("candidate_key"), queue=args.queue)
        ),
    }
    record = {**judgement_envelope(), **record}
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    # `subject` is echoed back so the Node layer can file a lesson under it. The designer's
    # expectation is that each subject has its own habitual errors, so a lesson that cannot say
    # which subject it came from is a lesson the next subject has to learn again.
    return {"appended": record, "store": STORE, "subject": record["subject"]}


def drafts_for(key: str) -> list:
    """This question's repair drafts, oldest first — every one of them, none approved by existing.

    A draft is a **proposal**, and reading is the only thing this function can do to one: it does
    not advance, select or approve. Acceptance is a separate act (the UI's key, 2.5), and the
    append-only file keeps every superseded proposal — a proposal the agent revised is a trail, not
    a loss, so re-writing or deleting "old" drafts is forbidden by shape (the file is opened with
    `"a"` or read, never `"w"`).
    """
    rows = []
    if not os.path.exists(DRAFTS):
        return rows
    with open(DRAFTS, encoding="utf-8") as handle:
        for line in handle:
            if key not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn tail line is later than any reader; skip it
            if row.get("candidate_key") == key or row.get("canonical_question_key") == key:
                rows.append(row)
    return rows


#: 與前端預覽同一組判準（一份契約，不許兩份）：`fix` 先剝掉「A: 」這種選項前綴；
#: `insert` 認 題幹/stem、答案/answer、選項 X（candidates 的 options 是 `{key, text}` 列）、
#: 或 candidates 列上真實存在的字串欄位名。生產者（驗證自己的草案）與落地寫入器（✅ 的前置
#: 驗證）都用這一份——兩份實作就是兩個可以不一致的地方。
_INSERT_OPTION = re.compile(r"(?:選項|options?)\s*([A-D])", re.IGNORECASE)
_FIX_OPTION_PREFIX = re.compile(r"^[A-D]\s*[:：]\s*")


def resolve_text_target(row: dict, insert: str) -> tuple[str, str]:
    """`insert` →（目標欄位描述, 現值）。欄位／選項不存在 → ValueError（負對照：靜默猜錯格）。"""
    text = (insert or "").strip()
    low = text.lower()
    if "題幹" in text or "stem" in low:
        return "stem", str(row.get("stem") or "")
    if "答案" in text or low.startswith("answer"):
        return "answer", str(row.get("answer") or "")
    om = _INSERT_OPTION.search(text)
    if om:
        letter = om.group(1).upper()
        for opt in row.get("options") or []:
            if isinstance(opt, dict) and str(opt.get("key") or "").upper() == letter:
                return "option:%s" % letter, str(opt.get("text") or "")
        raise ValueError("insert 指向不存在的選項：%s（這一題沒有 %s）" % (text, letter))
    if text and isinstance(row.get(text), str):
        return "field:%s" % text, row[text]
    raise ValueError("insert 指向不存在的欄位：%r" % text)


def strip_option_prefix(fix: str) -> str:
    """草案 fix 帶「A: 」前綴（草案面板的顯示形態）；套用到選項欄時要剝掉。"""
    return _FIX_OPTION_PREFIX.sub("", (fix or "").strip())


def is_full_replacement(old: str, fix: str) -> bool:
    """fix 是不是「改完後的完整文字」——機械判準：與原句的相似度。

    舊判準「須含原句開頭 8 字」擋得住建議（「把X改成Y」），也擋得住**開頭本身的錯字修正**
    （「一般人→健康成人」改在前 8 字內）：合法修正在前、建議在後，兩者都會撞上。
    相似度擋得住片段與建議（與原句相似度低），放行任何位置的修正；整句重寫（相似度低）
    會被拒——那不是這個生產者該提的形態，設計者會叫 agent 重提。前端預覽讀後端算好的值。
    """
    old_ws, fix_ws = re.sub(r"\s+", "", old), re.sub(r"\s+", "", fix)
    if not old_ws:
        return True
    return difflib.SequenceMatcher(None, old_ws, fix_ws).ratio() >= 0.5


def do_propose(args) -> dict:
    """Append the agent's own repair draft — a proposal, which is never a change.

    The name is the contract: `propose`, not `apply`. This writes the agent's own file only
    (`store/repair_drafts.jsonl`); the question's JSONL, the review streams and the human ledger
    are untouched, and multiple drafts may pile up per question — writing a second version is how
    the agent revises, not a dispute and not a self-approval (不逐稿批).

    The four columns travel together (2026-09-30): `fix` (改成什麼), `insert` (落到哪一格),
    `basis` (依據 — 紙本哪個字、哪張圖), and the **crop's own checksum** — a draft whose evidence
    cannot be re-proved later is advice, not a proposal. Provenance is the same
    `judgement_envelope()` stamp the judgement stream uses.
    """
    for column, value in (("--fix (修法：改成什麼)", args.fix),
                          ("--insert (插入點：哪一格/哪一欄/哪一選項)", args.insert),
                          ("--basis (依據：紙本哪個字、哪張圖)", args.basis)):
        if not (value or "").strip():
            _die("a repair draft must say what to change: %s is required" % column)
    question = load_question(args.key)
    crop_ref = None
    if args.crop:
        # The cited crop must be on disk: a citation of a file that does not exist is not evidence,
        # and the checksum is what makes the evidence auditable after a rebuild moves paths.
        if not os.path.isfile(args.crop):
            _die("crop evidence file does not exist: %s" % args.crop)
        import hashlib

        with open(args.crop, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        crop_ref = {"path": args.crop, "bytes": os.path.getsize(args.crop), "sha256": digest}
    record = {
        "action": "repair_draft",
        "schema": "repair_agent_test/repair_drafts v1",
        "candidate_key": question.get("candidate_key"),
        "question_number": question.get("question_number"),
        "subject": (question.get("metadata") or {}).get("normalized_subject_name"),
        "fix": args.fix,
        "insert": args.insert,
        "basis": args.basis,
        "crop": crop_ref,
        "status": "proposed",
    }
    record = {**judgement_envelope(), **record}
    os.makedirs(os.path.dirname(DRAFTS), exist_ok=True)
    with open(DRAFTS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"appended": record, "drafts": DRAFTS}


def do_question(args) -> dict:
    question = load_question(args.key)
    view = question_view(question)
    if args.with_paper:
        view["paper_questions"] = [
            {"candidate_key": row.get("candidate_key"),
             "question_number": row.get("question_number"),
             "stem": (row.get("stem") or "")[:120],
             "figures": len(row.get("image_refs") or [])}
            for row in questions_of_paper(question)
        ]
    return view


# The human review stream: the designer's own decisions. Read-only here, always.
HUMAN_EVENTS = os.path.join(QUEUE, "question_review_events.jsonl")

#: Every action the reviewer's stream carries, and which of them is a **verdict** on the question.
#:
#: The designer's complaint (2026-09-29): 「沙盒沒有把已經審過、有問題嚴格區分出來」. He was right, and
#: the list was worse than it looked: until now the loop kept only `block`／`comment` and folded every
#: other action away, so **10,371 questions he had accepted and 3,938 he had reset were drawn exactly
#: like questions nobody had ever opened** — measured on the local mirror, 2026-09-27: accept 11,239
#: events／10,371 questions, reset_review 7,767／3,938, block 1,145／756, comment 102／87, correct 67／51.
HUMAN_ACTIONS = ("accept", "block", "comment", "correct", "needs_review", "reviewed",
                 "reset_review")

#: The actions that say what he decided. A verdict is a *state*: `accept` then `block` means blocked
#: (the stream is append-only and read in file order, which is time order).
VERDICT_ACTIONS = ("accept", "block", "needs_review", "reviewed")

#: `reset_review` is not a verdict, it is the withdrawal of one: the parser changed what is under the
#: question, so a question that was accepted may not be the question he accepted. 7,767 of those
#: exist. Keeping the old verdict would be the list claiming a decision about text nobody looked at.
STATUS_LABELS = {
    "accept": "已接受",
    "block": "已封鎖",
    "needs_review": "需人工確認",
    "reviewed": "已審",
    "reset": "已重設（需重審）",
    "commented": "只有註解",
    "untouched": "未審",
}
#: Codes the list can filter on, in the order the UI shows them.
STATUS_CODES = ("untouched", "accept", "block", "needs_review", "reviewed", "commented", "reset")


def human_status_of(entry) -> str:
    """One status code per question, from the folded stream (see `do_browse`).

    Precedence, and why: a live verdict outranks a withdrawal (a `reset_review` that came before a
    later `accept` is history, not the current state); a withdrawal outranks a bare comment (the
    question's text changed, so the comment is about text that is gone); a comment alone is its own
    state, because 「他寫過話」 and 「他沒看過」 are different facts and the list has to say which.
    """
    if not entry:
        return "untouched"
    if entry["verdict"]:
        return entry["verdict"]
    if entry["reset"]:
        return "reset"
    if entry["counts"].get("comment") or entry["counts"].get("correct"):
        return "commented"
    return "untouched"


def human_disputes(actions=("block", "comment"), limit: int = 50) -> dict:
    """Where the designer said a question was wrong — the corpus view the agent did not have.

    Measured 2026-09-28: 20,324 human events, of which **1,247 are `block`／`comment`**, and 313 of
    those carry text in `notes` (average 25 characters). The agent could see a dispute only if it
    already knew the question's key; it had no way to answer 「哪些題有問題」, which is why it told the
    designer 「我需要更多資訊」 and asked for a key. The corpus is what it was missing.

    `notes` is the field, **not `reason`**: `reason` is empty on every human event, and a query for it
    returns "nobody wrote anything", which is false. The designer's example of what matters:
    「答案沒進去」（q041）, 「表格應該用截圖的」.

    Read-only by construction — this function opens the file for reading and the agent has no tool
    that writes it.
    """
    if not os.path.exists(HUMAN_EVENTS):
        return {"count": 0, "disputes": [], "note": "no human review stream at %s" % HUMAN_EVENTS}

    # Newest first: a person's most recent complaint is the one still worth acting on, and the file
    # is append-only so the end of it is the present.
    rows = []
    with open(HUMAN_EVENTS, encoding="utf-8") as handle:
        for line in handle:
            if not any('"%s"' % action in line for action in actions):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("action") not in actions:
                continue
            rows.append({
                "candidate_key": row.get("candidate_key") or row.get("canonical_question_key"),
                "action": row.get("action"),
                "notes": row.get("notes") or "",
                "reviewer": row.get("reviewer"),
                "created_at": row.get("created_at") or row.get("at"),
            })
    rows.reverse()
    with_notes = [row for row in rows if row["notes"].strip()]
    return {
        "count": len(rows),
        "with_notes": len(with_notes),
        "shown": min(len(rows), limit),
        "disputes": rows[:limit],
    }


def do_disputes(args) -> dict:
    return human_disputes(
        actions=tuple(args.actions.split(",")),
        limit=args.limit,
    )


def do_overview(args) -> dict:
    """What the whole corpus looks like: how much is there, what state it is in.

    The agent's answer to「我需要更多資訊」should not be a request for a key. This is the shape of the
    corpus — per category, with how many carry a figure, how many a machine flagged, how many the
    designer disputed, and how many the agent itself has judged. Every number is counted from a single
    pass over the candidate file, which is measured at 0.3 s for all 79,090 questions.
    """
    for path in (CANDIDATES, HUMAN_EVENTS):
        if not os.path.exists(path):
            _die("%s not found; run scripts/sync_from_station.sh first" % path)

    disputes = {}
    with open(HUMAN_EVENTS, encoding="utf-8") as handle:
        for line in handle:
            if '"block"' not in line and '"comment"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("action") not in ("block", "comment"):
                continue
            key = row.get("candidate_key") or row.get("canonical_question_key")
            entry = disputes.setdefault(key, {"block": 0, "comment": 0, "notes": ""})
            entry[row["action"]] = entry.get(row["action"], 0) + 1
            if (row.get("notes") or "").strip():
                # Keep the newest sentence with text; an empty one must not overwrite a real one.
                entry["notes"] = row["notes"]

    judged = {}
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("candidate_key"):
                    judged[row["candidate_key"]] = row.get("rating")

    by_category = {}
    total = 0
    with open(CANDIDATES, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            total += 1
            metadata = row.get("metadata") or {}
            name = metadata.get("official_category_name") or "(未分類)"
            bucket = by_category.setdefault(name, {
                "questions": 0, "with_figures": 0, "machine_flagged": 0,
                "disputed": 0, "disputed_with_notes": 0, "judged_by_agent": 0, "examples": [],
            })
            bucket["questions"] += 1
            if row.get("image_refs"):
                bucket["with_figures"] += 1
            if row.get("quality_status") and row.get("quality_status") != "pass":
                bucket["machine_flagged"] += 1
            key = row.get("candidate_key")
            dispute = disputes.get(key)
            if dispute:
                bucket["disputed"] += 1
                if dispute["notes"].strip():
                    bucket["disputed_with_notes"] += 1
                    # A few keys with the designer's own words, so the agent can go straight to a
                    # question where its judgement is worth the most, without another round trip.
                    if len(bucket["examples"]) < 3:
                        bucket["examples"].append({
                            "candidate_key": key,
                            "question_number": row.get("question_number"),
                            "notes": dispute["notes"][:160],
                        })
            if judged.get(key):
                bucket["judged_by_agent"] += 1

    return {
        "total_questions": total,
        "categories": by_category,
        "disputes_total": sum(1 for _ in disputes),
        "judged_by_agent_total": len(judged),
    }


def do_browse(args) -> dict:
    """List categories and their questions, so no key has to be remembered.

    The designer's complaint (2026-09-28): 「我不可能記得 key，應該要有候選列表」. A UI whose only
    way in is a `candidate_key` text box is a UI that can only be used by whoever just built it.

    Reading the corpus is cheap and measured: a full 199 MB pass over all 79,090 questions takes
    **0.3 s** (single pass, substring pre-filter before `json.loads`), so this does not need an
    index or a cache — and a cache would be a second copy of the corpus that can be stale.

    Each question carries the two things the designer needs to choose one: what the machine said
    (`quality_status`) and whether anyone has already judged it (`judged`). Without `judged` the
    list cannot answer 「我做到哪了」, and the same questions get re-read every session.
    """
    if not os.path.exists(CANDIDATES):
        _die("candidates.jsonl not found at %s; run scripts/sync_from_station.sh first" % CANDIDATES)

    judgments = {}
    # The designer's own guidance, read **once**. Measured 2026-09-29: calling `prior_judgements()`
    # inside the row loop below opened this file once per candidate — 79,090 opens, 3.38 s, for a
    # 15 KB file. Same rows, read once, in the same file order.
    #
    # `store/agent_feedback.jsonl` holds **two** kinds of judgement, and they are not the same fact:
    # the agent writes its own (`"source": "agent"`, 27 rows on 2026-09-29) and the designer writes
    # his from the pane (`"source": "designer"`, 2 rows). One `judged` flag covering both is how a
    # list ends up saying 「已判」 about a question no person has opened. They stay split; `judged`
    # keeps its old meaning (any rating at all) so the existing `未判讀` filter is unchanged.
    priors = {}
    judged_by = {"agent": {}, "designer": {}}
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                key = row.get("candidate_key")
                if not key:
                    continue
                judgments[key] = row.get("rating")
                if row.get("source") in judged_by:
                    judged_by[row["source"]][key] = row.get("rating")
                priors.setdefault(key, []).append(row)

    # The designer's own sentences, keyed for the list. His friction, verbatim: 「我原本在 v2
    # 審核過的大量題目也沒有紀錄，我想要針對 block 的題目跟你進行對話暫時也做不到」. A list that
    # cannot show which questions he already flagged is a list he has to remember his way through.
    #
    # 2026-09-29: this loop kept **only** `block`／`comment` (a `'"block"' in line` pre-filter), so
    # his 10,371 accepted questions and 3,938 reset ones were drawn exactly like untouched ones — and
    # he said so: 「沙盒沒有把已經審過、有問題嚴格區分出來」. Every action is folded now, in file
    # order (the stream is append-only, so file order is time order), into one status per question.
    dispute_notes = {}
    human = {}
    if os.path.exists(HUMAN_EVENTS):
        with open(HUMAN_EVENTS, encoding="utf-8") as handle:
            for line in handle:
                if '"action"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                action = row.get("action")
                if action not in HUMAN_ACTIONS:
                    continue
                key = row.get("candidate_key") or row.get("canonical_question_key")
                if not key:
                    continue
                entry = human.setdefault(key, {"verdict": None, "reset": False, "counts": {},
                                               "notes": ""})
                entry["counts"][action] = entry["counts"].get(action, 0) + 1
                notes = (row.get("notes") or "").strip()
                if notes:
                    entry["notes"] = notes
                if action == "reset_review":
                    entry["verdict"] = None
                    entry["reset"] = True
                elif action in VERDICT_ACTIONS:
                    entry["verdict"] = action
                    entry["reset"] = False
                if action in ("block", "comment"):
                    said = dispute_notes.setdefault(key, {"actions": [], "notes": ""})
                    said["actions"].append(action)
                    if notes:
                        said["notes"] = notes

    by_category = {}
    with open(CANDIDATES, encoding="utf-8") as handle:
        for line in handle:
            if args.category and args.category not in line:
                continue
            row = json.loads(line)
            metadata = row.get("metadata") or {}
            category = metadata.get("official_category_name") or "(未分類)"
            if args.category and args.category not in category:
                continue
            key = row.get("candidate_key")
            status = human_status_of(human.get(key))
            entry = {
                "candidate_key": key,
                "question_number": row.get("question_number"),
                "subject": metadata.get("normalized_subject_name"),
                "year": metadata.get("year"),
                "exam_ordinal": metadata.get("exam_ordinal"),
                "quality_status": row.get("quality_status"),
                "figures": len(row.get("image_refs") or []),
                "judged": judgments.get(key),
                "stem": (row.get("stem") or "")[:70],
                "disputed": bool(dispute_notes.get(key)),
                "dispute_actions": (dispute_notes.get(key) or {}).get("actions", []),
                # The person's own words. Shown in the list so he can pick by what he wrote, and
                # so the agent is looking at the same sentence the reviewer saw.
                "notes": (dispute_notes.get(key) or {}).get("notes", ""),
                "prior_judgements": priors.get(key) or [],
                # Three separate facts, never one flag. `human_status` is what the **reviewer**
                # decided on the station (folded above); `judged_by_designer` is what he pressed in
                # this sandbox; `judged_by_agent` is what the agent said on its own. A list that
                # merges them can say 「已判」 about a question nobody has opened.
                "human_status": status,
                "human_status_label": STATUS_LABELS[status],
                "human_actions": (human.get(key) or {}).get("counts") or {},
                "judged_by_designer": judged_by["designer"].get(key),
                "judged_by_agent": judged_by["agent"].get(key),
            }
            by_category.setdefault(category, []).append(entry)

    if not args.category:
        # The first call is the map: which categories exist, how big, how much is left. Sorted by
        # size so the biggest unfinished pile is the first thing visible.
        return {
            "categories": [
                {"category": name,
                 "total": len(rows),
                 "judged": sum(1 for row in rows if row.get("judged")),
                 "with_figures": sum(1 for row in rows if row.get("figures")),
                 "disputed": sum(1 for row in rows if row.get("disputed")),
                 # What the reviewer has already decided, per category, in the station's own
                 # vocabulary (accept／block／reset…), not in this list's private store's. This is
                 # the number that answers 「我做到哪了」 — the old one only counted questions the
                 # agent had rated, which is a different question wearing the same word.
                 "human": {code: sum(1 for row in rows if row.get("human_status") == code)
                           for code in STATUS_CODES}}
                for name, rows in sorted(by_category.items(), key=lambda kv: -len(kv[1]))
            ],
        }

    rows = []
    for name in by_category:
        rows.extend(by_category[name])
    rows.sort(key=lambda r: (str(r.get("subject") or ""), r.get("year") or 0,
                             r.get("question_number") or 0))
    if args.unjudged:
        rows = [row for row in rows if not row.get("judged")]
    if args.with_figures:
        rows = [row for row in rows if row.get("figures")]
    # `--disputed` is the designer's actual workflow: 「我想要針對 block 的題目跟你進行對話」.
    # The filter is on the **list**, not on what the walker sees, so `S` on a filtered list still
    # moves within that list (the v2 rule: 導覽跟隨被畫出的清單).
    if getattr(args, "disputed", False):
        rows = [row for row in rows if row.get("disputed")]
    if getattr(args, "status", None):
        # Comma-separated codes (`untouched,block`). Filtering on the **list** only, same rule as
        # `--disputed`: the walker follows what is drawn.
        wanted = [code for code in str(args.status).split(",") if code]
        rows = [row for row in rows if row.get("human_status") in wanted]
    total = len(rows)
    rows = rows[args.offset:args.offset + args.limit] if args.limit else rows[args.offset:]
    return {"category": args.category, "total": total, "offset": args.offset, "questions": rows}


def do_find(args) -> dict:
    """Find questions by subject / number / text, so no key is ever invented.

    The `subject` match is a substring against the **category**, because that is the word a
    person uses (「醫事檢驗師」) and the normalized subject name is a long parenthesised
    string that nobody types. `contains` is a substring of the stem, kept deliberately dumb:
    a smarter search would be a second opinion about what the question is, and the agent is
    supposed to form that opinion from the question itself.
    """
    if not os.path.exists(CANDIDATES):
        _die("candidates.jsonl not found at %s; run scripts/sync_from_station.sh first" % CANDIDATES)

    hits = []
    seen_contains = args.contains or ""
    with open(CANDIDATES, encoding="utf-8") as handle:
        for line in handle:
            if seen_contains and seen_contains not in line:
                continue
            row = json.loads(line)
            metadata = row.get("metadata") or {}
            if args.subject:
                haystack = " ".join(str(metadata.get(field) or "") for field in (
                    "official_category_name", "normalized_subject_name", "official_subject_name"))
                if args.subject not in haystack:
                    continue
            if args.number is not None and row.get("question_number") != args.number:
                continue
            if seen_contains and seen_contains not in (row.get("stem") or ""):
                continue
            # `--with-figures` exists because the whole purpose of this agent is the pictures:
            # a question with no figure has no crop worth showing a vision model, and filtering
            # here is cheaper than loading 199 MB of questions the agent is not going to ask about.
            if args.with_figures and not (row.get("image_refs") or []):
                continue
            hits.append({
                "candidate_key": row.get("candidate_key"),
                "question_number": row.get("question_number"),
                "category": metadata.get("official_category_name"),
                "subject": metadata.get("normalized_subject_name"),
                "year": metadata.get("year"),
                "exam_ordinal": metadata.get("exam_ordinal"),
                "figures": len(row.get("image_refs") or []),
                "figure_clipped": [
                    {"label": ref.get("label"), "page": ref.get("page"),
                     "clipped": ref.get("clipped"), "ownership_note": ref.get("ownership_note")}
                    for ref in (row.get("image_refs") or [])
                ],
                "stem": (row.get("stem") or "")[:80],
            })
            if len(hits) >= args.limit:
                break
    return {"count": len(hits), "questions": hits}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--key", required=True, help="candidate_key of the question")
        p.add_argument("--json", action="store_true", help="print JSON (always on; kept for symmetry)")

    p_question = sub.add_parser("question", help="the complete question")
    common(p_question)
    p_question.add_argument("--with-paper", action="store_true", help="also list the rest of the paper")
    p_question.set_defaults(func=do_question)

    p_browse = sub.add_parser("browse", help="list categories, or a category's questions")
    p_browse.add_argument("--category", default="", help="category substring; omit to list categories")
    p_browse.add_argument("--unjudged", action="store_true", help="only questions nobody has judged")
    p_browse.add_argument("--with-figures", action="store_true", help="only questions that have a figure")
    p_browse.add_argument("--disputed", action="store_true",
                          help="only questions the designer flagged (block/comment)")
    p_browse.add_argument("--status", default="",
                          help="only these reviewer statuses (comma-separated: "
                               + ",".join(STATUS_CODES) + ")")
    p_browse.add_argument("--offset", type=int, default=0)
    p_browse.add_argument("--limit", type=int, default=0, help="0 = all")
    p_browse.set_defaults(func=do_browse)

    p_overview = sub.add_parser("overview", help="the shape of the whole corpus, per category")
    p_overview.set_defaults(func=do_overview)

    p_disputes = sub.add_parser("disputes", help="questions the designer said were wrong")
    p_disputes.add_argument("--actions", default="block,comment",
                            help="which human actions count as a dispute")
    p_disputes.add_argument("--limit", type=int, default=50)
    p_disputes.set_defaults(func=do_disputes)

    p_find = sub.add_parser("find", help="find questions by subject / number / text")
    p_find.add_argument("--subject", default="", help="substring of the category or subject name")
    p_find.add_argument("--number", type=int, default=None, help="question number")
    p_find.add_argument("--contains", default="", help="substring of the stem")
    p_find.add_argument("--with-figures", action="store_true", help="only questions that have a figure")
    p_find.add_argument("--limit", type=int, default=20)
    p_find.set_defaults(func=do_find)

    p_crop = sub.add_parser("crop", help="crop this question's band off the page")
    common(p_crop)
    p_crop.add_argument("--out")
    p_crop.add_argument("--dpi", type=int, default=150)
    p_crop.set_defaults(func=do_crop)

    p_read = sub.add_parser("read", help="show the model the page and collect its reading")
    common(p_read)
    p_read.add_argument("--engine", default=DEFAULT_ENGINE)
    p_read.add_argument("--out")
    p_read.add_argument("--dpi", type=int, default=150)
    p_read.add_argument("--max-tokens", type=int, default=2000)
    p_read.add_argument("--timeout", type=int, default=300)
    p_read.add_argument("--no-image", action="store_true", help="negative control: same prompt, no picture")
    # 預設就是 live 快照的 `review-ui/`；`--queue` 是為了在別的快照上量同一件事（測試與 A/B）。
    p_read.add_argument("--queue", default="", help="queue dir holding the review streams (default: live snapshot)")
    p_read.add_argument("--principles", default="", help="principles stream path override")
    p_read.set_defaults(func=do_read)

    p_feedback = sub.add_parser("feedback", help="append a judgement to the learning store")
    common(p_feedback)
    p_feedback.add_argument("--rating", required=True, choices=JUDGEMENT_RATINGS)
    p_feedback.add_argument("--reason", default="")
    p_feedback.add_argument("--audit-scope", default="question",
                            choices=("question", "group", "visual", "answer"))
    p_feedback.add_argument("--engine", default="")
    p_feedback.add_argument("--source", default="agent", choices=("agent", "human"))
    p_feedback.add_argument("--queue", default="", help="queue dir holding the review streams (default: live snapshot)")
    p_feedback.set_defaults(func=do_feedback)

    p_propose = sub.add_parser("propose",
                               help="append the agent's own repair draft (a proposal, never a change)")
    p_propose.add_argument("--key", required=True)
    p_propose.add_argument("--fix", required=True, help="修法：改成什麼字／哪個欄位值")
    p_propose.add_argument("--insert", required=True, help="插入點：哪一格／哪一欄／哪一選項")
    p_propose.add_argument("--basis", required=True, help="依據：紙本哪個字、哪張圖")
    p_propose.add_argument("--crop", default="", help="裁片憑證：這次判讀依據的裁片路徑（記其 SHA-256）")
    p_propose.set_defaults(func=do_propose)

    args = parser.parse_args()
    try:
        result = args.func(args)
    except QuestionNotFound as error:
        # The CLI's contract is a JSON envelope on stdout plus a non-zero exit — the same shape
        # `_die` produces. Catching here (not inside `load_question`) is what lets the UI reuse
        # `load_question` without `SystemExit` taking down a request thread.
        _die(str(error))
    if result:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
