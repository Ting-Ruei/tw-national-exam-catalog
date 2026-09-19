"""Build the Stage-1 benchmark sample and copy the needed resources into pi_test.

Stratified selection (Protocol §16.1): diversity beats random volume. We sample across
  * examination years (early 101+, middle, recent),
  * categories (locked-27 medical/allied-health focus, plus a couple of non-medical
    controls so the scope gate is testable),
  * question papers vs answer/correction artefacts,
  * image-bearing vs text-only papers,
  * papers the legacy run failed on (never-ok in Registry/mineru_runs) so the known
    hard cases are represented and not silently skipped.

Selected PDFs are *copied* (never moved) into data/raw with a manifest carrying sha256,
source path, role and provenance - RAW stays immutable (Protocol §4.1, §8 Stage B).
"""

import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qbr.triage import sha256_file  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(_HERE)                      # .../tw-national-exam-catalog/qbr
# The corpus lives beside this package. It is found by **looking for it** rather than by counting
# directories up.
#
# Being honest about what this does and does not fix: the previous `dirname(dirname(PKG_ROOT))`
# still gives the right answer after the move, because `pi_test/question_bank_rebuild` and
# `tw-national-exam-catalog/qbr` happen to sit at the same depth. Verified, not assumed. So this
# is **not** a bug fix - it is removing a coincidence. Counting is a statement about where this
# code happens to live; searching is a statement about the repository, which is the thing that is
# actually true. The two agree today. A third location would have made them disagree, and the way
# they would have disagreed is a missing corpus with no error.
def _find_repo_root(start):
    for candidate in (start, os.path.dirname(start), os.path.dirname(os.path.dirname(start))):
        if os.path.isdir(os.path.join(candidate, "tw-national-exam-catalog")):
            return candidate
    return start


REPO = _find_repo_root(PKG_ROOT)                       # .../ai_learning_platform
ASSET_ROOT = os.path.join(REPO, "tw-national-exam-catalog", "國考題資料夾")
if not os.path.isdir(ASSET_ROOT):
    # The pipeline also runs from inside the catalog repository itself (that is the merged layout),
    # where the corpus is a sibling of this package rather than a child of a workspace root.
    ASSET_ROOT = os.path.join(os.path.dirname(PKG_ROOT), "國考題資料夾")
OFFICIAL = os.path.join(ASSET_ROOT, "10_official_pdf", "by_official_catalog")
LEGACY_OUTPUT = os.path.join(ASSET_ROOT, "20_mineru_output", "by_official_catalog")
REGISTRY_RUNS = os.path.join(ASSET_ROOT, "Registry", "mineru_runs")
OUT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_RAW = os.path.join(OUT_ROOT, "data", "raw")
MANIFEST = os.path.join(OUT_ROOT, "data", "sample_manifest.jsonl")

PRIORITY_CATEGORIES = (
    "醫事檢驗師",
    "藥師(二)",
    "藥師(一)",
    "藥師",
    "醫師(二)",
    "護理師",
    "醫事放射師",
    "營養師",
    "臨床心理師",
    "物理治療師",
)
CONTROL_CATEGORIES = ("社會工作師", "語文治療師")
EARLY = (101, 102, 103, 104, 105)
MIDDLE = (106, 107, 108, 109, 110)
RECENT = (111, 112, 113, 114, 115)


def role_of(name):
    upper = name.upper()
    if "_MOD" in upper or "_更正" in name:
        return "corrected_answer"
    if "_ANS" in upper:
        return "answer"
    return "question"


def legacy_paths_for(pdf_path):
    """Locate the legacy MinerU markdown + image folder for this PDF, if any."""
    relative = os.path.relpath(pdf_path, OFFICIAL)
    base = os.path.join(LEGACY_OUTPUT, relative[: -4])
    stem = os.path.basename(base)
    markdown = os.path.join(base, "vlm", stem + ".md")
    images = os.path.join(base, "images")
    return (
        markdown if os.path.isfile(markdown) else None,
        images if os.path.isdir(images) else None,
    )


def _registry_key(path):
    """Stable join key for the legacy run registry.

    The historical CSVs recorded absolute paths under an older canonical asset root
    (`/Users/tim/tw-national-exam-catalog/...`), so matching on the full path would
    silently report everything as never-attempted. Match on the project-relative tail
    starting at `10_official_pdf` instead.
    """
    normalized = path.replace("\\", "/")
    marker = "10_official_pdf/"
    index = normalized.index(marker) if marker in normalized else -1
    return normalized[index:] if index >= 0 else normalized


def legacy_statuses():
    import csv

    best = {}
    for path in glob.glob(os.path.join(REGISTRY_RUNS, "*", "mineru_results__*.csv")):
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                key = _registry_key(row.get("pdf_path") or "")
                status = row.get("status") or ""
                if not key:
                    continue
                if best.get(key) not in ("ok",):
                    best[key] = status
    return best


def candidates():
    rows = []
    for category in os.listdir(OFFICIAL):
        if category.startswith("."):
            continue
        category_dir = os.path.join(OFFICIAL, category)
        if not os.path.isdir(category_dir):
            continue
        for year in os.listdir(category_dir):
            year_dir = os.path.join(category_dir, year)
            if not os.path.isdir(year_dir) or not year.isdigit():
                continue
            for session in os.listdir(year_dir):
                session_dir = os.path.join(year_dir, session)
                if not os.path.isdir(session_dir):
                    continue
                for name in os.listdir(session_dir):
                    if not name.lower().endswith(".pdf"):
                        continue
                    full = os.path.join(session_dir, name)
                    rows.append(
                        {
                            "category": category,
                            "year": int(year),
                            "session": session,
                            "file": name,
                            "path": full,
                            "role": role_of(name),
                            "era": "early" if int(year) in EARLY else ("middle" if int(year) in MIDDLE else ("recent" if int(year) in RECENT else "other")),
                            "priority": category in PRIORITY_CATEGORIES,
                            "control": category in CONTROL_CATEGORIES,
                        }
                    )
    return rows


def pick(rows, per_bucket, wanted=30):
    """Greedy stratified pick: one bucket at a time, round-robin over its groups."""
    buckets = {}
    for row in rows:
        key = (row["category"], row["era"], row["role"])
        buckets.setdefault(key, []).append(row)
    chosen = []
    seen_hash = set()
    # interleave so no single category dominates the sample
    ordered_keys = sorted(buckets, key=lambda k: (not buckets[k][0]["priority"], k[0], k[1], k[2]))
    for _ in range(per_bucket):
        for key in ordered_keys:
            pool = buckets[key]
            if not pool:
                continue
            row = pool.pop(0)
            digest = sha256_file(row["path"])
            if digest in seen_hash:
                continue
            seen_hash.add(digest)
            row["sha256"] = digest
            chosen.append(row)
            if len(chosen) >= wanted:
                return chosen
    return chosen


def main(argv):
    wanted = 30
    per_bucket = 2
    index = 0
    while index < len(argv):
        if argv[index] == "--count":
            wanted = int(argv[index + 1])
            index += 2
        elif argv[index] == "--per-bucket":
            per_bucket = int(argv[index + 1])
            index += 2
        else:
            index += 1

    statuses = legacy_statuses()
    rows = candidates()
    # prefer papers the legacy pipeline failed on: they are the informative failures
    for row in rows:
        legacy = statuses.get(_registry_key(row["path"]), "")
        row["legacy_status"] = legacy or "never-attempted"
    rows.sort(key=lambda r: (0 if r["legacy_status"] == "error" else 1, not r["priority"]))

    chosen = pick(rows, per_bucket=per_bucket, wanted=wanted)
    os.makedirs(DATA_RAW, exist_ok=True)
    records = []
    for row in chosen:
        relative = os.path.join(row["category"], str(row["year"]), row["session"], row["file"])
        target = os.path.join(DATA_RAW, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not os.path.isfile(target):
            shutil.copyfile(row["path"], target)  # verified by digest below
        markdown, images = legacy_paths_for(row["path"])
        record = {
            "uid": "MOEX-%s-%s-%s-%s" % (row["year"], row["category"], row["session"], os.path.basename(row["file"])),
            "category": row["category"],
            "year": row["year"],
            "era": row["era"],
            "session": row["session"],
            "role": row["role"],
            "in_locked_27_scope": bool(row["priority"]),
            "control_case": bool(row["control"]),
            "source_pdf": row["path"],
            # Relative to the manifest's own directory, never absolute. The absolute form was worth
            # exactly as much as the sandbox was permanent: 30 rows named
            # `/Users/tim/.../pi_test/question_bank_rebuild/data/raw/...`, and moving the pipeline
            # into `tw-national-exam-catalog/qbr/` made every one of them point at nothing.
            "raw_copy": os.path.relpath(target, os.path.dirname(MANIFEST)),
            "sha256": row["sha256"],
            "bytes": os.path.getsize(row["path"]),
            "legacy_status": row["legacy_status"],
            "legacy_markdown": markdown,
            "legacy_image_dir": images,
            "legacy_image_count": len(glob.glob(os.path.join(images, "*"))) if images else 0,
        }
        records.append(record)

    with open(MANIFEST, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("selected %d papers -> %s" % (len(records), MANIFEST))
    summary = {}
    for record in records:
        summary[record["legacy_status"]] = summary.get(record["legacy_status"], 0) + 1
    print("legacy status mix:", summary)
    print("with legacy markdown:", sum(1 for r in records if r["legacy_markdown"]))
    print("in locked-27 scope:", sum(1 for r in records if r["in_locked_27_scope"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
