# -*- coding: utf-8 -*-
"""Gather many packaged runs into one review queue.

Reviewing a subject is one act of reading, and reviewing it in eighty-question pieces means
re-opening the paper and re-finding the question every time. So the queues a batch wrote are
merged here into a single file, ordered by year and session and subject, and served once.

What this does *not* do is merge the papers. A candidate keeps the question paper it came from,
and the review UI opens that paper beside the question - so a merged queue is still one paper per
question, and the reviewer's PDF pane still shows the right sheet.

The order is the order the papers were sat: newest year first, because the newest papers are the
ones a reader can still check against their own knowledge, and the oldest are the ones most likely
to have been superseded by a correction.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import ai_findings  # noqa: E402
from qbr import groups  # noqa: E402
from qbr import review_queue  # noqa: E402


def run_dirs(work_roots):
    """Every packaged run under the given work roots, newest paper first."""
    found = []
    for root in work_roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            run = os.path.join(root, name)
            if os.path.isfile(os.path.join(run, "review-ui", "candidates.jsonl")):
                found.append(run)
    return found


def taxonomy_of(run_name, rows):
    """The category, subject and year a run belongs to, read from what the run already carries.

    Taken from the candidate rows rather than from the directory name, because the rows are what
    the review UI shows and they hold the *official* names: `normalized_category_name` is the
    subject as the catalogue spells it, and `normalized_subject_name` is the paper's subject. The
    directory name is only a fallback, and it is needed because the oldest runs predate the
    metadata.

    The year comes from the exam code (`115090` is year 115, sitting 2) rather than from the first
    four characters of the directory name, which were assumed to be `YYYO`-style but are not for
    every category: `藥師(一)` runs are named `1151_藥師(一)_…`, and a category whose name contains
    an underscore would break the assumption entirely.
    """
    category = subject = ""
    year = ordinal = None
    for row in rows:
        metadata = row.get("metadata") or {}
        category = category or (metadata.get("normalized_category_name")
                                or metadata.get("official_category_name") or "")
        subject = subject or (metadata.get("normalized_subject_name") or "")
        exam_code = str(metadata.get("exam_code") or "")
        if len(exam_code) >= 5 and exam_code[:3].isdigit():
            year = year or int(exam_code[:3])
        if metadata.get("exam_ordinal"):
            ordinal = ordinal or int(metadata["exam_ordinal"])
        if category and subject and year:
            break
    if not (category and year):
        head = run_name.split("_")
        if len(head) >= 2 and head[0][:3].isdigit():
            year = year or int(head[0][:3])
            ordinal = ordinal if ordinal is not None else (int(head[0][3])
                                                           if head[0][3:4].isdigit() else None)
            category = category or head[1]
        if not subject and len(head) >= 3:
            subject = "_".join(head[2:])
    return {"category": category, "subject": subject, "year": year,
            "ordinal": ordinal, "run": run_name}


def sort_key(run):
    """Newest year first, then session, then subject name, then path.

    The year and session come from the directory name - `1152_醫事檢驗師_生物化學與臨床生化學` -
    which is the corpus's own naming and is the only thing in the run that carries them. A run
    whose name does not carry them sorts last rather than raising: a queue that cannot be ordered
    is still worth serving.
    """
    name = os.path.basename(run)
    head = name.split("_", 1)[0]
    if len(head) == 4 and head.isdigit():
        year, session = int(head[:3]), int(head[3])
    else:
        year, session = 0, 0
    return (-year, -session, name)


def review_events_to_carry(out_dir, previous=()):
    """Review records that must survive a rebuild of the queue.

    A reviewer's decisions are the most expensive thing this whole pipeline produces - a human read
    every one of those questions - and they used to live only inside the queue directory being
    replaced. Rebuilding the queue therefore **silently discarded them**. Measured: 30 decisions
    were written to `/tmp/qbr-live-stripfix3/review-ui/question_review_events.jsonl`, and the next
    `build_review_queue.py --out` would have started that file empty.

    They are carried forward because the key they are written under is stable: `candidate_key` is
    the source question keyed by the registry key, so the same question keeps the same key however
    the queue around it is rebuilt, re-cut or re-merged. A record whose question no longer exists
    is kept anyway and reported - dropping it would be the same silent loss in a smaller size.

    Returns `(records, carried, orphaned)`.
    """
    names = ("question_review_events.jsonl", "answer_review_events.jsonl",
             "question_ai_review_events.jsonl", "question_ai_feedback_events.jsonl",
             "question_ai_learning_events.jsonl",
             "question_correction_feedback_events.jsonl",
             # A model's finding about a question a person blocked is not a decision, but it is the
             # same kind of irreplaceable data: it says what was believed about one particular
             # reading, and the readings it is about are the strange one-offs nobody can classify on
             # sight. It is carried by the same mechanism as the decision logs because the failure
             # mode is the same - a rebuild that silently discards it - and the `ai_findings.STREAM`
             # name is imported rather than retyped so the two cannot drift.
             ai_findings.STREAM)
    records = []
    seen = set()
    for source in previous:
        for name in names:
            path = os.path.join(source, "review-ui", name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    # Identity is the record's **content**, not where it was found.
                    #
                    # The rule lives in `review_queue.record_identity` so that it is the same rule
                    # the push helper uses; see that function for the two measured mistakes
                    # (including `_carried_from`, which made every carried record a new record, and
                    # keying too narrowly, which dropped a reviewer's later decisions).
                    key = (name, review_queue.record_identity(record))
                    if key in seen:
                        continue
                    seen.add(key)
                    record["_carried_from"] = source
                    records.append((name, record))
    return records


def _keys_of(candidates_path):
    keys = set()
    if not os.path.isfile(candidates_path):
        return keys
    with open(candidates_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                keys.add(json.loads(line).get("candidate_key"))
            except ValueError:
                continue
    return keys


def merge(work_roots, out_dir, *, include_papers=None, previous=()):
    # Made absolute **before anything reads it**, so `_adopt_crops` can compute a queue-relative
    # path with `os.path.relpath` regardless of how `--out` was spelled. Without this, a relative
    # `--out` would leave the stored crop reference pointing outside the queue. `abspath` (not
    # `realpath`) on purpose: `/tmp` is a symlink to `/private/tmp` on this machine, and resolving
    # it would make the stored path and the served root disagree by a prefix.
    out_dir = os.path.abspath(out_dir)
    runs = run_dirs(work_roots)
    if include_papers:
        wanted = set(include_papers)
        runs = [r for r in runs if os.path.basename(r) in wanted]
    runs.sort(key=sort_key)

    # The merged queue is itself a run directory - `served as a run`, in the shape a single run
    # has - so a crop it refers to has to live inside it. The alternative was to register every
    # source package directory as an allowed asset root, which works only while those directories
    # still exist and makes the queue depend on 243 other folders staying put. Copying costs 991
    # files for 30,440 questions and makes the queue self-contained: copy it anywhere, serve it
    # there, and the pictures come with it.
    crops_root = os.path.join(out_dir, "review-ui", "crops")
    # The root the stored crop references are relative to. Taken once, here, from the argument as
    # given, because the stored path must not depend on the caller's working directory - see
    # `_adopt_crops`.
    queue_root = out_dir
    candidates, issues = [], []
    per_paper = []
    seen_keys = {}
    copied = collections.Counter()
    for run in runs:
        path = os.path.join(run, "review-ui", "candidates.jsonl")
        with open(path, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        paper = os.path.basename(run)
        # Groups are bound here as well as at packaging time, because this merge also runs over
        # queues that were packaged before grouping existed. The unit is the paper - the rows of
        # one run are one paper - so `承上題` on the first question of the next paper cannot attach
        # to the last question of this one.
        groups.bind(rows, paper_key=str((rows[0].get("source_registry_key") or paper) if rows else paper))
        for row in rows:
            if row.get("image_refs"):
                row["image_refs"] = _adopt_crops(row["image_refs"], run, paper, crops_root,
                                                 copied, queue_root=queue_root)
            # The review log keys on `candidate_key`, which is the source question keyed by the
            # registry key - unique across the corpus. A collision means two runs packaged the
            # same paper twice, which is a finding rather than something to paper over.
            key = row.get("candidate_key")
            if key in seen_keys:
                raise SystemExit("duplicate candidate_key %s in %s and %s"
                                 % (key, seen_keys[key], run))
            seen_keys[key] = run
            candidates.append(row)
        issue_path = os.path.join(run, "review-ui", "issues.csv")
        if os.path.isfile(issue_path):
            with open(issue_path, newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    row["paper"] = os.path.basename(run)
                    issues.append(row)
        entry = {"paper": os.path.basename(run), "questions": len(rows),
                 "candidates_sha256": _sha256(path)}
        entry.update(taxonomy_of(os.path.basename(run), rows))
        per_paper.append(entry)

    # Written into a `review-ui/` subdirectory because that is the shape a single run has, and
    # `scripts/review_run.sh` serves a run directory. A merged queue is a run directory with one
    # queue in it, so it is given the same shape rather than a second layout to keep in step.
    out_dir = os.path.join(out_dir, "review-ui")
    os.makedirs(out_dir, exist_ok=True)
    candidates_path = os.path.join(out_dir, "candidates.jsonl")
    with open(candidates_path, "w", encoding="utf-8") as handle:
        for row in candidates:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    # A reviewer's decisions are carried across a rebuild, keyed by `candidate_key`, which is the
    # stable identity of a question. Written before the taxonomy so a queue that is served the
    # instant this returns already knows what has been reviewed.
    carried = collections.Counter()
    orphaned = collections.Counter()
    records = review_events_to_carry(out_dir, previous)
    live = {row.get("candidate_key") for row in candidates}
    streams = {}
    # Where the carried records came from, counted by the queue directory itself. Printed below
    # because auto-discovery scans *every* sibling of `--out`, and the failure mode is silent:
    # build into `/tmp` and a leftover browser-test queue (`/tmp/ann_test`) is carried in as if it
    # were a person's decisions. Measured 2026-09-22: 3 records from `115090:311:0704` got into a
    # rebuild that way. Naming the sources turns an invisible over-scan into something the operator
    # can see while it is still cheap to notice. Same reason the record keeps `_carried_from`.
    origins = collections.Counter()
    for name, record in records:
        origins[record.get("_carried_from") or "?"] += 1
        streams.setdefault(name, []).append(record)
        if record.get("candidate_key") in live:
            carried[name] += 1
        else:
            orphaned[name] += 1
    for name, entries in streams.items():
        # Truncated, not appended. `previous` defaults to this very directory, so the file being
        # written is also the file being read: appending doubled every record on the second
        # rebuild in place. Measured: 45 records became 90. A record that came from elsewhere is
        # written out; one that was already here is rewritten in its place.
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as handle:
            for record in entries:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    # A stream that exists here and had no records to carry still holds whatever is on disk, which
    # is right: nothing was read from it, so nothing about it changed.

    issues_path = os.path.join(out_dir, "issues.csv")
    fieldnames = sorted({key for row in issues for key in row}) or ["paper"]
    with open(issues_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in issues:
            writer.writerow(row)

    # The taxonomy the review UI groups by. It is computed here, once, rather than in the browser,
    # because the browser would have to read all 21,150 candidate rows to answer "which subjects
    # are in this queue" - and that question is asked before anything is rendered.
    index = {"papers": len(runs), "questions": len(candidates), "issues": len(issues),
             "candidates_sha256": _sha256(candidates_path),
             "review_events_carried": dict(carried),
             "review_events_orphaned": dict(orphaned),
             "per_paper": per_paper,
             "categories": sorted({_fold_category(p["category"]) for p in per_paper
                                  if p.get("category")}),
             "subjects": sorted({p["subject"] for p in per_paper if p.get("subject")}),
             "years": sorted({p["year"] for p in per_paper if p.get("year")}, reverse=True),
             "taxonomy": _taxonomy(per_paper),
             "order": [p["paper"] for p in per_paper]}
    with open(os.path.join(out_dir, "queue_index.json"), "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=2)

    print("%d papers, %d questions, %d issue rows" % (len(runs), len(candidates), len(issues)))
    if copied:
        print("  crops: %d copied, %d missing" % (copied["copied"], copied["missing"]))
    if carried or orphaned:
        print("  review records: %d carried, %d orphaned"
              % (sum(carried.values()), sum(orphaned.values())))
        # Naming the source queues is what makes an over-broad scan visible. Auto-discovery carries
        # every sibling of `--out`; a test queue left beside a real one is then indistinguishable
        # from a person's work once it is inside the queue. It never is once it is listed here.
        for origin, count in origins.most_common():
            print("    from %s (%d)" % (origin, count))
    return index


def _taxonomy(per_paper):
    """`category -> year -> subject -> [paper]`, and the counts at every level.

    Built as a tree rather than as a flat list of facet values because the facets are not
    independent: 醫事檢驗師 has papers in 115 but 藥師(一) does not have the same subjects, so a
    flat subject list would offer choices that lead to an empty list. A tree cannot.
    """
    tree = {}
    for entry in per_paper:
        entry = dict(entry)
        # The two spellings of one category are folded together, because they *are* one category:
        # the catalog spells `藥師（一）` with full-width brackets in some years and `藥師(一)` in
        # others, and both are the same examination class. Measured: without folding, the review UI
        # offers six categories where there are four, and the two 藥師(一) entries split 63 and 12
        # papers - so a reviewer choosing one sees a quarter of the papers that exist.
        category = _fold_category(entry.get("category")) or "(未分類)"
        year = str(entry.get("year") or "(未知)")
        subject = entry.get("subject") or entry.get("paper") or "(未知)"
        bucket = tree.setdefault(category, {"years": {}, "papers": 0, "questions": 0})
        bucket["papers"] += 1
        bucket["questions"] += entry["questions"]
        year_bucket = bucket["years"].setdefault(
            year, {"sittings": {}, "papers": 0, "questions": 0})
        year_bucket["papers"] += 1
        year_bucket["questions"] += entry["questions"]
        # The sitting is its own level, between the year and the subject, because the same subject
        # is set twice a year and the two settings are two different papers: 1151 and 1152 of
        # 藥師(一) 藥學(二) share a subject name and share nothing else. Without this level a
        # reviewer comparing a question against the paper it came from cannot say which sitting
        # they mean, which is the whole point of the comparison.
        sitting = str(entry.get("ordinal") or "")
        sitting_bucket = year_bucket["sittings"].setdefault(
            sitting, {"subjects": {}, "papers": 0, "questions": 0})
        sitting_bucket["papers"] += 1
        sitting_bucket["questions"] += entry["questions"]
        subject_bucket = sitting_bucket["subjects"].setdefault(
            subject, {"papers": [], "questions": 0})
        subject_bucket["papers"].append(entry["paper"])
        subject_bucket["questions"] += entry["questions"]
    return tree


def _adopt_crops(refs, run, paper, crops_root, copied, *, queue_root):
    """Copy a run's crops into the merged queue and rewrite the references to point there.

    The path a candidate carries is absolute and points into the package run it was built from.
    Left alone it would make the merged queue depend on 243 directories that a later cleanup may
    remove, and the Review UI - which serves files only under registered roots - would answer 404
    for every crop. The relative reference is what the queue keeps; the copy is what makes it true.

    **Relative to the queue, not to the caller's working directory.** This is the one thing the
    version above got wrong, and it was invisible for as long as no merged queue had any pictures:
    `path` was left as the value of `os.path.join(out_dir, ...)`, so what the row stored depended on
    how the operator spelled `--out`. Run `build_review_queue.py --out data/review-queues/x` from
    `qbr/` and the row says `data/review-queues/x/review-ui/crops/...`; run it with an absolute
    `--out` and the row says `/abs/...`. Serving that queue from a container (where `/queue` is the
    queue and the CWD is `/workspace`) then answered **404 for every figure**, which is exactly the
    failure this function exists to prevent. Measured: 3,483 questions with 4,549 crops, all 404 at
    `/file`, until the path was made queue-relative.

    So the stored value is always `review-ui/crops/<paper>/<name>`, which is the one spelling that
    resolves against whichever root the queue is mounted at - `/queue` in the container, the queue
    directory anywhere else. A copy of the queue is still self-contained; the reference no longer
    encodes where it was built.
    """
    adopted = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        source = ref.get("path") or ""
        if not os.path.isfile(source):
            ref = dict(ref)
            ref["exists"] = False
            adopted.append(ref)
            copied["missing"] += 1
            continue
        target_dir = os.path.join(crops_root, paper)
        os.makedirs(target_dir, exist_ok=True)
        name = os.path.basename(source)
        target = os.path.join(target_dir, name)
        if not os.path.isfile(target):
            shutil.copyfile(source, target)
            copied["copied"] += 1
        relative = os.path.relpath(target, queue_root)
        adopted.append({**ref, "path": relative, "exists": True})
    return adopted


def _fold_category(name):
    """One category's name, with the brackets folded so two spellings are one entry.

    The corpus's own spelling is preferred when it can be told which is which: `藥師(一)` is the
    directory name and `藥師（一）` is the catalog's, and the directory is the authority on its own
    layout. Both are folded to the half-width form, which is the form the directories use.
    """
    if not name:
        return name
    return name.replace("（", "(").replace("）", ")")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


#: Where sibling queues are looked for. A rebuild writes into one of these roots, and the records
#: it might be about to drop can only be in a sibling of the destination - so a review log that lives
#: under a different root is a different body of work, not something this rebuild was carrying.
#: Measured: scanning all of `/tmp` flagged `/tmp/qbr-golden-001` (42 records from an unrelated
#: experiment) as "about to be lost" on every rebuild of the live queue, which is a false alarm that
#: would train the operator to pass `--allow-lost-reviews` by reflex.
def review_logs_elsewhere(out_dir, roots=None):
    """Review records that exist in another queue but are not being carried into `out_dir`.

    This exists because the default in `main()` was only *half* safe. Rebuilding in place defaults
    `--carry-from` to the destination, which is right - but rebuilding to a **new** directory, which
    is what this project actually does (`sf7` -> `sf8` -> `sf9`), defaults to carrying nothing and
    starts the new queue with an empty review log. The records are not lost from disk; they are lost
    from the queue that gets served, which is worse, because the reviewer's next session opens on a
    question they already judged and says nothing about it. Measured at the moment of writing: 144
    human decisions sat in `/tmp/qbr-live-sf9` while `--out /tmp/qbr-live-sf10` would have carried 0.

    So the count is reported before anything is written, and a non-empty result is refused rather
    than warned about - a warning printed into a build log is not a safeguard, and "the reviewer's
    decisions were preserved" has to be true by construction or it is not true.
    """
    if roots is None:
        roots = [os.path.dirname(os.path.abspath(out_dir)) or "."]
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            run = os.path.join(root, name)
            # `realpath`, not `abspath`: the queue that is being served is reached through a
            # symlink (`/tmp/qbr-live-served` -> `/tmp/qbr-live-sf9`), and comparing unresolved
            # paths made the same directory look like two - so carrying from sf9 was still refused
            # because "sf9" and "served" appeared to be different queues holding 144 records each.
            if os.path.realpath(run) == os.path.realpath(out_dir):
                continue
            if not os.path.isdir(os.path.join(run, "review-ui")):
                continue
            keys = set()
            for stream in ("question_review_events.jsonl", "answer_review_events.jsonl"):
                path = os.path.join(run, "review-ui", stream)
                if not os.path.isfile(path):
                    continue
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        # The rule lives in `review_queue.record_identity`; see it for the two
                        # measured mistakes this prevents (a carried record looking new, and a key
                        # narrower than the record dropping later decisions).
                        keys.add((stream, review_queue.record_identity(record)))
            if keys:
                found.append((run, keys))
    return found


def sibling_queues_with_reviews(out_dir, roots=None):
    """Every sibling queue directory that holds review records, excluding the destination.

    Ordered so that the destination itself comes last when it is among them, because `previous` is
    read in order and a record carried forward keeps the `_carried_from` of the *first* source that
    held it - and naming the queue the reviewer most recently used is more useful than naming a
    stale one that happens to sit earlier in `ls`.

    Auto-discovery exists so that carrying the reviewer's work is not something an operator can
    forget. The flag-based design had the right default for an in-place rebuild and the wrong one for
    a rebuild into a new directory, and rebuilding into a new directory is what this project actually
    does - so the failure mode was reachable by doing the normal thing. There is now no flag to
    forget: every sibling queue's records are carried, deduplicated by record identity, and a record
    whose question no longer exists is still carried and counted as an orphan.
    """
    if roots is None:
        roots = [os.path.dirname(os.path.abspath(out_dir)) or "."]
    here = os.path.realpath(out_dir)
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            run = os.path.join(root, name)
            if os.path.realpath(run) == here:
                continue
            if not os.path.isdir(os.path.join(run, "review-ui")):
                continue
            for stream in ("question_review_events.jsonl", "answer_review_events.jsonl"):
                if os.path.isfile(os.path.join(run, "review-ui", stream)):
                    found.append(run)
                    break
    # The destination last, so the most recent work names the carry.
    if os.path.isdir(os.path.join(out_dir, "review-ui")):
        found.append(out_dir)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge packaged runs into one review queue.")
    parser.add_argument("--work", nargs="+", required=True, help="batch_package work roots")
    parser.add_argument("--out", required=True)
    parser.add_argument("--paper", nargs="*", default=None,
                        help="only these paper directory names")
    parser.add_argument("--carry-from", nargs="*", default=None,
                        help="queue directories whose review records must be kept "
                             "(default: the --out directory itself, so a rebuild in place "
                             "cannot lose a reviewer's decisions)")
    parser.add_argument("--allow-lost-reviews", action="store_true",
                        help="rebuild into a queue that carries none of the decisions sitting in "
                             "another queue; only for a deliberate throw-away run")
    args = parser.parse_args()
    # Every sibling queue's records are carried, not only a named one's, and `--carry-from` **adds**
    # to that set rather than replacing it.
    #
    # Rebuilding in place already defaulted to carrying the destination, which was right but not
    # enough: rebuilding into a *new* directory - `sf7` -> `sf8` -> `sf9`, which is what this project
    # does - defaulted to carrying nothing and served a queue with no memory of the work already
    # done. Measured at the time of writing: 144 human decisions lived in `/tmp/qbr-live-sf9` and the
    # sibling queues held 299 more; all 392 are now carried with no flag at all.
    #
    # Additive rather than replacing, because replacing made the two mechanisms fight: naming one
    # source switched auto-discovery off, and the guard below then reported every *other* sibling as
    # about to be lost - a refusal on work that was in fact complete. A safeguard that fires on
    # correct usage is a safeguard that gets passed `--allow-lost-reviews` by reflex.
    previous = sibling_queues_with_reviews(args.out)
    for extra in (args.carry_from or []):
        if os.path.isdir(os.path.join(extra, "review-ui")) and os.path.realpath(extra) not in {
                os.path.realpath(p) for p in previous}:
            previous.append(extra)

    # The last-resort guard: refuse to write a queue that carries **nothing** while records exist
    # beside it. With auto-discovery this can only fire when the caller has pointed somewhere
    # unexpected, so it does not fire on correct usage - and it is still checked before `merge`, so a
    # refusal leaves the destination untouched.
    if not args.allow_lost_reviews and not previous:
        elsewhere = review_logs_elsewhere(args.out)
        if elsewhere:
            total = sum(len(keys) for _run, keys in elsewhere)
            print("拒絕啟動：這次重建不會保留任何人工審核紀錄。", file=sys.stderr)
            print("  這些紀錄存在，但沒有一個被帶入（共 %d 筆）：" % total, file=sys.stderr)
            for run, keys in sorted(elsewhere):
                print("    %s（%d 筆）" % (run, len(keys)), file=sys.stderr)
            print("  若這確實是丟棄用的執行，加 --allow-lost-reviews。", file=sys.stderr)
            raise SystemExit(2)

    merge(args.work, args.out, include_papers=args.paper, previous=previous)


if __name__ == "__main__":
    main()
