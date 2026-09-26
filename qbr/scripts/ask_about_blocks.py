# -*- coding: utf-8 -*-
"""Ask the local model what is wrong with each question a person blocked, and record the answer.

This is the loop's step 3, made concrete. `repair_loop.py` finds the blocks no detector explains
and prints them; this script asks the model about exactly those questions, one at a time, and
writes down for each one: **where it thinks the error is**, **how it would fix it**, and **whether
it thinks this is a class or a one-off**.

Two populations, and the second is the user's rule
------------------------------------------------

    default   the questions a person **blocked** and no detector explains.
    --all     **every** question in the queue. After the pipeline runs, the whole corpus goes past
              the model once - not only what a person already rejected, because a defect the reader
              has not reached yet is exactly what this is for.

The engine is Splash (8088) for everything. It was chosen by measurement, not by size: on
`108030:305 q076` it reported `FIGURE_MISSING` where the smaller engine said NONE, and on `q049` it
named the Kangxi radicals that the deterministic scan had explicitly decided were harmless. One
engine answering everything also matters for the record - two engines answering the same question is
how this project ends up with two notes that disagree and no rule for which one is right.

Where this sits in the loop
---------------------------

    pipeline  ──►  every question goes past Splash once (--all)
                          │
                          └─►  a person reads the DEFECT findings, confirms
                                        │
                                        └─►  the confirmed class becomes a rule in disputes.py
                                                   │
                                                   └─►  rescan the corpus for that shape

What the record is for
----------------------
Some of these questions are 國考題的意外 - real defects whose shape is strange enough that nobody can
classify them on sight. For those, a model's opinion is not a verdict, it is a note: something to
read later when a person sits down to discuss that single case. That only works if the note is
written down at the time, next to the exact text the model saw, naming the model that said it. A
suggestion remembered but not recorded is a suggestion nobody can check.

The one distinction that matters to the caller
----------------------------------------------
`rule_worthy` separates a class from a one-off, because the two need opposite treatment. A class
gets a rule in `disputes.py` and a rescan. A one-off gets looked at by a person and stays a
one-off - writing a rule for a single accident is how a detector starts flagging innocent
questions, which is the treadmill the charter warns about.

Usage
-----
    # what does the model say about the blocks no detector explains?
    .venv/bin/python scripts/ask_about_blocks.py --queue data/review-queues/live

    # the corpus pass: every question, resumable, ~2s/question at concurrency 4
    .venv/bin/python scripts/ask_about_blocks.py --queue data/review-queues/live --all \
        --concurrency 4 --resume

    # only some of them, or re-ask without losing the earlier answer
    .venv/bin/python scripts/ask_about_blocks.py --queue ... --only q012 q059
    .venv/bin/python scripts/ask_about_blocks.py --queue ... --limit 5

    # read back what was recorded, classes and one-offs apart
    .venv/bin/python scripts/ask_about_blocks.py --queue ... --report
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import ai_findings  # noqa: E402
from qbr import discuss  # noqa: E402
from qbr import engines  # noqa: E402
from qbr import vision  # noqa: E402
import repair_loop  # noqa: E402

# The engines, by name. Stored in the library (`qbr.engines`) rather than here, because
# `reread.py` also calls a model and **a second copy of "how to turn thinking off" is exactly the
# bug that module exists to prevent**: the wrong spelling is accepted with HTTP 200 and silently
# ignored, so a duplicate that drifts costs 4.7x the latency with no error. This name is kept as an
# alias so existing callers and tests keep working.
ENDPOINTS = engines.ENDPOINTS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; decisions live in its review-ui/ subdirectory")
    parser.add_argument("--model", default="splash", choices=sorted(ENDPOINTS))
    parser.add_argument("--only", nargs="*", default=None,
                        help="question numbers (q012 or 12); default is every unexplained block")
    parser.add_argument("--limit", type=int, default=0, help="ask at most this many (0 = all)")
    parser.add_argument("--all", action="store_true",
                        help="ask about *every* question in the queue, not only the human blocks "
                             "(the corpus pass; 79,090 questions at ~2s/question concurrent)")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="how many questions to ask at once; measured 1->4.9s/q, 4->2.0s/q, "
                             "8->1.95s/q on Splash, so past 4 the engine queues rather than helps")
    parser.add_argument("--out", help="findings path; default <queue>/review-ui/"
                                        + ai_findings.STREAM)
    parser.add_argument("--report", action="store_true",
                        help="read back what was recorded instead of asking")
    parser.add_argument("--max-tokens", type=int, default=4000,
                        help="Splash needs ~1,200-3,300 for its reasoning when thinking is on; with "
                             "it off the answer alone is under 400. Kept above the answer length "
                             "because a budget that cuts the JSON off mid-field loses the note.")
    parser.add_argument("--learned", action="append", default=None,
                        help="what earlier batches already settled, so the next batch starts from it "
                             "instead of rediscovering it: `CODE=what it means` repeated, or a "
                             "quoted string. Recorded on every note, because the prompt is part of "
                             "the measurement and a changed prompt is a different pass. "
                             "Must be `action=\"append\"`: without it argparse keeps only the last "
                             "flag, and `parse_learned` then iterates the *string* one character at "
                             "a time - measured on the first corpus sweep, whose stored `learned` is "
                             "a bulleted list of single characters. It failed quietly because a "
                             "block of junk still reads as \"already known\"")
    parser.add_argument("--resume", action="store_true",
                        help="skip questions that already have a finding (a corpus pass is ~44h, "
                             "so stopping and continuing must not re-ask anything)")
    parser.add_argument("--restale", action="store_true",
                        help="re-ask the questions whose current finding was written under an older "
                             "prompt version - the prompt is part of the measurement, so after a "
                             "fix the answers that were asked the old question have to be asked "
                             "again, and only those")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--principles", metavar="PATH",
                        help="the reviewer's 基本原則 stream (default: the queue's own "
                             + discuss.PRINCIPLES_STREAM + "); each active principle is added to the "
                             "prompt as a constraint. Same flag and same default as "
                             "`confirm_dispute.py`, because both passes ask the model about a page "
                             "and a constraint that bound one of them but not the other would make "
                             "two measurements out of one reviewer's notes.")
    return parser.parse_args()


def _endpoint(base: str) -> str:
    return engines.endpoint_url(base)


def ask(messages, *, endpoint, max_tokens, timeout):
    """One call. Returns (parsed_or_None, raw, complaint, usage, seconds).

    The request is built by `qbr.engines`, which owns the engine-specific thinking switch. Splash
    takes `reasoning_effort: none` (measured 21-54s -> 3-5s with the same findings); MTPLX takes
    `chat_template_kwargs.enable_thinking` and **silently ignores** the other spelling - which is how
    a "thinking off" run keeps thinking and returns an empty string when the budget runs out.
    """
    raw_response, seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens,
                                        timeout=timeout)
    if raw_response is None:
        return None, "", "request failed", {}, seconds
    content = engines.content_of(raw_response)
    return (ai_findings.parse_finding(content), content, None, engines.usage_of(raw_response),
            seconds)


# 審題者對某一題的**圖**留的話，只有這五種意思。標籤是封閉集合，所以這一支的答案可以被拒絕
# （不在集合裡就是抱怨，不是一個比較寬鬆的答案）。
FIGURE_DIRECTIVE_LABELS = ("no-figure", "extra-crop", "wrong-region", "keep", "unclear")

FIGURE_DIRECTIVE_SYSTEM = """你是審題者留言的轉錄員。題庫的主人對**某一題的圖**留了一句話，你要把它轉成一個標籤。

只回一個 JSON，不要解釋、不要加字：
{"directive": "<標籤>", "quote": "<他那句話裡讓你下這個標籤的原文片段，照抄>"}

標籤只能是這五個之一：
- no-figure：他說這一題（的題目／題幹／本體）本來就沒有圖、不該有圖、多放了一張圖、硬要截圖。
- extra-crop：他說多截了圖、額外多截、截到別題的圖（但沒有說這一題本來沒有圖）。
- wrong-region：他說這一題有圖，只是截的範圍錯了（切掉了、跨頁只截一半、截錯位置、截得不完整）。
- keep：他在要求保留這張圖，或這句話與圖無關（例如只講文字、只講選項）。
- unclear：看不出來。**不要猜**，不確定就用這個。

注意：選項 C 的圖沒截完整、而題目本體沒有圖，是 no-figure（他要的是這一題不要放圖）。
"""

FIGURE_DIRECTIVE_USER = """這一題目前在平台上的文字（供你判斷他講的是哪一張圖）：\n%s\n\n他對這一題的圖留的話：\n%s\n"""


def read_figure_directive(note, *, question="", model=None, max_tokens=400, timeout=300):
    """這一題的圖，他的話是什麼意思。回 `(標籤, 原話片段, 抱怨)`。

    為什麼要讓引擎讀這句話：站上實測（2026-09-25），業主明確說「沒有圖／多截」的 11 題裡，
    用紙本量得到的訊號只解釋得了 **5 題**（這一題自己的區域裡連一個圖物件都量不到）。剩下 6 題的
    區域**確實量到 2-4 個圖物件**（選項的圖、隔壁題的圖），量測於是留著一張，而他看到的就是
    「他不改」。紙本量不出「這張圖該不該交給審題者」——那是他的意思，所以這裡是一個提示詞，不是一條
    新的量測規則（charter：腳本只保留紙張的性質）。

    走 `qbr.engines` 的同一個 lane（`LANE`／`QBR_NOTE_DIRECTIVE_MODEL`，預設與常駐迴圈一致），因為
    「怎麼讀他的話」只能有一份：這一支與 `ask_about_blocks` 的判讀同一個門，兩份就是兩個答案。
    """
    lane = (model or os.environ.get("QBR_NOTE_DIRECTIVE_MODEL") or os.environ.get("LANE")
            or "mtplx-35b")
    endpoint = ENDPOINTS.get(lane)
    if endpoint is None:
        return "", "", "unknown-local-lane: " + str(lane)
    refusal = engines.egress_refusal(endpoint)
    if refusal:
        return "", "", refusal
    messages = [
        {"role": "system", "content": FIGURE_DIRECTIVE_SYSTEM},
        {"role": "user", "content": FIGURE_DIRECTIVE_USER % (
            (question or "").strip()[:1500] or "（沒有提供）", (note or "").strip()[:500])}]
    raw, _seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens,
                               timeout=timeout)
    if raw is None:
        return "", "", "request-failed"
    parsed, complaint = vision._json_of(engines.content_of(raw), expect=("directive",))
    if parsed is None:
        return "", "", complaint or "unparsed"
    label = str(parsed.get("directive") or "").strip()
    quote = str(parsed.get("quote") or "").strip()
    if label not in FIGURE_DIRECTIVE_LABELS:
        return "", quote, "unknown-directive: " + (label[:40] or "empty")
    return label, quote, ""


def targets(queue_dir: str, only, limit, everything=False, done=(), questions=None):
    """Which questions to ask about.

    Two modes, and the difference matters to what the answer is worth:

      default   the questions a person **blocked** and no detector explains - the loop's own step 3.
                Reusing `repair_loop.explain` rather than recomputing is deliberate: two definitions
                of "the blocks that nothing explains" is two places for it to drift, and the whole
                workflow is that the model is asked about the *same* set the loop printed.
      --all     **every** question in the queue. The user's rule is that after the pipeline runs, the
                whole corpus goes past the model once - not only what a person already rejected -
                because a defect the reader has not reached yet is exactly what this is for. It is
                the same question asked of every row, so the prompt and the record are identical;
                what changes is the population.

    `done` skips keys already recorded, so a 44-hour corpus pass can be stopped and resumed without
    re-asking anything. A question is only skipped when it has a finding, so a failed call is retried.
    """
    if everything:
        rows = (questions if questions is not None
                else repair_loop.load_candidates(os.path.join(queue_dir, "candidates.jsonl")))
        # `population="corpus"` is set here, where we know the rows came from the file rather than
        # from a person's block. The prompt must not claim a human flagged these; measured cost of
        # getting that wrong is a whole-corpus pass priming itself to invent 79,090 defects.
        entries = [{"candidate_key": q.get("candidate_key"), "question": q,
                    "notes": "", "kinds": [], "population": "corpus",
                    "paper": repair_loop.paper_of(q.get("candidate_key"))}
                   for q in rows]
    else:
        blocked, rows = repair_loop.collect_blocks(queue_dir)
        # 被退過的次數與那筆被退的改動（同一份事件流、同一個折疊）。沒有它，被問到的題目帶著
        # 「不知道上次的改動被退過」出發，只能再發明一次同一個改動（`ask_one` 把它交給提示詞）。
        rejections = repair_loop.rejections_by_key(
            os.path.join(queue_dir, "question_review_events.jsonl"))
        _explained, unexplained = repair_loop.explain(blocked, rows, rejections)
        entries = [{**entry, "population": "blocked", "question": rows.get(entry["candidate_key"])}
                   for entry in unexplained]
    if only:
        wanted = {str(item).lower().lstrip("q").lstrip("0") or "0" for item in only}
        entries = [entry for entry in entries
                   if str(entry["question"].get("question_number")).lstrip("0") in wanted]
    if done:
        skip = set(done)
        entries = [entry for entry in entries if entry["candidate_key"] not in skip]
    if limit:
        entries = entries[:limit]
    return entries


def parse_learned(values):
    """Turn `--learned CODE=what it means` into the mapping `learned_note` expects.

    A bare string is passed through unchanged, because a direction that is not one code ("this paper
    prints the answer key on a separate sheet") is still worth carrying forward, and forcing it into
    `CODE=` would lose it.

    **A single string is not a list of items.** This function used to iterate `values` directly, so a
    bare string (which argparse produces when `--learned` is missing `action="append"`, keeping only
    the last flag) was walked one **character** at a time and came out as a bulleted list of single
    characters - measured on the first corpus sweep. `learned_note` accepts a string too, so this is
    also the path that a hand-typed multi-line direction takes: a string that *contains* newlines or
    `=`-free text is one direction, not many.
    """
    if not values:
        return None
    if isinstance(values, str):
        values = [values]
    learned = {}
    extras = []
    for item in values:
        if "=" in item:
            code, meaning = item.split("=", 1)
            learned[code.strip()] = meaning.strip()
        else:
            extras.append(item)
    if extras:
        return "\n".join(["- %s：%s" % (code, meaning)
                          for code, meaning in sorted(learned.items())]
                         + ["- %s" % extra for extra in extras])
    return learned or None


def _paper_scope(path: str) -> dict:
    """paper code (`115090:305`) -> (考別, 考科), read from the queue's own candidates.

    Not from `queue_index.json`: its `per_paper` is keyed by run name (`1152_物理治療師_...`), which a
    finding's `paper` field (`115090:305`) does not name, so joining on it silently matched nothing.
    The candidates themselves carry the paper code and the names together, so streaming them is the
    join that actually works. They sit next to the findings, which is why they are found from there.
    """
    directory = os.path.dirname(path)
    candidates = os.path.join(directory, "candidates.jsonl")
    if not os.path.exists(candidates) and os.path.basename(directory) == "review-ui":
        candidates = os.path.join(os.path.dirname(directory), "candidates.jsonl")
    scope = {}
    try:
        with open(candidates, encoding="utf-8") as handle:
            for line in handle:
                try:
                    question = json.loads(line)
                except ValueError:
                    continue
                code = ":".join(str(question.get("candidate_key") or "").split(":")[1:3])
                if code and code not in scope:
                    metadata = question.get("metadata") or {}
                    scope[code] = (
                        metadata.get("normalized_category_name")
                        or metadata.get("official_category_name") or "",
                        metadata.get("normalized_subject_name")
                        or metadata.get("official_subject_name") or "",
                    )
    except OSError:
        return {}
    return scope


def _scope_line(records_by_key, keys, paper_scope=None):
    """Where a class's questions actually come from: one 考科, one 考別, or spread out.

    This is the difference between a rule and a rule that should be scoped. Nine hits inside one
    考科 is one cause in one paper's shape, and widening it to every paper is how a rule starts
    misfiring on papers that never had the problem. Nine hits across nine 考別 is the opposite claim.
    The report has to say which, or a reader cannot tell them apart.
    """
    paper_scope = paper_scope or {}
    cats, subs = collections.Counter(), collections.Counter()
    for key in keys:
        record = records_by_key.get(key) or {}
        category = record.get("category")
        subject = record.get("subject")
        if not category:
            category, indexed_subject = paper_scope.get(record.get("paper") or "", ("", ""))
            subject = subject or indexed_subject
        cats[category or "（未知）"] += 1
        subs[(category or "?", subject or "?")] += 1
    if len(subs) == 1:
        (cat, sub), count = next(iter(subs.items()))
        return "考科限定：%s / %s（%d 題都在同一考科）" % (cat, sub, count)
    if len(cats) == 1:
        cat = next(iter(cats))
        return "考別限定：%s（%d 個考科）" % (cat, len(subs))
    return "跨考別：%d 個考別、%d 個考科" % (len(cats), len(subs))


def report(path: str) -> int:
    records = ai_findings.load(path)
    if not records:
        print("還沒有任何 AI 記錄：%s" % path)
        return 0
    summary = ai_findings.summarize(records)
    by_key = {r.get("candidate_key"): r for r in records}
    paper_scope = _paper_scope(path)
    print("AI 記錄：%s" % path)
    print("  共 %d 筆（模型判定不是抽取缺陷的 %d 筆、無法解析的 %d 筆）"
          % (len(records), len(summary["not_extraction"]), len(summary["failed"])))
    print()
    print("== 模型認為是「一類」問題（值得寫規則）==")
    if not summary["classes"]:
        print("  （無）")
    for what, keys in sorted(summary["classes"].items(),
                             key=lambda item: (-len(item[1]), item[0])):
        print("  %-26s %d 題" % (what, len(keys)))
        # 一個類別要寫成全域規則還是限定某考科，取決於它出現在哪裡；這一行就是那個證據。
        print("      範圍：%s" % _scope_line(by_key, keys, paper_scope))
        for key in keys:
            record = by_key.get(key) or {}
            category = record.get("category") or paper_scope.get(record.get("paper") or "", ("", ""))[0]
            print("      %-40s %s / %s" % (key.replace("moex:", ""), category or "?",
                                            (record.get("subject") or "")[:22]))
    print()
    print("== 模型認為是個案（要單獨探討，不要寫規則）==")
    if not summary["one_offs"]:
        print("  （無）")
    for item in summary["one_offs"]:
        record = by_key.get(item["candidate_key"]) or {}
        print("  %s  [%s]  %s" % (item["candidate_key"].replace("moex:", ""), item["what"],
                                  (record.get("subject") or "")[:28]))
        print("      哪裡錯：%s" % (item["where"] or "（沒說）"))
        print("      怎麼修：%s" % (item["fix"] or "（沒說）"))
    print()
    print("== 模型認為不是抽取缺陷（＝可能是紙本或題目本身的問題）==")
    for key in summary["not_extraction"] or ["（無）"]:
        print("  %s" % str(key).replace("moex:", ""))
    if summary["unclassified"]:
        print()
        print("== 有備註、但那個代碼／判定不認識（筆記照樣可讀）==")
        for item in summary["unclassified"]:
            label = item.get("what_reported") or item.get("verdict_reported") or "?"
            print("  %s  [%s]" % (item["candidate_key"].replace("moex:", ""), label))
            print("      哪裡錯：%s" % (item["where"] or "（沒說）"))
            print("      怎麼修：%s" % (item["fix"] or "（沒說）"))
    return 0


def ask_one(entry, *, endpoint, out, args):
    """Ask about one question and append the finding. Returns a one-line summary.

    Called from a worker thread, so it touches only its own records and one append - the append is
    a single `open(...,"a")` write, and the store is append-only by shape, so concurrent writers
    cannot corrupt each other's records or a previous run's.
    """
    question = entry["question"]
    if not question:
        return None
    # The population decides the prompt's framing, and it must come from how the entry was
    # collected rather than from a flag re-read here - those two could disagree, and the whole
    # point of the fix is that the question "did a person block this" is answered by the data.
    population = entry.get("population") or "blocked"
    # The reviewer's 註解 on this question, when they wrote one (`repair_loop.explain` already read it
    # out of the same fold that decided this question was a block). It goes into the prompt because it
    # is the one thing that says *where* the person thinks the problem is: without it the model is
    # handed "a human flagged this, he did not say where" and told to re-derive the problem, which is
    # the open question this project has measured as unreliable - see `ai_findings.POPULATIONS`.
    notes = entry.get("notes") or None
    # **這一題被機器改過又被退過幾次、上一次被退的是哪一筆改動**（`repair_loop.explain` 從同一份
    # 折疊讀出來的）。與註解同一個理由：不知道上一次的改動被退過，模型只會再造一次同一個改動；
    # 這是這個迴圈唯一能收斂的地方，而不是一直重問同一題。
    rejected = entry.get("rejected") or None
    system, user = ai_findings.build_prompt(question, learned=args.learned,
                                            population=population,
                                            principles=args.principles_for_prompt,
                                            notes=notes, rejected=rejected)
    parsed, raw, complaint, usage, seconds = ask(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        endpoint=endpoint, max_tokens=args.max_tokens, timeout=args.timeout)
    error = None if parsed is not None else (complaint or "unparsed")
    record = ai_findings.make_record(
        question=question, finding=parsed, model=endpoint["name"], endpoint=endpoint["url"],
        prompt_system=system, prompt_user=user, raw="" if parsed else raw, usage=usage,
        seconds=round(seconds, 1), error=error, learned=args.learned, population=population,
        principles=args.principles_for_prompt, notes=notes, rejected=rejected)
    ai_findings.append(out, record)
    return {"key": entry["candidate_key"], "finding": parsed, "error": error,
            "seconds": seconds, "raw": raw}


def main() -> int:
    args = parse_args()
    args.learned = parse_learned(args.learned)
    queue_dir = repair_loop.review_ui_dir(args.queue)
    out = args.out or ai_findings.store_path(args.queue)

    if args.report:
        return report(out)

    candidates = os.path.join(queue_dir, "candidates.jsonl")
    if not os.path.exists(candidates):
        print("找不到 candidates.jsonl：%s" % queue_dir, file=sys.stderr)
        return 2

    # The reviewer's 基本原則, read **once per run**: they are a standing constraint, and re-reading
    # the file per question would let a principle added mid-run apply to some of the batch and not
    # others - the same measurement claiming two prompt versions. This is the same resolution and
    # the same default `confirm_dispute.py` uses, so the two passes cannot disagree about what the
    # reviewer's notes are.
    principles_path = args.principles or os.path.join(queue_dir, discuss.PRINCIPLES_STREAM)
    # **只有人核准過的原則會進提示詞**（2026-09-24）。核准是原則區上的一顆按鈕，寫進同一條
    # append-only 流；`discuss.active_principles` 是畫面上要畫的（含待核准，人才知道有東西等他
    # 點頭），提示詞讀的是 `ai_findings.principles_for_prompt`——否則一句還沒有人看過的話就會去
    # 改題目文字。兩個數字都印出來：「有 N 條原則」與「模型看到 N 條」現在是兩個量。
    principle_events = discuss.load_events(principles_path)
    projection = discuss.principles_projection(principle_events)
    args.principles_for_prompt = ai_findings.principles_for_prompt(principle_events)
    if args.principles_for_prompt:
        print("基本原則 %d 條（已核准 %d／待核准 %d）（%s）"
              % (len(args.principles_for_prompt), projection["approved_count"],
                 projection["pending_count"], principles_path))
    else:
        print("基本原則：無已核准的（待核准 %d 條；%s）"
              % (projection["pending_count"], principles_path))

    # `--resume`'s skip set is "questions that have an **answer**", not "questions that have a
    # record": a failed call leaves a record with `finding: None`, and counting it would skip the
    # question forever (`ai_findings.is_answer`). Read only when one of the two flags needs it —
    # `latest_by_question` walks the whole stream, which is 500 MB on the station.
    done = ()
    if args.resume or args.restale:
        latest = ai_findings.latest_by_question(out)
        answered = {key for key, record in latest.items() if ai_findings.is_answer(record)}
        done = answered
    if args.restale:
        # Re-ask the questions whose current finding belongs to an older prompt generation. The
        # skip set has to become "everything except those", because `--resume` skips every question
        # that has *any* finding - including the stale ones this flag exists to re-ask.
        population = "corpus" if args.all else "blocked"
        # The version has to be computed with the *same* learned block this run will use - the
        # learned text is part of the prompt, so a run with a different block is a different
        # measurement and every record from the other one is legitimately stale. The principles go
        # in for the same reason and one more: the reviewer can write a new one between two runs,
        # and a note written before it is an answer to a question that is no longer being asked.
        #
        # The expected version must use exactly the inputs `targets`/`ask_one` use: the standing note
        # from `read_latest_actions` (not note history, whose candidate-key labels change the rendered
        # block) and the current rejected-repair row. The prompt hash includes that rendered text.
        review_log = os.path.join(queue_dir, "question_review_events.jsonl")
        latest_actions = repair_loop.read_latest_actions(review_log)
        rejections_by_key = repair_loop.rejections_by_key(review_log)
        versions = {key: ai_findings.prompt_version(population, args.learned,
                                                    args.principles_for_prompt,
                                                    notes=(latest_actions.get(key) or {}).get("notes"),
                                                    rejected=rejections_by_key.get(key))
                    for key in latest}
        stale = ai_findings.stale_questions(out, versions, population)
        done = answered - stale
        print("提示詞版本 %d 種（逐題，含註解與被退修復）：%d 題的現行 finding 是舊版，要重問。"
              % (len(set(versions.values())), len(stale)))
    questions = None
    if args.all:
        # Read once and hold, rather than have every worker re-read 196 MB.
        questions = list(repair_loop.load_candidates(candidates))
    entries = targets(queue_dir, args.only, args.limit, everything=args.all, done=done,
                      questions=questions)
    if not entries:
        print("沒有要問的題目（沒有未解釋的 block、--only 沒對上，或都已問過）。")
        return 0
    endpoint = ENDPOINTS[args.model]
    population = "全部題目" if args.all else "未解釋的 block"
    print("問 %s（%s）關於 %d 題%s%s"
          % (endpoint["name"], endpoint["url"], len(entries), population,
             "（已跳過 %d 題問過的）" % len(done) if done else ""))
    print("並發 %d；記錄寫到：%s" % (args.concurrency, out))
    print()

    asked = 0
    failures = 0
    done_lock = threading.Lock()
    printed = [0]
    if args.concurrency > 1:
        from concurrent.futures import ThreadPoolExecutor

        def work(entry):
            return ask_one(entry, endpoint=endpoint, out=out, args=args)

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            for result in pool.map(work, entries):
                asked += 1
                if result is None:
                    continue
                if result["finding"] is None:
                    failures += 1
                with done_lock:
                    printed[0] += 1
                    if printed[0] % 50 == 0 or printed[0] <= 20:
                        _print_result(result, printed[0], len(entries))
    else:
        for entry in entries:
            result = ask_one(entry, endpoint=endpoint, out=out, args=args)
            asked += 1
            if result is None:
                continue
            if result["finding"] is None:
                failures += 1
            printed[0] += 1
            _print_result(result, printed[0], len(entries))

    print()
    print("已記錄 %d 筆（其中無法解析 %d 筆）。讀回來：--report" % (asked, failures))
    return 0


def _print_result(result, index, total) -> None:
    key = result["key"].replace("moex:", "")
    finding = result["finding"]
    if finding is None:
        print("[%d/%d] %-46s 無法解析（%s）%s"
              % (index, total, key, result["error"],
                 (result["raw"] or "")[-70:].replace("\n", " ")), flush=True)
        return
    print("[%d/%d] %-46s %s／%s 規則=%s %.1fs"
          % (index, total, key, finding.get("verdict"), finding.get("what"),
             finding.get("rule_worthy"), result["seconds"]), flush=True)
    if result["seconds"] > 20 or finding.get("verdict") == "DEFECT":
        # Only the interesting ones get their note printed; the corpus pass would otherwise write
        # 79,090 notes to the terminal, and a log nobody can read is not a record.
        print("        哪裡錯：%s" % (finding.get("where") or "（沒說）"), flush=True)
        print("        怎麼修：%s" % (finding.get("fix") or "（沒說）"), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
