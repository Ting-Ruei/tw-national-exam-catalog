#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score a model run **against the ceiling**: of the edits it could reach, how many did it? (A2)

A single accuracy number over all corrections mixes two different failures:

    the model could not have known (the character is not in its input), and
    the model was shown the character and still missed it.

Only the second is about the model. This script joins a `compare_models.py` run against
`text-sufficiency.json` and reports the accuracy **within the reachable subset**, where a good model
has something to prove, next to the overall number, where it does not.

Usage:
    python3 split_by_ceiling.py runs/20260928T...jsonl
    python3 split_by_ceiling.py runs/....jsonl --models mtplx-35b occamy-6bit
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import probe_text_sufficiency as probe  # noqa: E402


def load_reachability(sufficiency_path: str) -> dict:
    """`{(candidate_key, field): kind}` from the sufficiency probe's examples and the dataset.

    The probe writes only a few examples per kind, so the kind is recomputed here from the dataset.
    Recomputing rather than reading keeps one definition of "reachable" (`probe.classify`) - the
    alternative is two tables that can disagree.
    """
    dataset_path = os.path.join(HERE, "dataset.jsonl")
    with open(dataset_path, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    kinds = {}
    for row in rows:
        for field in row["fields_touched"]:
            if field == "stem":
                shipped, human = row["shipped"]["stem"], row["human"]["stem"]
            elif field == "answer":
                shipped, human = row["shipped"]["answer"], row["human"]["answer"]
            else:
                key = field.split()[1]
                shipped, human = probe.option_text(row["shipped"], key), probe.option_text(row["human"], key)
            kinds[(row["candidate_key"], field)] = probe.classify(shipped, human)
    return kinds


def summarize_side(rows: list) -> dict:
    """Accuracy on one side of the line, over the touched fields only.

    `reachable` and `unreachable` are disjoint by construction (the join key is `(key, field)`), so
    the two counts add up to the whole and the split cannot hide a field from both.
    """
    touched = correct = 0
    for row in rows:
        for field, verdict in row["verdicts"].items():
            if field not in row["fields_touched"]:
                continue
            touched += 1
            correct += verdict in ("exact", "near")
    return {"touched": touched, "correct": correct,
            "accuracy": round(correct / touched, 4) if touched else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="a runs/*.jsonl written by compare_models.py")
    parser.add_argument("--models", nargs="*", default=None)
    parser.add_argument("--sufficiency", default=os.path.join(HERE, "text-sufficiency.json"))
    args = parser.parse_args(argv)

    kinds = load_reachability(args.sufficiency)
    out = {}
    with open(args.run, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            name = record.get("model")
            if not name or (args.models and name not in args.models):
                continue
            sides = {"reachable": [], "unreachable": [], "unscored": []}
            for row in record["rows"]:
                kinds_for_row = {"reachable": {}, "unreachable": {}}
                for field, verdict in row["verdicts"].items():
                    if field not in row["fields_touched"]:
                        continue
                    kind = kinds.get((row["candidate_key"], field))
                    if kind is None:
                        # The corrections this run scored and the dataset disagree. Saying so is the
                        # point: a silent `continue` here would move fields off the books, and the
                        # split would then look better than the model is.
                        sides["unscored"].append({"candidate_key": row["candidate_key"], "field": field})
                        continue
                    side = "unreachable" if kind in probe.NEEDS_SOURCE else "reachable"
                    kinds_for_row[side][field] = verdict
                for side, verdicts in kinds_for_row.items():
                    if verdicts:
                        sides[side].append({"candidate_key": row["candidate_key"],
                                            "fields_touched": list(verdicts),
                                            "verdicts": verdicts})
            out[name] = {
                "reachable": summarize_side(sides["reachable"]),
                "unreachable": summarize_side(sides["unreachable"]),
                "unscored_fields": len(sides["unscored"]),
                "overall": summarize_side(record["rows"]),
            }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
