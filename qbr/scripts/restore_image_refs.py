# -*- coding: utf-8 -*-
"""Restore `image_refs` on a rebuilt queue from a pre-rebuild backup of `candidates.jsonl`.

The 2026-09-29 21:56 rebuild wrote 79,090 rows whose `image_refs` were all empty, because
the packaged runs it merged had no crops to adopt (`batch_package` never ran the crop
stage). The rows' other fields — stem, options, disputes, answer sources — are the newer
build and must survive untouched; only the refs come back, from a donor backup whose
`candidate_key`s align 1:1 with the current queue.

Rules this script keeps:
- Never edits anything but `image_refs` (and the `exists` flag inside each restored ref).
- Refuses to run when keys do not align exactly (donor and queue must cover the same set).
- Writes to `--out` first, verifies counts, and only `--apply` swaps the queue's file
  after the target verifies; the original is kept as `candidates.jsonl.before-<stamp>`.
- Every ref's file existence is measured after the copy, so the report states how many
  of the restored refs point at a file that is actually on disk (the 53-crop gap and any
  other drift in the crops directory show up here, not silently).
- Prints a SHA-256 manifest of every file it reads and writes.

Usage:
    .venv/bin/python scripts/restore_image_refs.py \
        --queue qbr/data/review-queues/live \
        --donor  qbr/data/review-queues/live/review-ui/candidates.jsonl.before-ownership-20260924-210749 \
        --out    /tmp/candidates.imagerefs-restored.jsonl   # verify only
    ... then the same command with `--apply` once the report is read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import datetime as dt
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def review_ui_dir(queue: str) -> str:
    """The `review-ui/` directory of a queue, tolerating either spelling. Same rule as
    `crop_run_figures.review_ui_dir`, which is where the queue's own paths are spelled."""
    from repair_loop import review_ui_dir as _r

    return _r(queue)


def queue_root(queue: str) -> str:
    directory = review_ui_dir(queue)
    return os.path.dirname(directory) if os.path.basename(directory) == "review-ui" else directory


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_donor_refs(donor_path: str) -> tuple:
    """`({candidate_key: image_refs}, set(all_keys), rows)` — refs only on rows that carry them,
    but the key set covers every donor row, because key alignment is checked on the whole file."""
    refs_by_key: dict = {}
    keys: set = set()
    rows = 0
    with open(donor_path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows += 1
            keys.add(row["candidate_key"])
            refs = row.get("image_refs") or []
            if refs:
                refs_by_key[row["candidate_key"]] = refs
    print(f"donor: {rows} rows, {len(keys)} keys, {len(refs_by_key)} rows with refs")
    return refs_by_key, keys, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", required=True, metavar="DIR", help="queue root")
    parser.add_argument("--donor", required=True, metavar="FILE",
                        help="a candidates.jsonl backup whose keys match the queue's")
    parser.add_argument("--out", required=True, metavar="FILE",
                        help="temp target for the restored candidates file")
    parser.add_argument("--apply", action="store_true",
                        help="swap --out into the queue after verification, keeping a .before backup")
    args = parser.parse_args()

    root = queue_root(args.queue)
    directory = review_ui_dir(args.queue)
    candidates_path = os.path.join(directory, "candidates.jsonl")
    donor_refs, donor_keys, donor_rows = load_donor_refs(args.donor)
    refs_by_key = donor_refs

    queue_keys = set()
    refs_rows_before = 0
    with open(candidates_path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            queue_keys.add(row.get("candidate_key"))
            if row.get("image_refs"):
                refs_rows_before += 1
    only_queue = sorted(queue_keys - donor_keys)
    only_donor = sorted(donor_keys - queue_keys)
    if only_queue or only_donor:
        print(f"REFUSING: keys do not align — {len(only_queue)} only-in-queue, "
              f"{len(only_donor)} only-in-donor")
        for key in (only_queue[:5] + only_donor[:5]):
            print("  diff key:", key)
        return 2
    if refs_rows_before and not refs_rows_before == len(refs_by_key):
        print(f"note: queue already carries refs on {refs_rows_before} rows "
              f"(donor has {len(refs_by_key)}); those rows are kept as-is")
    donor_keys_path = os.path.join(os.path.dirname(args.out), "restore_image_refs.keys")
    with open(donor_keys_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(sorted(refs_by_key)) + "\n")

    stats = {"rows_in": 0, "rows_with_refs_after": 0, "refs_restored_rows": 0,
             "refs_present": 0, "refs_missing": 0}
    missing_by_paper: dict = {}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    with open(candidates_path, encoding="utf-8") as src, \
            open(args.out, "w", encoding="utf-8") as dst:
        for line in src:
            row = json.loads(line)
            stats["rows_in"] += 1
            key = row.get("candidate_key")
            refs = refs_by_key.get(key)
            if refs is not None:
                # `exists` is measured, not inherited: the crops directory may have moved
                # since the donor was cut.
                for ref in refs:
                    rel = str(ref.get("path") or "")
                    if rel.startswith("review-ui/"):
                        target = os.path.join(root, rel)
                    else:
                        target = os.path.join(root, "review-ui", rel)
                    if os.path.exists(target):
                        stats["refs_present"] += 1
                        ref["exists"] = True
                    else:
                        stats["refs_missing"] += 1
                        ref["exists"] = False
                        missing_by_paper.setdefault(os.path.dirname(rel), []).append(os.path.basename(rel))
                row["image_refs"] = refs
                stats["rows_with_refs_after"] += 1
                stats["refs_restored_rows"] += 1
            dst.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if missing_by_paper:
        print(json.dumps(missing_by_paper, ensure_ascii=False, indent=2)[:4000])

    if not args.apply:
        print(f"verify-only: inspect {args.out}, then rerun with --apply")
        print(f"sha256[out]={sha256_of(args.out)}")
        return

    backup = f"{candidates_path}.before-imagerefs-{stamp}"
    shutil.copy2(candidates_path, backup)
    os.replace(args.out, candidates_path)
    print(f"swapped into {candidates_path}")
    print(f"previous file kept as {backup}")
    print(f"sha256[new]={sha256_of(candidates_path)}")


if __name__ == "__main__":
    main()