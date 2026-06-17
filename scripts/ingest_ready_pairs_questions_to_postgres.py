#!/usr/bin/env python3
"""
Batch-ingest all currently ready question/answer markdown pairs into PostgreSQL.

Ready means:
- question document has a markdown asset
- primary answer document has a markdown asset
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_ROOT = PROJECT_ROOT / "國考題資料夾" / "Registry"
LOG_DIR = REGISTRY_ROOT / "processing_logs"


def load_sample_module():
    script_path = PROJECT_ROOT / "scripts" / "ingest_sample_questions_to_postgres.py"
    spec = importlib.util.spec_from_file_location("sample_question_ingest", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch ingest ready parsed question/answer markdown pairs.")
    parser.add_argument("--postgres-db", default="tw_national_exam_dev")
    parser.add_argument("--postgres-user", default="national_exam")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def ready_pairs(module, db_args: argparse.Namespace, limit: int | None) -> list[dict[str, str]]:
    sql = """
WITH markdown_docs AS (
    SELECT DISTINCT od.id, od.registry_key
    FROM exam.official_documents od
    JOIN exam.document_assets da ON da.official_document_id = od.id
    JOIN exam.assets a ON a.id = da.asset_id
    WHERE a.asset_type = 'markdown'
)
SELECT
    q.registry_key AS question_registry_key,
    pa.registry_key AS primary_answer_registry_key
FROM exam.question_answer_document_pairs p
JOIN exam.official_documents q ON q.id = p.question_document_id
JOIN exam.official_documents pa ON pa.id = p.primary_answer_document_id
JOIN markdown_docs qmd ON qmd.id = q.id
JOIN markdown_docs amd ON amd.id = pa.id
ORDER BY q.registry_key;
"""
    output = module.scalar(db_args, sql)
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        q_key, a_key = line.split("|", 1)
        rows.append({
            "question_registry_key": q_key,
            "primary_answer_registry_key": a_key,
        })
    if limit:
        rows = rows[:limit]
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    module = load_sample_module()
    args = parse_args()
    args_obj = argparse.Namespace(
        postgres_db=args.postgres_db,
        postgres_user=args.postgres_user,
        question_registry_key="",
    )

    pairs = ready_pairs(module, args_obj, args.limit)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = LOG_DIR / f"question_ingest_batch__{stamp}"
    result_rows: list[dict[str, object]] = []

    for pair in pairs:
        q_key = pair["question_registry_key"]
        try:
            question_md_path = module.markdown_asset_path(args_obj, q_key)
            answer_registry_key, answer_md_path, is_correction = module.primary_answer_markdown_path(args_obj, q_key)
            question_md = question_md_path.read_text(encoding="utf-8")
            answer_md = answer_md_path.read_text(encoding="utf-8")
            questions = module.parse_questions(question_md)
            answers = module.parse_answers(answer_md)
            module.ingest_parsed(args_obj, q_key, answer_registry_key, is_correction, questions, answers)
            result_rows.append(
                {
                    "question_registry_key": q_key,
                    "answer_registry_key": answer_registry_key,
                    "is_correction": is_correction,
                    "status": "ok",
                    "parsed_questions": len(questions),
                    "parsed_options": sum(len(question.options) for question in questions),
                    "parsed_answers": len(answers),
                    "question_markdown": str(question_md_path),
                    "answer_markdown": str(answer_md_path),
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            result_rows.append(
                {
                    "question_registry_key": q_key,
                    "answer_registry_key": pair["primary_answer_registry_key"],
                    "is_correction": "",
                    "status": "error",
                    "parsed_questions": "",
                    "parsed_options": "",
                    "parsed_answers": "",
                    "question_markdown": "",
                    "answer_markdown": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    write_csv(run_dir / "ingest_ready_pairs_results.csv", result_rows)
    summary = {
        "run_dir": str(run_dir),
        "pair_count": len(pairs),
        "ok_count": sum(1 for row in result_rows if row["status"] == "ok"),
        "error_count": sum(1 for row in result_rows if row["status"] == "error"),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
