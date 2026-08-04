#!/usr/bin/env python3
"""Build a per-question comparison report from local compact-audit runs.

The report is intentionally read-only and advisory.  It combines the frozen
question text with each model's validated preview.  When a chunk failed schema
validation, every affected question is labelled as unassessed rather than as a
pass; any partially parsed findings are retained but explicitly marked
provisional.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


LANES = ("ocr_text", "meaning", "visual", "group")
LANE_LABELS = {
    "ocr_text": "OCR 文字",
    "meaning": "題義",
    "visual": "圖片題",
    "group": "題組題",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


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
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )


def model_roots(benchmark_root: Path) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for summary_path in benchmark_root.glob("*/benchmark_summary.json"):
        summary = read_json(summary_path)
        model = str(summary.get("model") or "")
        if model:
            roots[model] = summary_path.parent
    return roots


def task_has_visual(task: dict[str, Any]) -> bool:
    content = task.get("content") or {}
    if content.get("stem_image") or content.get("image_refs"):
        return True
    return any(option.get("image") for option in content.get("options") or [])


def task_has_group(task: dict[str, Any]) -> bool:
    content = task.get("content") or {}
    return bool(content.get("group_ref") or content.get("group_sequence_no"))


def lane_selected(task: dict[str, Any], lane: str) -> bool:
    if lane in ("ocr_text", "meaning"):
        return True
    if lane == "visual":
        return task_has_visual(task)
    return task_has_group(task)


def lane_status(
    task: dict[str, Any],
    lane: str,
    meta: dict[str, Any],
    issues: list[dict[str, Any]],
) -> str:
    if not lane_selected(task, lane):
        return "not_applicable"
    if meta.get("unavailable"):
        return "unavailable"
    if not meta.get("complete", False):
        return "validation_failed"
    return "needs_review" if issues else "pass"


def load_lane_validation(
    chunk_dir: Path,
    lane: str,
) -> dict[str, Any]:
    path = chunk_dir / lane / "validation.json"
    if path.is_file():
        return read_json(path)
    return {}


def failed_chunk_result(
    task: dict[str, Any],
    model: str,
    summary: dict[str, Any],
    chunk_dir: Path,
) -> dict[str, Any]:
    candidate_key = task["candidate_key"]
    lane_results: dict[str, Any] = {}
    all_errors: list[str] = []
    provisional_issue_count = 0
    for lane in LANES:
        meta = dict((summary.get("lane_validation") or {}).get(lane) or {})
        validation = load_lane_validation(chunk_dir, lane)
        issues = [
            issue
            for issue in validation.get("issues") or []
            if issue.get("candidate_key") == candidate_key
        ]
        errors = list(meta.get("errors") or validation.get("errors") or [])
        warnings = list(meta.get("warnings") or validation.get("warnings") or [])
        all_errors.extend(f"{lane}:{error}" for error in errors)
        provisional_issue_count += len(issues)
        lane_results[lane] = {
            "status": lane_status(task, lane, meta, issues),
            "issues": issues,
            "errors": errors,
            "warnings": warnings,
            "provisional": bool(issues),
        }
    return {
        "model": model,
        "batch_offset": int(summary.get("source_offset") or 0),
        "batch_size": int(summary.get("task_count") or 0),
        "batch_valid": False,
        "status": "validation_failed",
        "recommended_action": "retry_smaller_batch",
        "summary": "批次格式驗證失敗；不得將此題視為模型判定無異常。",
        "lane_results": lane_results,
        "findings": [],
        "provisional_issue_count": provisional_issue_count,
        "suggested_changes": [],
        "suggested_correction": None,
        "materialization_warnings": [],
        "batch_errors": sorted(set(all_errors)),
    }


def valid_chunk_result(
    model: str,
    summary: dict[str, Any],
    preview: dict[str, Any],
) -> dict[str, Any]:
    lane_results: dict[str, Any] = {}
    for lane in LANES:
        check = dict((preview.get("checks") or {}).get(lane) or {})
        validation = dict((summary.get("lane_validation") or {}).get(lane) or {})
        lane_results[lane] = {
            "status": str(check.get("status") or "unknown"),
            "issues": list((preview.get("channel_results") or {}).get(lane) or []),
            "errors": [],
            "warnings": list(validation.get("warnings") or []),
            "dropped_issue_count": int(
                validation.get("dropped_issue_count") or 0
            ),
            "filtered_non_issue_count": int(
                validation.get("filtered_non_issue_count") or 0
            ),
            "provisional": False,
        }
    return {
        "model": model,
        "batch_offset": int(summary.get("source_offset") or 0),
        "batch_size": int(summary.get("task_count") or 0),
        "batch_valid": True,
        "status": str(preview.get("status") or "unknown"),
        "recommended_action": preview.get("recommended_action"),
        "summary": preview.get("summary") or preview.get("reason") or "",
        "lane_results": lane_results,
        "findings": list(preview.get("findings") or []),
        "provisional_issue_count": 0,
        "suggested_changes": list(preview.get("suggested_changes") or []),
        "suggested_correction": preview.get("suggested_correction"),
        "materialization_warnings": list(
            preview.get("materialization_warnings") or []
        ),
        "batch_errors": [],
    }


def collect_model_results(
    tasks: list[dict[str, Any]],
    model: str,
    root: Path,
    batch_size: int,
) -> dict[str, dict[str, Any]]:
    tasks_by_key = {task["candidate_key"]: task for task in tasks}
    results: dict[str, dict[str, Any]] = {}
    canonical_chunk = re.compile(rf"^bs{batch_size}-o\d+$")
    for chunk_dir in sorted(
        path
        for path in root.glob(f"bs{batch_size}-o*")
        if path.is_dir() and canonical_chunk.fullmatch(path.name)
    ):
        summary_path = chunk_dir / "summary.json"
        if not summary_path.is_file():
            continue
        summary = read_json(summary_path)
        candidate_keys = list(summary.get("candidate_keys") or [])
        if summary.get("all_lanes_complete"):
            preview_path = chunk_dir / "review_ui_results_preview.jsonl"
            previews = {
                row["candidate_key"]: row
                for row in read_jsonl(preview_path)
            }
            for candidate_key in candidate_keys:
                if candidate_key not in previews:
                    raise ValueError(
                        f"{model} valid chunk missing preview for {candidate_key}"
                    )
                results[candidate_key] = valid_chunk_result(
                    model,
                    summary,
                    previews[candidate_key],
                )
        else:
            for candidate_key in candidate_keys:
                task = tasks_by_key[candidate_key]
                results[candidate_key] = failed_chunk_result(
                    task,
                    model,
                    summary,
                    chunk_dir,
                )
    missing = sorted(set(tasks_by_key) - set(results))
    if missing:
        raise ValueError(f"{model} has {len(missing)} missing question results")
    return results


def valid_text_issues(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not result.get("batch_valid"):
        return []
    rows: list[dict[str, Any]] = []
    for lane in ("ocr_text", "meaning"):
        rows.extend(result["lane_results"][lane]["issues"])
    return rows


def model_metrics(
    rows: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    model_results = [row["model_results"][model] for row in rows]
    valid_results = [result for result in model_results if result["batch_valid"]]
    flagged_results = [
        result for result in valid_results if valid_text_issues(result)
    ]
    batches = {
        int(result.get("batch_offset") or 0): result
        for result in valid_results
    }
    return {
        "model": model,
        "question_count": len(model_results),
        "valid_question_count": len(valid_results),
        "invalid_question_count": len(model_results) - len(valid_results),
        "valid_rate": round(len(valid_results) / len(model_results), 6),
        "questions_with_valid_text_findings": len(flagged_results),
        "valid_text_finding_count": sum(
            len(valid_text_issues(result)) for result in valid_results
        ),
        "questions_with_suggested_correction": sum(
            bool(result.get("suggested_correction")) for result in valid_results
        ),
        "dropped_issue_count": sum(
            int(lane.get("dropped_issue_count") or 0)
            for result in batches.values()
            for lane in result.get("lane_results", {}).values()
        ),
        "filtered_non_issue_count": sum(
            int(lane.get("filtered_non_issue_count") or 0)
            for result in batches.values()
            for lane in result.get("lane_results", {}).values()
        ),
    }


def build_rows(
    tasks: list[dict[str, Any]],
    models: list[str],
    results_by_model: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        candidate_key = task["candidate_key"]
        row = {
            "candidate_key": candidate_key,
            "exam": task.get("exam") or {},
            "source_registry_key": task.get("source_registry_key"),
            "effective_content_hash": task.get("effective_content_hash"),
            "content": task.get("content") or {},
            "signals": task.get("signals") or {},
            "sources": task.get("sources") or {},
            "model_results": {
                model: results_by_model[model][candidate_key] for model in models
            },
            "human_quality_evaluation": {
                "status": "",
                "notes": "",
            },
        }
        valid_models = [
            model
            for model in models
            if row["model_results"][model]["batch_valid"]
        ]
        flagged_models = [
            model
            for model in valid_models
            if valid_text_issues(row["model_results"][model])
        ]
        row["comparison"] = {
            "valid_models": valid_models,
            "flagged_models": flagged_models,
            "valid_model_count": len(valid_models),
            "flagged_model_count": len(flagged_models),
            "all_models_unassessed": not valid_models,
        }
        rows.append(row)
    return rows


def issue_text(issue: dict[str, Any]) -> str:
    code = str(issue.get("code") or "issue")
    location = str(issue.get("location") or "")
    observed = str(issue.get("observed") or "")
    replacement = issue.get("replacement")
    note = str(issue.get("note") or "")
    confidence = issue.get("confidence")
    parts = [f"`{code}`"]
    if location:
        parts.append(location)
    if observed:
        change = f"「{observed}」"
        if replacement:
            change += f" → 「{replacement}」"
        parts.append(change)
    if note:
        parts.append(note)
    if confidence is not None:
        parts.append(f"信心 {confidence}")
    return "；".join(parts)


def markdown_model_result(model: str, result: dict[str, Any]) -> list[str]:
    offset = result["batch_offset"]
    valid_label = "批次有效" if result["batch_valid"] else "批次無效"
    lines = [f"- **{model}**（offset {offset}，{valid_label}）"]
    if not result["batch_valid"]:
        lines.append(
            "  - ⚠️ 批次格式驗證失敗；本題不能當成「AI 認為無異常」，應縮小批次重跑。"
        )
        if result["batch_errors"]:
            lines.append("  - 驗證錯誤：" + "、".join(result["batch_errors"]))
    for lane in LANES:
        lane_result = result["lane_results"][lane]
        label = LANE_LABELS[lane]
        issues = lane_result["issues"]
        status = lane_result["status"]
        if issues:
            qualifier = "（部分解析，僅供參考）" if not result["batch_valid"] else ""
            lines.append(f"  - {label}{qualifier}：")
            for issue in issues:
                lines.append(f"    - {issue_text(issue)}")
        else:
            status_text = {
                "pass": "無註記",
                "not_applicable": "不適用",
                "unavailable": "模型未檢查，需人工核圖",
                "validation_failed": "驗證失敗，尚未得到可靠判定",
            }.get(status, status)
            lines.append(f"  - {label}：{status_text}")
    if result.get("suggested_changes"):
        lines.append("  - 建議修改：" + "；".join(result["suggested_changes"]))
    if result.get("suggested_correction"):
        correction = json.dumps(
            result["suggested_correction"],
            ensure_ascii=False,
            sort_keys=True,
        )
        lines.append(f"  - 完整 correction：`{correction}`")
    if result.get("materialization_warnings"):
        lines.append(
            "  - correction 產生警告："
            + "、".join(result["materialization_warnings"])
        )
    return lines


def markdown_report(
    rows: list[dict[str, Any]],
    models: list[str],
    summary: dict[str, Any],
    title: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        "> 此檔僅供人工審核品質評估。沒有寫入 Review UI、沒有更動人為標記，也沒有把題目丟回未審。",
        "",
        "## 執行摘要",
        "",
        f"- 題目總數：{summary['question_count']} 題／{summary['paper_count']} 份。",
        f"- 批次：每批 {summary['batch_size']} 題，依考科分界，不跨卷。",
        f"- 所有模型都未取得可靠結果：{summary['all_models_unassessed_questions']} 題；這些題不能視為通過。",
        f"- {len(models)} 個模型皆有有效結果：{summary['all_models_valid_questions']} 題。",
        "",
        "| 模型 | 有效題數 | 無效題數 | 有效率 | 有文字註記題數 | 文字註記總數 | correction 題數 | 丟棄格式錯誤 | 過濾非問題 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for metric in summary["models"]:
        lines.append(
            "| {model} | {valid_question_count} | {invalid_question_count} | "
            "{rate:.2%} | {questions_with_valid_text_findings} | "
            "{valid_text_finding_count} | "
            "{questions_with_suggested_correction} | "
            "{dropped_issue_count} | {filtered_non_issue_count} |".format(
                rate=metric["valid_rate"],
                **metric,
            )
        )
    current_paper = None
    for row in rows:
        exam = row["exam"]
        paper = row["source_registry_key"]
        if paper != current_paper:
            current_paper = paper
            lines.extend(
                [
                    "",
                    f"## {exam.get('year')} 年第 {exam.get('ordinal')} 次｜"
                    f"{exam.get('subject')}（{exam.get('subject_code')}）",
                    "",
                ]
            )
        question_number = exam.get("question_number")
        content = row["content"]
        lines.extend(
            [
                f"### 第 {question_number} 題",
                "",
                f"`{row['candidate_key']}`",
                "",
                "**題幹**",
                "",
                str(content.get("stem") or "（空白）"),
                "",
                "**選項**",
                "",
            ]
        )
        options = content.get("options") or []
        if options:
            for option in options:
                lines.append(
                    f"- {option.get('key')}. {option.get('text') or '（空白）'}"
                )
        else:
            lines.append("- （無文字選項）")
        if task_has_visual({"content": content}):
            lines.append("- 圖片：本題含圖片資產；本次本地模型未可靠檢查像素。")
        lines.extend(["", "**AI 註記**", ""])
        for model in models:
            lines.extend(markdown_model_result(model, row["model_results"][model]))
        lines.extend(
            [
                "",
                "**人工品質判定（留白）**："
                "□ 正確抓漏　□ 誤報　□ 漏報　□ 無錯且判定合理　□ 待查",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def comparison_summary(
    rows: list[dict[str, Any]],
    models: list[str],
    batch_size: int,
) -> dict[str, Any]:
    valid_count_distribution = Counter(
        row["comparison"]["valid_model_count"] for row in rows
    )
    flagged_count_distribution = Counter(
        row["comparison"]["flagged_model_count"] for row in rows
    )
    return {
        "advisory_only": True,
        "writes_review_events": False,
        "question_count": len(rows),
        "paper_count": len({row["source_registry_key"] for row in rows}),
        "batch_size": batch_size,
        "models": [model_metrics(rows, model) for model in models],
        "all_models_valid_questions": valid_count_distribution[len(models)],
        "all_models_unassessed_questions": valid_count_distribution[0],
        "valid_model_count_distribution": dict(
            sorted(valid_count_distribution.items())
        ),
        "flagged_model_count_distribution": dict(
            sorted(flagged_count_distribution.items())
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--artifact-prefix",
        default="question-audit",
        help="Filename prefix for JSONL, Markdown, and summary outputs.",
    )
    parser.add_argument(
        "--report-title",
        default="國考題本地模型逐題初審註記",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tasks = read_jsonl(args.tasks)
    roots = model_roots(args.benchmark_root)
    missing_models = [model for model in args.models if model not in roots]
    if missing_models:
        raise SystemExit(f"missing model benchmark roots: {missing_models}")
    results_by_model = {
        model: collect_model_results(
            tasks,
            model,
            roots[model],
            args.batch_size,
        )
        for model in args.models
    }
    rows = build_rows(tasks, args.models, results_by_model)
    summary = comparison_summary(rows, args.models, args.batch_size)
    output_dir: Path = args.output_dir
    jsonl_path = output_dir / f"{args.artifact_prefix}-per-question-ai-annotations.jsonl"
    markdown_path = output_dir / f"{args.artifact_prefix}-per-question-ai-annotations.md"
    summary_path = output_dir / f"{args.artifact_prefix}-comparison-summary.json"
    write_jsonl(jsonl_path, rows)
    write_json(summary_path, summary)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(
        markdown_report(rows, args.models, summary, args.report_title),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "rows": len(rows),
                "markdown": str(markdown_path),
                "jsonl": str(jsonl_path),
                "summary": str(summary_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
