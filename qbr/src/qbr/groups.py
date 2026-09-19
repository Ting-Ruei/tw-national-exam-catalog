"""Question groups: several questions that share one printed stem.

A national-exam paper prints groups. One setup paragraph is followed by two, three or four
questions that all ask about it, and the paper signals this in two different ways:

  * **A continuation marker.** `承上題` / `續上題` / `上題` - the question says, in words, that it
    depends on something already printed. Measured over the served queue: 441 questions open with
    `承上題`, and they form 429 runs (414 of length 1, 15 of length 2).
  * **A shared stem with no marker at all.** The setup paragraph and the questions that follow are
    separated on the page by more vertical space than the paper uses between unrelated questions,
    and the questions after it carry no marker. This is the case a keyword search cannot see, and
    it is why this module measures the paper instead of matching words.

Why this must be one module and not a post-pass
-----------------------------------------------
The grouping is a property of the **paper**, so it is decided while the paper is being segmented,
from the same geometry the segmentation already uses. A group found afterwards, by looking at the
finished text, would have to guess the boundary back from the text alone - and the text is exactly
what is damaged when the split is wrong (a shared stem that got attached to only the first question
leaves the others looking like questions with no stem at all).

What the group is for
---------------------
Two things, and they are different:

  1. **Reading.** A reviewer must see the shared stem when looking at the second question of the
     group, or the question is unjudgeable: `承上題，達穩定狀態之平均血中濃度約為多少mg/L？`
     cannot be answered, or reviewed, without `A、B、C、D 四位病人接受某線性一室模式抗生素治療…`.
  2. **Scoring.** A group is one printed unit. If the group is split across a bad question, the
     whole group is suspect together, so the group is the scope a dispute is raised over.

What this module deliberately does NOT do
-----------------------------------------
It does not ask a model. Grouping is a measurement on the page (`shared stem` + `continuation`),
and a model asked "do these share a stem?" would answer plausibly and unverifiably. A model is
useful for reading a damaged stem and not for deciding a boundary that the page already states.

Nor does it invent a stem. The shared stem is the previous question's own stem **as printed**; if
that stem is damaged, the group carries the damage and the damage is reported, rather than being
papered over with a summary.
"""

from __future__ import annotations

import re

#: The paper's own words for "this question depends on one already printed". Taken from the corpus,
#: not invented: each marker here was found in the served queue.
CONTINUATION_MARKERS = (
    "承上題", "續上題", "同上題", "承前題", "接上題", "上題中", "上題之", "上題所",
    "承上述", "同上述", "上述",
)

_CONTINUATION = re.compile(r"^(?:" + "|".join(re.escape(m) for m in CONTINUATION_MARKERS) + r")")

#: The paper **declaring** a group: 「依序回答下列三題」 / 「依序回答下列3 題」 / 「請依上文回答第1 題至第2 題」.
#:
#: This is stronger evidence than a continuation marker and it was missing entirely. Measured over
#: the served queue: **106 questions** carry this phrase and **every one of them had no group at
#: all**, because the marker list only looked at what the *later* questions say. The declaring
#: question names the size itself (95 declare 三題, 11 declare 四題), so the group's extent is
#: stated by the paper rather than inferred - the second and third questions need say nothing.
#:
#: Measured on `1152_藥師(二)_藥學(四)` Q37: its stem ends 「依序回答下列三題。」, Q38 and Q39 follow
#: (`上述劑量調整的原因為何？`), and none of the three carried a `group_ref`, so a reviewer saw three
#: questions with no shared context and Q39 read as a question about nothing.
_DECLARED_GROUP = re.compile(
    r"(?:依序回答下列|請依上文回答第)\s*([一二三四五六七八九十\d]+)\s*題")

#: Chinese numerals as they appear in the declaration. Only the sizes the corpus uses are listed;
#: an unlisted numeral falls back to "the following questions that are continuations", which is the
#: conservative answer rather than a guess at a number.
_GROUP_SIZE_WORDS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                     "七": 7, "八": 8, "九": 9, "十": 10}


def declared_group_size(stem: str):
    """How many questions the paper says belong together, or `None` when it does not say.

    Read from the sentence the paper uses to introduce a group. `None` and `1` are different: `1`
    would mean "the paper declared a group of one", which the corpus never writes, and conflating
    them would end the group at the declaring question.
    """
    hit = _DECLARED_GROUP.search(stem or "")
    if not hit:
        return None
    token = hit.group(1)
    if token.isdigit():
        size = int(token)
    else:
        size = _GROUP_SIZE_WORDS.get(token)
    # A declaration of one, or of a number this module cannot read, is not a group: the declaring
    # sentence alone does not make a group of one, and guessing the size would attach questions the
    # paper never tied together.
    return size if size and size >= 2 else None

#: How much more vertical space than the paper's own question gap means "a new group starts here".
#:
#: **Kept as a record of a measurement, not as a rule.** It is deliberately not used anywhere, and
#: the measurement is why: over 377 real group transitions (head -> member) the gap median is
#: **104.9 pt**, while over 28,137 non-group adjacent questions it is **99.8 pt**. The two
#: distributions are not two populations; they overlap almost completely (p25 77.8 vs 69.6, p75
#: 147.8 vs 127.1). Swept over every threshold from 20 to 300 pt, the best achievable precision is
#: **6.9%** - 13.4 false positives for every true group found. A rule that cannot be told apart from
#: a coin flip is not a rule, and adopting it would have marked half the corpus as grouped.
#:
#: This is left here rather than deleted because "the gap does not separate groups" is itself a
#: finding that cost a sweep to establish, and the next person to think of it should find the number
#: instead of repeating the work.
GROUP_GAP_RATIO = 1.8


def continuation_of(stem: str) -> str:
    """The continuation marker a stem opens with, or `""`.

    Matched at the start only. `承上題` in the middle of a stem is part of the question's own
    sentence and does not make the question a continuation.
    """
    hit = _CONTINUATION.match((stem or "").strip())
    return hit.group(0) if hit else ""


def is_continuation(stem: str) -> bool:
    return bool(continuation_of(stem))


def group_runs(items):
    """Split one paper's questions into groups, in order.

    `items` is an iterable of mappings with `question_number` and `stem`, already sorted by
    question number. Returns a list of groups, each a list of those mappings. A question that is
    not a continuation and is not followed by one is a group of one, which is most of the paper.

    Two signals, and the paper states both:

    1. **A declaration.** One question says 「依序回答下列三題」. That sentence names the size, so
       the group is the declaring question and the next two - the following questions need carry no
       marker at all. Measured: **106 questions** declare a group this way and had none.
    2. **A continuation marker.** `承上題` on a later question, which binds it to the question
       printed immediately before it.

    The declaration is applied first because it is the stronger statement: it gives the size, while
    a marker only relates two neighbours. A declaration is also given **precedence over the marker
    rule inside its own extent**, so a group of three stays one group of three even when its last
    member happens to open with a marker.
    """
    ordered = sorted(items, key=lambda item: int(item.get("question_number") or 0))
    groups = []
    # How many more questions the current declared group still expects. While this is positive, the
    # next question joins the current group whether or not it carries a marker.
    expecting = 0
    for item in ordered:
        stem = item.get("stem") or ""
        if expecting > 0:
            groups[-1].append(item)
            expecting -= 1
            continue
        if groups and is_continuation(stem):
            groups[-1].append(item)
            continue
        groups.append([item])
        # A question that declares a group of N also asks the first of them, so N-1 follow it.
        size = declared_group_size(stem)
        if size:
            expecting = size - 1
    return groups


def heads_and_gaps(groups, *, gap_of=None):
    """The group head and the vertical gap that opened it, for `gap_of(item)`.

    `gap_of` answers the paper's leading for one question (`None` when unknown). Kept separate
    from `group_runs` so the geometry can be reported and measured without being able to change
    the grouping that the marker already decided.
    """
    out = []
    for group in groups:
        head = group[0]
        gap = gap_of(head) if gap_of else None
        out.append({"head": head, "members": group,
                    "size": len(group), "gap": gap,
                    "marker": continuation_of(group[1].get("stem") or "") if len(group) > 1 else ""})
    return out


def shared_stem_of(group):
    """The stem every member of a group after the first depends on.

    The head's own stem, unmodified. `None` for a group of one - a question that stands alone has
    no shared stem, and returning its own stem here would make every question in the paper look
    like part of a group.
    """
    if len(group) < 2:
        return None
    return group[0].get("stem") or ""


def group_key(paper_key: str, head_number) -> str:
    """A stable name for a group: the paper and the head question's number.

    Keyed on the head rather than on the member, because the group is the unit a dispute is raised
    over and every member must resolve to the same name.
    """
    return f"{paper_key}:group:q{int(head_number):03d}"


def bind(items, *, paper_key=""):
    """Attach `group_ref` and `shared_stem` to every question of a paper.

    Returns the groups. Each member - the head included - carries the group's `group_ref`, so a
    reviewer on the second question can fetch the first. `shared_stem` is set only on members after
    the first.
    """
    groups = group_runs(items)
    for group in groups:
        if len(group) == 1:
            for item in group:
                item["group_ref"] = None
                item["shared_stem"] = None
            continue
        key = group_key(paper_key, group[0].get("question_number"))
        stem = shared_stem_of(group)
        for position, item in enumerate(group):
            item["group_ref"] = key
            item["group_position"] = position
            item["group_size"] = len(group)
            item["shared_stem"] = stem if position else None
    return groups


def _group_reason(group):
    """Why these questions are one group: the paper's own declaration, or a continuation marker."""
    declared = declared_group_size(group[0].get("stem") or "")
    if declared and len(group) >= 2:
        return "declared:%d" % declared
    if len(group) >= 2:
        return "marker:" + (continuation_of(group[1].get("stem") or "") or "?")
    return ""


def summarise(items):
    """Counts for reporting, so a run's grouping can be checked at a glance."""
    groups = group_runs(items)
    sizes = {}
    for group in groups:
        sizes[len(group)] = sizes.get(len(group), 0) + 1
    reasons = {}
    for group in groups:
        reason = _group_reason(group)
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "groups": len(groups),
        "grouped_questions": sum(len(g) for g in groups if len(g) > 1),
        "sizes": dict(sorted(sizes.items())),
        "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "markers": sorted({continuation_of((g[1].get("stem") or "")) for g in groups if len(g) > 1}),
    }
