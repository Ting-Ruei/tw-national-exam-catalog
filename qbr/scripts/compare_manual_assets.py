# -*- coding: utf-8 -*-
"""Score the pipeline's figure detection against the hand-cut reference bank.

The reference bank is the only ground truth in this workspace that was not produced by the code
under test. `40_exports/question_bank_packages/tw-national-exam-medtech-v2026.08.04-r1/` holds 556
assets that a person cut, each filed against a question and - for option pictures - an option key.
Its `assets/` files are 549/556 byte-identical to the picture objects they came from, so it is also
a statement about *how* to cut: take the object, not a region of the page.

So this compares two things, and the second is the one that matters:

    which questions carry a figure   (detection)
    which option each picture is for (binding)

Detection is scored as agreement against the bank. The bank is not assumed to be complete - it
covers the questions a person got to - so a question the pipeline finds and the bank does not hold
is reported separately from a disagreement, and the missing side is what is counted as an error:
a question the bank says has a figure and the pipeline does not find is a **miss**, and it is the
only kind of error that silently loses content.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qbr import paths  # noqa: E402

# Found, not written down. The exported bank lives under the corpus, which is not in git, so this
# path is only meaningful next to a corpus; `paths` says which one and the caller can report it.
DEFAULT_BANK = os.path.join(paths.asset_root(), "40_exports", "question_bank_packages",
                            "tw-national-exam-medtech-v2026.08.04-r1")


def bank_questions(bank_dir):
    """The bank's own record of which question holds which picture, keyed by paper and number.

    Reads `questions.jsonl`, which carries the category, year, sitting, subject and the asset
    references together - so the key is the paper's identity and the question number, and no
    decoding of the registry string is needed.
    """
    rows = []
    path = os.path.join(bank_dir, "questions.jsonl")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            metadata = record.get("metadata") or {}
            assets = metadata.get("asset_refs") or []
            if isinstance(assets, str):
                try:
                    assets = json.loads(assets)
                except Exception:
                    assets = []
            roles = collections.Counter()
            options = {}
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                role = asset.get("role") or "?"
                roles[role] += 1
                if role == "option_image" and asset.get("option_key"):
                    options.setdefault(str(asset["option_key"]), 0)
                    options[str(asset["option_key"])] += 1
            rows.append({
                "category": record.get("normalized_category_name") or "",
                "subject": record.get("normalized_subject_name") or "",
                "year": str(record.get("roc_year") or ""),
                "sitting": str(record.get("exam_number") or ""),
                "number": int(record.get("question_number") or 0),
                "figures": roles.get("figure", 0),
                "option_images": roles.get("option_image", 0),
                "options": options,
                "has_figure": bool(roles.get("figure", 0) or roles.get("option_image", 0)),
            })
    return rows


def pipeline_papers(work_root):
    """Every packaged run under a `batch_package` work root, as `{run_name: run_dir}`."""
    papers = {}
    for name in sorted(os.listdir(work_root)):
        run_dir = os.path.join(work_root, name)
        if os.path.isfile(os.path.join(run_dir, "review-ui", "candidates.jsonl")):
            papers[name] = run_dir
    return papers


def pipeline_figures(run_dir, paper_key):
    """The pipeline's own record for one run, from the `figures.json` the crop stage wrote."""
    path = os.path.join(run_dir, "review-ui", "figures.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        record = json.load(handle)
    found = {}
    for entry in record.get("figures") or []:
        # A crop whose render is blank is not a figure, whatever objects the page lists. The flag is
        # written by the crop stage, which is the only stage that looks at the render.
        if entry.get("blank_render"):
            continue
        if entry.get("error") and not entry.get("file"):
            continue
        number = int(entry.get("number") or 0)
        options = {}
        for option in entry.get("option_images") or []:
            options[str(option.get("key"))] = options.get(str(option.get("key")), 0) + 1
        found[number] = {"reasons": entry.get("reasons") or [],
                         "option_images": options,
                         "page": entry.get("page")}
    return found


def match_paper(bank_row, papers):
    """The run a bank question belongs to, by the paper's identity rather than by string surgery.

    A run is named `1152_醫事檢驗師_生物化學與臨床生化學`, which carries the year, the sitting, the
    category and the subject. Matching on those four is the paper's identity, and the sitting is
    part of it: `藥師(一)` has both a 第1次 and a 第2次 in some years, and matching without the
    sitting silently pairs a question with the wrong paper.
    """
    year = bank_row["year"]
    sitting = bank_row["sitting"]
    subject = bank_row["subject"]
    for name in papers:
        head = name.split("_", 1)[0]
        if len(head) < 4 or not head[:3].isdigit():
            continue
        if head[:3] != year or head[3] != sitting:
            continue
        body = name.split("_", 2)
        if len(body) < 3:
            continue
        run_subject = body[2]
        if _fold(run_subject) == _fold(subject):
            return name
    return None


_BRACKETS = str.maketrans({"（": "(", "）": ")", "理": "理", "臨": "臨", "行": "行"})


def _fold(value):
    return re.sub(r"\s+", "", str(value or "")).translate(_BRACKETS)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score figure detection against the hand-cut reference bank.")
    parser.add_argument("--bank", default=DEFAULT_BANK)
    parser.add_argument("--work", default="/tmp/qbr-final2")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    bank = bank_questions(args.bank)
    papers = pipeline_papers(args.work)
    totals = collections.Counter()
    misses, wrong_options, extra = [], [], []

    by_paper = collections.defaultdict(list)
    for row in bank:
        by_paper[(row["category"], row["year"], row["sitting"], row["subject"])].append(row)

    for (category, year, sitting, subject), rows in sorted(by_paper.items()):
        name = match_paper(rows[0], papers)
        totals["bank_papers"] += 1
        if name is None:
            totals["unmatched_papers"] += 1
            continue
        totals["matched_papers"] += 1
        found = pipeline_figures(papers[name], name)
        for row in rows:
            totals["bank_questions"] += 1
            has = row["has_figure"]
            got = found.get(row["number"])
            if has and not got:
                totals["miss"] += 1
                misses.append({"paper": name, "number": row["number"],
                               "figures": row["figures"],
                               "option_images": row["option_images"],
                               "options": row["options"]})
                continue
            if not has:
                if got:
                    totals["extra"] += 1
                    extra.append({"paper": name, "number": row["number"],
                                  "reasons": got["reasons"]})
                continue
            totals["agree"] += 1
            # Binding: the bank names the option each picture belongs to, so the option keys the
            # pipeline produced can be compared directly. The counts are compared as well, because
            # a question whose every option has a picture is the case the answer slot needs.
            want = tuple(sorted(row["options"]))
            have = tuple(sorted(got["option_images"]))
            if want and want != have:
                totals["option_mismatch"] += 1
                wrong_options.append({"paper": name, "number": row["number"],
                                      "bank": list(want), "pipeline": list(have)})
            elif want:
                totals["option_agree"] += 1

    # The denominator is the bank's *figure-bearing* questions, not every question it holds. The
    # bank lists all 15,120 of its questions, and only a few hundred carry a picture; scoring
    # against the full list would report agreement on the questions that have nothing to agree
    # about and make the one number that matters unreadable.
    with_figure = totals["agree"] + totals["miss"]
    rate = (totals["agree"] / with_figure * 100) if with_figure else 0.0
    summary = {
        "bank_questions": totals["bank_questions"],
        "bank_with_figure": with_figure,
        "recall_pct": round(rate, 1),
        "matched_papers": totals["matched_papers"],
        "unmatched_papers": totals["unmatched_papers"],
        "agree": totals["agree"],
        "miss": totals["miss"],
        "extra": totals["extra"],
        "option_agree": totals["option_agree"],
        "option_mismatch": totals["option_mismatch"],
        "agreement_pct": round(rate, 1),
    }
    if args.json:
        print(json.dumps({"summary": summary, "misses": misses,
                          "option_mismatches": wrong_options, "extra": extra},
                         ensure_ascii=False))
        return

    print("=== 與手工題庫比對（外部標準）")
    for key in ("bank_questions", "bank_with_figure", "matched_papers", "unmatched_papers",
                "agree", "miss", "recall_pct", "extra", "option_agree", "option_mismatch"):
        print(f"   {key:18} {summary[key]}")
    print("\n  漏掉（題庫有圖、管線沒找到）:")
    for row in misses[:15]:
        print(f"     {row['paper'][:44]:46} Q{row['number']:<3} 圖{row['figures']} "
              f"選項圖{row['option_images']} {row['options']}")
    print("\n  選項綁定不同:")
    for row in wrong_options[:15]:
        print(f"     {row['paper'][:44]:46} Q{row['number']:<3} 題庫{row['bank']} "
              f"管線{row['pipeline']}")
    print("\n  多找到（題庫沒有、管線有）:")
    for row in extra[:15]:
        print(f"     {row['paper'][:44]:46} Q{row['number']:<3} {row['reasons']}")


if __name__ == "__main__":
    main()
