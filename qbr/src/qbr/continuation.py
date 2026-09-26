"""Whether a shipped field stopped where the paper did not: the acceptance standard's instrument.

The charter's standard is "two engines agreeing on the same sheet". Taken as *literal text
agreement* that is unreachable, and measurably so: engine A carries font size (which is the only
way to know a superscript's direction) and engine B does not; engine B puts an option's marker, and
each wrapped line, on a row of its own, while A keeps the marker on the text's line. Those are
differences in what the two instruments *measure*, not disagreements about the paper
(`qbr/ENGINE_STRATEGY.md` 4.1).

What is reachable, and is what the standard should have meant:

    every field we ship must be FOUND by the second instrument at the boundary that instrument
    reports. A field no second reading can find is the thing worth escalating.

`option-continuation-loss` is the first check under that standard, and it is the largest unflagged
defect class found so far. Measured on 160 sampled papers (12,840 questions), taking a line WRAP as
the unit:

    engine A alone sees 631, engine B alone sees 9,157, BOTH see 463 fields on 301 questions
    (2.34%), across 75 of the 160 papers.

The single-engine counts differ by an order of magnitude because the two engines split lines
differently. A loss seen by BOTH is not that: two independent readings agree the paper continues
while our field stops. That intersection is the threshold, and using either engine alone would
either drown the reviewer (9,157) or miss two thirds of the real cases.

Why this lives in a module rather than in the script that calls it: S6 has to report the number, and
a number reported by a script nobody tests is the kind of claim this project has already been burned
by. The negative control in `tests/test_option_continuation.py` is what makes the number a
measurement.
"""
import re

#: An option's marker at the very start of a line. Only the start counts: inside a line, "A." is
#: as likely to be a chemical formula or an abbreviation as a marker, and the engines disagree
#: about which - that disagreement is the reason this module exists.
_OPTION_MARK = re.compile(r"^\s*([A-Za-z])[.．]\s*(.*)$")
#: A question number at the very start of a line.
_QUESTION_NUMBER = re.compile(r"^\s*(\d{1,3})[.．]")
#: Below this many characters a line is a fragment and carries no evidence either way. A one- or
#: two-character continuation is real on the paper (`異常`) but cannot be told from a stray glyph
#: of the other engine, so the caller's own threshold belongs here rather than a guess.
MIN_CONTINUATION = 2
#: A shipped option shorter than this is too short for a prefix comparison to mean anything.
MIN_SHIPPED = 10


def _dense(text):
    """Whitespace removed. The engines place the same sentence differently along the line."""
    return "".join((text or "").split())


def continuation_losses(lines, shipped):
    """Option text the paper continues but the shipped field does not contain.

    `lines` is one engine's reading, in reading order. `shipped` maps a question number to
    `{option key: shipped text}` - the fields as packaged, from `candidates.jsonl` or from any
    stage holding them.

    An option's text runs from its marker to the next thing that opens a line (a marker or a
    number). Everything between is that option's continuation, and it may be several lines: engine
    B spreads one option over three or four rows where A uses two. If the shipped text is a strict
    prefix of what the engine shows, the field stopped at a wrap and the remainder is the evidence.

    This measures ONE engine. A loss that only one engine sees may be that engine's line
    splitting, which is why `verify_paper` runs both and reports the intersection.
    """
    out = []
    current_question = None
    current_option = None
    buffer = []

    def flush():
        """Compare the option just completed against the field as shipped."""
        if current_question is None or current_option is None:
            return
        full = _dense("".join(buffer))
        text = _dense((shipped.get(current_question) or {}).get(current_option, ""))
        if len(text) < MIN_SHIPPED or len(full) <= len(text) or not full.startswith(text):
            return
        remainder = full[len(text):]
        if len(remainder) < MIN_CONTINUATION:
            return
        out.append({"question_number": current_question, "option": current_option,
                    "shipped_tail": text[-45:], "dropped": remainder[:45]})

    for line in lines or []:
        number = _QUESTION_NUMBER.match(line or "")
        if number:
            flush()
            current_question = int(number.group(1))
            current_option, buffer = None, []
            continue
        marker = _OPTION_MARK.match(line or "")
        if marker:
            flush()
            current_option, buffer = marker.group(1), [marker.group(2)]
            continue
        if current_option is not None:
            buffer.append(line or "")
    flush()
    return out


def verify_paper(lines_a, lines_b, shipped):
    """What both engines agree is missing, and what each saw alone.

    The return is deliberately three numbers rather than one: the single-engine counts are what
    show the intersection is doing work. A caller that reports only the total cannot tell a real
    defect from one engine's line splitting.
    """
    found_a = continuation_losses(lines_a, shipped)
    found_b = continuation_losses(lines_b, shipped)
    keys_a = {(item["question_number"], item["option"]) for item in found_a}
    keys_b = {(item["question_number"], item["option"]) for item in found_b}
    both = sorted(keys_a & keys_b)
    return {
        "both": [item for item in found_a if (item["question_number"], item["option"]) in both],
        "only_a": sorted(keys_a - keys_b),
        "only_b": sorted(keys_b - keys_a),
        "loss_a": len(keys_a), "loss_b": len(keys_b), "loss_both": len(both),
    }


def confirm_against(losses, lines, shipped):
    """The subset of `losses` that the OTHER engine's reading corroborates.

    This exists because the intersection in `verify_paper` is not sufficient once the fields are
    produced FROM one of the two readings. `segment_best(engine_a_lines)` derives the fields from
    A, so checking those fields against A's lines cannot fail - the count came back 0 the run after
    the wrap fix, which proves the check there is a tautology, not a clean result.

    What is not a tautology: the other engine prints the field's own text and the dropped text as
    ONE CONTINUOUS RUN. Then that engine read the option as an unbroken string that the shipped
    field stops in the middle of - which is the claim worth acting on. Two weaker tests were tried
    and rejected while building this:

    * `dropped in haystack` - nearly always true, because the other reading contains the whole
      paper. It reported 1,784 corroborated losses on the FIXED code, which is how it was caught.
    * `losses` seen by both engines - not available when the fields come from one of them, and a
      tautology when they do.

    A loss only the one engine reports is that engine's own line splitting, and is not evidence
    against the field.
    """
    haystack = _dense("".join(lines or []))
    out = []
    for item in losses or []:
        text = _dense((shipped.get(item["question_number"]) or {}).get(item["option"], ""))
        dropped = _dense(item.get("dropped", ""))
        if len(dropped) >= MIN_CONTINUATION and text and (text + dropped) in haystack:
            out.append(item)
    return out


def summarise(papers):
    """Totals over `verify_paper` results, for the queue index and for S6."""
    loss_a = loss_b = loss_both = 0
    questions = set()
    for index, result in enumerate(papers):
        loss_a += result.get("loss_a") or 0
        loss_b += result.get("loss_b") or 0
        loss_both += result.get("loss_both") or 0
        for item in result.get("both") or []:
            questions.add((index, item["question_number"]))
    return {"papers": len(papers), "loss_a": loss_a, "loss_b": loss_b,
            "loss_both": loss_both, "questions_with_loss": len(questions)}
