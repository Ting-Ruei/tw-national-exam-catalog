#!/usr/bin/env python3
"""Build an approval-gated reset proposal from an accepted-question re-audit.

This is deliberately a join/verification step, not a model runner.  It only
marks an accepted question reset-eligible when the advisory result is
non-pass, has concrete findings/evidence, and the observed text or structural
signal is still present in the immutable SQL-first task snapshot.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, action="append", required=True)
    parser.add_argument("--result", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--prompt-version", default="national_exam_sparse_audit_v4")
    parser.add_argument("--min-confidence", type=float, default=0.80)
    return parser.parse_args()


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    paths = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
    for file_path in paths:
        with file_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{file_path}:{line_number} is not a JSON object")
                yield value


def flatten_result(value: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if value.get("candidate_key"):
        row = value.get("result")
        if isinstance(row, dict):
            yield {**row, "candidate_key": value["candidate_key"]}
        else:
            yield value
        return
    # v4 sparse results are batch envelopes: they intentionally emit only
    # issues, not one pass object per candidate.  Rehydrate flagged candidates
    # here; the coverage validator remains the authority for checked_count.
    issues = value.get("issues")
    if isinstance(issues, list):
        grouped: dict[str, dict[str, Any]] = {}
        for issue in issues:
            if not isinstance(issue, dict) or not issue.get("candidate_key"):
                continue
            key = str(issue["candidate_key"])
            row = grouped.setdefault(
                key,
                {
                    "candidate_key": key,
                    "status": "needs_review",
                    "confidence": 1.0,
                    "issue_families": [],
                    "findings": [],
                    "evidence": [],
                    "recommended_action": "human_review_text",
                    "model": value.get("model"),
                    "prompt_version": value.get("prompt_version"),
                },
            )
            family = issue.get("issue_family")
            if family and family not in row["issue_families"]:
                row["issue_families"].append(family)
            field = str(issue.get("field") or "")
            row["findings"].append(
                {
                    "issue_family": family,
                    "location": field,
                    "observed": issue.get("before"),
                    "suggested": issue.get("after"),
                    "confidence": issue.get("confidence"),
                    "message": issue.get("note") or "",
                    "reason": issue.get("note") or "",
                    "route": issue.get("route"),
                    "source_class": issue.get("source_class"),
                    "rule_id": issue.get("rule_id"),
                    "correction_applicable": bool(issue.get("after")),
                    "correction_omission_reason": None if issue.get("after") else "v4 sparse issue did not provide a replacement",
                }
            )
            row["evidence"].append(
                {
                    "field": f"content.{field}" if field else "content",
                    "before": issue.get("before"),
                    "after": issue.get("after"),
                    "source_class": issue.get("source_class"),
                    "route": issue.get("route"),
                    "rule_id": issue.get("rule_id"),
                    "reason": issue.get("note") or "",
                }
            )
            row["confidence"] = min(float(row["confidence"]), float(issue.get("confidence") or 0))
        for row in grouped.values():
            row["reason"] = f"v4 sparse 稽核發現 {len(row['findings'])} 個需人工處理的疑點。"
            yield row
        return
    for key in ("results", "rows", "items", "advisories", "candidates"):
        nested = value.get(key)
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    yield from flatten_result(item)


def normal(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def task_visible(task: dict[str, Any], location: str | None = None) -> str:
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    if location == "stem":
        return str(content.get("stem") or "")
    if location and re.fullmatch(r"option_[A-Z]", location):
        key = location[-1]
        for option in content.get("options") or []:
            if isinstance(option, dict) and str(option.get("key") or "").upper() == key:
                return str(option.get("text") or "")
        return ""
    values = [str(content.get("stem") or "")]
    values.extend(str(option.get("text") or "") for option in content.get("options") or [] if isinstance(option, dict))
    values.append(str(content.get("group_ref") or ""))
    values.append(str(content.get("group_sequence_no") or ""))
    return "\n".join(values)


def finding_evidence(task: dict[str, Any], row: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return matched and unmatched concrete evidence for one result row."""
    matched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    findings = row.get("findings") if isinstance(row.get("findings"), list) else []
    row_evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            continue
        observed = normal(finding.get("observed"))
        location = str(finding.get("location") or "")
        haystack = normal(task_visible(task, location or None))
        hint = next(
            (
                item
                for item in row_evidence
                if isinstance(item, dict)
                and (
                    str(item.get("field") or "").endswith(location)
                    or str(item.get("field") or "") == location
                )
            ),
            {},
        )
        evidence = {
            "finding_index": index,
            "issue_family": finding.get("issue_family"),
            "location": location,
            "observed": finding.get("observed"),
            "suggested": finding.get("suggested"),
            "confidence": finding.get("confidence", row.get("confidence")),
            "route": finding.get("route") or hint.get("route"),
            "source_class": finding.get("source_class") or hint.get("source_class"),
            "rule_id": finding.get("rule_id") or hint.get("rule_id"),
            "reason": (
                finding.get("message")
                or finding.get("reason")
                or hint.get("reason")
                or ""
            ),
        }
        content = task.get("content") if isinstance(task.get("content"), dict) else {}
        structural_present = (
            (location == "image_refs" and bool(content.get("image_refs")))
            or (location == "group_ref" and bool(content.get("group_ref")))
            or (location == "group_sequence_no" and content.get("group_sequence_no") is not None)
            or (
                str(finding.get("route") or "") == "parser"
                and bool((task.get("signals") or {}).get("parser_issues"))
            )
        )
        if observed and observed in haystack:
            matched.append(evidence)
        elif structural_present:
            matched.append({**evidence, "structural_present": True})
        elif not observed:
            # Structural findings may be evidenced by the result's explicit
            # field/value pair rather than a literal text substring.
            missing.append({**evidence, "reason": "finding has no observed text"})
        else:
            missing.append({**evidence, "reason": "observed text is absent from current candidate"})

    for evidence in row.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        field = str(evidence.get("field") or "")
        value = evidence.get("value", evidence.get("before"))
        if field == "signals.exam_header_false_question" and bool(value):
            matched.append({"field": field, "value": value})
        elif field == "content.option_count":
            option_count = (task.get("content") or {}).get("option_count")
            if str(option_count) == str(value):
                matched.append({"field": field, "value": value})
    return matched, missing


def main() -> int:
    args = parse_args()
    tasks: dict[str, dict[str, Any]] = {}
    for task_path in args.tasks:
        for task in read_jsonl(task_path):
            key = str(task.get("candidate_key") or "")
            if not key:
                raise SystemExit(f"task missing candidate_key: {task_path}")
            if key in tasks:
                raise SystemExit(f"duplicate task candidate_key: {key}")
            tasks[key] = task

    results: dict[str, dict[str, Any]] = {}
    checked_count = 0
    for result_path in args.result:
        for envelope in read_jsonl(result_path):
            if isinstance(envelope.get("issues"), list) and isinstance(envelope.get("checked_count"), int):
                checked_count += int(envelope["checked_count"])
            for row in flatten_result(envelope):
                key = str(row.get("candidate_key") or "")
                if not key:
                    continue
                if key in results:
                    raise SystemExit(f"duplicate result candidate_key: {key}")
                results[key] = row

    candidates: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for key, row in results.items():
        task = tasks.get(key)
        if task is None:
            raise SystemExit(f"result candidate not found in tasks: {key}")
        status = str(row.get("status") or "pass")
        status_counts[status] += 1
        action = str((task.get("human_state") or {}).get("action") or "unreviewed")
        if status == "pass" or action not in {"accept", "unblock"}:
            continue
        confidence = float(row.get("confidence") or 0)
        matched, missing = finding_evidence(task, row)
        # A non-pass row without current, concrete evidence is retained in the
        # report for coverage accounting but is not eligible for reset.
        eligible = bool(matched) and confidence >= args.min_confidence
        reason = "evidence_present" if eligible else "evidence_missing_or_low_confidence"
        reason_counts[reason] += 1
        candidates.append(
            {
                "candidate_key": key,
                "effective_content_hash": task.get("effective_content_hash"),
                "exam": task.get("exam") or {},
                "current_human_action": action,
                "current_human_notes": (task.get("human_state") or {}).get("notes") or "",
                "requires_reset": eligible,
                "evidence_verified": eligible,
                "evidence_matches": matched,
                "evidence_missing": missing,
                "ai_issue_families": row.get("issue_families") or [],
                "ai_summary": row.get("reason") or "",
                "ai_evidence": (row.get("evidence") or [])[:12],
                "model_agreement": row.get("ensemble") or row.get("model_agreement") or {},
                "suggested_correction": row.get("suggested_correction"),
                "audit_status": status,
                "audit_confidence": confidence,
                "audit_result": row,
                "task_content": task.get("content") or {},
            }
        )

    covered_count = checked_count or len(results)
    eligible_count = sum(bool(row.get("requires_reset")) for row in candidates)
    proposal = {
        "schema_version": "national_exam_accepted_reaudit_reset_proposal_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "advisory_only": True,
        "requires_explicit_human_approval_before_reset": True,
        "model": args.model,
        "prompt_version": args.prompt_version,
        "scope": {
            "task_count": len(tasks),
            "result_count": len(results),
            "checked_count": covered_count,
            "coverage_complete": covered_count == len(tasks),
            "missing_result_count": 0 if covered_count == len(tasks) else len(set(tasks) - set(results)),
            "accepted_nonpass_count": len(candidates),
            "reset_eligible_count": eligible_count,
            "status_counts": dict(status_counts),
            "eligibility_reasons": dict(reason_counts),
            "categories": sorted({str((row.get("exam") or {}).get("category") or "") for row in candidates}),
        },
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(args.output), **proposal["scope"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
