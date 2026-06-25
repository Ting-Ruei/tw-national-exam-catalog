#!/usr/bin/env python3
"""
Export high-risk question candidates for Codex skill-based advisory audit.

This script does not call OpenAI APIs. It prepares compact task JSONL for the
current Codex session to inspect with docs/skills/national-exam-ai-audit.
Human review state is read for filtering only and is never changed.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from run_question_ai_audit_batch import candidate_sort_key, metadata_matches
from serve_question_review_ui import DEFAULT_CANDIDATE_ROOT, ReviewState, latest_path


IMAGE_OR_TABLE_RE = re.compile(
    r"(下表|附表|表中|下圖|附圖|圖中|圖示|如圖|依下列資料|檢驗結果|following table|figure)",
    re.I,
)
AMINO_ACID_ANCHOR_RE = re.compile(
    r"\b(glycine|alanine|valine|leucine|isoleucine|serine|threonine|cysteine|methionine|aspart(?:ic acid|ate)|glutam(?:ic acid|ate)|asparagine|glutamine|lysine|arginine|histidine|phenylalanine|tyrosine|tryptophan|proline)\b",
    re.I,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export candidates for Codex advisory audit.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--category", default="")
    parser.add_argument("--subject", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--ordinal", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--chunk-size", type=int, default=0, help="Split selected tasks into chunked JSONL files. 0 writes a single file.")
    parser.add_argument("--force", action="store_true", help="Include candidates already audited by Codex.")
    parser.add_argument("--include-accepted", action="store_true", help="Include human accepted/unblocked candidates.")
    parser.add_argument("--all-matching", action="store_true", help="Export every matching candidate, not just high-risk ones.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_CANDIDATE_ROOT / "codex_audit_tasks")
    return parser.parse_args()


def latest_action(state: ReviewState, key: str) -> str:
    event = state.latest_reviews.get(key) or {}
    return str(event.get("action") or "")


def already_codex_audited(state: ReviewState, key: str) -> bool:
    event = state.latest_ai_reviews.get(key) or {}
    provider = str(event.get("provider") or "")
    reviewer = str(event.get("reviewer") or "")
    return provider == "codex" or reviewer.startswith("codex")


def option_keys(candidate: dict[str, Any]) -> list[str]:
    return [str(option.get("key") or "") for option in candidate.get("options") or [] if isinstance(option, dict)]


def risk_labels(candidate: dict[str, Any], state: ReviewState) -> list[str]:
    labels: list[str] = []
    key = str(candidate.get("candidate_key") or "")
    action = latest_action(state, key)
    quality = str(candidate.get("question_quality_status") or candidate.get("quality_status") or "")
    issues = candidate.get("question_issues") or candidate.get("issues") or []
    stem = str(candidate.get("stem") or "")
    raw_block = str((candidate.get("metadata") or {}).get("raw_block") or "")
    combined = f"{stem}\n{raw_block}"
    image_refs = candidate.get("image_refs") or []
    keys = option_keys(candidate)

    if quality and quality != "pass":
        labels.append(f"parser_{quality}")
    if issues:
        labels.append("parser_issue")
    if action in {"block", "needs_review"}:
        labels.append(f"human_{action}")
    if IMAGE_OR_TABLE_RE.search(combined):
        labels.append("image_or_table_cue")
    if "<table" in combined.lower():
        labels.append("table_markup")
    if image_refs:
        labels.append("has_image_refs")
    if len(keys) != len(set(keys)):
        labels.append("duplicate_option_keys")
    if len(keys) not in {4, 5}:
        labels.append("unusual_option_count")
    if re.search(r"[αβγδ]\s+\d|γ\s*[- ]?\s*麩|mg\s*/\s*dL|μg\s*/\s*dL|\^\s*\{?\\circ\}?\s*C|°\s*C|˚\s*C|\\[A-Za-z]+|<sub>|<sup>", combined):
        labels.append("science_notation_risk")
    if AMINO_ACID_ANCHOR_RE.search(combined) and re.search(r"(胺|氨|酸|醯|酰|麩|麸|纈|缬|蘇|苏|絲|丝|離|离|賴|赖|酪|苯|色|脯|硫)", combined):
        labels.append("amino_acid_translation_risk")
    return sorted(set(labels))


def compact_for_codex(candidate: dict[str, Any], state: ReviewState) -> dict[str, Any]:
    metadata = candidate.get("metadata") or {}
    issues = candidate.get("question_issues") or candidate.get("issues") or []
    key = str(candidate.get("candidate_key") or "")
    latest_human = state.latest_reviews.get(key) or {}
    latest_ai = state.latest_ai_reviews.get(key) or {}
    return {
        "candidate_key": key,
        "category": metadata.get("normalized_category_name") or metadata.get("group_name"),
        "subject": metadata.get("normalized_subject_name"),
        "year": metadata.get("year"),
        "exam_ordinal": metadata.get("exam_ordinal"),
        "question_number": candidate.get("question_number"),
        "quality_status": candidate.get("question_quality_status") or candidate.get("quality_status"),
        "risk_labels": risk_labels(candidate, state),
        "stem": candidate.get("stem"),
        "options": [
            {
                "key": option.get("key"),
                "text": option.get("text"),
                "has_image": bool(isinstance(option.get("image"), dict) and option["image"].get("exists")),
            }
            for option in candidate.get("options") or []
            if isinstance(option, dict)
        ],
        "answer": candidate.get("answer"),
        "group_ref": candidate.get("group_ref"),
        "image_refs": [
            {
                "exists": ref.get("exists"),
                "bytes": ref.get("bytes"),
                "relative_path": ref.get("relative_path") or ref.get("path_relative"),
            }
            for ref in candidate.get("image_refs") or []
            if isinstance(ref, dict)
        ],
        "question_issues": [
            {
                "issue_code": issue.get("issue_code"),
                "severity": issue.get("severity"),
                "message": issue.get("message"),
            }
            for issue in issues
        ],
        "latest_human_review": {
            "action": latest_human.get("action"),
            "notes": latest_human.get("notes"),
            "created_at": latest_human.get("created_at"),
        },
        "latest_ai_review": {
            "provider": latest_ai.get("provider"),
            "model": latest_ai.get("model"),
            "status": (latest_ai.get("audit") or {}).get("status") if isinstance(latest_ai.get("audit"), dict) else None,
        },
        "question_pdf_relative": metadata.get("question_pdf_relative"),
        "raw_block": str(metadata.get("raw_block") or "")[:2000],
    }


def main() -> None:
    args = parse_args()
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(candidate_path, issue_path, review_log)

    selected: list[dict[str, Any]] = []
    for item in sorted([item for item in state.candidates if metadata_matches(item, args)], key=candidate_sort_key):
        key = str(item.get("candidate_key") or "")
        if not args.include_accepted and latest_action(state, key) in {"accept", "unblock"}:
            continue
        if not args.force and already_codex_audited(state, key):
            continue
        payload = state.candidate_payload(item)
        labels = risk_labels(payload, state)
        if not args.all_matching and not labels:
            continue
        selected.append(payload)
        if args.limit > 0 and len(selected) >= args.limit:
            break

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    task_jsonl = run_dir / f"codex_question_audit_tasks__{timestamp}.jsonl"
    result_jsonl = run_dir / f"codex_question_audit_results__{timestamp}.jsonl"
    prompt_md = run_dir / "CODEX_AUDIT_PROMPT.md"
    summary_json = run_dir / f"codex_question_audit_summary__{timestamp}.json"

    task_files: list[Path] = []
    result_files: list[Path] = []
    if args.chunk_size and args.chunk_size > 0:
        chunk_dir = run_dir / "chunks"
        chunk_dir.mkdir()
        chunks = [selected[index : index + args.chunk_size] for index in range(0, len(selected), args.chunk_size)]
        for chunk_index, chunk in enumerate(chunks, start=1):
            chunk_task_jsonl = chunk_dir / f"codex_question_audit_tasks__{timestamp}__part{chunk_index:04d}.jsonl"
            chunk_result_jsonl = chunk_dir / f"codex_question_audit_results__{timestamp}__part{chunk_index:04d}.jsonl"
            with chunk_task_jsonl.open("w", encoding="utf-8") as f:
                for item in chunk:
                    f.write(json.dumps(compact_for_codex(item, state), ensure_ascii=False, sort_keys=True) + "\n")
            task_files.append(chunk_task_jsonl)
            result_files.append(chunk_result_jsonl)
    else:
        with task_jsonl.open("w", encoding="utf-8") as f:
            for item in selected:
                f.write(json.dumps(compact_for_codex(item, state), ensure_ascii=False, sort_keys=True) + "\n")
        task_files.append(task_jsonl)
        result_files.append(result_jsonl)

    prompt_md.write_text(
        "\n".join(
            [
                "# Codex 題目格式稽核任務",
                "",
                "使用 `docs/skills/national-exam-ai-audit/SKILL.md`。",
                "逐行讀取 task JSONL，只輸出 advisory AI labels，不要修改人工審核狀態。",
                "",
                f"任務檔：`{task_jsonl}`" if len(task_files) == 1 else f"任務切片資料夾：`{run_dir / 'chunks'}`",
                f"輸出檔：`{result_jsonl}`" if len(result_files) == 1 else "每個 part 請輸出同名 `codex_question_audit_results__...__partXXXX.jsonl`。",
                "",
                "每行輸出 `docs/skills/national-exam-ai-audit/references/output-schema.md` 定義的 JSON 物件。",
                "題目格式稽核只判斷題幹、選項、圖表、題組與 parser 邊界；答案缺漏、多答案格式、MOD/ANS 優先序請用 `recommended_action: \"defer_to_answer_audit\"` 留到答案核對，不要因此降低題目 status。",
                "若能提出安全的 OCR/格式修正，請填 `suggested_correction` 與 `suggested_changes`；它只會成為 Review UI 的一鍵套用建議，不會自動通過。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_count": len(selected),
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "review_log": str(review_log),
        "task_jsonl": str(task_jsonl),
        "expected_result_jsonl": str(result_jsonl),
        "task_jsonl_files": [str(path) for path in task_files],
        "expected_result_jsonl_files": [str(path) for path in result_files],
        "prompt_md": str(prompt_md),
        "filters": {
            "category": args.category,
            "subject": args.subject,
            "year": args.year,
            "ordinal": args.ordinal,
            "limit": args.limit,
            "chunk_size": args.chunk_size,
            "force": args.force,
            "include_accepted": args.include_accepted,
            "all_matching": args.all_matching,
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
