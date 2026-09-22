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
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import ai_findings, extract, paths, reread  # noqa: E402
import ask_about_blocks  # noqa: E402
import repair_loop  # noqa: E402

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


def disputed_questions(queue_dir, only, kinds, limit):
    candidates = os.path.join(queue_dir, "candidates.jsonl")
    if not os.path.isfile(candidates):
        print("找不到 candidates.jsonl：%s" % queue_dir, file=sys.stderr)
        return []
    wanted_kinds = set(kinds) if kinds else set(CONFIRMABLE_KINDS)
    wanted_numbers = None
    if only:
        wanted_numbers = {str(item).lower().lstrip("q").lstrip("0") or "0" for item in only}
    rows = []
    for question in repair_loop.load_candidates(candidates):
        number = str(question.get("question_number")).lstrip("0") or "0"
        if wanted_numbers is not None and number not in wanted_numbers:
            continue
        found = wanted_kinds.intersection(dispute_kinds(question))
        if not found:
            continue
        rows.append(question)
    if limit:
        rows = rows[:limit]
    return rows


def crop_for(pdf_path, number, *, dpi, out_png=None):
    """The page's own view of one question: the rows its number owns, cropped and stitched.

    Uses `reread.band_rows`, which is the tested definition of "the rows this question owns" - its
    number down to the next number, across page breaks. Rendering the *rows* rather than a fixed
    region is what keeps a question whose stem continues onto the next page whole.
    """
    kept_rows = extract.extract_cells_a(pdf_path)
    rows = reread.band_rows(kept_rows, int(number))
    if not rows:
        return None, 0, ("no-rows",)
    png = reread.crop_rows(pdf_path, rows, dpi=dpi)
    if not png:
        return None, len(rows), ("no-crop",)
    if out_png:
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        with open(out_png, "wb") as handle:
            handle.write(png)
    return png, len(rows), None


def transcribe(png, *, endpoint, max_tokens, timeout):
    """Ask the model what the crop says. Returns `(seen_or_None, raw, error, usage, seconds)`.

    The engine controls come from `ask_about_blocks` rather than being written again here, because
    the thinking switch is engine-specific and a spelling the engine ignores is silent: measured on
    Splash, `chat_template_kwargs.enable_thinking` is accepted with HTTP 200 and takes 31.5s with
    612 characters of reasoning, while `reasoning_effort: none` takes 6.7s with none. Two copies of
    that table is two places for a "thinking off" run to keep thinking.
    """
    import base64

    messages = [
        {"role": "system", "content": reread.SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": "請轉錄這張截圖。"},
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


def finding_from(changes, *, seen, error):
    """The `finding` dict, built from the mechanical diff rather than from the model's prose.

    A transcription model is not asked to judge, so it has no `verdict` to give. Synthesising one
    from the diff is the honest translation: if the page and the extraction differ, the extraction
    is defective **with respect to the page**, and the `where`/`fix` are the diff's own two sides.
    That keeps this record in the same schema as every other finding - one loader, one panel - while
    the value in it comes from a subtraction, not from an opinion.
    """
    if error is not None:
        return {"verdict": None, "what": None, "where": None, "fix": None,
                "rule_worthy": False, "confidence": None, "error": error}
    if not changes:
        return {"verdict": "OK", "what": "NONE", "where": "紙本與抽取一致",
                "fix": "不需要修", "rule_worthy": False, "confidence": 0.9}
    where = "；".join("%s：紙本是「%s」，抽取成「%s」" % (c["field"], c["to"], c["from"])
                      for c in changes)
    fix = "；".join("把 %s 從「%s」改成「%s」" % (c["field"], c["from"], c["to"])
                    for c in changes)
    return {"verdict": "DEFECT", "what": None, "where": where, "fix": fix,
            # Not rule-worthy by construction: this was raised by a detector that already exists,
            # so the class is known and what is left is one reading to fix. Saying `true` here would
            # propose writing a second rule for a shape that already has one.
            "rule_worthy": False, "confidence": 0.8,
            # The raw transcription is kept whole, because the diff is a claim *about* it.
            "transcription": seen}


def crop_output_path(crops_root, pdf_path, number):
    """Where a question's screenshot is kept, under the crops root.

    The paper's own file name identifies the directory, the same name `_adopt_crops` uses, so a later
    rebuild finds these beside the figure crops instead of learning a second directory scheme.
    """
    paper = os.path.basename(pdf_path)[:-4]  # strip .pdf
    return os.path.join(crops_root, paper, "q%s-dispute.png" % str(number).zfill(3))


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


def confirm_one(question, *, endpoint, args, crops_root, queue_root):
    key = question.get("candidate_key")
    number = question.get("question_number")
    pdf_path = paper_pdf_of(question)
    record_base = {
        "candidate_key": key, "question_number": number,
        "kinds": sorted(set(dispute_kinds(question))),
    }
    if not pdf_path:
        return {**record_base, "error": "no-paper", "seconds": 0.0}
    out_png = None
    if crops_root:
        out_png = crop_output_path(crops_root, pdf_path, number)
    png, rows, failure = crop_for(pdf_path, number, dpi=args.dpi, out_png=out_png)
    if failure:
        return {**record_base, "error": failure[0], "rows": rows, "seconds": 0.0}
    seen, raw, error, usage, seconds = transcribe(
        png, endpoint=endpoint, max_tokens=args.max_tokens, timeout=args.timeout)
    report, changes = changes_between(question, seen)
    finding = finding_from(changes, seen=seen, error=error)
    return {**record_base, "pdf": pdf_path, "rows": rows, "crop_png": out_png,
            "crop": queue_relative(out_png, queue_root), "seen": seen, "raw": raw,
            "usage": usage, "seconds": round(seconds, 1), "changes": changes, "report": report,
            "finding": finding, "error": error}


def main() -> int:
    args = parse_args()
    queue = args.queue
    queue_dir = repair_loop.review_ui_dir(queue)
    out = args.out or ai_findings.store_path(queue)

    if args.report:
        return report(out)

    questions = disputed_questions(queue_dir, args.only, args.kind, args.limit)
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
    print("對 %d 題爭議題看紙本並轉錄（%s @ %s）"
          % (len(questions), endpoint["name"], endpoint["url"]))
    print("寫到：%s" % out)
    print("截圖留存：%s" % crops_root)
    print()

    confirmed = 0
    agreed = 0
    failures = 0
    for index, question in enumerate(questions, 1):
        result = confirm_one(question, endpoint=endpoint, args=args, crops_root=crops_root,
                             queue_root=queue_root)
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
        else:
            agreed += 1
            print("[%d/%d] q%-4s %-28s 紙本與抽取一致"
                  % (index, len(questions), number, kinds))
        _append_finding(out, question, result, endpoint, args)

    print()
    print("已記錄 %d 筆：不一致 %d、一致 %d、讀不到 %d。"
          % (len(questions), confirmed, agreed, failures))
    print("這些都是 advisory：沒有任何 review event、沒有任何題目被改（GOV-05）。")
    return 0


def _append_finding(out, question, result, endpoint, args):
    """One finding, in the shared schema, carrying the crop and the mechanical diff."""
    kinds = result.get("kinds") or []
    learned = {"confirm_dispute": "；".join(kinds)} if kinds else None
    finding = result["finding"]
    system, user = ai_findings.build_prompt(question, learned=learned, population="dispute")
    record = ai_findings.make_record(
        question=question, finding=finding, model=endpoint["name"], endpoint=endpoint["url"],
        prompt_system=system, prompt_user=user, raw=result.get("raw") or "",
        usage=result.get("usage") or {}, seconds=result.get("seconds") or 0.0,
        error=None if result.get("error") is None else result["error"],
        learned=learned, population="dispute", crop=result.get("crop"),
        changes=result.get("changes"))
    # The dispute itself, and the crop's size, are the evidence this note is about. Kept beside the
    # note rather than inside it, because a reader has to be able to see the claim and the picture.
    record["evidence"] = {**(record.get("evidence") or {}),
                          "disputes": dispute_reasons(question, []),
                          "rows": result.get("rows"),
                          "crop_bytes": (os.path.getsize(result["crop_png"])
                                         if result.get("crop_png")
                                         and os.path.exists(result["crop_png"]) else None)}
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
    for record in confirms:
        finding = record.get("finding") or {}
        for kind in (record.get("evidence") or {}).get("disputes", [{}]):
            by_kind.setdefault(kind.get("kind") or "?", 0)
            by_kind[kind.get("kind") or "?"] += 1
        if record.get("error"):
            failed += 1
        elif record.get("changes"):
            changed += 1
        else:
            agreed += 1
    print("爭議確認 %d 筆：不一致 %d、一致 %d、讀不到 %d" % (len(confirms), changed, agreed, failed))
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
