#!/usr/bin/env python3
"""
Export Codex/ChatGPT audit tasks grouped by exam category and subject.

This script only prepares task JSONL files and prompts. It never calls a model
and never writes review events. Import model output later with
scripts/import_codex_audit_results.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from export_codex_audit_batch import compact_for_codex, latest_action
from run_question_ai_audit_batch import candidate_sort_key, metadata_matches
from serve_question_review_ui import DEFAULT_CANDIDATE_ROOT, ReviewState, latest_path


UNRELIABLE_AI_MODELS = {
    "",
    "heuristic",
    "gpt-5.4-mini",
    "codex-gpt5-pilot",
}
UNRELIABLE_AI_REVIEWERS = {
    "batch-ai-audit",
    "local",
    "codex-5.4mini-pilot",
    "codex-pilot-5parts",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export subject-separated Codex audit task batches.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--category", default="")
    parser.add_argument("--subject", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--ordinal", default="")
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--include-accepted", action="store_true", help="Include human accepted/unblocked candidates.")
    parser.add_argument(
        "--ai-policy",
        choices=("pending-or-unreliable", "pending-only", "all"),
        default="pending-or-unreliable",
        help="Which candidates to export based on the latest AI review event.",
    )
    parser.add_argument("--model-target", default="5.4", help="Model name to write into prompts for the human/Codex runner.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_CANDIDATE_ROOT / "subject_codex_audit_tasks")
    return parser.parse_args()


def slugify(value: str, max_len: int = 96) -> str:
    value = value.strip() or "unknown"
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = value.strip("._")
    return value[:max_len] or "unknown"


def latest_ai_is_reliable(state: ReviewState, key: str) -> bool:
    event = state.latest_ai_reviews.get(key) or {}
    if not event:
        return False
    if event.get("action") in {"unreviewed", "reset_review", "reset_ai_review"}:
        return False
    audit = event.get("audit") if isinstance(event.get("audit"), dict) else {}
    model = str(event.get("model") or audit.get("model") or "")
    reviewer = str(event.get("reviewer") or "")
    provider = str(event.get("provider") or audit.get("provider") or "")
    if model in UNRELIABLE_AI_MODELS or reviewer in UNRELIABLE_AI_REVIEWERS:
        return False
    return provider == "codex" or reviewer.startswith("codex")


def should_export(item: dict[str, Any], state: ReviewState, args: argparse.Namespace) -> bool:
    key = str(item.get("candidate_key") or "")
    if not args.include_accepted and latest_action(state, key) in {"accept", "unblock"}:
        return False
    if args.ai_policy == "all":
        return True
    has_ai = key in state.latest_ai_reviews
    if args.ai_policy == "pending-only":
        return not has_ai
    return not latest_ai_is_reliable(state, key)


def metadata_for(item: dict[str, Any]) -> tuple[str, str, str, str]:
    metadata = item.get("metadata") or {}
    category = str(metadata.get("normalized_category_name") or metadata.get("group_name") or "unknown")
    subject = str(metadata.get("normalized_subject_name") or "unknown")
    year = str(metadata.get("year") or "")
    ordinal = str(metadata.get("exam_ordinal") or "")
    return category, subject, year, ordinal


def write_prompt(path: Path, *, category: str, subject: str, model_target: str, chunk_dir: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# Codex 科目題目格式稽核任務",
                "",
                f"建議模型：`{model_target}`",
                f"考別：`{category}`",
                f"科目：`{subject}`",
                "",
                "使用 `docs/skills/national-exam-ai-audit/SKILL.md`。",
                "逐行讀取本資料夾 `chunks/` 內的 task JSONL，只輸出 advisory AI labels，不要修改人工審核狀態。",
                "",
                f"任務切片資料夾：`{chunk_dir}`",
                "每個 part 請輸出同名 `codex_question_audit_results__...__partXXXX.jsonl`。",
                "",
                "每行輸出 `docs/skills/national-exam-ai-audit/references/output-schema.md` 定義的 JSON 物件。",
                "題目格式稽核只判斷題幹、選項、圖表、題組與 parser 邊界；答案缺漏、多答案格式、MOD/ANS 優先序請用 `recommended_action: \"defer_to_answer_audit\"` 留到答案核對，不要因此降低題目 status。",
                "若能提出安全的 OCR/格式修正，請填 `suggested_correction` 與 `suggested_changes`；它只會成為 Review UI 的一鍵套用建議，不會自動通過。",
                "不要呼叫本機 heuristic，也不要呼叫 OpenAI API fallback；模型審核結果必須由目前指定的 Codex/ChatGPT 模型直接產生。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(candidate_path, issue_path, review_log)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in sorted([item for item in state.candidates if metadata_matches(item, args)], key=candidate_sort_key):
        payload = state.candidate_payload(item)
        if not should_export(payload, state, args):
            continue
        category, subject, _year, _ordinal = metadata_for(payload)
        grouped[(category, subject)].append(payload)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_csv = run_dir / f"subject_audit_manifest__{timestamp}.csv"
    summary_json = run_dir / f"subject_audit_summary__{timestamp}.json"

    manifest_rows: list[dict[str, Any]] = []
    total_candidates = 0
    for subject_index, ((category, subject), items) in enumerate(sorted(grouped.items()), start=1):
        subject_dir = run_dir / f"{subject_index:03d}__{slugify(category)}__{slugify(subject)}"
        chunk_dir = subject_dir / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        prompt_md = subject_dir / "CODEX_SUBJECT_AUDIT_PROMPT.md"
        subject_manifest = subject_dir / "subject_manifest.json"
        write_prompt(prompt_md, category=category, subject=subject, model_target=args.model_target, chunk_dir=chunk_dir)

        task_files: list[str] = []
        result_files: list[str] = []
        years: dict[str, int] = {}
        ordinals: dict[str, int] = {}
        for item in items:
            _category, _subject, year, ordinal = metadata_for(item)
            years[year] = years.get(year, 0) + 1
            ordinals[ordinal] = ordinals.get(ordinal, 0) + 1
        chunks = [items[index : index + args.chunk_size] for index in range(0, len(items), args.chunk_size)]
        for chunk_index, chunk in enumerate(chunks, start=1):
            task_path = chunk_dir / f"codex_question_audit_tasks__{timestamp}__subject{subject_index:03d}__part{chunk_index:04d}.jsonl"
            result_path = chunk_dir / f"codex_question_audit_results__{timestamp}__subject{subject_index:03d}__part{chunk_index:04d}.jsonl"
            with task_path.open("w", encoding="utf-8") as f:
                for item in chunk:
                    f.write(json.dumps(compact_for_codex(item, state), ensure_ascii=False, sort_keys=True) + "\n")
            task_files.append(str(task_path))
            result_files.append(str(result_path))

        subject_summary = {
            "category": category,
            "subject": subject,
            "candidate_count": len(items),
            "chunk_count": len(chunks),
            "chunk_size": args.chunk_size,
            "years": dict(sorted(years.items())),
            "ordinals": dict(sorted(ordinals.items())),
            "prompt_md": str(prompt_md),
            "task_files": task_files,
            "expected_result_files": result_files,
        }
        subject_manifest.write_text(json.dumps(subject_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        total_candidates += len(items)
        manifest_rows.append(
            {
                "subject_index": subject_index,
                "category": category,
                "subject": subject,
                "candidate_count": len(items),
                "chunk_count": len(chunks),
                "subject_dir": str(subject_dir),
                "prompt_md": str(prompt_md),
                "subject_manifest": str(subject_manifest),
                "years": ",".join(f"{year}:{count}" for year, count in sorted(years.items())),
                "ordinals": ",".join(f"{ordinal}:{count}" for ordinal, count in sorted(ordinals.items())),
            }
        )

    with manifest_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "subject_index",
                "category",
                "subject",
                "candidate_count",
                "chunk_count",
                "subject_dir",
                "prompt_md",
                "subject_manifest",
                "years",
                "ordinals",
            ],
        )
        writer.writeheader()
        writer.writerows(manifest_rows)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_count": total_candidates,
        "subject_count": len(manifest_rows),
        "chunk_size": args.chunk_size,
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "review_log": str(review_log),
        "manifest_csv": str(manifest_csv),
        "run_dir": str(run_dir),
        "model_target": args.model_target,
        "ai_policy": args.ai_policy,
        "include_accepted": args.include_accepted,
        "filters": {
            "category": args.category,
            "subject": args.subject,
            "year": args.year,
            "ordinal": args.ordinal,
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
