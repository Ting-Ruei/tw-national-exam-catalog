"""Phase 3 same-input comparison: legacy MinerU corpus vs the new deterministic path.

Runs, over exactly the PDFs copied into data/raw (same inputs for both sides):

  Stage C  cheap triage (PyMuPDF, no inference)
  Stage D  dual deterministic extraction (PyMuPDF + poppler) + agreement classification
  §15.3    legacy-vs-new error taxonomy on the same document
  §17.2    resource counters (wall, CPU, peak RSS, engine calls)

Writes reports/. Blockers go out immediately (§21): an unexplained divergence, an
unreachable engine, or a legacy artefact that cannot be mapped to an official source is
reported instead of being quietly averaged away.
"""

import json
import os
import resource
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(PKG_ROOT, "src"))

from qbr import cjk, extract, manifests, repair, triage  # noqa: E402

REPORTS = os.path.join(PKG_ROOT, "reports")
MANIFEST = manifests.of_package(PKG_ROOT)

# Starting thresholds. They are *not* blessed: run_pilot also dumps the observed
# distributions so the final values are benchmark-derived (Protocol §8 Stage C, §32.7).
THRESHOLDS = {
    "content_floor": 0.999,
    "structure_identity": 0.999,
    "legacy_match": 0.999,
    "legacy_whitespace_only": 0.995,
    "max_simplified_ratio_native": 0.002,
}


def _gaps(records):
    numbers = sorted({rec.get("number") for rec in records if rec.get("number")})
    if len(numbers) < 2:
        return 0
    return max(b - a for a, b in zip(numbers, numbers[1:]))


def _now():
    return time.time()


def _resource_snapshot():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "cpu_seconds": (usage.ru_utime + usage.ru_stime) / 1e6,
        "peak_rss_mb": round(usage.ru_maxrss / 1024.0 / 1024.0, 1),
    }


def _read_text(path):
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def classify_legacy_vs_new(new_text, legacy_text):
    """Coarse §15.3 taxonomy, deterministic only, no model in the loop."""
    if not legacy_text.strip():
        return "NO_LEGACY_OUTPUT", 0.0
    new_norm = extract.safe_normalize(new_text)
    legacy_norm = extract.safe_normalize(legacy_text)
    dense_new = extract._strip_all_whitespace(new_norm)
    dense_legacy = extract._strip_all_whitespace(legacy_norm)
    score = extract._bag_similarity(extract._bag(dense_new), extract._bag(dense_legacy))
    if score >= THRESHOLDS["legacy_match"]:
        return "MATCH", score
    if score >= THRESHOLDS["legacy_whitespace_only"]:
        return "WHITESPACE_ONLY", score
    new_audit = cjk.audit_text(new_text)
    legacy_audit = cjk.audit_text(legacy_text)
    tags = []
    if legacy_audit["simplified_only_hits"] > 0 and new_audit["simplified_only_hits"] == 0:
        tags.append("UNICODE_SYMBOL_ERROR")
    if abs(len(dense_legacy) - len(dense_new)) > max(40, 0.05 * max(len(dense_new), 1)):
        tags.append("MISSING_TEXT" if len(dense_legacy) < len(dense_new) else "EXTRA_TEXT")
    if len(legacy_audit["question_anchors"]) != len(new_audit["question_anchors"]):
        tags.append("QUESTION_SPLIT_ERROR")
    if legacy_audit["replacement_characters"] or legacy_audit["pua_characters"]:
        tags.append("HEADER_FOOTER_CONTAMINATION" if not tags else tags[0])
    return ("|" .join(tags)) if tags else "UNKNOWN", score


def run_record(record, blockers, issues):
    pdf = record["raw_copy"]
    row = {
        "uid": record["uid"],
        "category": record["category"],
        "year": record["year"],
        "role": record["role"],
        "sha256": record["sha256"][:16],
        "bytes": record["bytes"],
        "legacy_status": record["legacy_status"],
    }
    started = _now()
    cpu_before = _resource_snapshot()["cpu_seconds"]

    try:
        triaged = triage.triage_pdf(pdf)
    except Exception as error:
        blockers.append(
            {
                "severity": "blocker",
                "uid": record["uid"],
                "what": "stage-C triage failed",
                "evidence": "%s: %s" % (type(error).__name__, error),
                "attempted": "PyMuPDF open + text/word/image/font scan",
                "options": ["inspect with poppler pdfinfo", "route to quarantine"],
                "can_continue_elsewhere": True,
            }
        )
        row["triage_class"] = "TRIAGE_ERROR"
        return row
    after_triage = _now()

    try:
        pair = extract.extract_pair(pdf)
    except Exception as error:
        blockers.append(
            {
                "severity": "blocker",
                "uid": record["uid"],
                "what": "Stage-D dual extraction failed",
                "evidence": "%s: %s" % (type(error).__name__, error),
                "attempted": "PyMuPDF lines + poppler pdftotext -bbox",
                "options": ["check poppler install", "single-parser run + mandatory review"],
                "can_continue_elsewhere": True,
            }
        )
        pair = {"rows_a": [], "rows_b": [], "verdict": {"classification": "MISSING_IN_ONE_PARSER", "content_similarity": 0.0, "similarity": 0.0, "first_difference": ""}}
    after_dual = _now()

    # Level-0 deterministic repair, then re-measure agreement. This is what separates a
    # benign print-form artefact (repeated header draws) from a real content dispute.
    try:
        repaired_a, dropped_a = repair.normalize_pretty(pair["text_a"])
        repaired_b, dropped_b = repair.normalize_pretty(pair["text_b"])
        verdict_after = extract.compare(repaired_a, repaired_b)
        # One view for the style and one for the segmentation is how a paper once lost 79 of
        # its 80 questions and the continuity check never noticed; the raw rows are offered
        # along with the merged text, and the declared parts of a paper (甲、申論 / 乙、測驗)
        # are segmented one by one, so that neither half is taken for the whole.
        views_a = [row.get("text") or "" for row in (pair.get("rows_a") or [])]
        views_b = [row.get("text") or "" for row in (pair.get("rows_b") or [])]
        questions_a, residual_a, diag_a = repair.segment_mixed(repaired_a, extra_views=[views_a, views_b])
        questions_b, residual_b, diag_b = repair.segment_mixed(repaired_b, extra_views=[views_b, views_a])
    except Exception as error:
        dropped_a, dropped_b, verdict_after = [], [], {"classification": "REPAIR_ERROR", "content_similarity": 0.0, "similarity": 0.0}
        questions_a, questions_b, residual_a, residual_b = [], [], [], []
        diag_a = diag_b = {"style": None, "detail": None}
        blockers.append({
            "severity": "high",
            "uid": record["uid"],
            "what": "level-0 deterministic repair raised",
            "evidence": "%s: %s" % (type(error).__name__, error),
            "attempted": "repair.normalize_pretty + repair.segment_questions",
            "options": ["inspect rule coverage", "route to quarantine"],
            "can_continue_elsewhere": True,
        })

    images_a = []
    try:
        images_a = extract.extract_images_a(pdf)
    except Exception:
        images_a = []
    try:
        images_b = extract.poppler_image_list(pdf)
    except Exception:
        images_b = []

    row.update(
        {
            "pages": triaged["pages"],
            "triage_class": triaged["triage_class"],
            "reasons": triaged["reasons"],
            "characters": triaged["characters"],
            "cjk": triaged["cjk_characters"],
            "native_simplified_hits": triaged["simplified_only_hits"],
            "native_simplified_ratio": triaged["simplified_ratio"],
            "damaged": triaged["damaged_characters"],
            "replacement": triaged["replacement_characters"],
            "pua": triaged["pua_characters"],
            "anchors": triaged["question_anchors"],
            "anchor_gap": triaged["max_gap_in_anchors"],
            "option_markers": triaged["option_markers"],
            "images_native_a": len(images_a),
            "images_native_b": len(images_b),
            "rows_a": len(pair["rows_a"]),
            "rows_b": len(pair["rows_b"]),
            "agreement": pair["verdict"]["classification"],
            "agreement_after_repair": verdict_after["classification"],
            "content_similarity_after": round(verdict_after.get("content_similarity", 0.0), 6),
            "structure_similarity_after": round(verdict_after.get("similarity", 0.0), 6),
            "dropped_lines_a": len(dropped_a),
            "dropped_lines_b": len(dropped_b),
            "questions_a": len(questions_a),
            "questions_b": len(questions_b),
            "residual_a": len(residual_a),
            "residual_b": len(residual_b),
            "anchor_style_a": (diag_a or {}).get("style"),
            "segmentation_ok_a": bool((diag_a or {}).get("ok", True)),
            "segmentation_reasons_a": list((diag_a or {}).get("reasons") or []),
            "paper_item_type_a": (diag_a or {}).get("paper_item_type"),
            "paper_parts_a": len((diag_a or {}).get("parts") or []),
            "anchor_coverage_a": ((diag_a or {}).get("detail") or {}).get("coverage"),
            "anchor_gaps_a": ((diag_a or {}).get("detail") or {}).get("gaps"),
            "anchor_duplicates_a": ((diag_a or {}).get("detail") or {}).get("duplicates"),
            "question_count_expected_a": ((diag_a or {}).get("detail") or {}).get("last"),
            "questions_resolved_a": sum(1 for q in questions_a if q.get("stem") and len(q.get("options") or {}) >= 2),
            "question_number_gaps_a": _gaps(questions_a),
            "missing_options_a": sum(1 for q in questions_a if len(q.get("options") or {}) < 4),
            "option_collisions_a": sum(len(q.get("option_collisions") or []) for q in questions_a),
            "content_similarity": round(pair["verdict"].get("content_similarity", 0.0), 6),
            "structure_similarity": round(pair["verdict"].get("similarity", 0.0), 6),
            "sec_triage": round(after_triage - started, 3),
            "sec_dual": round(after_dual - after_triage, 3),
            "cpu_seconds": round(_resource_snapshot()["cpu_seconds"] - cpu_before, 3),
            "peak_rss_mb": _resource_snapshot()["peak_rss_mb"],
        }
    )

    legacy_text = _read_text(record.get("legacy_markdown"))
    if legacy_text:
        label, score = classify_legacy_vs_new(pair["text_a"], legacy_text)
        legacy_audit = cjk.audit_text(legacy_text)
        row.update(
            {
                "legacy_verdict": label,
                "legacy_content_similarity": round(score, 6),
                "legacy_simplified_hits": legacy_audit["simplified_only_hits"],
                "legacy_simplified_types": legacy_audit["simplified_only_types"],
                "legacy_replacement": legacy_audit["replacement_characters"],
                "legacy_pua": legacy_audit["pua_characters"],
                "legacy_anchors": len(legacy_audit["question_anchors"]),
                "legacy_images_on_disk": record.get("legacy_image_count", 0),
            }
        )
        if legacy_audit["simplified_only_hits"] > 0:
            issues.append(
                {
                    "kind": "simplified_contamination",
                    "severity": "high" if legacy_audit["simplified_ratio"] > 0.002 else "medium",
                    "uid": record["uid"],
                    "count": legacy_audit["simplified_only_hits"],
                    "ratio": round(legacy_audit["simplified_ratio"], 5),
                    "samples": legacy_audit["simplified_samples"][:6],
                    "note": "legacy MinerU output contains simplified-only codepoints that the "
                    "native text layer does not; this is an extraction artefact, not official wording",
                }
            )
        if row["legacy_anchors"] and row["anchors"] and row["legacy_anchors"] != row["anchors"]:
            issues.append(
                {
                    "kind": "question_count_divergence",
                    "severity": "high",
                    "uid": record["uid"],
                    "native_anchors": row["anchors"],
                    "legacy_anchors": row["legacy_anchors"],
                    "note": "number of detectable question anchors differs between native text layer and legacy markdown",
                }
            )
        if row["legacy_images_on_disk"] and row["images_native_a"] and row["legacy_images_on_disk"] != row["images_native_a"]:
            issues.append(
                {
                    "kind": "asset_count_divergence",
                    "severity": "medium",
                    "uid": record["uid"],
                    "native_embedded": row["images_native_a"],
                    "legacy_crops": row["legacy_images_on_disk"],
                    "note": "legacy crop count differs from the count of embedded image objects; crop geometry must be re-derived from the native object, not from the legacy folder",
                }
            )
    else:
        row.update({"legacy_verdict": "NO_LEGACY_OUTPUT", "legacy_content_similarity": 0.0})

    if triaged["triage_class"] == "SCANNED_IMAGE":
        issues.append(
            {
                "kind": "needs_ocr",
                "severity": "info",
                "uid": record["uid"],
                "note": "no text layer: this is exactly the case the escalation ladder is for (crop-level OCR, not full-document VLM)",
            }
        )
    if pair["verdict"]["classification"] in ("TEXT_DISAGREEMENT", "MISSING_IN_ONE_PARSER"):
        issues.append(
            {
                "kind": "parser_disagreement",
                "severity": "blocker" if pair["verdict"]["classification"] == "MISSING_IN_ONE_PARSER" else "high",
                "uid": record["uid"],
                "classification": pair["verdict"]["classification"],
                "content_similarity": pair["verdict"].get("content_similarity"),
                "evidence": (pair["verdict"].get("first_difference") or "")[:220],
                "note": "two independent engines disagree on character content: quarantine, do not auto-publish",
            }
        )
    if triaged["simplified_only_hits"] > 0:
        issues.append(
            {
                "kind": "native_simplified_contamination",
                "severity": "high",
                "uid": record["uid"],
                "count": triaged["simplified_only_hits"],
                "note": "the official PDF's own text layer carries simplified-only codepoints: check whether this is official wording or a broken ToUnicode CMap before any normalization",
            }
        )
    if triaged["pua_characters"] or triaged["replacement_characters"]:
        issues.append(
            {
                "kind": "unmapped_glyph",
                "severity": "high",
                "uid": record["uid"],
                "replacement": triaged["replacement_characters"],
                "pua": triaged["pua_characters"],
                "note": "glyphs without a usable ToUnicode mapping: these tokens are not text; escalate the affected region only (Protocol §13 Level 1/2), never re-OCR the whole paper",
            }
        )
    return row


def write_reports(rows, issues, blockers):
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "issues.jsonl"), "w", encoding="utf-8") as handle:
        for item in issues:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    with open(os.path.join(REPORTS, "stage_c_d_results.jsonl"), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with open(os.path.join(REPORTS, "stage_c_d_results.csv"), "w", encoding="utf-8") as handle:
        handle.write(",".join(columns) + "\n")
        for row in rows:
            handle.write(",".join('"' + str(row.get(c, "")).replace('"', "'") + '"' for c in columns) + "\n")

    lines = ["# BLOCKERS", "", "Emitted as required by Protocol §21. Nothing below was silently resolved.", ""]
    if not blockers:
        lines.append("(none)")
    for item in blockers:
        lines.append("## %s - %s" % (item["severity"].upper(), item.get("uid", "-")))
        lines.append("- what: %s" % item["what"])
        lines.append("- evidence: %s" % item["evidence"])
        lines.append("- attempted: %s" % item.get("attempted", "-"))
        lines.append("- options: %s" % "; ".join(item.get("options", [])))
        lines.append("- pipeline can continue elsewhere: %s" % item.get("can_continue_elsewhere"))
        lines.append("")
    with open(os.path.join(REPORTS, "BLOCKERS.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv):
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    # `manifests.load` resolves each row's `raw_copy` against the manifest's own directory, so
    # `record["raw_copy"]` below is a real path whatever directory the pilot was run from.
    records = manifests.load(MANIFEST)
    if limit:
        records = records[:limit]

    rows, issues, blockers = [], [], []
    for index, record in enumerate(records, start=1):
        rows.append(run_record(record, blockers, issues))
        print("  [%2d/%d] %s %s" % (index, len(records), record["category"], record["role"]))

    write_reports(rows, issues, blockers)
    print("wrote reports to", REPORTS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
