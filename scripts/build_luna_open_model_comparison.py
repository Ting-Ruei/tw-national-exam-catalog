#!/usr/bin/env python3
"""Compare historical LUNA findings with current open-model audit results.

The comparison has two explicitly separate layers:

1. Descriptive flag density on the 478 questions that have stored LUNA events.
   This is not an accuracy comparison because the candidate text changed.
2. Same-site recall on the 17 questions whose LUNA `before` string is still
   present in the current effective text.  LUNA remains only a candidate
   reference until a human labels each item as a true error or false alarm.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


MODELS = (
    "gemma4:31b-mlx",
    "qwen3.6:35b-mlx",
    "qwen3.6:27b-mlx",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalized_match_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def same_site_match(issue: dict[str, Any], pair: dict[str, Any]) -> bool:
    before = normalized_match_text(pair.get("before"))
    after = normalized_match_text(pair.get("after"))
    observed = normalized_match_text(issue.get("observed"))
    replacement = normalized_match_text(issue.get("replacement"))
    note = normalized_match_text(issue.get("note"))
    return bool(
        (observed and (observed in before or before in observed))
        or (replacement and (replacement in after or after in replacement))
        or (before and before in note)
        or (after and after in note)
    )


def local_text_issues(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not result.get("batch_valid"):
        return []
    issues: list[dict[str, Any]] = []
    for lane in ("ocr_text", "meaning"):
        issues.extend((result.get("lane_results") or {}).get(lane, {}).get("issues") or [])
    return issues


def present_pairs(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        pair
        for pair in row["luna_reference"]["evidence_transition"]["pairs"]
        if pair.get("before_present_now")
    ]


def compare_model(row: dict[str, Any], model: str) -> dict[str, Any]:
    result = row["model_results"][model]
    if not result.get("batch_valid"):
        return {
            "state": "invalid_batch",
            "batch_valid": False,
            "any_text_flag": False,
            "same_site_match": False,
            "matched_issues": [],
            "other_issues": [],
        }
    issues = local_text_issues(result)
    pairs = present_pairs(row)
    matched = [
        issue
        for issue in issues
        if any(same_site_match(issue, pair) for pair in pairs)
    ]
    other = [issue for issue in issues if issue not in matched]
    if matched:
        state = "same_site_match"
    elif issues:
        state = "other_issue_only"
    else:
        state = "no_text_flag"
    return {
        "state": state,
        "batch_valid": True,
        "any_text_flag": bool(issues),
        "same_site_match": bool(matched),
        "matched_issues": matched,
        "other_issues": other,
    }


def descriptive_density(
    audited_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    luna_flagged = sum(
        row["luna_reference"].get("audit_status") != "pass"
        for row in audited_rows
    )
    result: dict[str, Any] = {
        "luna": {
            "stored_event_questions": len(audited_rows),
            "flagged_questions": luna_flagged,
            "flag_density": round(luna_flagged / len(audited_rows), 6),
            "finding_count": sum(
                len(row["luna_reference"].get("findings") or [])
                for row in audited_rows
            ),
            "suggested_correction_questions": sum(
                bool(row["luna_reference"].get("suggested_correction"))
                for row in audited_rows
            ),
        }
    }
    for model in MODELS:
        valid = [
            row
            for row in audited_rows
            if row["model_results"][model].get("batch_valid")
        ]
        flagged = [
            row
            for row in valid
            if local_text_issues(row["model_results"][model])
        ]
        result[model] = {
            "questions_in_luna_scope": len(audited_rows),
            "valid_questions": len(valid),
            "invalid_questions": len(audited_rows) - len(valid),
            "flagged_questions_among_valid": len(flagged),
            "flag_density_among_valid": round(len(flagged) / len(valid), 6),
            "text_finding_count": sum(
                len(local_text_issues(row["model_results"][model]))
                for row in valid
            ),
            "suggested_correction_questions": sum(
                bool(row["model_results"][model].get("suggested_correction"))
                for row in valid
            ),
        }
    return result


def same_text_subset(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row["luna_reference"].get("audited")
        and row["luna_reference"]["evidence_transition"]["status"]
        == "historical_text_still_present"
    ]


def build_comparison(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    audited = [row for row in rows if row["luna_reference"].get("audited")]
    subset = same_text_subset(rows)
    details: list[dict[str, Any]] = []
    union_matches: set[str] = set()
    for row in subset:
        model_comparison = {
            model: compare_model(row, model) for model in MODELS
        }
        if any(value["same_site_match"] for value in model_comparison.values()):
            union_matches.add(row["candidate_key"])
        details.append(
            {
                "candidate_key": row["candidate_key"],
                "exam": row.get("exam") or {},
                "content": row.get("content") or {},
                "luna": {
                    "event_id": row["luna_reference"].get("event_id"),
                    "audit_status": row["luna_reference"].get("audit_status"),
                    "reason": row["luna_reference"].get("reason"),
                    "findings": row["luna_reference"].get("findings") or [],
                    "present_pairs": present_pairs(row),
                },
                "models": model_comparison,
                "human_ground_truth": {
                    "status": "",
                    "notes": "",
                },
            }
        )
    model_metrics: dict[str, Any] = {}
    for model in MODELS:
        comparisons = [detail["models"][model] for detail in details]
        valid = [value for value in comparisons if value["batch_valid"]]
        any_flags = [value for value in valid if value["any_text_flag"]]
        matches = [value for value in valid if value["same_site_match"]]
        other_only = [value for value in valid if value["state"] == "other_issue_only"]
        model_metrics[model] = {
            "subset_questions": len(details),
            "valid_questions": len(valid),
            "invalid_questions": len(details) - len(valid),
            "any_text_flag_questions": len(any_flags),
            "same_site_match_questions": len(matches),
            "other_issue_only_questions": len(other_only),
            "same_site_recall_among_valid_luna_candidates": round(
                len(matches) / len(valid),
                6,
            ),
            "end_to_end_same_site_coverage": round(
                len(matches) / len(details),
                6,
            ),
        }
    summary = {
        "advisory_only": True,
        "comparison_limit": (
            "LUNA is a candidate reference, not ground truth. Precision and true "
            "recall require human labels."
        ),
        "input_version_warning": (
            "The 478-question density comparison uses different input versions. "
            "Only the 17-question same-text subset supports same-site recall analysis."
        ),
        "descriptive_density_on_luna_event_questions": descriptive_density(audited),
        "same_text_subset": {
            "question_count": len(details),
            "luna_finding_code_counts": dict(
                Counter(
                    finding.get("code")
                    for detail in details
                    for finding in detail["luna"]["findings"]
                )
            ),
            "models": model_metrics,
            "any_open_model_same_site_match_questions": len(union_matches),
            "no_open_model_same_site_match_questions": len(details)
            - len(union_matches),
            "any_open_model_end_to_end_coverage": round(
                len(union_matches) / len(details),
                6,
            ),
        },
    }
    return summary, details


def issue_short(issue: dict[str, Any]) -> str:
    observed = str(issue.get("observed") or "")
    replacement = issue.get("replacement")
    change = f"「{observed}」"
    if replacement:
        change += f"→「{replacement}」"
    return (
        f"{issue.get('lane')}/{issue.get('code')}/{issue.get('location')} "
        f"{change} {issue.get('note') or ''}"
    ).strip()


def state_label(value: dict[str, Any]) -> str:
    return {
        "invalid_batch": "批次無效",
        "same_site_match": "命中同一處",
        "other_issue_only": "只報其他疑點",
        "no_text_flag": "未報文字疑點",
    }[value["state"]]


def markdown_report(summary: dict[str, Any], details: list[dict[str, Any]]) -> str:
    density = summary["descriptive_density_on_luna_event_questions"]
    subset = summary["same_text_subset"]
    lines = [
        "# GPT-5.6 LUNA 與三個開源模型比較",
        "",
        "> LUNA 不是 ground truth。LUNA 審的是人工修正前版本；本地模型審的是最新人工修正版。全部 478 題只能比較輸出行為，不能拿來計算誰比較準。",
        "",
        "## 結論",
        "",
        f"- 可近似同題比較的子集：{subset['question_count']} 題；這些題目前仍包含 LUNA 當時引用的 `before` 字串。",
        f"- 任一開源模型命中 LUNA 同一處：{subset['any_open_model_same_site_match_questions']} 題（{subset['any_open_model_end_to_end_coverage']:.2%}）。",
        f"- 三個開源模型都未命中同一處：{subset['no_open_model_same_site_match_questions']} 題。",
        "- 目前只能稱為「相對 LUNA 候選的命中率」；人工確認每題真偽後，才能改稱真正 recall／precision。",
        "",
        "## 17 題同字串子集",
        "",
        "| 模型 | 有效題數 | 批次無效 | 任意文字疑點 | 命中同一處 | 只報其他疑點 | 有效題中的同處命中率 | 端到端覆蓋 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in MODELS:
        metric = subset["models"][model]
        lines.append(
            f"| `{model}` | {metric['valid_questions']} | "
            f"{metric['invalid_questions']} | {metric['any_text_flag_questions']} | "
            f"{metric['same_site_match_questions']} | "
            f"{metric['other_issue_only_questions']} | "
            f"{metric['same_site_recall_among_valid_luna_candidates']:.2%} | "
            f"{metric['end_to_end_same_site_coverage']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## 478 題輸出密度（不同版本，僅描述）",
            "",
            "| 模型 | 可用題數 | 有文字疑點題數 | 疑點密度 | findings | correction 題數 |",
            "|---|---:|---:|---:|---:|---:|",
            f"| `gpt-5.6-luna` | {density['luna']['stored_event_questions']} | "
            f"{density['luna']['flagged_questions']} | "
            f"{density['luna']['flag_density']:.2%} | "
            f"{density['luna']['finding_count']} | "
            f"{density['luna']['suggested_correction_questions']} |",
        ]
    )
    for model in MODELS:
        metric = density[model]
        lines.append(
            f"| `{model}` | {metric['valid_questions']} | "
            f"{metric['flagged_questions_among_valid']} | "
            f"{metric['flag_density_among_valid']:.2%} | "
            f"{metric['text_finding_count']} | "
            f"{metric['suggested_correction_questions']} |"
        )
    lines.extend(
        [
            "",
            "LUNA 的 64 個 flagged 中，不少錯字已由後續人工 correction 移除；因此本地模型沒有再次報錯通常是正確行為，不可算漏報。",
            "",
            "## 逐題同處比較",
            "",
        ]
    )
    current_paper = None
    for detail in details:
        exam = detail["exam"]
        paper = (exam.get("ordinal"), exam.get("subject_code"))
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
                f"`{detail['candidate_key']}`",
                "",
                f"- LUNA：{detail['luna']['reason']}",
            ]
        )
        for pair in detail["luna"]["present_pairs"]:
            lines.append(
                f"- 比較位置：{pair.get('field')}｜"
                f"「{pair.get('before')}」→「{pair.get('after')}」"
            )
        for model in MODELS:
            value = detail["models"][model]
            lines.append(f"- `{model}`：**{state_label(value)}**")
            for issue in value["matched_issues"]:
                lines.append(f"  - 同處：{issue_short(issue)}")
            for issue in value["other_issues"]:
                lines.append(f"  - 其他：{issue_short(issue)}")
        lines.extend(
            [
                "- 人工 ground truth：□ LUNA 正確　□ LUNA 誤報　□ 官方原文需保留　□ 待看 PDF",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--combined-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.combined_jsonl)
    summary, details = build_comparison(rows)
    output_dir: Path = args.output_dir
    report_path = output_dir / "medtech-115-luna-vs-open-models.md"
    details_path = output_dir / "medtech-115-luna-vs-open-models-details.json"
    summary_path = output_dir / "medtech-115-luna-vs-open-models-summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown_report(summary, details), encoding="utf-8")
    write_json(details_path, details)
    write_json(summary_path, summary)
    print(
        json.dumps(
            {
                "report": str(report_path),
                "summary": str(summary_path),
                "details": str(details_path),
                "same_text_questions": len(details),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
