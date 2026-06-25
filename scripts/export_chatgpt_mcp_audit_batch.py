#!/usr/bin/env python3
"""
Export question candidates for ChatGPT subscription review through DevSpace MCP.

This script does not call an API. It prepares compact JSONL tasks and a prompt
file that ChatGPT can read through the MCP connection, then answer with a JSONL
result file for import_chatgpt_mcp_audit_results.py.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from serve_question_review_ui import DEFAULT_CANDIDATE_ROOT, ReviewState, compact_candidate_for_ai, latest_path
from run_question_ai_audit_batch import candidate_sort_key, metadata_matches


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a ChatGPT MCP audit task batch.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--category", default="")
    parser.add_argument("--subject", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--ordinal", default="")
    parser.add_argument("--limit", type=int, default=0, help="Maximum candidates to export. 0 means no limit.")
    parser.add_argument("--force", action="store_true", help="Include candidates that already have an active AI audit.")
    parser.add_argument("--model", default="5.4")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_CANDIDATE_ROOT / "chatgpt_mcp_audit_tasks")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(candidate_path, issue_path, review_log)

    candidates = sorted(
        [item for item in state.candidates if metadata_matches(item, args)],
        key=candidate_sort_key,
    )
    if not args.force:
        candidates = [item for item in candidates if item.get("candidate_key") not in state.latest_ai_reviews]
    if args.limit and args.limit > 0:
        candidates = candidates[: args.limit]

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    task_jsonl = run_dir / f"chatgpt_mcp_question_audit_tasks__{timestamp}.jsonl"
    prompt_md = run_dir / "CHATGPT_MCP_AUDIT_PROMPT.md"
    result_jsonl = run_dir / f"chatgpt_mcp_question_audit_results__{timestamp}.jsonl"
    summary_json = run_dir / f"chatgpt_mcp_question_audit_summary__{timestamp}.json"

    with task_jsonl.open("w", encoding="utf-8") as f:
        for item in candidates:
            payload = compact_candidate_for_ai(state.candidate_payload(item))
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    prompt = f"""# ChatGPT MCP 題目格式稽核任務

請使用模型：{args.model}

任務檔：
`{task_jsonl}`

請逐行讀取 JSONL。每一行是一題 candidate。請只檢查格式、OCR、題幹/選項結構、科學符號、圖表引用與 parser 可疑處，不要判斷題目答案正確性。

請輸出 JSONL 到：
`{result_jsonl}`

每一行請使用這個 schema：

```json
{{"candidate_key":"...","status":"pass|needs_review|blocked","confidence":0.0,"summary":"繁體中文摘要","recommended_action":"no_action|human_review|parser_fix|manual_image_check","findings":[{{"code":"...","severity":"info|warning|error","field":"stem|options|answer|images|group_ref|parser","message":"繁體中文說明","evidence":"原文片段","suggestion":"建議處理"}}]}}
```

規則：
- 沒看到明顯問題時 status 用 `pass`，findings 用空陣列。
- 疑似 OCR 字形錯誤、希臘字母/上下標/單位格式可能錯誤，用 `needs_review`。
- 題幹或選項嚴重缺漏、題組/圖片明顯對不上，用 `blocked`。
- 不要輸出版權課本內容。
- 不要修改題目檔；只寫結果 JSONL。
"""
    prompt_md.write_text(prompt, encoding="utf-8")

    summary: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "candidate_count": len(candidates),
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path) if issue_path else None,
        "review_log": str(review_log),
        "task_jsonl": str(task_jsonl),
        "prompt_md": str(prompt_md),
        "expected_result_jsonl": str(result_jsonl),
        "filters": {
            "category": args.category,
            "subject": args.subject,
            "year": args.year,
            "ordinal": args.ordinal,
            "force": args.force,
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
