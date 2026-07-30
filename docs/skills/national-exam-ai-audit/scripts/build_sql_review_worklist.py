#!/usr/bin/env python3
"""Build SQL-first national-exam review inventory and model task packets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import psycopg


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "tmp" / "national_exam_ai_audit"
ANSWER_ISSUES = ("missing_answer", "missing_answer_markdown", "unexpected_answer_value")
HUMAN_TERMINAL = ("accept", "unblock", "block", "needs_review", "exclude", "reviewed", "correct")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("question", "image", "group", "answer"), default="question")
    parser.add_argument(
        "--policy",
        choices=("all", "risk", "unreviewed", "human_open", "accepted_drift"),
        default="risk",
    )
    parser.add_argument("--category")
    parser.add_argument("--subject")
    parser.add_argument("--year")
    parser.add_argument("--ordinal")
    parser.add_argument("--source-registry-key", help="Restrict a pilot to one source paper without changing its question order")
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--model", default="glm-5.2")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    return parser.parse_args()


def json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def effective_content_hash(candidate: dict[str, Any]) -> str:
    """Fingerprint the text the model is asked to review, not DB metadata."""
    visible = {
        "stem": candidate.get("stem", ""),
        "options": candidate.get("options") or [],
        "group_ref": candidate.get("group_ref"),
        "group_sequence_no": candidate.get("group_sequence_no"),
        "image_refs": candidate.get("image_refs") or [],
    }
    encoded = json.dumps(visible, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def batched(rows: Iterable[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def scope_sql(args: argparse.Namespace, alias: str = "c") -> tuple[str, list[str]]:
    clauses = ["COALESCE(lq.action, '') <> 'exclude'"]
    values: list[str] = []
    fields = {
        "category": "normalized_category_name",
        "subject": "normalized_subject_name",
        "year": "year",
        "ordinal": "exam_ordinal",
    }
    for name, metadata_field in fields.items():
        value = getattr(args, name)
        if not value:
            continue
        clauses.append(f"COALESCE({alias}.raw_candidate_json->'metadata'->>%s, '') = %s")
        values.extend([metadata_field, value])
    if args.source_registry_key:
        clauses.append(f"{alias}.source_registry_key = %s")
        values.append(args.source_registry_key)
    return " AND ".join(clauses), values


def base_cte(args: argparse.Namespace) -> tuple[str, list[str]]:
    scope, values = scope_sql(args)
    visual_ai = "(COALESCE(prompt_version, '') LIKE 'visual_%%' OR COALESCE(model_name, '') LIKE '%%visual%%' OR COALESCE(audit_json, '{}'::jsonb) ? 'visual_status' OR COALESCE(audit_json->>'stage', '') = 'image')"
    ai_stage_filter = visual_ai if args.stage == "image" else f"NOT {visual_ai}"
    cte = f"""
WITH latest_question AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key, action, corrected_candidate_json, notes, reviewer, created_at, id
    FROM exam.question_review_events
    WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
    ORDER BY candidate_key, id DESC
),
latest_visual AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key, corrected_candidate_json, created_at, id
    FROM exam.question_review_events
    WHERE corrected_candidate_json ? 'visual_review'
    ORDER BY candidate_key, id DESC
),
latest_answer AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key, action, corrected_answer_json, notes, created_at, id
    FROM exam.answer_review_events
    ORDER BY candidate_key, id DESC
),
latest_ai AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key, action, audit_status, recommended_action, audit_json, provider, model_name, prompt_version, created_at, id
    FROM exam.question_ai_review_events
    WHERE {ai_stage_filter}
    ORDER BY candidate_key, id DESC
),
issues AS (
    SELECT
        candidate_key,
        bool_or(severity IN ('blocked', 'error')) FILTER (WHERE issue_code <> ALL(%s)) AS hard_issue,
        bool_or(severity = 'warning') FILTER (WHERE issue_code <> ALL(%s)) AS warning_issue,
        jsonb_agg(
            jsonb_build_object('code', issue_code, 'severity', severity, 'message', message)
            ORDER BY id
        ) FILTER (WHERE issue_code <> ALL(%s)) AS issue_rows
    FROM exam.question_parse_issues
    WHERE resolved_at IS NULL
    GROUP BY candidate_key
),
base AS (
    SELECT
        c.candidate_key,
        c.source_registry_key,
        c.question_number,
        CASE
            WHEN COALESCE(c.raw_candidate_json->>'question_number_occurrence', '') ~ '^[0-9]+$'
                THEN (c.raw_candidate_json->>'question_number_occurrence')::integer
            ELSE 1
        END AS question_number_occurrence,
        c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb) AS candidate,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') AS category,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
        COALESCE(c.raw_candidate_json->'metadata'->>'year', '') AS year,
        COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') AS ordinal,
        lq.action AS human_action,
        lq.notes AS human_notes,
        lq.reviewer AS human_reviewer,
        lq.created_at AS human_at,
        la.action AS answer_action,
        la.corrected_answer_json,
        la.created_at AS answer_at,
        lai.action AS ai_action,
        CASE WHEN lai.action IN ('unreviewed', 'reset_review', 'reset_ai_review') THEN NULL ELSE lai.audit_status END AS ai_status,
        CASE WHEN lai.action IN ('unreviewed', 'reset_review', 'reset_ai_review') THEN NULL ELSE lai.recommended_action END AS ai_recommended_action,
        CASE WHEN lai.action IN ('unreviewed', 'reset_review', 'reset_ai_review') THEN NULL ELSE lai.audit_json END AS ai_audit,
        lai.provider AS ai_provider,
        lai.model_name AS ai_model,
        lai.prompt_version AS ai_prompt_version,
        lai.created_at AS ai_at,
        COALESCE(i.hard_issue, false) AS hard_issue,
        COALESCE(i.warning_issue, false) AS warning_issue,
        COALESCE(i.issue_rows, '[]'::jsonb) AS parser_issues,
        COALESCE(lv.corrected_candidate_json->>'visual_review', '') AS visual_review,
        (
            lai.action NOT IN ('unreviewed', 'reset_review', 'reset_ai_review')
            AND lq.action = ANY(%s)
            AND lq.created_at >= lai.created_at
        ) AS ai_superseded,
        (
            c.question_number ~ '^[0-9]+$'
            AND c.raw_candidate_json->'metadata'->>'year' = c.question_number
            AND length(COALESCE(c.raw_candidate_json->>'stem', '')) > 80
            AND COALESCE(c.raw_candidate_json->>'stem', '') ~ '(考試時間|類科名稱|科目名稱|座號|本試題)'
            AND CASE
                WHEN jsonb_typeof(c.raw_candidate_json->'options') = 'array'
                    THEN jsonb_array_length(c.raw_candidate_json->'options')
                ELSE 0
            END > 8
        ) AS exam_header_false_question,
        CASE
            WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'options') = 'array'
                THEN jsonb_array_length((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'options')
            ELSE 0
        END AS option_count
    FROM exam.question_candidates c
    LEFT JOIN latest_question lq USING (candidate_key)
    LEFT JOIN latest_visual lv USING (candidate_key)
    LEFT JOIN latest_answer la USING (candidate_key)
    LEFT JOIN latest_ai lai USING (candidate_key)
    LEFT JOIN issues i USING (candidate_key)
    WHERE {scope}
),
windowed AS (
    SELECT
        *,
        lag(jsonb_build_object('candidate_key', candidate_key, 'question_number', question_number, 'stem', left(COALESCE(candidate->>'stem', ''), 500)))
            OVER (PARTITION BY category, subject, year, ordinal, source_registry_key ORDER BY CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END, question_number_occurrence, candidate_key) AS previous_question,
        lead(jsonb_build_object('candidate_key', candidate_key, 'question_number', question_number, 'stem', left(COALESCE(candidate->>'stem', ''), 500)))
            OVER (PARTITION BY category, subject, year, ordinal, source_registry_key ORDER BY CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END, question_number_occurrence, candidate_key) AS next_question
    FROM base
)
"""
    return cte, [list(ANSWER_ISSUES), list(ANSWER_ISSUES), list(ANSWER_ISSUES), list(HUMAN_TERMINAL), *values]


def policy_clause(args: argparse.Namespace) -> str:
    active_ai_risk = "(ai_status IN ('needs_review', 'block') AND NOT COALESCE(ai_superseded, false))"
    policies = {
        "all": "true",
        "risk": f"(exam_header_false_question OR hard_issue OR warning_issue OR {active_ai_risk} OR human_action IN ('block', 'needs_review'))",
        "unreviewed": "COALESCE(human_action, '') IN ('', 'unreviewed', 'reset_review')",
        "human_open": "human_action IN ('block', 'needs_review')",
        "accepted_drift": f"(human_action IN ('accept', 'unblock') AND (exam_header_false_question OR hard_issue OR {active_ai_risk}))",
    }
    stage = {
        "question": "true",
        "image": "(visual_review = '' AND (COALESCE(candidate->'image_refs', '[]'::jsonb) <> '[]'::jsonb OR COALESCE(candidate->>'stem', '') ~ '(下圖|附圖|圖中|圖示|如圖|圖片|影像|照片|箭頭|表中|下表|附表)'))",
        "group": "(COALESCE(candidate->>'group_ref', '') <> '' OR COALESCE(candidate->>'stem', '') ~ '(回答(下列|以下).{0,8}題|承上題|呈上題|上題|前述)')",
        "answer": "human_action IN ('accept', 'unblock')",
    }
    return f"({policies[args.policy]}) AND ({stage[args.stage]})"


def inventory(conn: psycopg.Connection[Any], args: argparse.Namespace) -> dict[str, Any]:
    cte, values = base_cte(args)
    with conn.cursor() as cur:
        cur.execute(
            cte
            + """
SELECT
    category,
    subject,
    count(*) AS total,
    count(*) FILTER (WHERE COALESCE(human_action, '') IN ('', 'unreviewed', 'reset_review')) AS unreviewed,
    count(*) FILTER (WHERE human_action IN ('accept', 'unblock')) AS accepted,
    count(*) FILTER (WHERE human_action IN ('block', 'needs_review')) AS human_open,
    count(*) FILTER (WHERE exam_header_false_question) AS exam_header_false_question,
    count(*) FILTER (WHERE hard_issue OR warning_issue) AS parser_risk,
    count(*) FILTER (WHERE ai_status IN ('needs_review', 'block') AND NOT COALESCE(ai_superseded, false)) AS active_ai_risk,
    count(*) FILTER (WHERE COALESCE(ai_superseded, false)) AS stale_ai_suppressed,
    count(*) FILTER (
        WHERE COALESCE(human_action, '') IN ('', 'unreviewed', 'reset_review')
          AND NOT exam_header_false_question
          AND NOT hard_issue
          AND NOT warning_issue
          AND NOT COALESCE((ai_status IN ('needs_review', 'block') AND NOT COALESCE(ai_superseded, false)), false)
    ) AS low_risk_unreviewed
FROM windowed
GROUP BY category, subject
ORDER BY category, subject
""",
            values,
        )
        rows = [dict(zip([column.name for column in cur.description], row)) for row in cur.fetchall()]
    totals = {key: sum(int(row[key]) for row in rows) for key in rows[0] if key not in {"category", "subject"}} if rows else {}
    return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "scope": vars_for_manifest(args), "totals": totals, "subjects": rows}


def vars_for_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "stage": args.stage,
        "policy": args.policy,
        "category": args.category,
        "subject": args.subject,
        "year": args.year,
        "ordinal": args.ordinal,
        "source_registry_key": args.source_registry_key,
        "model": args.model,
    }


def task_rows(conn: psycopg.Connection[Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    cte, values = base_cte(args)
    limit_sql = " LIMIT %s" if args.limit > 0 else ""
    if args.limit > 0:
        values.append(args.limit)
    with conn.cursor() as cur:
        cur.execute(
            cte
            + f"""
SELECT
    candidate_key, source_registry_key, question_number, question_number_occurrence,
    category, subject, year, ordinal, candidate, option_count,
    human_action, human_notes, human_reviewer, human_at,
    answer_action, ai_action, ai_status, ai_recommended_action, ai_audit, ai_provider, ai_model,
    ai_prompt_version, ai_at, ai_superseded, parser_issues,
    exam_header_false_question, previous_question, next_question
FROM windowed
WHERE {policy_clause(args)}
ORDER BY
    exam_header_false_question DESC,
    hard_issue DESC,
    warning_issue DESC,
    category, subject,
    CASE WHEN year ~ '^[0-9]+$' THEN year::integer ELSE 0 END DESC,
    CASE WHEN ordinal ~ '^[0-9]+$' THEN ordinal::integer ELSE 0 END DESC,
    CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END,
    question_number_occurrence,
    candidate_key
{limit_sql}
""",
            values,
        )
        columns = [column.name for column in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    tasks: list[dict[str, Any]] = []
    for row in rows:
        candidate = json_value(row.pop("candidate")) or {}
        options = candidate.get("options") if isinstance(candidate, dict) else []
        task = {
            "candidate_key": row["candidate_key"],
            # Keep the original paper identifier at top level so downstream
            # dispatch can make half-paper and full-paper batches safely.
            "source_registry_key": row["source_registry_key"],
            "effective_content_hash": effective_content_hash(candidate),
            "stage": args.stage,
            "exam": {
                "category": row["category"],
                "subject": row["subject"],
                "year": row["year"],
                "ordinal": row["ordinal"],
                "question_number": row["question_number"],
                "occurrence": row["question_number_occurrence"],
            },
            "content": {
                "stem": candidate.get("stem", ""),
                "options": (options or [])[:8],
                "option_count": row["option_count"],
                "group_ref": candidate.get("group_ref"),
                "group_sequence_no": candidate.get("group_sequence_no"),
                "image_refs": candidate.get("image_refs") or [],
                "stem_image": candidate.get("stem_image"),
                "answer": candidate.get("answer"),
                "raw_block_excerpt": str((candidate.get("metadata") or {}).get("raw_block") or candidate.get("raw_block") or "")[:2500],
            },
            "neighbors": {
                "previous": json_value(row["previous_question"]),
                "next": json_value(row["next_question"]),
            },
            "sources": {
                key: value
                for key, value in (candidate.get("metadata") or {}).items()
                if key in {
                    "question_pdf_relative",
                    "question_markdown_relative",
                    "answer_pdf_primary_relative",
                    "answer_markdown_relative",
                }
            },
            "signals": {
                "parser_issues": json_value(row["parser_issues"]) or [],
                "exam_header_false_question": bool(row["exam_header_false_question"]),
                "previous_ai": {
                    "action": row["ai_action"],
                    "status": row["ai_status"],
                    "recommended_action": row["ai_recommended_action"],
                    "audit": json_value(row["ai_audit"]),
                    "provider": row["ai_provider"],
                    "model": row["ai_model"],
                    "prompt_version": row["ai_prompt_version"],
                    "superseded": bool(row["ai_superseded"]),
                },
            },
            "human_state": {
                "action": row["human_action"],
                "notes": row["human_notes"],
                "reviewer": row["human_reviewer"],
                "updated_at": row["human_at"].isoformat() if row["human_at"] else None,
            },
        }
        tasks.append(task)
    return tasks


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required; run inside the review-ui container or pass --database-url.")
    if args.chunk_size < 1 or args.chunk_size > 500:
        raise SystemExit("--chunk-size must be between 1 and 500")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = (args.output_dir or DEFAULT_OUTPUT_ROOT / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(args.database_url) as conn:
        inventory_value = inventory(conn, args)
        write_json(output_dir / "inventory.json", inventory_value)
        tasks = [] if args.inventory_only else task_rows(conn, args)
    task_files: list[str] = []
    for index, batch in enumerate(batched(tasks, args.chunk_size), start=1):
        path = output_dir / "tasks" / f"{args.stage}__part{index:04d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for task in batch:
                handle.write(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n")
        task_files.append(str(path.relative_to(output_dir)))
    manifest = {
        "schema_version": "national_exam_review_task_v1",
        "run_id": run_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": vars_for_manifest(args),
        "task_count": len(tasks),
        "chunk_size": args.chunk_size,
        "task_files": task_files,
        "skill_path": "docs/skills/national-exam-ai-audit/SKILL.md",
        "output_contract": "docs/skills/national-exam-ai-audit/references/output-schema.md",
        "source_priority": ["official_pdf", "human_correction", "mineru_layout", "mineru_markdown", "parsed_candidate"],
        "advisory_only": True,
    }
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps({"ok": True, "output_dir": str(output_dir), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
