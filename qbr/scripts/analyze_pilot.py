"""Aggregate the pilot run into the comparison tables the protocol asks for (§17, §23).

Reads reports/stage_c_d_results.jsonl plus the legacy run registry, and writes
reports/pilot_summary.md. Threshold recommendations are derived from the observed
distributions rather than asserted.
"""

import glob
import csv
import json
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(PKG_ROOT, "src"))

REPORTS = os.path.join(PKG_ROOT, "reports")
RESULTS = os.path.join(REPORTS, "stage_c_d_results.jsonl")
ISSUES = os.path.join(REPORTS, "issues.jsonl")

ASSET_ROOT = os.path.abspath(
    os.path.join(PKG_ROOT, "..", "..", "tw-national-exam-catalog", "國考題資料夾")
)
REGISTRY = os.path.join(ASSET_ROOT, "Registry", "mineru_runs")


def _rows():
    out = []
    with open(RESULTS, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                out.append(json.loads(line))
    return out


def _issues():
    out = []
    if not os.path.isfile(ISSUES):
        return out
    with open(ISSUES, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                out.append(json.loads(line))
    return out


def _percentiles(values):
    if not values:
        return {}
    ordered = sorted(values)

    def at(fraction):
        position = (len(ordered) - 1) * fraction
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "p05": round(at(0.05), 4),
        "p50": round(at(0.5), 4),
        "p90": round(at(0.9), 4),
        "p95": round(at(0.95), 4),
        "max": round(ordered[-1], 4),
        "mean": round(statistics.fmean(ordered), 4),
    }


def _count(field, rows, present_only=True):
    values = [row[field] for row in rows if field in row and row[field] not in (None, "")]
    return values


def _legacy_cost_model():
    """Wall-clock cost of the legacy MinerU path, straight from its own run registry."""
    durations = []
    statuses = {}
    for path in glob.glob(os.path.join(REGISTRY, "*", "mineru_results__*.csv")):
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                status = row.get("status") or ""
                statuses[status] = statuses.get(status, 0) + 1
                raw = row.get("elapsed_seconds") or ""
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if status == "ok":
                    durations.append(value)
    return {
        "rows": sum(statuses.values()),
        "statuses": statuses,
        "ok_seconds": _percentiles(durations),
        "total_hours": round(sum(durations) / 3600.0, 1) if durations else 0.0,
    }


def _cell(value):
    """One table cell. A verdict may itself contain a vertical bar - `UNICODE_SYMBOL_ERROR|
    EXTRA_TEXT|QUESTION_SPLIT_ERROR` is one such compound verdict - and an unescaped one
    breaks the row it stands in. Measured: §5 and §5b of `reports/pilot_summary.md` rendered
    as ragged tables because of exactly that."""
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers, rows):
    width = len(headers)
    out = ["| " + " | ".join(_cell(h) for h in headers) + " |", "|" + "|".join(["---"] * width) + "|"]
    for row in rows:
        cells = [_cell(value) for value in row]
        if len(cells) < width:
            cells += [""] * (width - len(cells))
        elif len(cells) > width:
            # A row wider than its header is a defect of the caller, not of the reader: say
            # so in the table instead of letting the surplus values silently re-align.
            cells[width - 1] = "%s (+%d more)" % (cells[width - 1], len(cells) - width)
            cells = cells[:width]
        out.append("| " + " | ".join(cells) + " |")
    return out


def main():
    rows = _rows()
    issues = _issues()
    papers = [row for row in rows if row.get("role") == "question"]

    triage = {}
    for row in rows:
        key = row.get("triage_class", "?")
        triage[key] = triage.get(key, 0) + 1
    agreement = {}
    for row in rows:
        key = row.get("agreement", "?")
        agreement[key] = agreement.get(key, 0) + 1
    legacy = {}
    for row in rows:
        key = row.get("legacy_verdict", "?")
        legacy[key] = legacy.get(key, 0) + 1

    kinds = {}
    for item in issues:
        key = "%s/%s" % (item.get("kind"), item.get("severity"))
        kinds[key] = kinds.get(key, 0) + 1

    lines = []

    def add(payload):
        if isinstance(payload, list):
            lines.extend(payload)
        else:
            lines.append(payload)

    add("# Stage 1 pilot - measured results")
    add("")
    add("Same input, both sides. Inputs are the copies under `data/raw` (digest-pinned in")
    add("`data/sample_manifest.jsonl`); the legacy side is the existing MinerU markdown for the")
    add("identical PDFs. No number in this file is estimated: everything below was produced by")
    add("`scripts/run_pilot.py` over the files named.")
    add("")
    add("## 1. Sample")
    add("")
    add(_table(
        ["metric", "value"],
        [
            ["papers processed", len(rows)],
            ["question papers", len(papers)],
            ["answer papers", sum(1 for r in rows if r.get("role") == "answer")],
            ["corrected-answer papers", sum(1 for r in rows if r.get("role") == "corrected_answer")],
            ["categories", len({r.get("category") for r in rows})],
            ["years", sorted({r.get("year") for r in rows})],
            ["with legacy MinerU markdown", sum(1 for r in rows if r.get("legacy_verdict") not in ("NO_LEGACY_OUTPUT", None))],
        ],
    ))
    add("")
    add("## 2. Stage C triage: how much OCR budget is avoidable")
    add("")
    add(_table(["triage class", "papers"], sorted(triage.items(), key=lambda kv: -kv[1])))
    add("")
    avoidable = triage.get("NATIVE_TEXT_GOOD", 0) + triage.get("NATIVE_TEXT_LAYOUT_RISK", 0) + triage.get("LEGACY_ENCODING", 0)
    need_ocr = triage.get("SCANNED_IMAGE", 0) + triage.get("MIXED", 0) + triage.get("UNKNOWN", 0)
    add("- no-text-layer cases needing real OCR: **%d / %d**" % (need_ocr, len(rows)))
    add("- usable text layer present (OCR avoidable for the text task): **%d / %d**" % (avoidable, len(rows)))
    add("")
    add("## 3. Stage D dual-parser agreement (independent engines)")
    add("")
    add(_table(["classification", "papers"], sorted(agreement.items(), key=lambda kv: -kv[1])))
    add("")
    add("Observed distributions (used to set the thresholds, not guessed):")
    add("")
    add(_table(
        ["signal", "n", "min", "p05", "p50", "p90", "p95", "max", "mean"],
        [
            ["content_similarity"] + [value for key, value in _percentiles(_count("content_similarity", rows)).items()],
            ["structure_similarity"] + [value for key, value in _percentiles(_count("structure_similarity", rows)).items()],
        ],
    ))
    add("")
    add("## 4. Glyph integrity of the native text layer (the 簡體 complaint)")
    add("")
    native_simplified = _count("native_simplified_hits", rows)
    legacy_simplified = _count("legacy_simplified_hits", rows)
    add(_table(
        ["source", "papers with hits", "total hits", "max/paper", "mean/paper"],
        [
            [
                "native PDF text layer",
                sum(1 for v in native_simplified if v),
                sum(native_simplified),
                max(native_simplified) if native_simplified else 0,
                round(statistics.fmean(native_simplified), 3) if native_simplified else 0,
            ],
            [
                "legacy MinerU markdown",
                sum(1 for v in legacy_simplified if v),
                sum(legacy_simplified),
                max(legacy_simplified) if legacy_simplified else 0,
                round(statistics.fmean(legacy_simplified), 3) if legacy_simplified else 0,
            ],
        ],
    ))
    add("")
    damaged = _count("damaged", rows)
    add("- papers whose native layer contains U+FFFD/PUA/control characters: %d (total %d characters)"
        % (sum(1 for v in damaged if v), sum(damaged)))
    add("")
    add("## 5. Legacy vs new, same document (§15.3 taxonomy)")
    add("")
    add(_table(["verdict", "papers"], sorted(legacy.items(), key=lambda kv: -kv[1])))
    add("")
    add("## 5b. Level-0 deterministic repair impact (same inputs)")
    add("")
    before = {}
    after = {}
    for row in rows:
        before[row.get("agreement", "?")] = before.get(row.get("agreement", "?"), 0) + 1
        after[row.get("agreement_after_repair", "?")] = after.get(row.get("agreement_after_repair", "?"), 0) + 1
    keys = sorted(set(before) | set(after))
    add(_table(["classification", "before repair", "after level-0 repair"],
               [[key, before.get(key, 0), after.get(key, 0)] for key in keys]))
    add("")
    rescued = sum(
        1
        for row in rows
        if row.get("agreement") == "TEXT_DISAGREEMENT"
        and row.get("agreement_after_repair") in ("EXACT_AGREEMENT", "SAFE_NORMALIZED_AGREEMENT", "STRUCTURAL_DISAGREEMENT")
    )
    still = sum(1 for row in rows if row.get("agreement_after_repair") in ("TEXT_DISAGREEMENT", "MISSING_IN_ONE_PARSER"))
    migrated = sum(1 for row in rows
                   if row.get("agreement") == "STRUCTURAL_DISAGREEMENT"
                   and row.get("agreement_after_repair") == "TEXT_DISAGREEMENT")
    add("")
    add("- papers that moved STRUCTURAL → TEXT under the repair: **%d**. This is the expected "
        % migrated)
    add("  effect of masking chrome: once the layout noise is out of the way, the two engines are")
    add("  left disagreeing about *characters*, which is the kind of dispute that has to be read,")
    add("  not averaged. A fall in the STRUCTURAL column is therefore not a fall in agreement.")
    add("")
    add("- apparent content disputes cleared by declared rules only: **%d**" % rescued)
    add("- still requiring escalation (quarantine or targeted crop work): **%d**" % still)
    add("")
    add("Question structure recovered by the deterministic segmenter (engine A view):")
    add("")
    add(_table(
        ["signal", "n", "min", "p05", "p50", "p90", "p95", "max", "mean"],
        [
            [name] + [value for _, value in _percentiles(_count(name, rows)).items()]
            for name in ("questions_a", "questions_b", "residual_a", "missing_options_a", "option_collisions_a", "question_number_gaps_a", "anchors")
        ],
    ))
    add("")
    add("## 5c. Segmentation gate: read the paper before you read the questions")
    add("")
    gated = [row for row in papers if "segmentation_ok_a" in row]
    refused = [row for row in gated if not row.get("segmentation_ok_a")]
    gate_counts = {"accepted": len(gated) - len(refused), "refused": len(refused)}
    add(_table(["gate", "papers"], sorted(gate_counts.items(), key=lambda kv: -kv[1])))
    add("")
    add("Item types read out of the declared parts (甲、申論 / 乙、測驗 in one file are two papers):")
    add("")
    types = {}
    for row in gated:
        key = row.get("paper_item_type_a") or "?"
        types[key] = types.get(key, 0) + 1
    add(_table(["paper item type", "papers"], sorted(types.items(), key=lambda kv: -kv[1])))
    add("")
    reasons = {}
    for row in refused:
        for why in row.get("segmentation_reasons_a") or []:
            key = why.split(":")[-1].split("-")[0]
            reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        add("Why the gate refused them (a refusal is a finding, not a repair):")
        add("")
        add(_table(["reason", "papers"], sorted(reasons.items(), key=lambda kv: -kv[1])))
        add("")
        add("## 6. Cost: legacy MinerU path vs deterministic path")
    add("")
    cost = _legacy_cost_model()
    dual = _count("sec_dual", rows)
    triage_seconds = _count("sec_triage", rows)
    add(_table(
        ["quantity", "legacy MinerU (own registry)", "new deterministic path (measured here)"],
        [
            ["registry rows", cost["rows"], len(rows)],
            ["status mix", json.dumps(cost["statuses"], ensure_ascii=False), "-"],
            [
                "wall seconds per paper (mean)",
                cost["ok_seconds"].get("mean"),
                round(statistics.fmean([a + b for a, b in zip(dual, triage_seconds)]), 3) if dual else None,
            ],
            [
                "wall seconds per paper (p50)",
                cost["ok_seconds"].get("p50"),
                round(statistics.median([a + b for a, b in zip(dual, triage_seconds)]), 3) if dual else None,
            ],
            [
                "wall seconds per paper (p95)",
                cost["ok_seconds"].get("p95"),
                round(statistics.quantiles([a + b for a, b in zip(dual, triage_seconds)], n=20)[18], 3)
                if len(dual) > 20 else None,
            ],
            ["total wall hours spent by legacy ok-runs", cost["total_hours"], "-"],
            ["GPU/VLM calls per paper", "1 full-document VLM pass (not instrumented in the registry)", 0],
            ["peak RSS (MB) of this process", "-", max(_count("peak_rss_mb", rows)) if _count("peak_rss_mb", rows) else None],
        ],
    ))
    add("")
    add("## 7. Issues raised (issues.jsonl)")
    add("")
    add(_table(["kind/severity", "count"], sorted(kinds.items(), key=lambda kv: -kv[1])))
    add("")
    add("## 8. What these measurements say about the thresholds (and what they may not say)")
    add("")
    content = _percentiles(_count("content_similarity", rows))
    structure = _percentiles(_count("structure_similarity", rows))
    add("- content agreement, observed p05: `%s`. **An observation, not a gate.** The gate is a"
        % content.get("p05"))
    add("  declared value that comes out of the gold set (`PROPOSED_WORKFLOW.md` §6 and §7.1);")
    add("  setting it where the sample happens to pass is the silent error the protocol forbids.")
    below_p50 = sum(1 for value in _count("structure_similarity", rows) if value < (structure.get("p50") or 0))
    add("- structure agreement, observed p50: `%s`; %d of %d papers sit below it and are the"
        % (structure.get("p50"), below_p50, len(rows)))
    add("  deterministic-repair candidates. Same caveat: it locates the work, it does not excuse a")
    add("  threshold.")
    add("- simplified-glyph alarm: any hit in the *native* layer is investigated (it means a"
        " broken ToUnicode CMap, not a wording issue); hits in the *legacy* side are treated as"
        " extraction artefacts and are the primary argument for retiring the character maps")
    add("")
    path = os.path.join(REPORTS, "pilot_summary.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
