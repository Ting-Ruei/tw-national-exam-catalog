#!/usr/bin/env python3
"""Summarize three-source evidence against an AI395 staging run."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

QUESTION_PREFIX_RE = re.compile(r"^\s*\d{1,3}(?:\s*[.．、]\s*|\s+)")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def compact_text(value: Any, *, nfkc: bool = False) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if nfkc:
        text = unicodedata.normalize("NFKC", text)
    text = QUESTION_PREFIX_RE.sub("", text, count=1)
    return re.sub(r"\s+", "", text)


def family_texts(packet: dict[str, Any]) -> dict[str, str]:
    engines = ((packet.get("official_pdf_second_source") or {}).get("question_text_by_engine") or {})
    grouped: dict[str, list[str]] = defaultdict(list)
    for engine in engines.values():
        if not isinstance(engine, dict):
            continue
        family = str(engine.get("source_family") or "").strip()
        text = compact_text(engine.get("text"))
        if family and text:
            grouped[family].append(text)
    return {family: max(values, key=len) for family, values in grouped.items()}


def classify_candidate_source(packet: dict[str, Any]) -> dict[str, Any]:
    candidate_stem = compact_text(((packet.get("effective_candidate") or {}).get("stem")))
    candidate_nfkc = compact_text(((packet.get("effective_candidate") or {}).get("stem")), nfkc=True)
    sources = family_texts(packet)
    raw_support = sum(candidate_stem in text for text in sources.values()) if candidate_stem else 0
    nfkc_support = sum(candidate_nfkc in compact_text(text, nfkc=True) for text in sources.values()) if candidate_nfkc else 0
    family_count = len(sources)
    consensus = ((packet.get("official_pdf_second_source") or {}).get("question_consensus") or {}).get("status")
    if family_count < 2:
        status = "insufficient_independent_families"
    elif raw_support == family_count:
        status = "candidate_matches_independent_text"
    elif nfkc_support == family_count:
        status = "compatibility_glyph_difference"
    elif raw_support >= 2:
        status = "candidate_matches_family_majority"
    elif nfkc_support >= 2:
        status = "partial_compatibility_glyph_difference"
    else:
        status = "source_text_difference"
    auto_rule_candidate = (
        family_count >= 2
        and consensus == "usable_question_consensus"
        and status == "compatibility_glyph_difference"
    )
    return {
        "status": status,
        "question_consensus": consensus,
        "family_count": family_count,
        "raw_support": raw_support,
        "nfkc_support": nfkc_support,
        "auto_rule_candidate": auto_rule_candidate,
    }


def model_findings(database: Path | None, run_id: str | None) -> dict[str, list[str]]:
    if not database or not run_id:
        return {}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT candidate_key, finding_code FROM review_findings WHERE run_id=? ORDER BY finding_id",
            (run_id,),
        ).fetchall()
    finally:
        connection.close()
    result: dict[str, list[str]] = defaultdict(list)
    for candidate_key, finding_code in rows:
        if finding_code not in result[candidate_key]:
            result[candidate_key].append(finding_code)
    return dict(result)


def analyze(
    packets: list[dict[str, Any]],
    *,
    scope: dict[str, Any] | None = None,
    findings: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    scope_cases = {str(row.get("candidate_key")): row for row in (scope or {}).get("cases") or []}
    findings = findings or {}
    case_rows: list[dict[str, Any]] = []
    source_classes: Counter[str] = Counter()
    consensus: Counter[str] = Counter()
    page_consensus: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    auto_candidates: list[str] = []
    for packet in packets:
        key = str(packet.get("candidate_key") or "")
        result = classify_candidate_source(packet)
        source_classes[result["status"]] += 1
        consensus[result["question_consensus"]] += 1
        second = packet.get("official_pdf_second_source") or {}
        page_consensus[str(second.get("consensus_status") or "unknown")] += 1
        for flag in second.get("flags") or []:
            flags[str(flag)] += 1
        row_scope = scope_cases.get(key) or {}
        row_tags = [str(value) for value in row_scope.get("scope_tags") or []]
        tags.update(row_tags)
        if result["auto_rule_candidate"]:
            auto_candidates.append(key)
        case_rows.append(
            {
                "candidate_key": key,
                "case_id": packet.get("case_id"),
                "scope_tags": row_tags,
                "source": result,
                "model_finding_codes": findings.get(key, []),
                "pdf_flags": sorted(str(value) for value in second.get("flags") or []),
                "current_asset_count": (packet.get("visual_evidence") or {}).get("current_asset_count", 0),
            }
        )
    return {
        "schema_version": "ai395_three_source_analysis_v1",
        "packet_count": len(packets),
        "scope_tag_counts": dict(sorted(tags.items())),
        "question_consensus_counts": dict(sorted(consensus.items())),
        "page_consensus_counts": dict(sorted(page_consensus.items())),
        "pdf_flag_counts": dict(sorted(flags.items())),
        "source_class_counts": dict(sorted(source_classes.items())),
        "automation_assessment": {
            "auto_rule_candidate_count": len(auto_candidates),
            "auto_rule_candidate_keys": auto_candidates,
            "requires_human_or_more_evidence_count": len(packets) - len(auto_candidates),
            "interpretation": (
                "auto_rule_candidate means at least two independent extractor families agree and "
                "only compatibility glyph normalization separates the candidate; it is a shadow proposal, "
                "not an automatic revision approval."
            ),
        },
        "cases": case_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scope = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec else None
    report = analyze(read_jsonl(args.packets), scope=scope, findings=model_findings(args.database, args.run_id))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "packet_count": report["packet_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
