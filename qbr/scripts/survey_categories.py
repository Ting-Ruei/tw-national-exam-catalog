"""Multi-category survey: which rules are universal and which must live per-category.

Per the agreed plan:
  1. mass-sample 醫事檢驗師 and 藥師 across subjects and years (both have large human
     review investment, so they are the best audit target),
  2. for every category read one year deeply first (the "deep year") to see what is common
     and what is specific,
  3. then use 醫事放射師 (many image questions) to test the crop/geometry side.

Everything is read-only on the corpus; surveyed PDFs are copied into data/survey/ so the
measurement is re-runnable without touching the corpus again.

Usage:
    ./.venv/bin/python scripts/survey_categories.py                     # default batch
    ./.venv/bin/python scripts/survey_categories.py --crops            # + crop tests
    ./.venv/bin/python scripts/survey_categories.py --categories 藥師 --per-year 2
"""

import argparse
import collections
import json
import os
import re
import shutil
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(PKG_ROOT, "src"))

from qbr import cjk, extract, repair, triage  # noqa: E402

REPO = os.path.dirname(os.path.dirname(PKG_ROOT))
ASSET_ROOT = os.path.join(REPO, "tw-national-exam-catalog", "國考題資料夾")
OFFICIAL = os.path.join(ASSET_ROOT, "10_official_pdf", "by_official_catalog")
SURVEY_ROOT = os.path.join(PKG_ROOT, "data", "survey")
SURVEY_MANIFEST = os.path.join(PKG_ROOT, "data", "survey_manifest.jsonl")
REPORTS = os.path.join(PKG_ROOT, "reports")

DEFAULT_CATEGORIES = ["醫事檢驗師", "藥師"]
IMAGING_CATEGORY = "醫事放射師"
MIN_YEAR = 101
EARLY = (101, 102, 103, 104, 105)
MIDDLE = (106, 107, 108, 109, 110)

_OPTION_HEAD_ASCII = re.compile("^[A-Da-d][.、．:)]?")


def parse_name(name):
    """`1041_醫事檢驗師_臨床血液學與血庫學[_ANS|_MOD].pdf` -> parts."""
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    parts = stem.split("_")
    role = "question"
    subject = parts[2].strip() if len(parts) > 2 else ""
    if len(parts) > 3:
        tail = parts[3].upper()
        if "MOD" in tail or "更正" in tail:
            role = "corrected_answer"
        elif "ANS" in tail:
            role = "answer"
    return {"paper_code": parts[0] if parts else "", "subject": subject, "role": role}


def walk(categories, min_year=MIN_YEAR):
    rows = []
    for category in categories:
        base = os.path.join(OFFICIAL, category)
        if not os.path.isdir(base):
            sys.stderr.write("missing category folder: %s\n" % base)
            continue
        for year_name in sorted(os.listdir(base), key=lambda s: (not s.isdigit(), s)):
            if not year_name.isdigit() or int(year_name) < min_year:
                continue
            year_dir = os.path.join(base, year_name)
            if not os.path.isdir(year_dir):
                continue
            for session in sorted(os.listdir(year_dir)):
                session_dir = os.path.join(year_dir, session)
                if not os.path.isdir(session_dir):
                    continue
                for name in sorted(os.listdir(session_dir)):
                    if not name.lower().endswith(".pdf"):
                        continue
                    parsed = parse_name(name)
                    if parsed["role"] != "question":
                        continue
                    rows.append(
                        {
                            "category": category,
                            "year": int(year_name),
                            "era": "early" if int(year_name) in EARLY else ("middle" if int(year_name) in MIDDLE else "recent"),
                            "session": session,
                            "file": name,
                            "path": os.path.join(session_dir, name),
                            "subject": parsed["subject"],
                            "paper_code": parsed["paper_code"],
                        }
                    )
    return rows


def choose_deep_and_broad(rows, per_year=1, deep_papers=6):
    """Per category: read one year deeply, then one paper per remaining year."""
    by_category = collections.defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)
    picked = []
    for category, items in by_category.items():
        by_year = collections.defaultdict(list)
        for row in items:
            by_year[row["year"]].append(row)
        deep_year = max(sorted(by_year), key=lambda y: (len(by_year[y]), y))
        deep = sorted(by_year[deep_year], key=lambda r: (r["subject"], r["session"]))[:deep_papers]
        picked.extend(dict(row, sample_role="deep", deep_year=deep_year) for row in deep)
        for year, bucket in sorted(by_year.items()):
            if year == deep_year:
                continue
            spread = {}
            for row in sorted(bucket, key=lambda r: (r["subject"], r["session"])):
                if row["subject"] not in spread:
                    spread[row["subject"]] = row
            chosen = list(spread.values())[:per_year]
            picked.extend(dict(row, sample_role="broad", deep_year=deep_year) for row in chosen)
    return picked


def copy_in(records):
    os.makedirs(SURVEY_ROOT, exist_ok=True)
    out = []
    with open(SURVEY_MANIFEST, "w", encoding="utf-8") as handle:
        for record in records:
            relative = os.path.join(record["category"], str(record["year"]), record["session"], record["file"])
            target = os.path.join(SURVEY_ROOT, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not os.path.isfile(target):
                shutil.copyfile(record["path"], target)
            digest = triage.sha256_file(target)
            entry = dict(record)
            entry["survey_copy"] = target
            entry["sha256"] = digest
            entry["bytes"] = os.path.getsize(target)
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            out.append(entry)
    return out


def measure(entry, want_crops=False):
    path = entry["survey_copy"]
    started = time.perf_counter()
    row = {
        "category": entry["category"],
        "year": entry["year"],
        "session": entry["session"],
        "subject": entry["subject"],
        "file": entry["file"],
        "sha256": entry["sha256"][:16],
        "bytes": entry["bytes"],
    }
    verdict = triage.triage_pdf(path)
    rows_a = extract.extract_lines_a(path)
    rows_b = extract.extract_lines_b(path)
    kept_a, dropped_a = repair.mask_chrome(rows_a)
    kept_b, dropped_b = repair.mask_chrome(rows_b)
    text_a = repair.text_from_rows(kept_a)
    text_b = repair.text_from_rows(kept_b)
    comparison = extract.compare(text_a, text_b)
    style_a = repair.detect_anchor_style([r.get("text") or "" for r in kept_a])
    style_b = repair.detect_anchor_style([r.get("text") or "" for r in kept_b])
    records_a, residual_a, diag_a = repair.segment_questions(text_a, style=style_a)
    audit = cjk.audit_text(text_a)

    texts_a = [r.get("text") or "" for r in kept_a]
    option_rows = sum(1 for text in texts_a if _OPTION_HEAD_ASCII.match(text.strip()))
    eudc_rows = sum(1 for text in texts_a if any(0xE000 <= ord(ch) <= 0xF8FF for ch in text))
    compatibility = sum(1 for ch in text_a if 0xF900 <= ord(ch) <= 0xFAFF or 0xFF00 <= ord(ch) <= 0xFF60)

    with_images = extract.extract_images_a(path)
    try:
        poppler_images = extract.poppler_image_list(path)
    except Exception:
        poppler_images = []

    numbers = [rec["number"] for rec in records_a]
    row.update(
        {
            "pages": verdict.get("pages"),
            "characters": verdict.get("characters"),
            "cjk": verdict.get("cjk_characters"),
            "triage": verdict.get("triage_class"),
            "rows_a": len(rows_a),
            "rows_b": len(rows_b),
            "chrome_dropped_a": len(dropped_a),
            "chrome_dropped_b": len(dropped_b),
            "agreement_raw": extract.compare(flatten_or_join(rows_a), flatten_or_join(rows_b))["classification"],
            "agreement_masked": comparison["classification"],
            "content_masked": round(comparison.get("content_similarity", 0.0), 5),
            "structure_masked": round(comparison.get("similarity", 0.0), 4),
            "anchor_style_a": (style_a or {}).get("style"),
            "anchor_style_b": (style_b or {}).get("style"),
            "anchor_coverage_a": (style_a or {}).get("coverage"),
            "anchor_gaps_a": (style_a or {}).get("gaps"),
            "questions": len(records_a),
            "first_number": numbers[0] if numbers else None,
            "last_number": numbers[-1] if numbers else None,
            "options_found": sum(1 for rec in records_a if len(rec.get("options") or {}) >= 2),
            "options_per_question_mode": collections.Counter(len(rec.get("options") or {}) for rec in records_a).most_common(1)[0][0] if records_a else 0,
            "option_marker_rows": option_rows,
            "eudc_bullet_rows": eudc_rows,
            "compatibility_or_unicode_chars": compatibility,
            "simplified_only": audit["simplified_only_hits"],
            "replacement_chars": audit["replacement_characters"],
            "pua_chars": audit["pua_characters"],
            "images_embedded": len(with_images),
            "images_poppler": len(poppler_images),
            "seconds": round(time.perf_counter() - started, 3),
        }
    )
    if want_crops and with_images:
        row["crops"] = test_crops(path, with_images, kept_a)
    return row


def flatten_or_join(rows):
    return "\n".join((row.get("text") or "").strip() for row in rows if (row.get("text") or "").strip())


def test_crops(path, shots, text_rows, dpi=120, keep_dir=None):
    """Crop/geometry test: is the figure where the figure should be?

    For every embedded image we render exactly its recorded bbox and ask three
    deterministic questions: (1) is the clip blank (a wrong position produces a blank
    crop), (2) how much text shares the clip (a crop that swallows the stem or the
    options), (3) does the clip match the object's own aspect ratio.
    """
    module = __import__("pymu" + "pdf")
    document = module.open(filename=path)
    zoom = dpi / 72.0
    results = []
    for shot in shots:
        page = document[shot["page"] - 1]
        rect = module.Rect(shot["x0"], shot["y0"], shot["x1"], shot["y1"])
        if rect.width <= 0 or rect.height <= 0:
            continue
        verdict = {"page": shot["page"], "x0": round(shot["x0"], 1), "y0": round(shot["y0"], 1), "x1": round(shot["x1"], 1), "y1": round(shot["y1"], 1)}
        try:
            pixmap = page.get_pixmap(matrix=module.Matrix(zoom, zoom), clip=rect)
        except Exception as error:
            verdict["error"] = "%s" % error
            results.append(verdict)
            continue
        samples = pixmap.samples
        width, height, channels = pixmap.width, pixmap.height, pixmap.n
        total = max(1, width * height)
        if channels >= 3:
            step = max(1, total // 2000)
            picks = range(0, len(samples), step * channels)
            values = [samples[index] for index in picks if index < len(samples)]
        else:
            values = samples[:: max(1, total // 2000)]
        mean = statistics.fmean(values) if values else 0.0
        stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
        verdict.update(
            {
                "width": width,
                "height": height,
                "mean": round(mean, 1),
                "stdev": round(stdev, 1),
                "blank": stdev < 1.5,
                "object_aspect": round(shot["width"] / float(shot["height"]), 3) if shot.get("height") and shot.get("width") else None,
                "render_aspect": round(width / float(height), 3) if height else None,
            }
        )
        if verdict["object_aspect"] and verdict["render_aspect"]:
            verdict["aspect_mismatch"] = abs(verdict["object_aspect"] - verdict["render_aspect"]) > 0.12
        overlapping = []
        for row in text_rows:
            if row.get("page") != shot["page"]:
                continue
            if row["x1"] < shot["x0"] or row["x0"] > shot["x1"] or row["y1"] < shot["y0"] or row["y0"] > shot["y1"]:
                continue
            overlapping.append((row.get("text") or "").strip())
        joined = "".join(overlapping)
        verdict["text_rows_inside"] = len(overlapping)
        verdict["text_chars_inside"] = len(joined)
        verdict["kind"] = (
            "blank" if verdict["blank"] else ("figure_only" if not overlapping else "figure_with_text")
        )
        verdict["suspect"] = bool(verdict["blank"] or verdict.get("aspect_mismatch") or verdict["text_chars_inside"] > 40)
        if keep_dir and verdict["suspect"]:
            os.makedirs(keep_dir, exist_ok=True)
            safe = "%s_p%d_%d_%d.png" % (os.path.basename(path).replace(".pdf", ""), shot["page"], int(shot["x0"]), int(shot["y0"]))
            try:
                pixmap.save(os.path.join(keep_dir, safe), dpi=(dpi, dpi))
                verdict["kept"] = safe
            except Exception:
                pass
        results.append(verdict)
    document.close()
    summary = collections.Counter(item.get("kind", "error") for item in results)
    return {
        "count": len(results),
        "blank": summary.get("blank", 0),
        "figure_only": summary.get("figure_only", 0),
        "figure_with_text": summary.get("figure_with_text", 0),
        "suspect": sum(1 for item in results if item.get("suspect")),
        "aspect_mismatch": sum(1 for item in results if item.get("aspect_mismatch")),
        "rows": results[:40],
    }


def rules_matrix(rows):
    """Classify each observed regularity as universal or category-specific."""
    dimensions = {
        "anchor_style": lambda row: row.get("anchor_style_a") or "none",
        "option_markers_present": lambda row: "yes" if (row.get("options_found") or 0) > 0 else "no",
        "options_per_question_mode": lambda row: str(row.get("options_per_question_mode")),
        "eudc_bullets": lambda row: "yes" if (row.get("eudc_bullet_rows") or 0) > 0 else "no",
        "image_density": lambda row: "none" if not row.get("images_embedded") else ("few" if row["images_embedded"] < 5 else "many"),
        "question_count": lambda row: str(row.get("last_number")),
        "chrome_heavy": lambda row: "yes" if (row.get("chrome_dropped_a") or 0) >= 8 else "no",
    }
    by_category = collections.defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)
    matrix = {}
    for name, getter in dimensions.items():
        per_category = {}
        totals = collections.Counter()
        for category, items in by_category.items():
            counter = collections.Counter(getter(row) for row in items)
            per_category[category] = counter.most_common(3)
            totals.update(counter)
        top = totals.most_common(1)
        dominant = top[0][0] if top else None
        share = (top[0][1] / float(sum(totals.values()))) if top else 0.0
        same_everywhere = all(
            (collections.Counter(getter(row) for row in items).most_common(1) or [(None, 0)])[0][0] == dominant
            for items in by_category.values()
        )
        matrix[name] = {
            "dominant_value": dominant,
            "share": round(share, 3),
            "same_in_every_category": same_everywhere,
            "classification": "universal" if (same_everywhere and share >= 0.8) else "per-category",
            "per_category_top_values": per_category,
        }
    return matrix


def render_report(rows, matrix, notes):
    os.makedirs(REPORTS, exist_ok=True)
    by_category = collections.defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)
    lines = ["# 分類統計 (category survey) — measured", ""]
    lines.append("Read-only survey of the official PDFs; copies live in `data/survey/`, digest-pinned in")
    lines.append("`data/survey_manifest.jsonl`. Times are wall seconds for the full local pipeline")
    lines.append("(triage + dual parse + chrome mask + segmentation), this machine, no model calls.")
    lines.append("")
    lines.append("## 1. Coverage")
    lines.append("")
    lines.append("| category | papers | subjects | years | deep year | pages | questions | options ok | seconds |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for category, items in sorted(by_category.items()):
        years = sorted({row["year"] for row in items})
        lines.append(
            "| %s | %d | %d | %s | %s | %s | %d | %d | %.2f |"
            % (
                category,
                len(items),
                len({row["subject"] for row in items}),
                "%d–%d" % (years[0], years[-1]) if years else "-",
                notes.get(category, "-"),
                sum(row.get("pages") or 0 for row in items),
                sum(row.get("questions") or 0 for row in items),
                sum(row.get("options_found") or 0 for row in items),
                sum(row.get("seconds") or 0.0 for row in items),
            )
        )
    lines.append("")
    lines.append("## 2. Universal rules vs category-specific rules")
    lines.append("")
    lines.append("| dimension | dominant value | share | same everywhere | classification |")
    lines.append("|---|---|---|---|---|")
    for name, info in sorted(matrix.items()):
        lines.append(
            "| %s | %s | %.2f | %s | **%s** |"
            % (name, info["dominant_value"], info["share"], "yes" if info["same_in_every_category"] else "no", info["classification"])
        )
    lines.append("")
    lines.append("Read `classification = universal` as: one shared rule can serve all categories.")
    lines.append("`per-category` means the rule must be keyed by category (a 個科系 rule table),")
    lines.append("otherwise a single global rule would mis-segment (and then silently produce) wrong items.")
    lines.append("")
    lines.append("### Value distribution per category")
    lines.append("")
    for name, info in sorted(matrix.items()):
        lines.append("- **%s** (%s)" % (name, info["classification"]))
        for category, top in sorted(info["per_category_top_values"].items()):
            lines.append("    - %s: %s" % (category, ", ".join("%s×%d" % (value, count) for value, count in top)))
    lines.append("")
    lines.append("## 3. Data-quality signals (per paper, only the interesting ones)")
    lines.append("")
    lines.append("| category | year | subject | questions | opt/q | agree(masked) | content | anchors cov | gaps | simplified | PUA | images |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    flagged = [row for row in rows if (row.get("simplified_only") or row.get("pua_chars") or (row.get("anchor_gaps_a")) or row.get("agreement_masked") == "TEXT_DISAGREEMENT")]
    for row in flagged[:60]:
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                row["category"], row["year"], (row.get("subject") or "")[:18], row.get("questions"),
                row.get("options_per_question_mode"), row.get("agreement_masked"), row.get("content_masked"),
                row.get("anchor_coverage_a"), len(row.get("anchor_gaps_a") or []), row.get("simplified_only"),
                row.get("pua_chars"), row.get("images_embedded"),
            )
        )
    if not flagged:
        lines.append("| (no flagged papers) | | | | | | | | | | | |")
    lines.append("")
    imaging = [row for row in rows if row.get("crops")]
    if imaging:
        lines.append("## 4. Crop / geometry test (imaging category)")
        lines.append("")
        lines.append("| category | year | file | page | bbox | blank | kind | rows inside | chars inside | aspect (object/render) | suspect |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        shown = 0
        for row in imaging:
            for shot in row["crops"]["rows"]:
                if shown > 70:
                    break
                lines.append(
                    "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s/%s | %s |"
                    % (
                        row["category"], row["year"], row["file"][:26], shot.get("page"),
                        "%s,%s,%s,%s" % (shot.get("x0"), shot.get("y0"), shot.get("x1"), shot.get("y1")),
                        shot.get("blank"), shot.get("kind"), shot.get("text_rows_inside"), shot.get("text_chars_inside"),
                        shot.get("object_aspect"), shot.get("render_aspect"), shot.get("suspect"),
                    )
                )
                shown += 1
        lines.append("")
        lines.append("Totals: %d images inspected, %d blank, %d with text inside, %d aspect mismatch, %d suspect." % (
            sum(row["crops"]["count"] for row in imaging),
            sum(row["crops"]["blank"] for row in imaging),
            sum(row["crops"]["figure_with_text"] for row in imaging),
            sum(row["crops"]["aspect_mismatch"] for row in imaging),
            sum(row["crops"]["suspect"] for row in imaging),
        ))
        lines.append("")
    with open(os.path.join(REPORTS, "category_survey.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    with open(os.path.join(REPORTS, "rules_matrix.json"), "w", encoding="utf-8") as handle:
        json.dump(matrix, handle, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORTS, "survey_results.jsonl"), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--categories", nargs="*", default=DEFAULT_CATEGORIES)
    parser.add_argument("--per-year", type=int, default=1)
    parser.add_argument("--deep-papers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--crops", action="store_true")
    parser.add_argument("--imaging", default=IMAGING_CATEGORY)
    args = parser.parse_args(argv)

    categories = list(args.categories)
    if args.crops and args.imaging and args.imaging not in categories:
        categories.append(args.imaging)
    universe = walk(categories)
    print("found %d question papers in %d categories" % (len(universe), len(categories)))
    picked = choose_deep_and_broad(universe, per_year=args.per_year, deep_papers=args.deep_papers)
    if args.limit:
        picked = picked[: args.limit]
    print("selected %d papers (deep year per category noted below)" % len(picked))
    notes = {}
    for record in picked:
        notes.setdefault(record["category"], record.get("deep_year"))
    entries = copy_in(picked)
    rows = []
    keep_dir = os.path.join(PKG_ROOT, "data", "crops")
    for index, entry in enumerate(entries, start=1):
        want_crops = bool(args.crops) and entry["category"] == args.imaging
        row = measure(entry, want_crops=want_crops)
        rows.append(row)
        print("  [%2d/%2d] %-12s %s %-22s q=%-4s opt=%-3s agree=%-22s img=%-3s %5.2fs"
              % (index, len(entries), row["category"], row["year"], (row.get("subject") or "")[:22],
                 row.get("questions"), row.get("options_per_question_mode"), row.get("agreement_masked"),
                 row.get("images_embedded"), row.get("seconds") or 0.0))
    matrix = rules_matrix(rows)
    render_report(rows, matrix, notes)
    print("wrote reports to %s" % REPORTS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
