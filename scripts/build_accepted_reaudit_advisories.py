#!/usr/bin/env python3
"""Convert an accepted re-audit proposal into Review UI advisory rows.

Only advisory rows are produced.  Human reset and AI-event import remain
separate, approval-gated operations.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs/skills/national-exam-ai-audit/scripts"))
from build_review_ui_advisory_results import (  # type: ignore[import-not-found]
    ABBREVIATED_BINOMIAL_RE,
    CAPSULE_COMPOUND_RE,
    LATIN_BINOMIAL_RE,
    build_candidate_suggestion,
    capsule_compound_context,
    group_scope_issue,
    semantic_anchor_conflict,
    scientific_name_in_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def json_rows(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return list(value.get("candidates") or []) if isinstance(value, dict) else []


def action_for(row: dict[str, Any]) -> str:
    families = {str(value) for value in row.get("ai_issue_families") or []}
    if "visual_dependency" in families:
        return "human_review_pdf"
    if "group_dependency" in families:
        return "review_group"
    if row.get("audit_status") == "block" or "option_structure" in families:
        return "fix_parser"
    return "human_review_text"


def field_text(content: dict[str, Any], location: str) -> str:
    if location == "stem":
        return str(content.get("stem") or "")
    if location.startswith("option_"):
        key = location[-1].upper()
        return next(
            (str(option.get("text") or "") for option in content.get("options") or []
             if isinstance(option, dict) and str(option.get("key") or "").upper() == key),
            "",
        )
    return "\n".join(
        [str(content.get("stem") or "")]
        + [str(option.get("text") or "") for option in content.get("options") or [] if isinstance(option, dict)]
    )


def evidence_by_index(candidate: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(item.get("finding_index")): item
        for item in candidate.get("evidence_matches") or []
        if isinstance(item, dict) and str(item.get("finding_index") or "").isdigit()
    }


def finding_reason(
    finding: dict[str, Any],
    *,
    row_reason: str,
    evidence: dict[str, Any] | None = None,
) -> str:
    """Return an operator-facing explanation, never a bare model verdict."""
    for value in (
        finding.get("message"),
        finding.get("reason"),
        finding.get("note"),
        (evidence or {}).get("note"),
    ):
        if str(value or "").strip():
            return str(value).strip()
    observed = str(finding.get("observed") or "").strip()
    suggested = str(finding.get("suggested") or "").strip()
    if observed and suggested:
        return (
            f"模型指出「{observed}」可能應為「{suggested}」，但目前沒有逐字來源或語意錨點證據；"
            "保留人工核對，不直接視為錯字。"
        )
    if observed:
        return f"模型指出欄位含「{observed}」疑點，但未提供可驗證的替代文字；請核對官方 PDF。"
    return row_reason or "模型指出結構或來源疑點；請核對官方 PDF、圖片或題組上下文。"


def scientific_name_finding(finding: dict[str, Any]) -> bool:
    before = str(finding.get("observed") or "")
    after = str(finding.get("suggested") or "")
    return scientific_name_in_text(f"{before} {after}")


def scientific_name_in_field(content: dict[str, Any], location: str) -> bool:
    return scientific_name_in_text(field_text(content, location))


def apply_semantic_guards(
    findings: list[dict[str, Any]],
    content: dict[str, Any],
    *,
    row_reason: str,
    evidence_index: dict[int, dict[str, Any]],
) -> None:
    """Attach reasons and suppress unsafe semantic/name one-click patches."""
    task = {"content": content}
    group_seen = False
    for index, finding in enumerate(findings, start=1):
        evidence = evidence_index.get(index, {})
        if evidence:
            for key in ("route", "source_class", "rule_id"):
                if not finding.get(key) and evidence.get(key):
                    finding[key] = evidence[key]
        group_scoped = group_scope_issue(
            task,
            {
                "field": finding.get("location"),
                "before": finding.get("observed"),
                "after": finding.get("suggested"),
                "issue_family": finding.get("issue_family"),
                "route": finding.get("route"),
            },
        )
        if group_scoped:
            if group_seen:
                finding["_drop_group_duplicate"] = True
                continue
            group_seen = True
            finding["location"] = "group_ref"
            finding["observed"] = None
            finding["suggested"] = None
            finding["issue_family"] = "group_dependency"
            finding["route"] = "group"
            finding["source_class"] = "source_unverified"
            finding["message"] = group_scoped["note"]
            finding["reason"] = group_scoped["note"]
            finding["correction_applicable"] = False
            finding["correction_omission_reason"] = group_scoped["note"]
            continue
        anchor_reason = semantic_anchor_conflict(
            task,
            {
                "field": finding.get("location"),
                "before": finding.get("observed"),
                "after": finding.get("suggested"),
            },
        )
        if anchor_reason:
            finding["message"] = anchor_reason
            finding["reason"] = anchor_reason
            finding["route"] = "human_pdf"
            finding["source_class"] = "source_unverified"
            finding["correction_applicable"] = False
            finding["correction_omission_reason"] = anchor_reason
            # Keep the local replacement semantically honest in the UI.  The
            # old model proposal often showed 氩 → 氬, which is only a glyph
            # conversion and is contradicted by the English anchor.
            observed = str(finding.get("observed") or "")
            if observed in {"氩", "氬"}:
                finding["suggested"] = "氫"
            elif "鍵" in observed:
                finding["suggested"] = "氫鍵"
            continue

        reason = finding_reason(finding, row_reason=row_reason, evidence=evidence)
        explicit_reason = any(
            str(value or "").strip()
            for value in (
                finding.get("message"),
                finding.get("reason"),
                finding.get("note"),
                evidence.get("note"),
            )
        )
        finding["_reason_fallback"] = not explicit_reason
        finding["message"] = reason
        finding["reason"] = reason
        capsule_reason = capsule_compound_context(
            task,
            {
                "field": finding.get("location"),
                "before": finding.get("observed"),
                "after": finding.get("suggested"),
                "route": finding.get("route"),
                "rule_id": finding.get("rule_id"),
            },
        )
        if capsule_reason:
            finding["message"] = capsule_reason
            finding["reason"] = capsule_reason
            finding["route"] = "human_pdf"
            finding["source_class"] = "source_unverified"
            finding["correction_applicable"] = False
            finding["correction_omission_reason"] = capsule_reason
            continue
        if scientific_name_finding(finding) or (
            scientific_name_in_field(content, str(finding.get("location") or ""))
            and not explicit_reason
        ):
            # A valid-looking binomial can be damaged, but model memory alone
            # cannot establish that it is damaged.  Keep it source/PDF owned
            # until an active exact rule or source mismatch is attached.
            if str(finding.get("route") or "") != "deterministic" or not str(finding.get("rule_id") or "").strip():
                finding["route"] = "human_pdf"
                finding["source_class"] = "source_unverified"
                finding["correction_applicable"] = False
                finding["correction_omission_reason"] = (
                    "拉丁學名可能是合法名稱；目前沒有官方 PDF 或 active exact rule 證據，"
                    "不能僅依模型記憶改字。"
                )


def suggestion_is_safe(row: dict[str, Any], content: dict[str, Any]) -> tuple[bool, str]:
    correction = row.get("suggested_correction")
    if not isinstance(correction, dict):
        return False, "no patch"
    for finding in row.get("findings") or []:
        if not isinstance(finding, dict) or not finding.get("correction_applicable"):
            continue
        route = str(finding.get("route") or "")
        if route == "deterministic" and not str(finding.get("rule_id") or "").strip():
            return False, "deterministic finding 未綁定 active rule_id"
        if route == "propose_rule" and not str(finding.get("message") or finding.get("reason") or "").strip():
            return False, "propose_rule finding 未提供可驗證的具體理由"
        note = str(finding.get("message") or finding.get("reason") or "").strip().lower()
        if route == "propose_rule" and not finding.get("_reason_fallback") and any(
            marker in note
            for marker in (
                "目前沒有逐字來源",
                "未提供可驗證",
                "保留人工核對",
                "source verification required",
            )
        ):
            return False, "finding 明確表示尚無來源證據，不能提供一鍵 patch"
        if scientific_name_finding(finding):
            return False, "拉丁學名需官方 PDF/active rule 證據，不能僅依模型記憶提供一鍵修正"
        capsule_reason = capsule_compound_context(
            {"content": content},
            {
                "field": finding.get("location"),
                "before": finding.get("observed"),
                "after": finding.get("suggested"),
                "route": route,
                "rule_id": finding.get("rule_id"),
            },
        )
        if capsule_reason:
            return False, capsule_reason
        if route == "group" or str(finding.get("issue_family") or "") == "group_dependency":
            return False, "承上題/題組線索由題組層處理，題目審核不提供文字 patch"
        anchor_reason = semantic_anchor_conflict(
            {"content": content},
            {
                "field": finding.get("location"),
                "before": finding.get("observed"),
                "after": finding.get("suggested"),
            },
        )
        if anchor_reason:
            return False, anchor_reason
        observed = str(finding.get("observed") or "")
        suggested = str(finding.get("suggested") or "")
        location = str(finding.get("location") or "")
        if not observed or not suggested or observed == suggested:
            return False, "applicable finding lacks a distinct replacement"
        original = field_text(content, location)
        if original.count(observed) != 1:
            return False, f"observed text is not unique in {location}"
        if location == "stem":
            patched = str(correction.get("stem") or "")
        elif location.startswith("option_"):
            key = location[-1].upper()
            patched = next(
                (str(option.get("text") or "") for option in correction.get("options") or []
                 if isinstance(option, dict) and str(option.get("key") or "").upper() == key),
                "",
            )
        else:
            patched = ""
        if suggested not in patched or observed in patched:
            return False, f"patch does not fully replace {location}"
    return True, ""


def sanitize_correction(correction: Any) -> dict[str, Any] | None:
    """Keep only the Review UI correction contract's patch fields.

    Task option objects may carry parser/display metadata (``image``,
    ``markup``, ``raw_order``).  Those fields are useful in the task snapshot
    but are deliberately not allowed in an AI patch; retaining them makes a
    structurally valid text correction fail the import validator.
    """
    if not isinstance(correction, dict):
        return None
    sanitized: dict[str, Any] = {}
    if isinstance(correction.get("stem"), str):
        sanitized["stem"] = correction["stem"]
    if isinstance(correction.get("options"), list):
        options: list[dict[str, str]] = []
        for option in correction["options"]:
            if not isinstance(option, dict):
                return None
            key = str(option.get("key") or "").strip().upper()
            text = option.get("text")
            if not key or not isinstance(text, str):
                return None
            options.append({"key": key, "text": text})
        if not options:
            return None
        sanitized["options"] = options
    if isinstance(correction.get("group_ref"), str):
        sanitized["group_ref"] = correction["group_ref"]
    if "group_sequence_no" in correction and (
        correction["group_sequence_no"] is None
        or isinstance(correction["group_sequence_no"], int)
        and not isinstance(correction["group_sequence_no"], bool)
    ):
        sanitized["group_sequence_no"] = correction["group_sequence_no"]
    return sanitized or None


def build_row(candidate: dict[str, Any]) -> dict[str, Any]:
    raw = candidate.get("audit_result") if isinstance(candidate.get("audit_result"), dict) else {}
    row = dict(raw)
    row.update(
        {
            "candidate_key": candidate.get("candidate_key"),
            "stage": "question",
            "status": candidate.get("audit_status") or raw.get("status") or "needs_review",
            "confidence": candidate.get("audit_confidence") or raw.get("confidence") or 0,
            "issue_families": candidate.get("ai_issue_families") or raw.get("issue_families") or [],
            "reason": candidate.get("ai_summary") or raw.get("reason") or "accepted-question re-audit found an advisory issue",
            "recommended_action": raw.get("recommended_action") or action_for(candidate),
            "model": raw.get("model") or "gpt-5.6-luna",
            "prompt_version": raw.get("prompt_version") or "national_exam_sparse_audit_v4",
            "advisory_only": True,
        }
    )
    content = candidate.get("task_content") if isinstance(candidate.get("task_content"), dict) else {}
    findings = [dict(item) for item in row.get("findings") or [] if isinstance(item, dict)]
    indexed_evidence = evidence_by_index(candidate)
    for finding in findings:
        if str(finding.get("location") or "") == "image_refs":
            finding["location"] = "stem"
        if not str(finding.get("observed") or "").strip():
            location = str(finding.get("location") or "")
            finding["observed"] = {
                "image_refs": "目前存在圖片或圖表資產",
                "group_ref": "目前存在題組連結線索",
                "group_sequence_no": "目前存在題組序號線索",
                "options": "目前存在選項結構疑點",
            }.get(location, "目前欄位需核對來源")
        if not isinstance(finding.get("correction_applicable"), bool):
            finding["correction_applicable"] = False
        if not finding.get("correction_applicable") and not str(finding.get("correction_omission_reason") or "").strip():
            finding["correction_omission_reason"] = "需要官方 PDF、圖片或結構人工核對，不能安全自動修正。"
    apply_semantic_guards(
        findings,
        content,
        row_reason=str(row.get("reason") or ""),
        evidence_index=indexed_evidence,
    )
    findings = [
        finding for finding in findings if not finding.pop("_drop_group_duplicate", False)
    ]
    if any(str(finding.get("route") or "") == "group" for finding in findings):
        row["issue_families"] = sorted(
            set(str(value) for value in row.get("issue_families") or [])
            | {"group_dependency"}
        )
        row["recommended_action"] = "review_group"
    row["findings"] = findings
    # Replace sparse evidence with a field-scoped, reason-bearing projection.
    # Keeping the old model route alone made the UI look as if a generic glyph
    # conversion were source-verified even after a semantic guard overruled it.
    projected_evidence: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        source = indexed_evidence.get(index, {})
        projected_evidence.append(
            {
                "field": f"content.{finding.get('location')}" if finding.get("location") else "content",
                "before": finding.get("observed"),
                "after": finding.get("suggested"),
                "source_class": finding.get("source_class") or source.get("source_class"),
                "route": finding.get("route") or source.get("route"),
                "rule_id": finding.get("rule_id") or source.get("rule_id"),
                "reason": finding.get("message") or finding.get("reason") or "",
            }
        )
    row["evidence"] = projected_evidence or row.get("evidence") or candidate.get("ai_evidence") or []
    if findings and str(findings[0].get("message") or "").strip():
        row["reason"] = f"{row['reason']} {findings[0]['message']}"
    if any(str(finding.get("route") or "") == "human_pdf" for finding in findings):
        row["recommended_action"] = "human_review_pdf"
    row["uncorrected_findings"] = row.get("uncorrected_findings") or []
    row["suggested_changes"] = row.get("suggested_changes") or []

    # Sparse batch rows contain local before/after pairs but no complete UI
    # patch.  Rebuild one only when the existing UI materializer's safety gate
    # accepts every finding; visual/group/parser findings remain manual.
    if row.get("suggested_correction"):
        safe, reason = suggestion_is_safe(row, content)
        if not safe:
            row.pop("suggested_correction", None)
            row["suggested_changes"] = []
            row["correction_coverage"] = "none"
            for index, finding in enumerate(findings, start=1):
                if finding.get("correction_applicable") and not any(
                    isinstance(item, dict)
                    and int(item.get("finding_index") or -1) == index
                    for item in row["uncorrected_findings"]
                ):
                    row["uncorrected_findings"].append(
                        {"finding_index": index, "reason": reason}
                    )
    if not row.get("suggested_correction"):
        task = {"content": content}
        issues: list[dict[str, Any]] = []
        for index, finding in enumerate(findings, start=1):
            evidence = indexed_evidence.get(index, {})
            issues.append(
                {
                    "field": finding.get("location"),
                    "before": finding.get("observed"),
                    "after": finding.get("suggested"),
                    "issue_family": finding.get("issue_family"),
                    "route": finding.get("route") or evidence.get("route") or "",
                    "source_class": finding.get("source_class") or evidence.get("source_class") or "",
                    "rule_id": finding.get("rule_id") or evidence.get("rule_id"),
                    "confidence": finding.get("confidence") or row["confidence"],
                    "note": finding.get("message") or candidate.get("ai_summary") or "",
                    "reason_source": "fallback" if finding.get("_reason_fallback") else "model",
                }
            )
        correction, changes, coverage, uncorrected = build_candidate_suggestion(task, issues)
        if correction:
            row["suggested_correction"] = correction
            row["suggested_changes"] = changes
            row["correction_coverage"] = coverage
        for item in uncorrected:
            index = item.get("finding_index") if isinstance(item, dict) else None
            if isinstance(index, int) and 1 <= index <= len(findings):
                message = str(findings[index - 1].get("message") or "").strip()
                if message:
                    item["reason"] = message
        row["uncorrected_findings"] = row["uncorrected_findings"] or uncorrected
    if row.get("suggested_correction"):
        sanitized = sanitize_correction(row.get("suggested_correction"))
        if sanitized:
            row["suggested_correction"] = sanitized
        else:
            row.pop("suggested_correction", None)
            row["suggested_changes"] = []
            row["correction_coverage"] = "none"

    if not row.get("suggested_correction"):
        # A sparse finding may contain a plausible before/after pair while
        # still being routed to human PDF/group/parser verification.  In that
        # case it is an advisory suggestion, not an applicable one-click
        # correction; mark it explicitly so the contract does not claim a
        # patch that was intentionally withheld.
        for index, finding in enumerate(findings, start=1):
            if finding.get("correction_applicable"):
                finding["correction_applicable"] = False
                if not str(finding.get("correction_omission_reason") or "").strip():
                    finding["correction_omission_reason"] = (
                        "目前僅有疑點建議，仍需官方 PDF、圖片或結構人工核對，不能安全自動修正。"
                    )
                if not any(
                    isinstance(item, dict)
                    and int(item.get("finding_index") or -1) == index
                    for item in row["uncorrected_findings"]
                ):
                    row["uncorrected_findings"].append(
                        {
                            "finding_index": index,
                            "reason": finding["correction_omission_reason"],
                        }
                    )
    for finding in findings:
        finding.pop("_reason_fallback", None)
    row.setdefault("correction_coverage", "complete" if row.get("suggested_correction") else "none")
    return row


def main() -> int:
    args = parse_args()
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for proposal in args.proposal:
        for candidate in json_rows(proposal):
            key = str(candidate.get("candidate_key") or "")
            if not key or key in seen:
                raise SystemExit(f"duplicate or empty candidate_key in proposals: {key}")
            seen.add(key)
            candidate = dict(candidate)
            # The proposal retains the full result, while the task content is
            # intentionally not duplicated.  Existing complete suggestions
            # are preserved; sparse rows without one remain manual.
            candidates.append(candidate)

    rows = [build_row(candidate) for candidate in candidates]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    report = {
        "ok": bool(rows) and all(row.get("status") in {"needs_review", "block", "pass"} for row in rows),
        "advisory_only": True,
        "database_written": False,
        "review_events_written": False,
        "candidate_count": len(rows),
        "status_counts": dict(Counter(str(row.get("status")) for row in rows)),
        "suggested_correction_count": sum(bool(row.get("suggested_correction")) for row in rows),
        "output": str(args.output.resolve()),
    }
    args.output.with_suffix(".validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
