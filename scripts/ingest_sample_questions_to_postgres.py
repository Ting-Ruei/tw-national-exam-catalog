#!/usr/bin/env python3
"""
Parse and ingest one completed multiple-choice exam document into PostgreSQL.

This is intentionally conservative: it targets the standard MOEX markdown pattern
where question numbers start a line and options are marked as (A) ... (D).
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import subprocess
from dataclasses import dataclass
from io import StringIO
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTION_REGISTRY_KEY = "moex:101110:104:0201:1:question"


@dataclass(frozen=True)
class ParsedQuestion:
    number: str
    text: str
    options: dict[str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse and ingest one sample exam question set.")
    parser.add_argument("--question-registry-key", default=DEFAULT_QUESTION_REGISTRY_KEY)
    parser.add_argument("--postgres-db", default="tw_national_exam_dev")
    parser.add_argument("--postgres-user", default="national_exam")
    return parser.parse_args()


def psql(args: argparse.Namespace, sql: str | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
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
    ]
    if sql is not None:
        cmd.extend(["-c", sql])
    try:
        return subprocess.run(cmd, cwd=PROJECT_ROOT, input=stdin, text=True, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        print(exc.stdout)
        print(exc.stderr)
        raise


def scalar(args: argparse.Namespace, sql: str) -> str:
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
        "-t",
        "-A",
        "-c",
        sql,
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, check=True, capture_output=True)
    return result.stdout.strip()


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def csv_text(rows: list[dict[str, object]], fields: list[str]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def copy_table(args: argparse.Namespace, table: str, rows: list[dict[str, object]], fields: list[str]) -> None:
    if not rows:
        return
    psql(args, stdin=f"\\copy {table} ({', '.join(fields)}) FROM STDIN WITH (FORMAT csv, HEADER true)\n" + csv_text(rows, fields))


def markdown_asset_path(args: argparse.Namespace, registry_key: str) -> Path:
    sql = f"""
SELECT a.asset_path
FROM exam.official_documents od
JOIN exam.document_assets da ON da.official_document_id = od.id
JOIN exam.assets a ON a.id = da.asset_id
WHERE od.registry_key = {sql_quote(registry_key)}
  AND a.asset_type = 'markdown'
ORDER BY a.id
LIMIT 1;
"""
    path = scalar(args, sql)
    if not path:
        raise SystemExit(f"No markdown asset found for {registry_key}")
    return Path(path)


def primary_answer_markdown_path(args: argparse.Namespace, question_registry_key: str) -> tuple[str, Path, bool]:
    sql = f"""
SELECT answer_doc.registry_key || E'\\t' || a.asset_path || E'\\t' || (answer_doc.document_role = 'correction')::text
FROM exam.question_answer_document_pairs pair
JOIN exam.official_documents q ON q.id = pair.question_document_id
JOIN exam.official_documents answer_doc ON answer_doc.id = pair.primary_answer_document_id
JOIN exam.document_assets da ON da.official_document_id = answer_doc.id
JOIN exam.assets a ON a.id = da.asset_id
WHERE q.registry_key = {sql_quote(question_registry_key)}
  AND a.asset_type = 'markdown'
ORDER BY a.id
LIMIT 1;
"""
    row = scalar(args, sql)
    if not row:
        raise SystemExit(f"No primary answer markdown asset found for {question_registry_key}")
    registry_key, path, is_correction = row.split("\t", 2)
    return registry_key, Path(path), (is_correction.lower() == "t" or is_correction.lower() == "true")


def normalize_text(value: str) -> str:
    lines = [line.strip() for line in value.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def parse_question_block(number: str, body: str) -> ParsedQuestion:
    markers = list(re.finditer(r"(?m)(?<!\S)\(([A-D])\)\s*", body))
    if not markers:
        raise ValueError(f"Question {number} has no options")
    question_text = normalize_text(body[: markers[0].start()])
    options: dict[str, str] = {}
    for index, marker in enumerate(markers):
        label = marker.group(1)
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        options[label] = normalize_text(body[start:end])
    return ParsedQuestion(number=number, text=question_text, options=options)


def parse_questions(markdown: str) -> list[ParsedQuestion]:
    starts = list(re.finditer(r"(?m)^(\d{1,3})\s+(.+)$", markdown))
    questions: list[ParsedQuestion] = []
    for index, start in enumerate(starts):
        number = start.group(1)
        body_start = start.start(2)
        body_end = starts[index + 1].start() if index + 1 < len(starts) else len(markdown)
        body = markdown[body_start:body_end]
        if int(number) < 1:
            continue
        questions.append(parse_question_block(number, body))
    return questions


def table_cells(row_html: str) -> list[str]:
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.S | re.I)
    return [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in cells]


def parse_correction_notes(markdown: str) -> dict[str, list[str]]:
    notes: dict[str, list[str]] = {}
    for number, values in re.findall(r"第(\d+)題答([A-D/或]+)者均給分", markdown):
        notes[number] = [item for item in re.split(r"或|/", values) if item]
    return notes


def parse_answers(markdown: str) -> dict[str, dict[str, object]]:
    corrections = parse_correction_notes(markdown)
    answers: dict[str, dict[str, object]] = {}
    for table in re.findall(r"<table>(.*?)</table>", markdown, flags=re.S | re.I):
        rows = re.findall(r"<tr>(.*?)</tr>", table, flags=re.S | re.I)
        parsed_rows = [table_cells(row) for row in rows]
        question_numbers: list[str] | None = None
        answer_values: list[str] | None = None
        for row in parsed_rows:
            if not row:
                continue
            if row[0] == "題號":
                question_numbers = [cell for cell in row[1:] if cell]
            elif row[0] == "答案":
                answer_values = [cell for cell in row[1:] if cell]
        if not question_numbers or not answer_values:
            continue
        for number, answer in zip(question_numbers, answer_values):
            if answer == "#" and number in corrections:
                answers[str(int(number))] = {
                    "answer_value": "|".join(corrections[number]),
                    "accepted_values": corrections[number],
                    "raw_answer_value": answer,
                }
            else:
                answers[str(int(number))] = {
                    "answer_value": answer,
                    "accepted_values": [answer] if answer else [],
                    "raw_answer_value": answer,
                }
    return answers


def create_staging(args: argparse.Namespace) -> None:
    psql(
        args,
        """
CREATE SCHEMA IF NOT EXISTS exam_staging;

CREATE TABLE IF NOT EXISTS exam_staging.sample_questions (
    question_registry_key TEXT,
    question_number TEXT,
    question_key TEXT,
    question_text TEXT,
    question_json TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.sample_question_options (
    question_key TEXT,
    option_label TEXT,
    option_text TEXT,
    option_json TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.sample_answers (
    question_key TEXT,
    answer_source_registry_key TEXT,
    answer_value TEXT,
    answer_json TEXT,
    is_correction TEXT
);

TRUNCATE exam_staging.sample_questions, exam_staging.sample_question_options, exam_staging.sample_answers;
""",
    )


def ingest_parsed(
    args: argparse.Namespace,
    question_registry_key: str,
    answer_registry_key: str,
    is_correction: bool,
    questions: list[ParsedQuestion],
    answers: dict[str, dict[str, object]],
) -> None:
    question_rows: list[dict[str, object]] = []
    option_rows: list[dict[str, object]] = []
    answer_rows: list[dict[str, object]] = []
    for question in questions:
        question_key = f"{question_registry_key}:q{int(question.number):03d}"
        question_rows.append(
            {
                "question_registry_key": question_registry_key,
                "question_number": question.number,
                "question_key": question_key,
                "question_text": question.text,
                "question_json": json.dumps(
                    {
                        "parser": "moex_markdown_mcq_v0",
                        "option_labels": sorted(question.options),
                    },
                    ensure_ascii=False,
                ),
            }
        )
        for label, text in sorted(question.options.items()):
            option_rows.append(
                {
                    "question_key": question_key,
                    "option_label": label,
                    "option_text": text,
                    "option_json": json.dumps({"parser": "moex_markdown_mcq_v0"}, ensure_ascii=False),
                }
            )
        answer = answers.get(str(int(question.number)))
        if answer:
            answer_rows.append(
                {
                    "question_key": question_key,
                    "answer_source_registry_key": answer_registry_key,
                    "answer_value": answer["answer_value"],
                    "answer_json": json.dumps(answer, ensure_ascii=False),
                    "is_correction": "true" if is_correction else "false",
                }
            )

    create_staging(args)
    copy_table(args, "exam_staging.sample_questions", question_rows, ["question_registry_key", "question_number", "question_key", "question_text", "question_json"])
    copy_table(args, "exam_staging.sample_question_options", option_rows, ["question_key", "option_label", "option_text", "option_json"])
    copy_table(args, "exam_staging.sample_answers", answer_rows, ["question_key", "answer_source_registry_key", "answer_value", "answer_json", "is_correction"])

    psql(
        args,
        """
INSERT INTO exam.questions (
    official_document_id,
    question_key,
    question_number,
    question_text,
    question_json,
    parser_version,
    review_status
)
SELECT
    od.id,
    s.question_key,
    s.question_number,
    s.question_text,
    s.question_json::jsonb,
    'moex_markdown_mcq_v0',
    'parsed_sample'
FROM exam_staging.sample_questions s
JOIN exam.official_documents od ON od.registry_key = s.question_registry_key
ON CONFLICT (question_key) DO UPDATE
SET question_text = EXCLUDED.question_text,
    question_json = EXCLUDED.question_json,
    parser_version = EXCLUDED.parser_version,
    review_status = EXCLUDED.review_status;

INSERT INTO exam.question_options (question_id, option_label, option_text, option_json)
SELECT q.id, s.option_label, s.option_text, s.option_json::jsonb
FROM exam_staging.sample_question_options s
JOIN exam.questions q ON q.question_key = s.question_key
ON CONFLICT (question_id, option_label) DO UPDATE
SET option_text = EXCLUDED.option_text,
    option_json = EXCLUDED.option_json;

INSERT INTO exam.answers (
    question_id,
    answer_source_document_id,
    answer_value,
    answer_json,
    is_correction
)
SELECT
    q.id,
    answer_doc.id,
    s.answer_value,
    s.answer_json::jsonb,
    lower(s.is_correction) = 'true'
FROM exam_staging.sample_answers s
JOIN exam.questions q ON q.question_key = s.question_key
JOIN exam.official_documents answer_doc ON answer_doc.registry_key = s.answer_source_registry_key
WHERE NOT EXISTS (
    SELECT 1
    FROM exam.answers existing
    WHERE existing.question_id = q.id
      AND existing.answer_source_document_id = answer_doc.id
);
""",
    )


def print_summary(args: argparse.Namespace, question_registry_key: str) -> None:
    result = psql(
        args,
        f"""
SELECT
    count(DISTINCT q.id) AS questions,
    count(DISTINCT o.id) AS options,
    count(DISTINCT a.id) AS answers
FROM exam.questions q
LEFT JOIN exam.question_options o ON o.question_id = q.id
LEFT JOIN exam.answers a ON a.question_id = q.id
WHERE q.question_key LIKE {sql_quote(question_registry_key + ":%")};

SELECT q.question_number, q.question_text, o.option_label, o.option_text, a.answer_value, a.answer_json
FROM exam.questions q
JOIN exam.question_options o ON o.question_id = q.id
LEFT JOIN exam.answers a ON a.question_id = q.id
WHERE q.question_key LIKE {sql_quote(question_registry_key + ":%")}
  AND q.question_number IN ('1', '44')
ORDER BY q.question_number::int, o.option_label;
""",
    )
    print(result.stdout)


def main() -> None:
    args = parse_args()
    question_md_path = markdown_asset_path(args, args.question_registry_key)
    answer_registry_key, answer_md_path, is_correction = primary_answer_markdown_path(args, args.question_registry_key)
    question_md = question_md_path.read_text(encoding="utf-8")
    answer_md = answer_md_path.read_text(encoding="utf-8")
    questions = parse_questions(question_md)
    answers = parse_answers(answer_md)
    if len(questions) < 1:
        raise SystemExit("No questions parsed")
    if len(answers) < 1:
        raise SystemExit("No answers parsed")
    ingest_parsed(args, args.question_registry_key, answer_registry_key, is_correction, questions, answers)
    print(json.dumps({
        "question_registry_key": args.question_registry_key,
        "question_markdown": str(question_md_path),
        "answer_registry_key": answer_registry_key,
        "answer_markdown": str(answer_md_path),
        "is_correction": is_correction,
        "parsed_questions": len(questions),
        "parsed_options": sum(len(question.options) for question in questions),
        "parsed_answers": len(answers),
    }, ensure_ascii=False, indent=2))
    print_summary(args, args.question_registry_key)


if __name__ == "__main__":
    main()
