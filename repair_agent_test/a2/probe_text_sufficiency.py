#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is the human's correction a function of the text the model is shown? (A2, the decisive probe)

`compare_models.py` measures how often a model reproduces a reviewer's correction. That number is
only meaningful if the correction is *derivable from the input the model gets*. If most corrections
turn on a character the shipped text does not contain - `ax` where the paper says `max`, `酶` that
was dropped altogether - then no text-only model can reach them, and a low score says nothing about
the model. It says the harness asked the wrong question.

So this probe asks the narrower, answerable one, per touched field:

    recoverable   the person's text differs from the shipped text only by things the *string itself*
                  contains - compatibility/decomposition variants (`ৡ`→`類` via NFKC), whitespace,
                  markup of the same characters. A model could in principle get there from the text.
    needs_source  the person introduced at least one character (other than a variant) that is not in
                  the shipped text, or removed one, or the change is a substitution only the picture
                  can justify. **The information is not in the input.**

The output is the share of corrections that are *out of reach* of a text-only pass. That share is a
property of the corpus and the pipeline, not of any model, which is why it is measured separately.

Usage:
    python3 probe_text_sufficiency.py
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
MARKUP = re.compile(r"</?(?:sub|sup|i|b|em|strong)\s*/?>", re.I)


def visible(text: str) -> str:
    """The characters a reader would see, with markup and whitespace removed."""
    return re.sub(r"\s+", "", MARKUP.sub("", str(text or "")))


def _base_fold(text: str) -> str:
    """Collapse the variants one codepoint can be spelled as.

    NFKC folds `ﬁ`→`fi`, full-width `Ａ`→`A`, and the compatibility ideographs. What it does **not**
    fold is a glyph read wrongly by the OCR (`ৡ` for `類`): those are two unrelated codepoints, and
    no amount of normalisation turns one into the other. That distinction is the whole measurement.
    """
    return unicodedata.normalize("NFKC", text)


def classify(shipped: str, human: str) -> str:
    """`markup_only`, `foldable`, `same_chars_respelled`, `gained_chars`, `lost_chars`, `swapped`.

    The names are about *where the information could have come from*, not about difficulty:
    `markup_only` and `foldable` need nothing but the string; `gained_chars`, `lost_chars` and
    `swapped` need the paper or the domain, because the shipped text does not carry them.
    """
    a, b = visible(shipped), visible(human)
    if a == b:
        return "markup_only"
    if _base_fold(a) == _base_fold(b):
        return "foldable"
    fa, fb = _base_fold(a), _base_fold(b)
    if set(fa) == set(fb):
        return "same_chars_respelled"
    gained = collections.Counter(fb) - collections.Counter(fa)
    lost = collections.Counter(fa) - collections.Counter(fb)
    if gained and not lost:
        return "gained_chars"
    if lost and not gained:
        return "lost_chars"
    return "swapped"


#: The three classes whose answer is not in the model's input. Kept as a named set so the report and
#: the tests cannot disagree about which side of the line a class is on.
NEEDS_SOURCE = frozenset({"gained_chars", "lost_chars", "swapped"})

OPTION = re.compile(r"^option ([A-F])$")


def option_text(question: dict, key: str) -> str:
    for option in question.get("options") or []:
        if str(option.get("key")) == key:
            return option.get("text") or ""
    return ""


def main(argv=None) -> int:
    path = os.path.join(HERE, "dataset.jsonl")
    if len(sys.argv) > 1:
        path = sys.argv[1]
    with open(path, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]

    kinds = collections.Counter()
    by_kind_examples = collections.defaultdict(list)
    per_field = collections.Counter()
    rows_fully_reachable = 0
    for row in rows:
        reachable = True
        for field in row["fields_touched"]:
            if field == "stem":
                shipped, human = row["shipped"]["stem"], row["human"]["stem"]
            elif field == "answer":
                shipped, human = row["shipped"]["answer"], row["human"]["answer"]
            else:
                match = OPTION.match(field)
                key = match.group(1)
                shipped = option_text(row["shipped"], key)
                human = option_text(row["human"], key)
            kind = classify(shipped, human)
            kinds[kind] += 1
            per_field[field] += 1
            if kind in NEEDS_SOURCE:
                reachable = False
                if len(by_kind_examples[kind]) < 4:
                    by_kind_examples[kind].append(
                        {"key": row["candidate_key"], "field": field,
                         "shipped": shipped[:120], "human": human[:120]})
        if reachable:
            rows_fully_reachable += 1

    total = sum(kinds.values())
    needing = sum(count for kind, count in kinds.items() if kind in NEEDS_SOURCE)
    report = {
        "rows": len(rows),
        "touched_fields": total,
        "kinds": dict(kinds.most_common()),
        "needs_source": needing,
        "needs_source_rate": round(needing / total, 4) if total else None,
        "rows_with_no_source_free_edit": len(rows) - rows_fully_reachable,
        "rows_fully_reachable_rate": round(rows_fully_reachable / len(rows), 4) if rows else None,
        "per_field": dict(per_field),
        "examples": {kind: value for kind, value in by_kind_examples.items()},
        "reading": ("needs_source_rate is the share of the reviewer's edits that cannot be reached "
                    "from the text the model is given. It is a property of the corpus and the "
                    "pipeline, not of a model: a text-only comparison is capped by (1 - this rate) "
                    "and no prompt can raise that cap."),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    out = os.path.join(HERE, "text-sufficiency.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print("\nwrote " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
