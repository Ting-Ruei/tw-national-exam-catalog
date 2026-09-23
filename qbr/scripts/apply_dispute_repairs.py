# -*- coding: utf-8 -*-
"""Apply the repairs that are **already decided by measurement** - no model opinion involved.

The gap this fills
------------------

The loop had three halves that did not meet:

    a `dispute` names the exact place and the exact character (`h-1` at stem[88], `⻑` -> `長`)
    a `confirm_dispute` finding carries the same thing when the page agrees (`changes`)
    the review UI writes a `correct` event, which *overlays* `stem`/`options` for the reader; and
    this step writes a `reset_review` that carries the same correction (see below for why a reset
    and not a `correct`)

and nothing joined them, so 304 blocked questions sat with prose findings and **215 of them had no
text change at all** (measured: 89 changed, 215 untouched). A finding that says "把 X 改成 Y" is a
description; this is the change.
What is applied, and what is not
--------------------------------

Only the two kinds whose repair is **deterministic** - the target character is in the dispute itself:

    substituted-ideograph  `⻑` -> `長`                     (the dispute carries `means`)

and explicitly **not** the `flat-offset` class any more, and that is a measured reversal. It was
in this list; the measurement that took it out is in `extract._body_centre`. `cm-1` was not a defect
in the text, it was a defect in the *reading*: the page raises the `-1` (9.03pt at y-centre 446.04
against 10.83pt prose at 449.93) and the extractor averaged the raised run into the baseline, so the
shift measured -1.61 instead of -2.82 and the run printed flat. Applying a substitution would have
written the right characters while leaving the reader that made them flat still in place, and the
next paper would have produced the same class again. The repair is the extractor, and it is measured
end to end: rebuilding `1141_醫事放射師_醫學物理學與輻射安全` now yields `0.5 R m² Ci⁻¹ h⁻¹` and the
`flat-offset` dispute is gone.

Explicitly **not** applied, and the reason is measured rather than stylistic:

    substituted-script     the dispute names the script (CYRILLIC) and *not* the intended character;
                           a Cyrillic `ћ` in `不可變參數？ћ總時間` is not recoverable by rule. 26
                           questions carry this and all 26 are left alone.
    lost-glyph             the paper has a character the text layer lacks; only the page says which.
    flattened-offset       the exponent form does not exist in Unicode; nothing to substitute.
    option-shape, empty-option, punctuation-only-option, dangling-answer, table-flattened
                           these are "content is missing", not "content is wrong" - the repair is a
                           re-read of the page, which is `confirm_dispute`'s job, not a substitution.
    the model findings     `qbr_ai_finding` is advisory (AGENTS.md); a prose `fix` never edits text.

Why an event and not a rewrite of `candidates.jsonl`
----------------------------------------------------

The same reason `append_reset_review_events.py` gives. A candidate file is a *reading of the paper*;
a correction is a *decision about* that reading, and the two are different artifacts with different
lifetimes. Writing the event keeps the original reading intact (`parser_original` is preserved by the
server for exactly this), records who decided what, and survives a queue rebuild. Editing the
candidate text would destroy the evidence that the extraction was wrong - which is the thing the
rule was built from.

**The event is one `reset_review`, not a `correct` plus a reset.** `load_review_events` counts every
non-reset action as "reviewed", so a machine `correct` on a question nobody has judged makes it read
as **已看過** - a machine event wearing a person's decision, which AGENTS.md forbids outright. The
server reads a `correction` from `latest_reset_review` as well as from `latest`, so one reset event
both serves the fixed text and lands the question in `repair_pending` (**修復後待複核**) - repaired,
waiting for a person, which is what happened.

Governance
----------

    G3. This mutates the review state of questions a person may already have judged, so `--apply`
    is required. Dry run is the default, and the reviewer name carries the server's `repair_`
    prefix so the projection cannot read the event as a human decision. The event log's sha256
    before the write is printed, and a backup of the log is taken before the append.

    It never writes `accept`. A repair reopens a question; it does not close one.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import ai_findings, extract  # noqa: E402

#: Kinds whose target character is carried by the dispute itself.
#: `flat-offset` is deliberately absent - it is repaired in `extract._body_centre`, at the reading.
APPLICABLE = ("substituted-ideograph",)
#: The actions a person's decision can stand at. Used only to *report* the previous state; the
#: write path does not branch on it (a `reset_review` is correct for a judged and an unjudged
#: question alike, because it never claims a verdict).
HUMAN_ACTIONS = {"accept", "block", "needs_review", "exclude", "unblock", "reviewed", "correct"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; candidates and the review log live under its review-ui/")
    parser.add_argument("--events", help="review-event log; default <queue>/review-ui/"
                                         "question_review_events.jsonl")
    parser.add_argument("--out", help="also write the resulting log here")
    parser.add_argument("--only", nargs="*", default=None,
                        help="question numbers (q004 or 4); default is every applicable question")
    parser.add_argument("--kind", nargs="*", default=None,
                        help="restrict to these dispute kinds (default: %s)" % (APPLICABLE,))
    parser.add_argument("--page-read", action="store_true",
                        help="also apply the repairs confirmed by a **page reading** against the "
                             "question's own disputes (advisory findings, population=dispute). "
                             "The model transcribes and this script subtracts; only substitutions "
                             "anchored on a detector's own flagged positions are applied")
    parser.add_argument("--limit", type=int, default=0, help="repair at most this many (0 = all)")
    parser.add_argument("--reviewer", default="repair_dispute_apply",
                        help="who is recorded as making the repair. Must carry the server's "
                             "`repair_` reviewer prefix (REPAIR_REVIEWER_PREFIXES) or the projection "
                             "would count a machine repair as a human decision")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it this is a dry run")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_candidates(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def latest_events(path: Path) -> dict[str, dict]:
    """The latest event per key, for the re-check that keeps a repair from undoing a decision.

    Read the same way the server reads it (`latest` wins), so "what action stands" cannot be one
    thing here and another there.
    """
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if key:
                latest[key] = event
    return latest


def field_text(question: dict, field: str) -> str:
    if field == "stem":
        return str(question.get("stem") or "")
    for option in question.get("options") or []:
        if "option %s" % option.get("key") == field:
            return str(option.get("text") or "")
    return ""


def substitutions_for(question: dict) -> list[dict]:
    """Every deterministic substitution this question's disputes imply, in field order.

    One `{field, position, before, after}` per place. `position` comes from the dispute, which
    measures it on the stored text, so it can be sliced directly. `substituted-ideograph` is the
    only kind here: its dispute carries `means` (the character the paper meant) beside the character
    the text layer stored, which is what makes the repair a substitution rather than a decision.

    `flat-offset` is deliberately not read, even though its dispute also carries a target - see the
    module docstring; that class is repaired at the reading (`extract._body_centre`), so substituting
    the characters here would fix the symptom and leave the cause in place.
    """
    out = []
    for dispute in question.get("disputes") or []:
        if not isinstance(dispute, dict):
            continue
        kind = dispute.get("kind")
        if kind == "substituted-ideograph":
            for sub in dispute.get("substitutions") or []:
                if sub.get("position") is None or not sub.get("char") or not sub.get("means"):
                    continue
                out.append({"field": sub.get("field"), "position": int(sub["position"]),
                            "before": sub["char"], "after": sub["means"], "rule": kind})
    return out


def flagged_positions(question: dict, field: str) -> dict:
    """`position -> substitution` for one field, from the question's own disputes.

    This is the anchor the page-read path is allowed to edit: the places a detector *already measured*
    as carrying a character that is not what the paper prints. Everything else in the field is text
    nobody has doubted, and a repair has no business rewriting it.
    """
    out = {}
    for dispute in question.get("disputes") or []:
        if not isinstance(dispute, dict):
            continue
        for sub in dispute.get("substitutions") or []:
            if sub.get("field") == field and sub.get("position") is not None:
                out[int(sub["position"])] = sub
    return out


def _without_whitespace(text: str):
    """The text's non-whitespace characters, each with the index it came from.

    Whitespace is dropped because the page read is a second *reading* of the same line: a model that
    collapses the two spaces the extractor kept between numbered items has not changed the content,
    and refusing the whole repair over a space would put the one character that does matter out of
    reach. The original indices are kept so the anchor check compares positions on the stored text.
    """
    chars, origins = [], []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        origins.append(index)
    return chars, origins


def anchored_page_changes(stored: str, page: str, flagged: set) -> list[dict] | None:
    """The substitutions a page reading implies, or `None` if it is not a repair but a rewrite.

    The rule, and every clause of it is a measured refusal from this corpus:

    * **Equal length per run.** A run that changes the *number* of characters is not a substitution:
      it moved every later position, and the diff that follows it is about a line that has become a
      different line. Measured: `115090 q053`'s read inserted 93 characters and deleted the four
      options, `114020 q060` inserted 80.
    * **Replace only.** An `insert`/`delete` run is the same failure expressed as a boundary instead
      of a length change. Measured: `113020 q076`'s read appended a table, `113020 q050` a figure
      description, `105020 q045` a whole compartment diagram with invented arrow labels.
    * **Every position must already be flagged, and every flagged position fixed.** Touching a
      character no detector doubted is the model editing prose; leaving a doubted one untouched is a
      partial repair that would reopen the question with the defect still in it. Both measured: the
      `flattened-offset` class (`C=5e-0.4t` -> `C=5e⁻⁰·⁴ᵗ`) has **no** flagged position at all, and
      reading it as a repair would rewrite physics formulas on a model's word alone - it is repaired
      at the reading, not here.
    """
    if not stored or not page:
        return None
    if stored == page:
        return None
    stored_chars, stored_origins = _without_whitespace(stored)
    page_chars, _ = _without_whitespace(page)
    matcher = difflib.SequenceMatcher(None, "".join(stored_chars), "".join(page_chars),
                                      autojunk=False)
    touched, changes = set(), []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            return None
        touched |= set(range(i1, i2))
        changes.append({"positions": (i1, i2), "from": "".join(stored_chars[i1:i2]),
                        "to": "".join(page_chars[j1:j2])})
    if not changes:
        return None
    if {stored_origins[i] for i in touched} != set(flagged):
        return None
    # Back to the stored field's own coordinates. `verify` and `apply_substitutions` slice the
    # original text, not the folded one - a position in the folded text would edit the wrong
    # character whenever the field contains a space, which the corpus's formulas and numbered
    # option lists always do. A run whose original indices are not contiguous cannot be expressed as
    # one `{position, before}` pair, so it is refused rather than silently split.
    located = []
    for change in changes:
        i1, i2 = change["positions"]
        originals = [stored_origins[i] for i in range(i1, i2)]
        if originals != list(range(originals[0], originals[-1] + 1)):
            return None
        located.append({"position": originals[0],
                        "before": stored[originals[0]:originals[-1] + 1],
                        "after": change["to"]})
    return located


def page_read_substitutions(question: dict, changes: list[dict]) -> list[dict]:
    """The `{field, position, before, after}` list a *confirmed page reading* justifies.

    Only for fields the reading covers and only where every edit is anchored on a detector's own
    measurement - see `anchored_page_changes`. The `before` text is taken from the stored field, not
    from the finding, so a stale finding (the text moved since it was written) refuses in `verify`
    instead of applying an edit to the wrong place.
    """
    out = []
    for change in changes or []:
        field = change.get("field")
        if not field:
            continue
        flagged = set(flagged_positions(question, field))
        anchored = anchored_page_changes(field_text(question, field),
                                         str(change.get("page") or ""), flagged)
        if not anchored:
            return []
        for edit in anchored:
            out.append({"field": field, "position": edit["position"],
                        "before": edit["before"], "after": edit["after"],
                        "rule": "page-read"})
    return out


def verify(question: dict, subs: list[dict]) -> list[str]:
    """Why a substitution cannot be applied, as a list of complaints (empty = safe).

    Checks the *stored* text at the claimed position, because a dispute is a claim about the storage
    and the claim has to still hold at write time. A stale dispute (the text changed since it was
    measured) must refuse rather than replace the wrong character.
    """
    complaints = []
    for sub in subs:
        text = field_text(question, sub["field"])
        at = sub["position"]
        actual = text[at:at + len(sub["before"])]
        if actual != sub["before"]:
            complaints.append("%s[%d] 是 %r，不是 %r（dispute 已過期）"
                              % (sub["field"], at, actual, sub["before"]))
    return complaints


def apply_substitutions(text: str, subs: list[dict]) -> str:
    """Replace right-to-left so an earlier replacement cannot move a later position.

    Order is not cosmetic: `h-1` at 88 and another run at 12 must both land at the index the dispute
    measured, and replacing left-to-right would shift everything after the first edit.
    """
    value = text
    for sub in sorted(subs, key=lambda s: -s["position"]):
        at = sub["position"]
        value = value[:at] + sub["after"] + value[at + len(sub["before"]):]
    return value


def build_correction(question: dict, subs: list[dict]) -> dict:
    """The `correction` payload the server understands: `stem` and/or `options`, whole.

    Whole fields rather than a diff, because that is the shape `normalized_correction` accepts and
    the overlay applies - and because a partial field would leave the reader unable to tell which
    text is the corrected one.
    """
    by_field: dict[str, list[dict]] = {}
    for sub in subs:
        by_field.setdefault(sub["field"], []).append(sub)
    correction: dict = {}
    if "stem" in by_field:
        correction["stem"] = apply_substitutions(field_text(question, "stem"), by_field["stem"])
    touched = {f for f in by_field if f != "stem"}
    if touched:
        options = []
        for option in question.get("options") or []:
            name = "option %s" % option.get("key")
            row = {"key": option.get("key"), "text": str(option.get("text") or "")}
            if name in touched:
                row["text"] = apply_substitutions(row["text"], by_field[name])
            options.append(row)
        correction["options"] = options
    return correction


def build_repair_event(question_key: str, subs: list[dict], correction: dict,
                       previous: dict | None, reviewer: str, created_at: str) -> dict:
    """One `reset_review` event that carries the repair.

    A single event of this action, and not a `correct` followed by a `reset_review`, and the reason
    is measured rather than stylistic. `load_review_events` puts every action that is not a group or
    reset action into `latest`, and `review_projection` calls that "reviewed". A machine `correct`
    on a question nobody has judged therefore makes it read as **已看過** - a machine event
    presented as a person's decision, which is the one thing AGENTS.md forbids outright ("an agent
    must never impersonate a human reviewer").

    `reset_review` is the action that does the two things the repair needs and nothing more: the
    server reads the correction from `latest_reset_review` as well as `latest` (so the fixed text is
    served), and the projection puts the question in `repair_pending` - **修復後待複核**, "repaired,
    waiting for a person" - which is what actually happened. It neither claims a decision nor hides
    the question.

    `previous_action` and its notes travel with the event so the reviewer can see what they had
    decided before the text moved underneath them; `_reaffirm_standing_action` is not involved
    because no verdict is being written here.
    """
    changes = [{"field": s["field"], "from": s["before"], "to": s["after"]} for s in subs]
    summary = "；".join("(%s→%s)" % (s["before"], s["after"]) for s in subs)[:120]
    return {
        "candidate_key": question_key,
        "action": "reset_review",
        "correction": correction,
        "reviewer": reviewer,
        "source": "qbr_dispute_apply",
        "repair_kind": "content_change",
        "notes": "依 dispute 的機械證據修復：" + summary,
        "reset_notes": "依 dispute 的機械證據修復：" + summary,
        "previous_action": (previous or {}).get("action"),
        "previous_notes": (previous or {}).get("notes") or "",
        "previous_reviewed_at": (previous or {}).get("created_at"),
        "changes": changes,
        "created_at": created_at,
    }


def applied_signature(event: dict):
    """What a repair event already changed, as an order-independent frozenset of edits.

    The queue's candidate text is **not** rewritten by design - a correction is an event that overlays
    the field, so the original reading survives. That means `substitutions_for` still finds the same
    `⻑ -> 長` on the next run, and without this the tool would append a second identical repair for
    every question it had already fixed (measured: 192 such events were already in the log). Comparing
    the edits, not the count, is what makes a *re*-repair possible: if the text has moved since, the
    signature differs and the question is repaired again, which is correct.
    """
    if not event or event.get("source") != "qbr_dispute_apply":
        return None
    return frozenset((str(c.get("field")), str(c.get("from")), str(c.get("to")))
                     for c in event.get("changes") or [])


def last_repair_signature(path) -> dict:
    """`candidate_key -> edits` of the **most recent `qbr_dispute_apply`** event for that question.

    Not the latest event overall, and the difference was measured. A person can review a question
    after the repair - the station's clock is UTC while the laptop's is UTC+8, so a human `accept`
    stamped `03:28:55` is appended **after** a repair stamped `11:25:44` - and the projection is
    last-line-wins, so the repair stops being the latest event. Reading the signature off the latest
    event then returned `None`, and the next `--page-read` run would have appended a *second*
    identical repair, re-resetting a question a person had just accepted. That is the one outcome
    this tool must never produce: it would silently undo a human decision.

    Scanning for the last repair event instead of the last event keeps the check about the tool's own
    work, and a human decision in between no longer erases the memory of it.
    """
    out = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if key and event.get("source") == "qbr_dispute_apply":
                out[key] = event
    return out


def repair_signature(subs: list[dict]):
    return frozenset((str(s["field"]), str(s["before"]), str(s["after"])) for s in subs)


def main() -> int:
    args = parse_args()
    queue_dir = os.path.join(args.queue, "review-ui")
    candidates_path = Path(os.path.join(queue_dir, "candidates.jsonl"))
    events_path = Path(args.events or os.path.join(queue_dir, "question_review_events.jsonl"))
    if not candidates_path.is_file():
        print("找不到 candidates.jsonl：%s" % candidates_path, file=sys.stderr)
        return 2
    if not events_path.is_file():
        print("找不到 review event log：%s" % events_path, file=sys.stderr)
        return 2

    wanted_kinds = set(args.kind) if args.kind else set(APPLICABLE)
    wanted_numbers = None
    if args.only:
        wanted_numbers = {str(item).lower().lstrip("q").lstrip("0") or "0" for item in args.only}

    # The page readings, when asked for. Read from the same finding stream the reviewer's screen
    # shows, so the repair is applied to exactly the diff a person can open and check.
    page_findings = {}
    if args.page_read:
        store = os.path.join(queue_dir, ai_findings.STREAM)
        for key, record in ai_findings.latest_by_question(store).items():
            if record.get("population") == "dispute" and record.get("changes"):
                page_findings[key] = record

    latest = latest_events(events_path)
    # The tool's own past repairs, found by scanning for `qbr_dispute_apply` rather than reading the
    # latest event. A human decision appended after a repair (their clock, or just their turn) must
    # not erase the memory that the repair already happened - otherwise the next run duplicates it
    # and re-resets a question a person just accepted. See `last_repair_signature`.
    prior_repairs = last_repair_signature(events_path)
    before = sha256_file(events_path)
    created_at = datetime.now().isoformat(timespec="seconds")

    planned, refused = [], []
    for question in load_candidates(candidates_path):
        number = str(question.get("question_number")).lstrip("0") or "0"
        if wanted_numbers is not None and number not in wanted_numbers:
            continue
        subs = [s for s in substitutions_for(question) if s["rule"] in wanted_kinds]
        if args.page_read:
            record = page_findings.get(question.get("candidate_key"))
            if record:
                subs = subs + page_read_substitutions(question, record.get("changes") or [])
        if not subs:
            continue
        complaints = verify(question, subs)
        if complaints:
            refused.append({"candidate_key": question["candidate_key"], "why": complaints})
            continue
        previous = latest.get(question["candidate_key"]) or {}
        previous_action = previous.get("action")
        # Already repaired, with exactly these edits. The candidate text is not rewritten (by
        # design), so the same substitution is found again every run; without this the tool would
        # duplicate its own past work. A different edit set - the text moved since - is not skipped.
        if applied_signature(prior_repairs.get(question["candidate_key"])) == repair_signature(subs):
            continue
        correction = build_correction(question, subs)
        planned.append({"candidate_key": question["candidate_key"], "subs": subs,
                        "correction": correction, "previous_action": previous_action,
                        "previous": previous})
        if args.limit and len(planned) >= args.limit:
            break

    print("queue        : %s" % args.queue)
    print("events file  : %s" % events_path)
    print("sha256 before: %s" % before)
    print("to repair    : %d" % len(planned))
    for item in planned:
        print("  %-44s %-14s %s" % (
            item["candidate_key"].replace("moex:", ""),
            item["previous_action"] or "-",
            "；".join("%s[%d] %r→%r" % (s["field"], s["position"], s["before"], s["after"])
                      for s in item["subs"])[:96]))
    if refused:
        print()
        print("REFUSED (%d) - not touched:" % len(refused))
        for item in refused:
            print("  %-44s %s" % (item["candidate_key"].replace("moex:", ""),
                                   "；".join(item["why"])[:90]))

    if not args.apply:
        print()
        print("dry run；pass --apply to write")
        return 0

    # The re-check that makes a write safe: a person may have decided a question between the
    # measurement above and this line, and reopening it would silently undo their work. This is the
    # same guard `append_reset_review_events.py` uses, and it is re-read from the file rather than
    # trusted from the earlier snapshot.
    now_latest = latest_events(events_path)
    confirmed = []
    for item in planned:
        if (now_latest.get(item["candidate_key"]) or {}).get("action") != item["previous_action"]:
            refused.append({"candidate_key": item["candidate_key"],
                            "why": ["寫入前重驗失敗：判定已變（%s → %s）" % (
                                item["previous_action"],
                                (now_latest.get(item["candidate_key"]) or {}).get("action"))]})
            continue
        confirmed.append(item)

    events = [build_repair_event(item["candidate_key"], item["subs"], item["correction"],
                                 item["previous"], args.reviewer, created_at)
              for item in confirmed]

    # A backup first, because this file is the reviewer's history: a wrong append must be a file to
    # put back, not a lost decision.
    backup = events_path.with_name(events_path.name + ".before-" + created_at.replace(":", ""))
    backup.write_bytes(events_path.read_bytes())
    with events_path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    after = sha256_file(events_path)
    print()
    print("backup       : %s" % backup)
    print("appended     : %d reset_review events (carrying the correction)" % len(events))
    print("sha256 after : %s" % after)
    if args.out:
        with Path(args.out).open("w", encoding="utf-8") as handle:
            handle.write(events_path.read_text(encoding="utf-8"))
        print("copy written : %s" % args.out)
    print()
    print("全部是 append-only 事件；原始抽取文字仍在 candidates.jsonl，由伺服器的 parser_original 保存。")
    print("結果桶位是「修復後待複核」：機器不宣告任何人的判定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
