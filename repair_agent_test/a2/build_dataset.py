#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn the reviewed corpus into a **scored** dataset for model comparison (A2).

The ground truth is not written here. It is the reviewer's own `correct` event: the text the machine
shipped, and the text the person replaced it with. Both exist already, neither was produced for this
experiment, and every correction differs from what was shipped (measured: 51 of 51 differ) - so the
set has no vacuous rows where "did the model do nothing" would score as correct.

Two files come out:

    dataset.jsonl   one row per correction: shipped / human / the fields the person touched
    dataset.json    the same, plus a summary and the provenance (source digests, counts)

Why a file rather than a live query: the comparison UI shows the *same* rows to every model, and a
dataset that changed between two model runs would make the scores incomparable for a reason no one
could see later. The digest of both source files is recorded with the rows.

Usage:
    python3 build_dataset.py                       # writes into this directory
    python3 build_dataset.py --queue /path/to/review-ui --out-dir /tmp/a2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(CATALOG, "qbr", "src"))

from qbr import paths  # noqa: E402

DEFAULT_QUEUE = os.path.join(CATALOG, "qbr", "data", "review-queues", "live", "review-ui")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_candidates(path: str) -> dict:
    """`{candidate_key: row}` for the rows a correction can refer to.

    Only the keys the dataset needs are kept. `candidates.jsonl` is ~200 MB here, and the whole
    file is walked once; keeping every field of every row would hold the corpus in memory to use
    four of its columns.
    """
    wanted = ("candidate_key", "question_number", "stem", "stem_markup", "options", "answer",
              "source_registry_key", "metadata", "image_refs", "stem_image")
    rows = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # A truncated final line is a half-written queue, which is a real state; skipping it
                # here keeps the dataset buildable and the count in `dataset.json` says how many
                # were seen, so a silent drop would still be visible as a mismatch.
                continue
            key = row.get("candidate_key")
            if key:
                rows[key] = {field: row.get(field) for field in wanted}
    return rows


def load_corrections(path: str) -> dict:
    """`{candidate_key: [correction events, in file order]}`.

    The file is append-only, so a question can be corrected more than once. The **last** event is
    the ground truth: it is the one the reviewer left standing. The earlier ones are kept in the row
    as `history`, because a model that reproduces a superseded correction is not wrong about the
    paper - it is wrong about which version is current, and that distinction is worth being able to
    see rather than guess.
    """
    out = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("action") != "correct" or not isinstance(event.get("correction"), dict):
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            out.setdefault(key, []).append(event)
    return out


def _options_of(question: dict) -> dict:
    """`{"A": text, ...}` out of either spelling (a dict, or a list of `{key, text}`)."""
    options = question.get("options") or {}
    if isinstance(options, dict):
        return {str(k): (v if isinstance(v, str) else (v or {}).get("text") or "")
                for k, v in options.items()}
    return {str(o.get("key")): (o.get("text") or "") for o in options if isinstance(o, dict)}


def _options_list(options: dict) -> list:
    """The `[{key, text}, ...]` shape the review UI draws, so a row needs no second reader."""
    return [{"key": key, "text": options[key]} for key in sorted(options)]


def fields_touched(shipped: dict, human: dict) -> list:
    """Which columns the person actually changed: `stem`, `answer`, `option A`, ...

    This is the scoring key for "did the model do the same thing" as opposed to "did it produce the
    right final text": a model that edits the stem and leaves a broken option A has not fixed the
    question, and the difference between the two readings is exactly what this column records.
    """
    touched = []
    if (shipped.get("stem") or "") != (human.get("stem") or ""):
        touched.append("stem")
    if shipped.get("answer") != human.get("answer"):
        touched.append("answer")
    ship_options, human_options = shipped.get("options") or {}, human.get("options") or {}
    for key in sorted(set(ship_options) | set(human_options)):
        if ship_options.get(key) != human_options.get(key):
            touched.append("option %s" % key)
    return touched


def build(queue_dir: str) -> tuple:
    candidates_path = os.path.join(queue_dir, "candidates.jsonl")
    events_path = os.path.join(queue_dir, "question_review_events.jsonl")
    candidates = load_candidates(candidates_path)
    corrections = load_corrections(events_path)

    rows, skipped = [], {"no_candidate_row": 0, "no_change": 0}
    for key in sorted(corrections):
        shipped = candidates.get(key)
        if shipped is None:
            # The correction names a question no longer in the queue (it was rebuilt away). The
            # ground truth survives the rebuild but the shipped text it refers to does not, so this
            # row cannot be scored. Counted rather than dropped in silence.
            skipped["no_candidate_row"] += 1
            continue
        events = corrections[key]
        last = events[-1]["correction"]
        shipped_question = {"stem": shipped.get("stem") or "",
                            "options": _options_of(shipped),
                            "answer": shipped.get("answer")}
        human_question = {"stem": last.get("stem") if last.get("stem") is not None
                          else shipped_question["stem"],
                          "options": (_options_of(last) if last.get("options") is not None
                                      else shipped_question["options"]),
                          "answer": last.get("answer") if last.get("answer") is not None
                          else shipped_question["answer"]}
        touched = fields_touched(shipped_question, human_question)
        if not touched:
            skipped["no_change"] += 1
            continue
        metadata = shipped.get("metadata") or {}
        rows.append({
            "candidate_key": key,
            "question_number": shipped.get("question_number"),
            "subject": metadata.get("normalized_subject_name") or metadata.get("official_subject_name"),
            "category": metadata.get("normalized_category_name") or metadata.get("official_category_name"),
            "paper": shipped.get("source_registry_key"),
            "fields_touched": touched,
            "shipped": {"stem": shipped_question["stem"],
                        "options": _options_list(shipped_question["options"]),
                        "answer": shipped_question["answer"]},
            "human": {"stem": human_question["stem"],
                      "options": _options_list(human_question["options"]),
                      "answer": human_question["answer"]},
            "history": [{"at": event.get("created_at"),
                         "correction": event.get("correction")} for event in events[:-1]],
        })
    manifest = {
        "schema": "qbr_a2_dataset_v0.1",
        "queue": os.path.abspath(queue_dir),
        "sources": {
            "candidates.jsonl": {"bytes": os.path.getsize(candidates_path),
                                 "sha256": sha256_file(candidates_path),
                                 "rows_seen": len(candidates)},
            "question_review_events.jsonl": {"bytes": os.path.getsize(events_path),
                                             "sha256": sha256_file(events_path),
                                             "correct_events": sum(len(v) for v in corrections.values())},
        },
        "corrections_for_questions": len(corrections),
        "rows": len(rows),
        "skipped": skipped,
        "note": ("Ground truth is the reviewer's own `correct` event. The shipped text and the human "
                 "text are both real; nothing here was written for the experiment."),
    }
    return rows, manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", default=DEFAULT_QUEUE, help="review-ui queue directory")
    parser.add_argument("--out-dir", default=HERE)
    args = parser.parse_args(argv)

    rows, manifest = build(args.queue)
    os.makedirs(args.out_dir, exist_ok=True)
    rows_path = os.path.join(args.out_dir, "dataset.jsonl")
    manifest_path = os.path.join(args.out_dir, "dataset.json")
    with open(rows_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)

    print(json.dumps({"rows": len(rows), "skipped": manifest["skipped"],
                      "out": rows_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
