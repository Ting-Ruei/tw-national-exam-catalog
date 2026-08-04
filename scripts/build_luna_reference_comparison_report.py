#!/usr/bin/env python3
"""Join historical GPT-5.6 LUNA audits to the current local-audit report.

LUNA reviewed an older candidate snapshot.  This script preserves that fact:
it exposes the historical finding and correction, checks whether quoted
before/after text is present in the current effective question, and never
treats disagreement with a newer model run as an accuracy comparison.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


LOCAL_MODELS = (
    "gemma4:31b-mlx",
    "qwen3.6:35b-mlx",
    "qwen3.6:27b-mlx",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def current_text(content: dict[str, Any]) -> str:
    parts = [str(content.get("stem") or "")]
    parts.extend(str(option.get("text") or "") for option in content.get("options") or [])
    return "\n".join(parts)


def evidence_transition(
    content: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    current = normalized_text(current_text(content))
    pairs: list[dict[str, Any]] = []
    for finding in audit.get("findings") or []:
        for evidence in finding.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            before = evidence.get("before")
            after = evidence.get("after")
            if before is None or after is None:
                continue
            before_text = normalized_text(before)
            after_text = normalized_text(after)
            pairs.append(
                {
                    "field": evidence.get("field"),
                    "before": before,
                    "after": after,
                    "before_present_now": bool(before_text and before_text in current),
                    "after_present_now": bool(after_text and after_text in current),
                }
            )
    if not pairs:
        status = "not_comparable"
    elif any(pair["before_present_now"] for pair in pairs):
        status = "historical_text_still_present"
    elif all(pair["after_present_now"] for pair in pairs):
        status = "historical_text_replaced_as_suggested"
    else:
        status = "historical_text_changed_differently_or_not_located"
    return {"status": status, "pairs": pairs}


def correction_reflection(
    content: dict[str, Any],
    correction: Any,
) -> dict[str, Any]:
    if not isinstance(correction, dict):
        return {"status": "no_correction", "checks": []}
    checks: list[dict[str, Any]] = []
    if correction.get("stem") is not None:
        checks.append(
            {
                "field": "stem",
                "matches_current": normalized_text(correction["stem"])
                == normalized_text(content.get("stem")),
            }
        )
    current_options = {
        str(option.get("key") or ""): normalized_text(option.get("text"))
        for option in content.get("options") or []
    }
    suggested_options = correction.get("options")
    if isinstance(suggested_options, list):
        if all(isinstance(option, dict) for option in suggested_options):
            for option in suggested_options:
                key = str(option.get("key") or "")
                if not key or option.get("text") is None:
                    continue
                checks.append(
                    {
                        "field": f"option_{key}",
                        "matches_current": current_options.get(key)
                        == normalized_text(option.get("text")),
                    }
                )
        elif all(isinstance(option, str) for option in suggested_options):
            current_rows = content.get("options") or []
            for index, text in enumerate(suggested_options):
                if index >= len(current_rows):
                    break
                key = str(current_rows[index].get("key") or index)
                checks.append(
                    {
                        "field": f"option_{key}",
                        "matches_current": normalized_text(text)
                        == normalized_text(current_rows[index].get("text")),
                    }
                )
    if not checks:
        status = "unable_to_compare"
    elif all(check["matches_current"] for check in checks):
        status = "matches_current_exactly"
    elif any(check["matches_current"] for check in checks):
        status = "partially_matches_current"
    else:
        status = "does_not_match_current_exactly"
    return {"status": status, "checks": checks}


def luna_reference(
    event: dict[str, Any],
    content: dict[str, Any],
    latest_candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    audit = event.get("audit_json") or (event.get("event_json") or {}).get("audit") or {}
    latest_ai = (latest_candidate or {}).get("ai_review") or {}
    return {
        "audited": True,
        "historical": True,
        "advisory_only": True,
        "event_id": event.get("id"),
        "model_run_id": event.get("model_run_id"),
        "provider": event.get("provider"),
        "model": event.get("model_name"),
        "prompt_version": event.get("prompt_version"),
        "created_at": event.get("created_at"),
        "input_hash": event.get("input_hash"),
        "audit_status": event.get("audit_status") or audit.get("status"),
        "recommended_action": event.get("recommended_action")
        or audit.get("recommended_action"),
        "confidence": audit.get("confidence"),
        "work_lane": audit.get("work_lane"),
        "summary": audit.get("summary") or audit.get("reason") or "",
        "reason": audit.get("reason") or audit.get("summary") or "",
        "labels": list(audit.get("labels") or []),
        "issue_families": list(audit.get("issue_families") or []),
        "findings": list(audit.get("findings") or []),
        "suggested_correction": audit.get("suggested_correction"),
        "evidence_transition": evidence_transition(content, audit),
        "correction_reflection": correction_reflection(
            content,
            audit.get("suggested_correction"),
        ),
        "superseded_by_human": bool(latest_ai.get("superseded_by_human")),
        "active_in_review_ui": bool(latest_ai.get("active")),
    }


def local_text_issues(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not result.get("batch_valid"):
        return []
    issues: list[dict[str, Any]] = []
    for lane in ("ocr_text", "meaning"):
        issues.extend((result.get("lane_results") or {}).get(lane, {}).get("issues") or [])
    return issues


def join_rows(
    local_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    latest_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    event_by_key = {
        event["candidate_key"]: event
        for event in events
        if event.get("model_name") == "gpt-5.6-luna"
    }
    candidate_by_key = {
        candidate["candidate_key"]: candidate for candidate in latest_candidates
    }
    joined: list[dict[str, Any]] = []
    for local_row in local_rows:
        row = dict(local_row)
        key = row["candidate_key"]
        event = event_by_key.get(key)
        if event:
            reference = luna_reference(
                event,
                row.get("content") or {},
                candidate_by_key.get(key),
            )
        else:
            reference = {
                "audited": False,
                "historical": True,
                "advisory_only": True,
                "model": "gpt-5.6-luna",
                "reason": "此題不在當時 LUNA 審核事件中。",
            }
        row["luna_reference"] = reference
        joined.append(row)
    return joined


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    audited = [row for row in rows if row["luna_reference"]["audited"]]
    historical_scope = [
        row for row in rows if str((row.get("exam") or {}).get("ordinal")) == "2"
    ]
    unaudited_in_scope = [
        row for row in historical_scope if not row["luna_reference"]["audited"]
    ]
    flagged = [
        row
        for row in audited
        if row["luna_reference"].get("audit_status") != "pass"
    ]
    statuses = Counter(
        row["luna_reference"].get("audit_status") for row in audited
    )
    actions = Counter(
        row["luna_reference"].get("recommended_action") for row in audited
    )
    codes: Counter[str] = Counter()
    for row in audited:
        for finding in row["luna_reference"].get("findings") or []:
            codes[str(finding.get("code") or "")] += 1
    transition_statuses = Counter(
        row["luna_reference"]["evidence_transition"]["status"] for row in flagged
    )
    correction_statuses = Counter(
        row["luna_reference"]["correction_reflection"]["status"]
        for row in audited
        if row["luna_reference"].get("suggested_correction")
    )
    luna_and_local = 0
    luna_only = 0
    for row in flagged:
        locally_flagged = any(
            local_text_issues(row["model_results"][model])
            for model in LOCAL_MODELS
        )
        if locally_flagged:
            luna_and_local += 1
        else:
            luna_only += 1
    return {
        "advisory_only": True,
        "historical_snapshot_warning": (
            "LUNA reviewed older candidates; local models reviewed the latest "
            "human-corrected content. Question-level overlap is not an accuracy score."
        ),
        "question_count": len(rows),
        "luna_historical_scope": "醫事檢驗師 115 年第 2 次六科",
        "luna_historical_scope_question_count": len(historical_scope),
        "outside_luna_historical_scope_questions": len(rows) - len(historical_scope),
        "luna_audited_questions": len(audited),
        "luna_not_audited_questions": len(rows) - len(audited),
        "luna_unaudited_questions_within_historical_scope": len(
            unaudited_in_scope
        ),
        "luna_unaudited_candidate_keys_within_historical_scope": [
            row["candidate_key"] for row in unaudited_in_scope
        ],
        "luna_superseded_by_human_questions": sum(
            bool(row["luna_reference"].get("superseded_by_human"))
            for row in audited
        ),
        "luna_statuses": dict(statuses),
        "luna_recommended_actions": dict(actions),
        "luna_flagged_questions": len(flagged),
        "luna_finding_count": sum(
            len(row["luna_reference"].get("findings") or []) for row in audited
        ),
        "luna_suggested_correction_count": sum(
            bool(row["luna_reference"].get("suggested_correction"))
            for row in audited
        ),
        "luna_finding_codes": dict(codes),
        "historical_evidence_transition_statuses": dict(transition_statuses),
        "correction_reflection_statuses": dict(correction_statuses),
        "flagged_question_overlap_with_current_local_runs": {
            "luna_flagged_and_at_least_one_local_text_flag": luna_and_local,
            "luna_flagged_without_current_local_text_flag": luna_only,
            "warning": "Inputs differ because human corrections occurred between runs.",
        },
    }


def evidence_text(evidence: dict[str, Any]) -> str:
    field = str(evidence.get("field") or "")
    before = evidence.get("before")
    after = evidence.get("after")
    value = evidence.get("value")
    if before is not None or after is not None:
        return f"{field}：「{before}」→「{after}」"
    return f"{field}：{json.dumps(value, ensure_ascii=False)}"


def local_result_text(model: str, result: dict[str, Any]) -> list[str]:
    if not result.get("batch_valid"):
        return [f"- `{model}`：批次格式無效，不能判定。"]
    issues = local_text_issues(result)
    if not issues:
        return [f"- `{model}`：目前版本無文字註記。"]
    lines = [f"- `{model}`：{len(issues)} 個目前版本文字註記。"]
    for issue in issues:
        observed = str(issue.get("observed") or "")
        replacement = issue.get("replacement")
        change = f"「{observed}」"
        if replacement:
            change += f" → 「{replacement}」"
        lines.append(
            f"  - {issue.get('lane')} / {issue.get('code')} / "
            f"{issue.get('location')}：{change}；{issue.get('note') or ''}"
        )
    return lines


def flagged_markdown(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        "# GPT-5.6 LUNA 歷史審核：醫事檢驗師 115-2 參考",
        "",
        "> LUNA 審的是人工修正前的舊 candidate；三個本地模型審的是 Mac Studio 最新人工修正版。以下重疊只供定位，不是模型正確率比較。",
        "",
        "## 摘要",
        "",
        f"- 歷史審核範圍：{summary['luna_historical_scope']}，共 {summary['luna_historical_scope_question_count']} 題；115-1 不在該次 LUNA 範圍。",
        f"- 範圍內有 LUNA 事件：{summary['luna_audited_questions']} 題；無事件：{summary['luna_unaudited_questions_within_historical_scope']} 題。",
        f"- pass：{summary['luna_statuses'].get('pass', 0)}；needs_review：{summary['luna_statuses'].get('needs_review', 0)}；block：{summary['luna_statuses'].get('block', 0)}。",
        f"- 需注意題目：{summary['luna_flagged_questions']} 題；findings：{summary['luna_finding_count']}；correction：{summary['luna_suggested_correction_count']}。",
        f"- 已被後續人工審核 supersede：{summary['luna_superseded_by_human_questions']} 題；舊事件只作歷史參考。",
        "- 第 26、28 題當時尚未被 parser 切出，因此沒有 LUNA 事件。",
        "",
        "## LUNA 提出疑點的題目",
        "",
    ]
    current_paper = None
    for row in rows:
        ref = row["luna_reference"]
        if not ref.get("audited") or ref.get("audit_status") == "pass":
            continue
        exam = row.get("exam") or {}
        paper = row.get("source_registry_key")
        if paper != current_paper:
            current_paper = paper
            lines.extend(
                [
                    f"## {exam.get('year')} 年第 {exam.get('ordinal')} 次｜"
                    f"{exam.get('subject')}（{exam.get('subject_code')}）",
                    "",
                ]
            )
        lines.extend(
            [
                f"### 第 {exam.get('question_number')} 題",
                "",
                f"`{row['candidate_key']}`",
                "",
                f"目前題幹：{row['content'].get('stem') or '（空白）'}",
                "",
                f"- LUNA 歷史狀態：`{ref.get('audit_status')}`；"
                f"建議動作：`{ref.get('recommended_action')}`；"
                f"事件：{ref.get('event_id')}。",
                f"- 理由：{ref.get('reason')}",
                f"- 舊文字與目前版本：`{ref['evidence_transition']['status']}`。",
                f"- correction 與目前版本：`{ref['correction_reflection']['status']}`。",
            ]
        )
        for finding in ref.get("findings") or []:
            lines.append(
                f"- finding `{finding.get('code')}`（{finding.get('severity')}）："
                f"{finding.get('message')}"
            )
            for evidence in finding.get("evidence") or []:
                if isinstance(evidence, dict):
                    lines.append(f"  - 證據：{evidence_text(evidence)}")
        if ref.get("suggested_correction"):
            correction = json.dumps(
                ref["suggested_correction"],
                ensure_ascii=False,
                sort_keys=True,
            )
            lines.append(f"- LUNA correction：`{correction}`")
        lines.extend(["", "目前版本的本地模型：", ""])
        for model in LOCAL_MODELS:
            lines.extend(local_result_text(model, row["model_results"][model]))
        lines.extend(["", "---", ""])
    return "\n".join(lines).rstrip() + "\n"


def full_reference_markdown(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    lines = [
        "# 醫事檢驗師 115 年：逐題 LUNA 歷史參考索引",
        "",
        "> LUNA 為歷史 advisory；人工審核已優先，這份檔案不會重新啟用或匯入任何舊 AI 事件。",
        "",
        f"- 全部題目：{summary['question_count']}",
        f"- LUNA 歷史範圍：{summary['luna_historical_scope']}（{summary['luna_historical_scope_question_count']} 題）",
        f"- 範圍內有 LUNA 事件：{summary['luna_audited_questions']}",
        f"- 範圍內無 LUNA 事件：{summary['luna_unaudited_questions_within_historical_scope']}",
        f"- LUNA 提出疑點：{summary['luna_flagged_questions']}",
        "",
    ]
    current_paper = None
    for row in rows:
        exam = row.get("exam") or {}
        paper = row.get("source_registry_key")
        if paper != current_paper:
            current_paper = paper
            lines.extend(
                [
                    f"## {exam.get('year')} 年第 {exam.get('ordinal')} 次｜"
                    f"{exam.get('subject')}（{exam.get('subject_code')}）",
                    "",
                ]
            )
        ref = row["luna_reference"]
        if ref.get("audited"):
            detail = (
                f"`{ref.get('audit_status')}`｜{ref.get('reason')}"
                if ref.get("audit_status") != "pass"
                else "`pass`"
            )
        else:
            detail = "無 LUNA 事件"
        lines.extend(
            [
                f"### 第 {exam.get('question_number')} 題",
                "",
                f"`{row['candidate_key']}`",
                "",
                f"- LUNA：{detail}",
                f"- 目前題幹：{row['content'].get('stem') or '（空白）'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def still_present_markdown(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    still_present = [
        row
        for row in rows
        if row["luna_reference"].get("audited")
        and row["luna_reference"]["evidence_transition"]["status"]
        == "historical_text_still_present"
    ]
    lines = [
        "# LUNA 歷史疑點在目前題面仍可定位：人工核對清單",
        "",
        "> 這是字串定位結果，不代表 LUNA 一定正確。尤其上下標、異體字與專業術語仍須核對官方 PDF；不得由此清單自動修改或把題目丟回未審。",
        "",
        f"- 候選題數：{len(still_present)}",
        f"- 來源：{summary['luna_historical_scope']} 的歷史 `gpt-5.6-luna` advisory。",
        "",
    ]
    current_paper = None
    for row in still_present:
        exam = row.get("exam") or {}
        paper = row.get("source_registry_key")
        ref = row["luna_reference"]
        if paper != current_paper:
            current_paper = paper
            lines.extend(
                [
                    f"## {exam.get('subject')}（{exam.get('subject_code')}）",
                    "",
                ]
            )
        lines.extend(
            [
                f"### 第 {exam.get('question_number')} 題",
                "",
                f"`{row['candidate_key']}`",
                "",
                f"- LUNA 理由：{ref.get('reason')}",
            ]
        )
        for pair in ref["evidence_transition"]["pairs"]:
            if pair.get("before_present_now"):
                lines.append(
                    f"- 目前仍可定位：{pair.get('field')}｜"
                    f"「{pair.get('before')}」→ LUNA 建議「{pair.get('after')}」"
                )
        lines.extend(
            [
                f"- 目前題幹：{row['content'].get('stem') or '（空白）'}",
                "- 人工判定：□ 確認需修正　□ 官方原文如此／不需修正　□ 需看 PDF",
                "",
                "目前版本的本地模型：",
                "",
            ]
        )
        for model in LOCAL_MODELS:
            lines.extend(local_result_text(model, row["model_results"][model]))
        lines.extend(["", "---", ""])
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-report-jsonl", type=Path, required=True)
    parser.add_argument("--luna-events-jsonl", type=Path, required=True)
    parser.add_argument("--latest-candidates-api", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    local_rows = read_jsonl(args.local_report_jsonl)
    events = read_jsonl(args.luna_events_jsonl)
    latest_payload = read_json(args.latest_candidates_api)
    rows = join_rows(local_rows, events, latest_payload.get("candidates") or [])
    summary = build_summary(rows)
    output_dir: Path = args.output_dir
    combined_path = output_dir / "medtech-115-local-models-with-luna-reference.jsonl"
    flagged_path = output_dir / "medtech-115-luna-flagged-reference.md"
    full_path = output_dir / "medtech-115-per-question-luna-reference-index.md"
    still_present_path = output_dir / "medtech-115-luna-still-present-review-list.md"
    summary_path = output_dir / "medtech-115-luna-reference-summary.json"
    write_jsonl(combined_path, rows)
    write_json(summary_path, summary)
    flagged_path.parent.mkdir(parents=True, exist_ok=True)
    flagged_path.write_text(flagged_markdown(rows, summary), encoding="utf-8")
    full_path.write_text(full_reference_markdown(rows, summary), encoding="utf-8")
    still_present_path.write_text(
        still_present_markdown(rows, summary),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "rows": len(rows),
                "luna_audited": summary["luna_audited_questions"],
                "luna_flagged": summary["luna_flagged_questions"],
                "flagged_report": str(flagged_path),
                "still_present_review_list": str(still_present_path),
                "full_index": str(full_path),
                "combined_jsonl": str(combined_path),
                "summary": str(summary_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
