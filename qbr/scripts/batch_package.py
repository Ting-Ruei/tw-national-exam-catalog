# -*- coding: utf-8 -*-
"""Build a review-ready package for every paper of a category, by calling the golden path.

The golden path proves one paper end to end. This drives it over a whole category and
reports what happened. It is deliberately a *driver*, not a second pipeline: every paper is
produced by `scripts/golden_path.py run`, so there is exactly one implementation of what a
package is, and a defect fixed there is fixed everywhere. A batch runner that reimplemented
the stages would be a second thing to keep in step, and the two would drift - which is
precisely how the extraction and segmentation paths came to disagree before.

Where a paper's metadata comes from, in order:

  1. The registry manifest the corpus ships with (`Registry/asset_manifests/*.csv`), which is
     the authority on which key a file came from. It covers 8,295 keys.
  2. The catalog (`catalogs/moex_subject_catalog__y100-115.csv`), which carries `registry_key`
     for 70,612 rows and names the session, subject code and official subject spelling.
  3. The directory name, which is what remains for papers neither names.

Every resolution records which of the three answered, because a key that was inferred from a
directory name and a key that was looked up are different kinds of fact.

Usage:
    python scripts/batch_package.py --category 醫事檢驗師 --work /tmp/qbr-pkgs
    python scripts/batch_package.py --category 醫事檢驗師 --years 115 --ordinals 2
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
PROJECT = os.path.normpath(os.path.join(PKG, "..", ".."))
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import generation  # noqa: E402

CATALOG = os.path.join(PROJECT, "tw-national-exam-catalog", "catalogs",
                       "moex_subject_catalog__y100-115.csv")
ASSET_ROOT = os.path.join(PROJECT, "tw-national-exam-catalog", "國考題資料夾")
CORPUS = os.path.join(ASSET_ROOT, "10_official_pdf", "by_official_catalog")
GOLDEN = os.path.join(HERE, "golden_path.py")


def catalog_by_key(category=None):
    """`registry_key` -> catalog row, for every row that names one.

    A paper may appear twice with different `question_set` values and the same key is unique
    per row, so the key is the identity. Nothing is merged: the row that is there is used.
    """
    out = {}
    if not os.path.isfile(CATALOG):
        return out
    # The filter is on the folded category name, not the exact one, because the catalog spells the
    # same category both ways across revisions - `藥師（一）` and `藥師(一)` - and an exact filter
    # discards the rows before `resolve` can compare them. Measured: with an exact filter the 103
    # and 104 papers of 藥師(一) had no row to match and fell through to `directory-name`, twelve
    # papers of 藥師(一) and nine of 藥師(二) with no registry key at all.
    folded = _fold_brackets(category) if category else None
    with open(CATALOG, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if folded and _fold_brackets(row.get("category_name") or "") != folded:
                continue
            key = (row.get("registry_key") or "").strip()
            if key:
                out[key] = row
    return out


def paper_pdfs(category, *, years=None, ordinals=None):
    """Every question paper of a category, as its own record. Answer sheets are not papers."""
    pattern = os.path.join(CORPUS, category, "*", "*", "*.pdf")
    found = []
    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path)
        if "_ANS" in name or "_MOD" in name:
            continue
        match = re.search(r"/(\d{3})/第(\d)次/", path.replace(os.sep, "/"))
        if not match:
            continue
        year, ordinal = int(match.group(1)), int(match.group(2))
        if years and year not in years:
            continue
        if ordinals and ordinal not in ordinals:
            continue
        subject = re.sub(r"^\d{3,4}_[^_]*_", "", name[:-4])
        stem = name[:-4]
        parts = stem.split("_")
        found.append({
            "path": path, "name": name, "year": year, "ordinal": ordinal,
            "category": category, "subject": subject,
            "exam_code_hint": parts[0] if parts and parts[0].isdigit() else None,
        })
    return found


def registry_index():
    """`(category, year, ordinal, subject-normalised)` -> registry entry, for question rows.

The registry is the authority: it carries the exam code, category code, subject code, year
and ordinal for every asset it names, and it carries them for the *paper* rather than for a
filename. Reading it first means the metadata in a package is the metadata the corpus
recorded, not something parsed back out of a name.

Its limit is coverage - 8,295 keys against the catalog's 70,612 rows - so it cannot be the
only source. It is the first source, and a paper it answers for is not then guessed at.
"""
    index = {}
    try:
        import read_the_registry
        table, _rows, _conflicts = read_the_registry.load_registry()
    except Exception:                                    # noqa: BLE001 - no registry is a datum
        return index
    for key, entry in table.items():
        if entry.get("document_role") != "question" or not entry.get("on-disk"):
            continue
        identity = (entry.get("category_name"), str(entry.get("year")),
                    str(entry.get("exam_ordinal")),
                    _normalise_subject(entry.get("subject_name")))
        index.setdefault(identity, key)
    return index


def resolve(paper, catalog, registry):
    """The `registry_key` and official names for one paper, and how they were found.

    Three sources, asked in order of authority: the registry manifest, the catalog, the
    directory name. `how` records which one answered, because a key that was looked up and a
    key that was inferred are different kinds of fact.
    """
    identity = (paper["category"], str(paper["year"]), str(paper["ordinal"]),
                _normalise_subject(paper["subject"]))
    key = registry.get(identity)
    if key:
        bits = key.split(":")
        entry = _registry_entry(key)
        return {
            "registry_key": ":".join(bits[:5]),
            "category_code": entry.get("category_code") or (bits[2] if len(bits) > 2 else None),
            "subject_code": entry.get("subject_code") or (bits[3] if len(bits) > 3 else None),
            "subject_name": entry.get("subject_name") or paper["subject"],
            "category_name": entry.get("category_name") or paper["category"],
            "session": bits[4] if len(bits) > 4 else None,
            "exam_label": None,
            "how": "registry-manifest",
        }

    # The catalog's key is `moex:<exam_code>:<category_code>:<subject_code>:<question_set>`.
    # The exam code is not derivable from the filename - `1152` is year 115 and session 2,
    # while the MOEX code is `115090` - so the catalog is matched on what a filename does carry:
    # the year, the sitting, the category and the subject.
    #
    # The sitting and the category are both load-bearing, and leaving either out produced a real
    # collision rather than a theoretical one. Measured on the 藥師 family: matching on
    # year + subject alone resolved `1051_藥師(一)_藥劑學(包括生物藥劑學)` and
    # `1052_藥師(一)_藥劑學(包括生物藥劑學)` to the same `moex:105020:305:33:1`, because the
    # catalog spells the two sittings `藥師(一)` and `藥師（一）` and the paper's own directory
    # says only `藥師(一)`. Two different papers were packaged under one registry key, which the
    # merged review queue then refused as a duplicate - three keys in 藥師(一) and three in
    # 藥師(二), six papers in all, plus the nine 103/104 papers the catalog has no row for.
    #
    # Measured over the 429 rows of these four categories, `(year, sitting, category, subject)` is
    # unique; `(year, subject)` is not. The sitting comes from `exam_label` (`105年第二次…`),
    # which is the catalog's own statement of it and agrees with the registry's `exam_ordinal`.
    for catalog_key, row in catalog.items():
        if str(row.get("year")) != str(paper["year"]):
            continue
        if _sitting_of(row.get("exam_label")) not in (None, str(paper["ordinal"])):
            continue
        if not _same_category(row.get("category_name"), paper["category"]):
            continue
        if not _same_subject(row.get("subject_name"), paper["subject"]):
            continue
        bits = catalog_key.split(":")
        return {
            "registry_key": ":".join(bits[:5]),
            "category_code": row.get("category_code"),
            "subject_code": row.get("subject_code"),
            "subject_name": row.get("subject_name"),
            "category_name": row.get("category_name"),
            "session": bits[4] if len(bits) > 4 else None,
            "exam_label": row.get("exam_label"),
            "how": "catalog",
        }
    return {
        "registry_key": None,
        "category_code": None,
        "subject_code": None,
        "subject_name": paper["subject"],
        "category_name": paper["category"],
        "session": str(paper["ordinal"]),
        "exam_label": None,
        "how": "directory-name",
    }


_REGISTRY_CACHE = {}


def _registry_entry(key):
    """The full registry row for a bare key, so the code fields can be read off it."""
    if not _REGISTRY_CACHE:
        try:
            import read_the_registry
            table, _rows, _conflicts = read_the_registry.load_registry()
            _REGISTRY_CACHE.update(table)
        except Exception:                                # noqa: BLE001
            pass
    return _REGISTRY_CACHE.get(key + ":question") or _REGISTRY_CACHE.get(key) or {}


def _normalise_subject(text):
    """A subject name in a form two spellings of it agree on.

    The catalog and the corpus disagree on the parentheses - 全形 `（包括寄生蟲學）` against
    半形 `(包括寄生蟲學)` - and on `及` against `與`. Comparing with the brackets and those two
    characters removed is what brings them together; comparing exactly would report 40 subjects
    as unresolvable that are merely spelled differently.
    """
    if not text:
        return ""
    text = text.replace("（", "(").replace("）", ")").replace("及", "與")
    return re.sub(r"[\s()]", "", text)


def _same_subject(a, b):
    return bool(a) and bool(b) and _normalise_subject(a) == _normalise_subject(b)


def _sitting_of(exam_label):
    """`105年第二次…` -> `2`. None when the label does not state one.

    A None is treated as "no opinion" by the caller rather than as a mismatch, because some
    labels - the police and port examinations - name a 梯次 and no 次, and refusing them would
    throw away a row that is otherwise unambiguous.
    """
    match = re.search(r"第\s*([一二三四1234])\s*次", exam_label or "")
    if not match:
        return None
    return {"一": "1", "二": "2", "三": "3", "四": "4"}.get(match.group(1), match.group(1))


def _same_category(a, b):
    """Whether a catalog category name and a directory name are the same category.

    The catalog spells `藥師（一）` with full-width brackets and a later revision of the same
    examination spells it `藥師(一)` with half-width ones; the corpus directory uses the half-width
    form throughout. Comparing exactly made the catalog answer for neither spelling - measured,
    the three 1051 papers fell through to `directory-name` and got no registry key at all, while
    the 1052 papers claimed the 1051 key.
    """
    if not a or not b:
        return False
    return _fold_brackets(a) == _fold_brackets(b)


def _fold_brackets(text):
    return re.sub(r"[\s]", "", text.replace("（", "(").replace("）", ")")).strip()


def build_one(paper, resolved, out_root, *, package_version, slug, extra=()):
    """Run the golden path for one paper. Returns a record; never raises."""
    started = time.time()
    run_dir = os.path.join(out_root, paper["name"][:-4])
    record = {"name": paper["name"], "year": paper["year"], "ordinal": paper["ordinal"],
              "subject": paper["subject"], "resolved": resolved.get("how"),
              "registry_key": resolved.get("registry_key"), "run_dir": run_dir}
    if not resolved.get("registry_key"):
        # The golden path takes the paper code out of the key. Without a key there is nothing
        # to hand it, and guessing the code from the filename would make the package claim an
        # identity the catalog never confirmed.
        record["status"] = "no-registry-key"
        record["seconds"] = round(time.time() - started, 2)
        return record
    command = [
        sys.executable, GOLDEN, "run",
        "--registry-key", resolved["registry_key"],
        "--asset-root", ASSET_ROOT,
        "--out", run_dir,
        "--year", str(paper["year"]),
        "--ordinal", str(paper["ordinal"]),
        "--category", resolved["category_name"],
        "--subject", resolved["subject_name"],
        "--category-code", str(resolved.get("subject_code") or ""),
        "--slug", slug,
        "--package-version", package_version,
        *(list(extra)),
    ]
    proc = subprocess.run(command, capture_output=True, text=True)
    record["exit"] = proc.returncode
    tail = (proc.stdout or "").strip().splitlines()
    record["stdout_tail"] = tail[-1][:400] if tail else ""
    if proc.returncode != 0:
        record["status"] = "blocked"
        record["stderr_tail"] = (proc.stderr or "").strip()[-400:]
    else:
        record["status"] = "packaged"
        # The count lives in the *package* manifest, not the run manifest: the run manifest
        # says which stages ran, and the package manifest is what the platform reads. Asking
        # the run manifest for a question count returns nothing and reports a package of zero
        # questions that has eighty in it.
        package_manifest = os.path.join(run_dir, "package", "manifest.json")
        if os.path.isfile(package_manifest):
            with open(package_manifest, encoding="utf-8") as handle:
                document = json.load(handle)
            record["questions"] = (document.get("counts") or {}).get("questions")
            gate = (document.get("provenance") or {}).get("deterministic_gate") or {}
            record["gate_verdict"] = gate.get("verdict")
            record["gate_blocking"] = gate.get("blocking")
    record["seconds"] = round(time.time() - started, 2)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Package every paper of a category.")
    parser.add_argument("--category", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--years", nargs="*", type=int, default=None)
    parser.add_argument("--ordinals", nargs="*", type=int, default=None)
    parser.add_argument("--slug", default=None, help="default: derived from the category")
    parser.add_argument("--package-version", default=None)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many papers")
    parser.add_argument("--force", action="store_true",
                        help="re-run papers whose package already exists")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    slug = args.slug or _slug(args.category)
    package_version = args.package_version or "tw-national-exam-%s-v0.0.1" % slug
    papers = paper_pdfs(args.category, years=args.years, ordinals=args.ordinals)
    if not papers:
        print("no papers found for %s" % args.category, file=sys.stderr)
        raise SystemExit(2)
    catalog = catalog_by_key(args.category)
    registry = registry_index()
    os.makedirs(args.work, exist_ok=True)

    print("packaging %d papers of %s (registry answers for %d, catalog names %d subjects)"
          % (len(papers), args.category, len(registry), len(catalog)), flush=True)
    results = []
    for index, paper in enumerate(papers, 1):
        resolved = resolve(paper, catalog, registry)
        run_dir = os.path.join(args.work, paper["name"][:-4])
        if os.path.isfile(os.path.join(run_dir, "package", "manifest.json")) and not args.force:
            with open(os.path.join(run_dir, "package", "manifest.json"),
                      encoding="utf-8") as handle:
                document = json.load(handle)
            results.append({"name": paper["name"], "status": "already-present",
                            "questions": (document.get("counts") or {}).get("questions"),
                            "run_dir": run_dir,
                            "resolved": resolved.get("how"),
                            "registry_key": resolved.get("registry_key"),
                            "subject": paper["subject"], "year": paper["year"],
                            "ordinal": paper["ordinal"]})
            continue
        record = build_one(paper, resolved, args.work, package_version=package_version,
                           slug=slug)
        results.append(record)
        mark = {"packaged": "ok ", "blocked": "!! ", "no-registry-key": "-- "}.get(
            record["status"], "?  ")
        print("  %3d/%d %s %-52s %-7s q=%-4s %5.1fs %s"
              % (index, len(papers), mark, paper["name"][:52], record["status"],
                 record.get("questions", "-"), record.get("seconds", 0),
                 ",".join(record.get("gate_blocking") or [])[:60]), flush=True)
        if args.limit and index >= args.limit:
            break

    out_path = os.path.join(args.work, "package_report.jsonl")
    with open(out_path, "w", encoding="utf-8") as handle:
        for record in results:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    counts = collections.Counter(r["status"] for r in results)
    by_how = collections.Counter(r.get("resolved") for r in results)
    questions = sum(r.get("questions") or 0 for r in results)
    summary = {"category": args.category, "papers": len(results), "status": dict(counts),
               "resolved_by": dict(by_how), "questions": questions,
               "blocked": [r["name"] for r in results if r["status"] == "blocked"][:40],
               "unresolved": [r["name"] for r in results if r["status"] == "no-registry-key"][:40]}
    with open(os.path.join(args.work, "package_summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_summary(summary)
    print("\nwrote %s" % out_path)


def _slug(category):
    """A stable slug from the category name, without inventing a romanisation.

    The corpus is Chinese and the slugs the platform already uses are romanisations a person
    chose (`medtech`). Inventing one here would produce a package version that reads as
    official and is not, so an unmapped category gets its own name.
    """
    known = {"醫事檢驗師": "medtech", "藥師": "pharmacist"}
    return known.get(category, category)


def _print_summary(summary):
    print("\n" + "=" * 78)
    print("%s: %d papers, %d questions" % (summary["category"], summary["papers"],
                                           summary["questions"]))
    for status, count in sorted(summary["status"].items()):
        print("  %-18s %d" % (status, count))
    print("  resolved by       %s" % summary["resolved_by"])
    if summary["blocked"]:
        print("  blocked (%d):" % len(summary["blocked"]))
        for name in summary["blocked"][:15]:
            print("    %s" % name)
    if summary["unresolved"]:
        print("  no registry key (%d):" % len(summary["unresolved"]))
        for name in summary["unresolved"][:15]:
            print("    %s" % name)


if __name__ == "__main__":
    main()
