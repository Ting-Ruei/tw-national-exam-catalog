#!/usr/bin/env python3
"""
Export visual-review candidates for model-based advisory triage.

This script does not call an AI model and does not write review state. It
prepares compact JSONL tasks from the SQL review staging layer so Codex,
ChatGPT MCP, OpenAI API, or a local model can classify whether a candidate
actually needs visual assets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "國考題資料夾" / "30_normalized_items" / "visual_ai_audit_tasks"

STRONG_VISUAL_SQL_RE = (
    r"(下圖|附圖|右圖|左圖|圖中|如圖|如下圖|依圖|依下圖|依據下圖|根據下圖|"
    r"圖示如下|下列圖示|圖示中|"
    r"圖.{0,12}所示|所示.{0,12}圖|承上圖|箭頭所指|箭頭指向|表中|下表|附表|如下表|"
    r"所提供之影像|提供之影像|經由.{0,8}影像|影像.{0,8}判斷|"
    r"心電圖如下|影像如下|照片如下)"
)
WEAK_VISUAL_SQL_RE = (
    r"(心電圖|X\\s*光|X光|超音波|影像|照片|切片圖|染色圖|鏡檢圖|"
    r"尿沉渣圖|電泳圖|曲線圖|流程圖|家系圖|圖示|圖片|箭頭)"
)
TABLE_DEPENDENCY_SQL_RE = r"(表中|下表|附表|如下表|(^|[^[:alnum:]_])table([^[:alnum:]_]|$))"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export visual candidates for advisory AI triage.")
    parser.add_argument("--category", default="")
    parser.add_argument("--subject", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--ordinal", default="")
    parser.add_argument(
        "--source",
        choices=["all", "existing_asset", "structured_table", "strong_text_cue", "weak_text_cue", "missing_asset"],
        default="all",
        help="Which Python/SQL visual candidate source to export.",
    )
    parser.add_argument("--limit", type=int, default=0, help="0 means export all matching rows.")
    parser.add_argument("--chunk-size", type=int, default=0, help="Split tasks into chunked JSONL files. 0 writes one file.")
    parser.add_argument(
        "--split-by-category",
        action="store_true",
        help="Write independent category subdirectories from one database query so progress can be resumed per exam category.",
    )
    parser.add_argument("--include-reviewed", action="store_true", help="Include candidates already manually visual-reviewed.")
    parser.add_argument(
        "--all-unreviewed",
        action="store_true",
        help="Audit every question-review-unreviewed candidate, not only candidates preselected by visual cues.",
    )
    parser.add_argument("--include-manual-assets", action="store_true", help="Include candidates that already have manual visual assets.")
    parser.add_argument("--force", action="store_true", help="Include candidates that already have visual AI advisory labels.")
    parser.add_argument(
        "--ai-visual-status",
        choices=["", "visual_required_likely", "visual_not_required_likely", "visual_uncertain"],
        default="",
        help="Re-export candidates whose latest visual AI advisory has this status.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    return parser.parse_args()


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql_json_lines(sql: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        args.postgres_user,
        "-d",
        args.postgres_db,
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-c",
        sql,
    ]
    completed = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, check=True, capture_output=True)
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def build_sql(args: argparse.Namespace) -> str:
    clauses = []
    if not args.include_reviewed:
        clauses.append("visual_review_status NOT IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem')")
    if not args.include_manual_assets:
        clauses.append("NOT has_manual_asset")
    if not args.force and not args.ai_visual_status:
        clauses.append("NOT has_visual_ai_advisory")
    if args.ai_visual_status:
        clauses.append(f"latest_visual_ai_status = {sql_literal(args.ai_visual_status)}")
    if args.all_unreviewed:
        clauses.append("COALESCE(human_review_action, '') IN ('', 'unreviewed', 'reset_review')")
    if args.category:
        clauses.append(f"category = {sql_literal(args.category)}")
    if args.subject:
        clauses.append(f"subject = {sql_literal(args.subject)}")
    if args.year:
        clauses.append(f"year = {sql_literal(args.year)}")
    if args.ordinal:
        clauses.append(f"ordinal = {sql_literal(args.ordinal)}")

    source_clause = (
        "true"
        if args.all_unreviewed
        else {
            "all": "(has_visual_asset OR has_structured_table OR has_strong_visual_cue OR has_weak_visual_cue)",
            "existing_asset": "has_visual_asset",
            "structured_table": "has_structured_table",
            "strong_text_cue": "has_strong_visual_cue",
            "weak_text_cue": "has_weak_visual_cue",
            "missing_asset": "(has_strong_visual_cue OR has_weak_visual_cue) AND NOT has_visual_asset AND NOT has_structured_table",
        }[args.source]
    )
    clauses.append(source_clause)
    where = " AND ".join(clauses) if clauses else "true"
    limit = f"LIMIT {int(args.limit)}" if args.limit and args.limit > 0 else ""

    return f"""
WITH latest_question AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        corrected_candidate_json,
        event_json,
        notes,
        created_at,
        id
    FROM exam.question_review_events
    WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
    ORDER BY candidate_key, id DESC
),
latest_visual AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        corrected_candidate_json,
        event_json,
        notes,
        created_at,
        id
    FROM exam.question_review_events
    WHERE corrected_candidate_json ? 'visual_review'
    ORDER BY candidate_key, id DESC
),
latest_ai AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        audit_json,
        event_json,
        provider,
        model_name,
        prompt_version,
        created_at,
        id
    FROM exam.question_ai_review_events
    ORDER BY candidate_key, id DESC
),
base AS (
    SELECT
        c.candidate_key,
        c.question_number,
        c.stem_text,
        c.raw_candidate_json,
        c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb) AS effective_json,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') AS category,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
        COALESCE(c.raw_candidate_json->'metadata'->>'year', '') AS year,
        COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') AS ordinal,
        COALESCE(lv.corrected_candidate_json->>'visual_review', lq.corrected_candidate_json->>'visual_review', '') AS visual_review_status,
        lq.action AS human_review_action,
        lq.notes AS human_review_notes,
        lai.provider AS latest_ai_provider,
        lai.model_name AS latest_ai_model,
        lai.prompt_version AS latest_ai_prompt_version,
        COALESCE(lai.audit_json->>'visual_status', (
            SELECT label.value
            FROM jsonb_array_elements_text(COALESCE(lai.audit_json->'labels', '[]'::jsonb)) label(value)
            WHERE label.value IN ('visual_required_likely', 'visual_not_required_likely', 'visual_uncertain')
            LIMIT 1
        ), '') AS latest_visual_ai_status,
        COALESCE(lai.audit_json->>'visual_reason', lai.audit_json->>'summary', '') AS latest_visual_ai_reason,
        (
            jsonb_typeof(COALESCE(lai.audit_json->'labels', '[]'::jsonb)) = 'array'
            AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(COALESCE(lai.audit_json->'labels', '[]'::jsonb)) label(value)
                WHERE label.value IN ('visual_required_likely', 'visual_not_required_likely', 'visual_uncertain')
            )
        ) AS has_visual_ai_advisory,
        concat_ws(
            ' ',
            c.stem_text,
            c.raw_candidate_json->>'stem',
            c.raw_candidate_json->'metadata'->>'raw_block',
            lq.corrected_candidate_json->>'stem'
        ) AS visual_text,
        (
            CASE
                WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'image_refs') = 'array'
                    THEN jsonb_array_length((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'image_refs')
                ELSE 0
            END > 0
            OR CASE
                WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs') = 'array'
                    THEN jsonb_array_length((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs')
                ELSE 0
            END > 0
            OR jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'stem_image') = 'object'
            OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'options') = 'array'
                            THEN (c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'options'
                        ELSE '[]'::jsonb
                    END
                ) option_row(value)
                WHERE jsonb_typeof(option_row.value->'image') = 'object'
            )
        ) AS has_visual_asset,
        (
            position('<table' in lower(concat_ws(' ', c.raw_candidate_json->>'stem', lq.corrected_candidate_json->>'stem'))) > 0
            OR concat_ws(
                ' ',
                c.stem_text,
                c.raw_candidate_json->>'stem',
                c.raw_candidate_json->'metadata'->>'raw_block',
                lq.corrected_candidate_json->>'stem'
            ) ~* '{TABLE_DEPENDENCY_SQL_RE}'
        ) AS has_structured_table,
        concat_ws(
            ' ',
            c.stem_text,
            c.raw_candidate_json->>'stem',
            c.raw_candidate_json->'metadata'->>'raw_block',
            lq.corrected_candidate_json->>'stem'
        ) ~* '{STRONG_VISUAL_SQL_RE}' AS has_strong_visual_cue,
        concat_ws(
            ' ',
            c.stem_text,
            c.raw_candidate_json->>'stem',
            c.raw_candidate_json->'metadata'->>'raw_block',
            lq.corrected_candidate_json->>'stem'
        ) ~* '{WEAK_VISUAL_SQL_RE}' AS has_weak_visual_cue,
        EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'image_refs') = 'array'
                        THEN (c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'image_refs'
                    ELSE '[]'::jsonb
                END
            ) ref_row(value)
            WHERE ref_row.value->>'manual_asset' IN ('true', '1')
               OR COALESCE(ref_row.value->>'asset_role', ref_row.value->>'role', '') LIKE '%%manual%%'
               OR COALESCE(ref_row.value->>'asset_role', ref_row.value->>'role', '') = 'table_manual_screenshot'
        ) AS has_manual_asset
    FROM exam.question_candidates c
    LEFT JOIN latest_question lq ON lq.candidate_key = c.candidate_key
    LEFT JOIN latest_visual lv ON lv.candidate_key = c.candidate_key
    LEFT JOIN latest_ai lai ON lai.candidate_key = c.candidate_key
),
filtered AS (
    SELECT *
    FROM base
    WHERE {where}
    ORDER BY
        category,
        subject,
        CASE WHEN year ~ '^[0-9]+$' THEN year::integer ELSE 0 END DESC,
        CASE WHEN ordinal ~ '^[0-9]+$' THEN ordinal::integer ELSE 0 END DESC,
        CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END
    {limit}
)
SELECT jsonb_build_object(
    'candidate_key', candidate_key,
    -- jsonb text is canonical in PostgreSQL; this detects parser or human
    -- corrections made while a long advisory run is still in progress.
    'source_fingerprint', md5(effective_json::text),
    'category', category,
    'subject', subject,
    'year', year,
    'exam_ordinal', ordinal,
    'question_number', question_number,
    'stem', effective_json->>'stem',
    'options', COALESCE(effective_json->'options', '[]'::jsonb),
    'answer', effective_json->>'answer',
    'visual_candidate_source',
        ARRAY_REMOVE(ARRAY[
            CASE WHEN has_visual_asset THEN 'existing_asset' END,
            CASE WHEN has_structured_table THEN 'structured_table' END,
            CASE WHEN has_strong_visual_cue THEN 'strong_text_cue' END,
            CASE WHEN has_weak_visual_cue THEN 'weak_text_cue' END
        ], NULL),
    'has_visual_asset', has_visual_asset,
    'has_structured_table', has_structured_table,
    'has_strong_visual_cue', has_strong_visual_cue,
    'has_weak_visual_cue', has_weak_visual_cue,
    'visual_review_status', visual_review_status,
    'human_review_action', human_review_action,
    'human_review_notes', human_review_notes,
    'latest_visual_ai_status', latest_visual_ai_status,
    'latest_visual_ai_reason', latest_visual_ai_reason,
    'image_refs', COALESCE(effective_json->'image_refs', '[]'::jsonb),
    'stem_image', effective_json->'stem_image',
    'raw_block_excerpt', left(COALESCE(raw_candidate_json->'metadata'->>'raw_block', ''), 1800),
    'question_pdf_relative', raw_candidate_json->'metadata'->>'question_pdf_relative',
    'instruction', '判斷此題是否真的需要圖片/表格資產才能審題或作答；只輸出 advisory，不修改人工 visual_review。'
)::text
FROM filtered;
"""


def write_chunks(selected: list[dict[str, Any]], run_dir: Path, timestamp: str, chunk_size: int) -> tuple[list[Path], list[Path]]:
    if chunk_size and chunk_size > 0:
        chunk_dir = run_dir / "chunks"
        chunk_dir.mkdir()
        task_paths: list[Path] = []
        result_paths: list[Path] = []
        for index in range(0, len(selected), chunk_size):
            part = index // chunk_size + 1
            chunk = selected[index : index + chunk_size]
            task_path = chunk_dir / f"visual_ai_audit_tasks__{timestamp}__part{part:04d}.jsonl"
            result_path = chunk_dir / f"visual_ai_audit_results__{timestamp}__part{part:04d}.jsonl"
            with task_path.open("w", encoding="utf-8") as f:
                for item in chunk:
                    f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            task_paths.append(task_path)
            result_paths.append(result_path)
        return task_paths, result_paths

    task_path = run_dir / f"visual_ai_audit_tasks__{timestamp}.jsonl"
    result_path = run_dir / f"visual_ai_audit_results__{timestamp}.jsonl"
    with task_path.open("w", encoding="utf-8") as f:
        for item in selected:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    return [task_path], [result_path]


def category_path_segment(category: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff()（）_-]+", "_", category).strip("_")
    return value or "uncategorized"


def write_category_chunks(
    selected: list[dict[str, Any]],
    run_dir: Path,
    timestamp: str,
    chunk_size: int,
    *,
    split_by_category: bool,
) -> tuple[list[Path], list[Path], dict[str, int]]:
    if not split_by_category:
        tasks, results = write_chunks(selected, run_dir, timestamp, chunk_size)
        return tasks, results, {"all": len(selected)}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in selected:
        category = str(item.get("category") or "").strip() or "未分類"
        grouped.setdefault(category, []).append(item)
    task_paths: list[Path] = []
    result_paths: list[Path] = []
    for category, rows in grouped.items():
        category_dir = run_dir / f"category__{category_path_segment(category)}"
        category_dir.mkdir()
        tasks, results = write_chunks(rows, category_dir, timestamp, chunk_size)
        task_paths.extend(tasks)
        result_paths.extend(results)
    return task_paths, result_paths, {category: len(rows) for category, rows in grouped.items()}


def main() -> None:
    args = parse_args()
    selected = psql_json_lines(build_sql(args), args)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    task_paths, result_paths, category_counts = write_category_chunks(
        selected,
        run_dir,
        timestamp,
        args.chunk_size,
        split_by_category=args.split_by_category,
    )

    prompt_path = run_dir / "VISUAL_AI_AUDIT_PROMPT.md"
    prompt_path.write_text(
        "\n".join(
            [
                "# 圖片語意稽核任務",
                "",
                "逐行讀取 task JSONL，判斷題目是否真的需要圖片/表格資產。",
                "請只輸出 advisory AI labels，不要修改人工審核狀態。",
                "",
                "輸出 JSONL 每行格式：",
                "",
                "```json",
                '{"candidate_key":"...","visual_status":"visual_required_likely|visual_not_required_likely|visual_uncertain","confidence":0.0,"reason":"繁體中文理由","evidence":"觸發判斷的原文片段","recommended_human_action":"review_pdf_visual|confirm_no_visual_required|keep_visual_candidate"}',
                "```",
                "",
                "判斷原則：",
                "- 已有 MinerU 圖片或表格資產者，通常標 `visual_required_likely` 或 `visual_uncertain`，因為人工要確認裁切是否正確。",
                "- `<table>...</table>` 或明確指向表格的題目，應標 `visual_required_likely`：正式審核會以官方 PDF 表格截圖為顯示資產，並移除人工校正版題幹中的表格文字，避免雙重表格與亂碼。",
                "- `tablet`、`tablets`、`stable`、`metastable`、`tabletting`、`tablespoon` 等只是英文單字，絕不是表格線索；不得因含有 `table` 字串而標為需要圖片。",
                "- `如下`、`如圖`、`下圖`、`圖中`、`承上圖`、`箭頭所指`、`表中`、`下表` 等明確指向視覺物者，標 `visual_required_likely`。",
                "- 單純出現 `心電圖`、`X光`、`超音波`、`影像`、`箭頭形`、`圖示法`、`圖示說明`、`概念圖示` 等概念詞，但沒有指向圖像，標 `visual_not_required_likely`。",
                "- `承上題圖示`、`承上題的圖示`、`承上題，...圖示`、`承上圖所示` 代表依賴前題或題組圖片，標 `visual_required_likely`。",
                "- 分不清是否 PDF 另有圖時標 `visual_uncertain`，不要硬判。",
                "",
                f"任務檔：`{task_paths[0]}`" if len(task_paths) == 1 else f"任務切片根目錄：`{run_dir}`",
                f"預期輸出：`{result_paths[0]}`" if len(result_paths) == 1 else "每個 part 輸出同名 `visual_ai_audit_results__...__partXXXX.jsonl`。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_count": len(selected),
        "task_jsonl_files": [str(path) for path in task_paths],
        "expected_result_jsonl_files": [str(path) for path in result_paths],
        "prompt_md": str(prompt_path),
        "filters": {
            "category": args.category,
            "subject": args.subject,
            "year": args.year,
            "ordinal": args.ordinal,
            "source": args.source,
            "limit": args.limit,
            "chunk_size": args.chunk_size,
            "split_by_category": args.split_by_category,
            "include_reviewed": args.include_reviewed,
            "all_unreviewed": args.all_unreviewed,
            "include_manual_assets": args.include_manual_assets,
            "ai_visual_status": args.ai_visual_status,
            "force": args.force,
        },
        "category_counts": category_counts,
    }
    summary_path = run_dir / f"visual_ai_audit_summary__{timestamp}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
