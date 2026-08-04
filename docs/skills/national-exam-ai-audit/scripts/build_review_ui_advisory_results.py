#!/usr/bin/env python3
"""Expand validated sparse batch results into per-candidate Review UI advisories."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from v4_common import (
    LANE_DISPLAY_NAMES,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)


ACTION_PRIORITY = {
    "parser": (0, "fix_parser_rule"),
    "visual": (1, "human_review_pdf_visual"),
    "group": (2, "review_group"),
    "human_pdf": (3, "human_review_pdf_visual"),
    "human_text": (4, "human_review_text"),
    "propose_rule": (5, "human_review_text"),
    "deterministic": (6, "human_review_text"),
    "none": (7, "human_review_text"),
}

# A suggestion is shown as one-click only when the sparse finding identifies a
# text field and an exact replacement.  The click still writes a human review
# correction and leaves the question needing review; this is not automatic
# materialization or acceptance.
SUGGESTION_MIN_CONFIDENCE = 0.95
SUGGESTION_ROUTES = {"deterministic", "propose_rule", "human_text"}
SUGGESTION_FAMILIES = {
    "ocr_character",
    "notation_markup",
    "semantic_ocr",
    "semantic_disfluency",
}
SUGGESTION_SOURCE_CLASSES = {
    "candidate_mismatch",
    "mineru_ocr_mismatch",
    "parser_error",
    "source_unverified",
}
OPTION_FIELD_RE = re.compile(r"^option_([A-Z])$")
EXPLICIT_MULTI_RE = re.compile(r"(?:[0-9２-９二三四五六七八九十百千兩]+\s*處|多處|數處|各處|全部|兩個|三個|四個)")
LATIN_BINOMIAL_RE = re.compile(r"\b[A-Z][a-z]{3,}\s+[a-z][a-z-]{2,}\b")
# A genus abbreviation such as ``B. cereus`` or ``C. difficile`` is valid
# binomial notation.  It must not be treated as an OCR punctuation error or
# expanded from model memory.  Keep the epithet requirement so ordinary
# option labels (``A. 下列何者``) are not classified as scientific names.
ABBREVIATED_BINOMIAL_RE = re.compile(r"\b[A-Z]\.\s+[a-z][a-z-]{2,}\b")
GROUP_CONTINUATION_RE = re.compile(
    r"^\s*[（(]?\s*(承上題|呈上題|上題|前述)\s*[）)]?[，,、：:]?",
    re.IGNORECASE,
)
CAPSULE_COMPOUND_RE = re.compile(
    r"(?:荧膜|荚膜|莢膜|萸膜).{0,12}(?:組織|漿菌|孢漿菌|胞漿菌|肥漿菌)"
    r"|Histoplasma\s+capsulatum",
    re.IGNORECASE,
)
HYDROGEN_ANCHOR_RE = re.compile(r"\bhydrogen\s+(?:bond|bonds|bonding)\b", re.IGNORECASE)
ARGON_BOND_RE = re.compile(r"氩鍵|氬鍵|氩鍵結|氬鍵結")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--provider", default="codex-subagent")
    parser.add_argument("--model", default="gpt-5.6-luna")
    return parser.parse_args()


def recommended_action(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "no_action"
    candidates = [
        ACTION_PRIORITY.get(str(issue.get("route") or ""), (8, "human_review_text"))
        for issue in issues
    ]
    return min(candidates)[1]


def finding(
    issue: dict[str, Any],
    *,
    correction_applicable: bool | None = None,
    correction_omission_reason: str | None = None,
) -> dict[str, Any]:
    after = issue.get("after")
    evidence = [
        {
            "field": issue.get("field"),
            "before": issue.get("before"),
            "source_class": issue.get("source_class"),
            "route": issue.get("route"),
        }
    ]
    result = {
        "issue_family": issue.get("issue_family"),
        "location": issue.get("field"),
        "observed": issue.get("before"),
        "suggested": after,
        "message": issue.get("note") or "",
        "evidence": evidence,
        "confidence": issue.get("confidence"),
        "route": issue.get("route"),
        **(
            {"rule_id": issue.get("rule_id")}
            if issue.get("rule_id")
            else {}
        ),
    }
    if correction_applicable is None:
        correction_applicable = isinstance(after, str) and bool(after)
    result["correction_applicable"] = bool(correction_applicable)
    if correction_omission_reason is None and not correction_applicable:
        correction_omission_reason = (
            "source verification required" if after is None else "安全完整 patch 未建立"
        )
    result["correction_omission_reason"] = correction_omission_reason
    return result


def _replace_exact_text(
    value: str,
    before: str,
    after: str,
    *,
    allow_repeated: bool,
) -> tuple[str | None, int, str | None]:
    """Apply one local replacement, rejecting ambiguous occurrences."""
    if not before:
        return None, 0, "finding.before 為空"
    if before == after:
        return None, 0, "before 與 after 相同"
    count = value.count(before)
    if count == 0:
        # A preceding longer replacement may already have fixed this exact
        # token.  Treat that as covered only when the target is now present.
        if after and after in value:
            return value, 0, None
        return None, 0, f"目前欄位找不到原文「{before}」"
    if count > 1 and not allow_repeated:
        return None, count, f"原文「{before}」出現 {count} 次，位置不唯一"
    return value.replace(before, after), count, None


def _suggestion_issue_allowed(issue: dict[str, Any]) -> tuple[bool, str]:
    route = str(issue.get("route") or "")
    family = str(issue.get("issue_family") or "")
    source_class = str(issue.get("source_class") or "")
    confidence = float(issue.get("confidence") or 0)
    after = issue.get("after")
    if route not in SUGGESTION_ROUTES:
        return False, f"route={route or 'unknown'} 仍需來源/PDF或結構人工處理"
    # A deterministic patch must point to an active exact rule.  A model may
    # still propose a new rule, but without a concrete explanation it is not
    # safe to turn that proposal into a one-click text patch.
    if route == "deterministic" and not str(issue.get("rule_id") or "").strip():
        return False, "deterministic finding 未綁定 active rule_id"
    if route == "propose_rule" and not str(issue.get("note") or "").strip():
        return False, "propose_rule finding 未提供可驗證的具體理由"
    note = str(issue.get("note") or "").strip().lower()
    if route == "propose_rule" and str(issue.get("reason_source") or "model") != "fallback" and any(
        marker in note
        for marker in (
            "目前沒有逐字來源",
            "未提供可驗證",
            "保留人工核對",
            "source verification required",
        )
    ):
        return False, "finding 明確表示尚無來源證據，不能提供一鍵 patch"
    if family not in SUGGESTION_FAMILIES:
        return False, f"issue_family={family or 'unknown'} 不屬於安全文字校正"
    if source_class not in SUGGESTION_SOURCE_CLASSES:
        return False, f"source_class={source_class or 'unknown'} 不允許直接提供文字 patch"
    if confidence < SUGGESTION_MIN_CONFIDENCE:
        return False, f"confidence={confidence:.2f} 低於 {SUGGESTION_MIN_CONFIDENCE:.2f}"
    if not isinstance(after, str) or not after.strip():
        return False, "AI 沒有提供可核對的 after 文字"
    field = str(issue.get("field") or "")
    if field == "options":
        return False, "options finding 沒有指定單一選項，避免誤套用到錯誤選項"
    if field != "stem" and not OPTION_FIELD_RE.fullmatch(field):
        return False, f"欄位 {field or 'unknown'} 不是可安全合併的文字欄位"
    # Scientific names are not ordinary spelling corrections.  Model memory
    # or a preferred taxonomy spelling is insufficient evidence; keep these
    # as PDF/source advisories until an exact rule has been verified.
    if scientific_name_in_text(str(issue.get("before") or "") + " " + str(after)):
        if route != "deterministic" or not str(issue.get("rule_id") or "").strip():
            return False, "拉丁學名需官方 PDF/active rule 證據，不能僅依模型記憶提供一鍵修正"
    return True, ""


def field_text_from_content(content: dict[str, Any], field: str) -> str:
    """Return the exact candidate field used by semantic guard checks."""
    if field == "stem":
        return str(content.get("stem") or "")
    if field.startswith("option_"):
        key = field[-1].upper()
        return next(
            (
                str(option.get("text") or "")
                for option in content.get("options") or []
                if isinstance(option, dict)
                and str(option.get("key") or "").upper() == key
            ),
            "",
        )
    return ""


def scientific_name_in_text(value: Any) -> bool:
    """Return whether text contains a full or abbreviated binomial name."""
    text = str(value or "")
    return bool(LATIN_BINOMIAL_RE.search(text) or ABBREVIATED_BINOMIAL_RE.search(text))


def capsule_compound_context(task: dict[str, Any], issue: dict[str, Any]) -> str | None:
    """Guard compound organism names containing a capsule term.

    ``莢膜`` by itself is an ordinary morphology term and may be handled by a
    verified exact rule.  In ``莢膜組織胞漿菌（Histoplasma capsulatum）`` the
    same characters are part of a translated organism name.  A short-token
    patch must not be applied to that longer name until the official PDF
    establishes the complete phrase.
    """
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    field = str(issue.get("field") or "")
    text = field_text_from_content(content, field)
    before = str(issue.get("before") or "")
    after = str(issue.get("after") or "")
    combined = " ".join((text, before, after))
    if not CAPSULE_COMPOUND_RE.search(combined):
        return None
    # Do not block an already source-bound exact rule.  The rule still needs
    # to be scoped to the complete task by the active-rule matcher.
    if str(issue.get("route") or "") == "deterministic" and str(issue.get("rule_id") or "").strip():
        return None
    return (
        "欄位中的「莢膜/荚膜」屬於含 Histoplasma capsulatum 的完整菌名或組織漿菌詞組；"
        "不能把單獨「莢膜」字形規則套到長詞。請以官方 PDF 第二來源核對完整中文名稱，"
        "確認後再建立限定範圍的 exact rule。"
    )


def group_scope_issue(task: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any] | None:
    """Move continuation markers out of the question-text audit lane.

    ``承上題``/``呈上題`` are valid source wording and a structural cue. They
    are not spelling candidates.  The output uses the group-layer contract so
    the question UI cannot offer a text patch or ask the reviewer to edit the
    phrase manually.
    """
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    field = str(issue.get("field") or "")
    text = field_text_from_content(content, field)
    before = str(issue.get("before") or "")
    family = str(issue.get("issue_family") or "")
    route = str(issue.get("route") or "")
    marker_in_field = bool(GROUP_CONTINUATION_RE.search(text)) if text else False
    marker_in_finding = bool(re.search(r"承上題|呈上題|上題|前述", before))
    is_group_finding = family == "group_dependency" or route == "group"
    if not (is_group_finding or marker_in_finding):
        return None
    if not marker_in_field and not marker_in_finding and field != "group_ref":
        return None
    marker = next(
        (value for value in ("承上題", "呈上題", "上題", "前述") if value in (text or before)),
        "題組延續線索",
    )
    return {
        **issue,
        "field": "group_ref",
        "before": None,
        "after": None,
        "issue_family": "group_dependency",
        "source_class": "source_unverified",
        "route": "group",
        "rule_id": None,
        "note": (
            f"題幹保留「{marker}」原文；這是題組延續/相鄰題線索，"
            "應到題組頁核對前後題與 group_ref，不在題目審核頁改字。"
        ),
    }


def route_group_scope_issues(
    task: dict[str, Any], issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Normalize group-only findings and deduplicate repeated marker reports."""
    normalized: list[dict[str, Any]] = []
    group_seen = False
    for issue in issues:
        scoped = group_scope_issue(task, issue)
        if scoped is None:
            normalized.append(issue)
            continue
        if group_seen:
            continue
        group_seen = True
        normalized.append(scoped)
    return normalized


def semantic_anchor_conflict(task: dict[str, Any], issue: dict[str, Any]) -> str | None:
    """Explain why a generic glyph conversion conflicts with a bilingual anchor.

    This is deliberately a guard, not a materializer.  The English term is
    strong evidence for the semantic target, but the official PDF still owns
    the final decision.
    """
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    field = str(issue.get("field") or "")
    text = field_text_from_content(content, field)
    observed = str(issue.get("before") or "")
    suggested = str(issue.get("after") or "")
    if not HYDROGEN_ANCHOR_RE.search(text) or not ARGON_BOND_RE.search(text):
        return None
    if not ({observed, suggested} & {"氩", "氬", "氩鍵", "氬鍵", "氩鍵結", "氬鍵結"}):
        return None
    return (
        "欄位同時出現「氩/氬鍵」與英文 hydrogen bond(s)；氩/氬是 argon 字形，"
        "英文語意錨點指向「氫鍵」，不能套用一般簡繁轉換。請核對官方 PDF，"
        "確認後再決定是否將該字改為「氫」。"
    )


def build_candidate_suggestion(
    task: dict[str, Any],
    issues: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str], str, list[dict[str, Any]]]:
    """Build a complete or partial full-field patch from sparse findings.

    Sparse results intentionally contain only local before/after pairs.  The
    Review UI contract requires a full stem or full option array, so this
    function joins each pair against the immutable packet text and refuses
    ambiguous or structural findings.  A partial patch is still advisory and
    remains followed by human review.
    """
    content = task.get("content") or {}
    stem = str(content.get("stem") or "")
    options = [dict(option) for option in content.get("options") or [] if isinstance(option, dict)]
    working_stem = stem
    working_options = {
        str(option.get("key") or "").strip().upper(): str(option.get("text") or "")
        for option in options
        if str(option.get("key") or "").strip()
    }
    changed_fields: set[str] = set()
    changes: list[str] = []
    uncorrected: list[dict[str, Any]] = []

    indexed = list(enumerate(issues, start=1))
    # Longer replacements first avoids a character-level finding consuming a
    # phrase-level finding.  Stable index order keeps the audit deterministic.
    indexed.sort(key=lambda pair: (-len(str(pair[1].get("before") or "")), pair[0]))
    for finding_index, issue in indexed:
        anchor_reason = semantic_anchor_conflict(task, issue)
        if anchor_reason:
            uncorrected.append({"finding_index": finding_index, "reason": anchor_reason})
            continue
        capsule_reason = capsule_compound_context(task, issue)
        if capsule_reason:
            uncorrected.append({"finding_index": finding_index, "reason": capsule_reason})
            continue
        allowed, reason = _suggestion_issue_allowed(issue)
        if not allowed:
            uncorrected.append({"finding_index": finding_index, "reason": reason})
            continue
        field = str(issue.get("field") or "")
        field_value = field_text_from_content(
            task.get("content") if isinstance(task.get("content"), dict) else {},
            field,
        )
        if scientific_name_in_text(field_value):
            route = str(issue.get("route") or "")
            if route != "deterministic" or not str(issue.get("rule_id") or "").strip():
                uncorrected.append(
                    {
                        "finding_index": finding_index,
                        "reason": "欄位含完整或縮寫拉丁學名；需官方 PDF/active exact rule 證據，不能展開或改寫。",
                    }
                )
                continue
        before = str(issue.get("before") or "")
        after = str(issue.get("after") or "")
        note = str(issue.get("note") or "")
        confidence = float(issue.get("confidence") or 0)
        allow_repeated = bool(
            EXPLICIT_MULTI_RE.search(note)
            or (
                str(issue.get("issue_family") or "") == "ocr_character"
                and len(before) == 1
                and len(after) == 1
                and confidence >= 0.99
            )
        )
        if field == "stem":
            updated, count, error = _replace_exact_text(
                working_stem,
                before,
                after,
                allow_repeated=allow_repeated,
            )
            if error:
                uncorrected.append({"finding_index": finding_index, "reason": error})
                continue
            if updated != working_stem:
                working_stem = str(updated)
                changed_fields.add("stem")
                changes.append(f"題幹：{before} → {after}" + (f"（{count} 處）" if count > 1 else ""))
            continue

        option_key = OPTION_FIELD_RE.fullmatch(field).group(1)
        if option_key not in working_options:
            uncorrected.append({"finding_index": finding_index, "reason": f"找不到選項 {option_key}"})
            continue
        updated, count, error = _replace_exact_text(
            working_options[option_key],
            before,
            after,
            allow_repeated=allow_repeated,
        )
        if error:
            uncorrected.append({"finding_index": finding_index, "reason": f"選項 {option_key}：{error}"})
            continue
        if updated != working_options[option_key]:
            working_options[option_key] = str(updated)
            changed_fields.add("options")
            changes.append(f"選項 {option_key}：{before} → {after}" + (f"（{count} 處）" if count > 1 else ""))

    correction: dict[str, Any] = {}
    if "stem" in changed_fields:
        correction["stem"] = working_stem
    if "options" in changed_fields:
        correction["options"] = [
            {
                **option,
                "key": str(option.get("key") or "").strip().upper(),
                "text": working_options.get(
                    str(option.get("key") or "").strip().upper(),
                    str(option.get("text") or ""),
                ),
            }
            for option in options
        ]
    if not correction:
        coverage = "none"
        return None, [], coverage, uncorrected
    coverage = "complete" if not uncorrected else "partial"
    return correction, sorted(set(changes)), coverage, uncorrected


def main() -> int:
    args = parse_args()
    tasks: dict[str, dict[str, Any]] = {}
    task_lanes: dict[str, str] = {}
    issues_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    batch_count = 0

    for run_dir in args.run_dir:
        manifest = read_json(run_dir / "manifest.json")
        validation = read_json(run_dir / "validation.json")
        if not validation.get("ok"):
            raise SystemExit(f"run validation failed: {run_dir}")
        lane = str(manifest.get("lane") or "")
        for batch in manifest.get("batches") or []:
            packet = read_json(run_dir / str(batch["packet_path"]))
            for task in packet.get("tasks") or []:
                key = str(task.get("candidate_key") or "")
                if not key:
                    raise SystemExit(f"empty candidate key: {run_dir}")
                if key in tasks:
                    raise SystemExit(f"candidate appears more than once: {key}")
                tasks[key] = task
                task_lanes[key] = lane
        for _, row in read_jsonl(run_dir / "results.jsonl"):
            batch_count += 1
            for issue in row.get("issues") or []:
                key = str(issue.get("candidate_key") or "")
                if key not in tasks:
                    raise SystemExit(f"result candidate not found in packets: {key}")
                issues_by_key[key].append(issue)

    # A continuation marker belongs to the group layer even when a model
    # returned it from an OCR/semantic lane.  Normalize before counts,
    # suggestions, and UI findings are built so no question-text patch can be
    # generated for ``承上題``/``呈上題``.
    for key, task in tasks.items():
        issues_by_key[key] = route_group_scope_issues(task, issues_by_key.get(key, []))

    rows: list[dict[str, Any]] = []
    route_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    suggestion_coverage_counts: Counter[str] = Counter()
    flagged_count = 0
    suggested_correction_count = 0
    for key, task in tasks.items():
        issues = issues_by_key.get(key, [])
        if issues:
            flagged_count += 1
        route_counts.update(str(issue.get("route") or "") for issue in issues)
        family_counts.update(str(issue.get("issue_family") or "") for issue in issues)
        families = sorted(
            {
                str(issue.get("issue_family") or "")
                for issue in issues
                if str(issue.get("issue_family") or "")
            }
        )
        confidence = (
            min(float(issue.get("confidence") or 0) for issue in issues)
            if issues
            else 1.0
        )
        suggestion, suggested_changes, correction_coverage, uncorrected_findings = (
            build_candidate_suggestion(task, issues)
        )
        suggestion_coverage_counts[correction_coverage] += 1
        if suggestion:
            suggested_correction_count += 1
        uncorrected_indices = {
            int(entry.get("finding_index"))
            for entry in uncorrected_findings
            if str(entry.get("finding_index") or "").isdigit()
        }
        advisory_findings = []
        for finding_index, issue in enumerate(issues, start=1):
            allowed, allowed_reason = _suggestion_issue_allowed(issue)
            omission_reason = next(
                (
                    str(entry.get("reason") or "")
                    for entry in uncorrected_findings
                    if int(entry.get("finding_index") or -1) == finding_index
                ),
                allowed_reason if not allowed else None,
            )
            advisory_findings.append(
                finding(
                    issue,
                    correction_applicable=allowed and finding_index not in uncorrected_indices,
                    correction_omission_reason=omission_reason,
                )
            )
        row = {
            "candidate_key": key,
            "stage": "question",
            "status": "needs_review" if issues else "pass",
            "confidence": confidence,
            "issue_families": families,
            "reason": (
                f"LUNA v4 發現 {len(issues)} 個需人工處理的 advisory issue。"
                if issues
                else "LUNA v4 稀疏稽核未發現實質問題。"
            ),
            "recommended_action": recommended_action(issues),
            "findings": advisory_findings,
            "evidence": [],
            "provider": args.provider,
            "model": args.model,
            "prompt_version": "national_exam_sparse_audit_v4",
            "audit_lane": LANE_DISPLAY_NAMES.get(
                task_lanes[key],
                task_lanes[key],
            ),
            "source_fingerprint": task.get("source_fingerprint"),
            "advisory_only": True,
            "correction_coverage": correction_coverage,
            "uncorrected_findings": uncorrected_findings,
        }
        if suggestion:
            row["suggested_correction"] = suggestion
            row["suggested_changes"] = suggested_changes
        rows.append(row)

    rows.sort(
        key=lambda row: (
            row["status"] == "pass",
            row["audit_lane"],
            row["candidate_key"],
        )
    )
    action_counts = Counter(
        f"{row['status']}|{row['recommended_action']}"
        for row in rows
    )
    pass_action_inconsistent = sum(
        row["status"] == "pass" and row["recommended_action"] != "no_action"
        for row in rows
    )
    needs_review_no_action = sum(
        row["status"] == "needs_review"
        and row["recommended_action"] == "no_action"
        for row in rows
    )
    export_ok = not pass_action_inconsistent and not needs_review_no_action
    write_jsonl(args.output, rows)
    report = {
        "ok": export_ok,
        "schema_version": "national_exam_review_ui_advisory_export_v1",
        "advisory_only": True,
        "database_written": False,
        "review_events_written": False,
        "candidate_count": len(rows),
        "flagged_candidate_count": flagged_count,
        "pass_candidate_count": len(rows) - flagged_count,
        "issue_count": sum(len(value) for value in issues_by_key.values()),
        "batch_count": batch_count,
        "suggested_correction_count": suggested_correction_count,
        "suggestion_coverage_counts": dict(sorted(suggestion_coverage_counts.items())),
        "route_counts": dict(sorted(route_counts.items())),
        "issue_family_counts": dict(sorted(family_counts.items())),
        "status_action_counts": dict(sorted(action_counts.items())),
        "pass_action_inconsistent_count": pass_action_inconsistent,
        "needs_review_no_action_count": needs_review_no_action,
        "output": str(args.output.resolve()),
    }
    report_path = args.report or args.output.with_suffix(".validation.json")
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if export_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
