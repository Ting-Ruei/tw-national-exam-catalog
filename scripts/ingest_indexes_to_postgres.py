#!/usr/bin/env python3
"""
Ingest local PDF indexes and a small MinerU output sample into PostgreSQL.

The script uses Docker Compose's PostgreSQL container and psql. It does not
require a Python database driver.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
REGISTRY_ROOT = ASSET_ROOT / "Registry"
PDF_INDEX_DIR = REGISTRY_ROOT / "pdf_indexes"
PAIR_INDEX_DIR = REGISTRY_ROOT / "paired_indexes"
MINERU_RUN_DIR = REGISTRY_ROOT / "mineru_runs"


PDF_FIELDS = [
    "group_name",
    "official_category_name",
    "normalized_category_name",
    "is_locked27",
    "manifest_timestamp",
    "manifest_path",
    "status",
    "year",
    "exam_ordinal",
    "exam_code",
    "category_code",
    "subject_code",
    "official_subject_name",
    "normalized_subject_name",
    "document_role",
    "source_url",
    "asset_path",
    "relative_asset_path",
    "bytes",
    "sha256",
    "registry_key",
    "has_collision_suffix",
    "notes",
]

PAIR_FIELDS = [
    "pair_key",
    "pair_status",
    "answer_role_primary",
    "question_registry_key",
    "answer_registry_key_primary",
    "answer_registry_key_ans",
    "answer_registry_key_mod",
    "notes",
]

MINERU_FIELDS = [
    "task_id",
    "status",
    "returncode",
    "elapsed_seconds",
    "md_count",
    "image_count",
    "pdf_path",
    "output_parent",
    "expected_md",
    "error_tail",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest catalog indexes into the local PostgreSQL dev DB.")
    parser.add_argument("--pdf-index", type=Path, default=latest_path(PDF_INDEX_DIR, "pdf_asset_index_detail__*.csv"))
    parser.add_argument("--pair-index", type=Path, default=latest_path(PAIR_INDEX_DIR, "question_answer_pairs_detail__*.csv"))
    parser.add_argument("--mineru-results", type=Path, default=latest_path(MINERU_RUN_DIR, "*/mineru_results__*.csv", required=False))
    parser.add_argument("--mineru-limit", type=int, default=2)
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    return parser.parse_args()


def latest_path(directory: Path, pattern: str, required: bool = True) -> Path | None:
    paths = sorted(directory.glob(pattern))
    if not paths:
        if required:
            raise SystemExit(f"No file found: {directory}/{pattern}")
        return None
    return paths[-1]


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


def read_rows(path: Path, fields: list[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    missing = [field for field in fields if field not in (rows[0].keys() if rows else [])]
    if missing:
        raise SystemExit(f"Missing fields in {path}: {missing}")
    return rows


def csv_text(rows: list[dict[str, str]], fields: list[str]) -> str:
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_to_project_or_asset(path: Path) -> str:
    resolved = path.resolve()
    for root in (PROJECT_ROOT.resolve(), ASSET_ROOT.resolve()):
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            pass
    return str(path)


def asset_row(asset_type: str, path: Path, asset_key_prefix: str) -> dict[str, object]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    stat = path.stat()
    digest = sha256_file(path)
    return {
        "asset_key": f"{asset_key_prefix}:{digest}",
        "asset_type": asset_type,
        "asset_path": str(path),
        "relative_asset_path": relative_to_project_or_asset(path),
        "sha256": digest,
        "bytes": stat.st_size,
        "mime_type": mime_type,
    }


def image_paths_for_mineru_result(row: dict[str, str]) -> list[Path]:
    output_parent = Path(row["output_parent"])
    stem = Path(row["pdf_path"]).stem
    output_dir = output_parent / stem
    if not output_dir.exists():
        return []
    return sorted(
        path for path in output_dir.glob("**/*")
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )


def mineru_sample_rows(path: Path | None, limit: int) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    if path is None or limit <= 0:
        return [], []
    rows = [row for row in read_rows(path, MINERU_FIELDS) if row["status"] == "ok" and Path(row["expected_md"]).exists()]
    rows = rows[:limit]

    asset_rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        md_path = Path(row["expected_md"])
        candidates = [("markdown", md_path, "mineru-md")]
        candidates.extend(("page_image", image, "mineru-image") for image in image_paths_for_mineru_result(row))
        for asset_type, path_item, prefix in candidates:
            record = asset_row(asset_type, path_item, prefix)
            if record["asset_key"] in seen:
                continue
            seen.add(str(record["asset_key"]))
            asset_rows.append(record)
    return rows, asset_rows


def create_staging(args: argparse.Namespace) -> None:
    psql(
        args,
        """
CREATE SCHEMA IF NOT EXISTS exam_staging;

CREATE TABLE IF NOT EXISTS exam_staging.pdf_asset_index (
    group_name TEXT,
    official_category_name TEXT,
    normalized_category_name TEXT,
    is_locked27 TEXT,
    manifest_timestamp TEXT,
    manifest_path TEXT,
    status TEXT,
    year TEXT,
    exam_ordinal TEXT,
    exam_code TEXT,
    category_code TEXT,
    subject_code TEXT,
    official_subject_name TEXT,
    normalized_subject_name TEXT,
    document_role TEXT,
    source_url TEXT,
    asset_path TEXT,
    relative_asset_path TEXT,
    bytes TEXT,
    sha256 TEXT,
    registry_key TEXT,
    has_collision_suffix TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.question_answer_pairs (
    pair_key TEXT,
    pair_status TEXT,
    answer_role_primary TEXT,
    question_registry_key TEXT,
    answer_registry_key_primary TEXT,
    answer_registry_key_ans TEXT,
    answer_registry_key_mod TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.mineru_results (
    task_id TEXT,
    status TEXT,
    returncode TEXT,
    elapsed_seconds TEXT,
    md_count TEXT,
    image_count TEXT,
    pdf_path TEXT,
    output_parent TEXT,
    expected_md TEXT,
    error_tail TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.mineru_assets (
    asset_key TEXT,
    asset_type TEXT,
    asset_path TEXT,
    relative_asset_path TEXT,
    sha256 TEXT,
    bytes TEXT,
    mime_type TEXT
);
""",
    )
    psql(args, "TRUNCATE exam_staging.pdf_asset_index, exam_staging.question_answer_pairs, exam_staging.mineru_results, exam_staging.mineru_assets;")


def copy_table(args: argparse.Namespace, table: str, rows: list[dict[str, object]], fields: list[str]) -> None:
    if not rows:
        return
    psql(args, stdin=f"\\copy {table} ({', '.join(fields)}) FROM STDIN WITH (FORMAT csv, HEADER true)\n" + csv_text(rows, fields))


def apply_upserts(args: argparse.Namespace) -> None:
    psql(
        args,
        """
INSERT INTO exam.source_systems (code, name, base_url, notes)
VALUES ('moex', '考選部歷年試題與解答查詢系統', 'https://wwwq.moex.gov.tw/', 'Imported from local catalog indexes')
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    base_url = EXCLUDED.base_url,
    notes = EXCLUDED.notes;

INSERT INTO exam.exam_sessions (source_system_id, exam_code, roc_year, exam_ordinal, exam_label, source_url)
SELECT DISTINCT
    ss.id,
    s.exam_code,
    NULLIF(s.year, '')::INTEGER,
    NULLIF(s.exam_ordinal, '')::INTEGER,
    concat('民國', s.year, '年 第', s.exam_ordinal, '次'),
    NULL
FROM exam_staging.pdf_asset_index s
JOIN exam.source_systems ss ON ss.code = 'moex'
WHERE s.exam_code <> ''
ON CONFLICT (source_system_id, exam_code) DO UPDATE
SET roc_year = EXCLUDED.roc_year,
    exam_ordinal = EXCLUDED.exam_ordinal,
    exam_label = EXCLUDED.exam_label;

INSERT INTO exam.categories (category_code, official_category_name, normalized_category_name, group_name, is_locked27, notes)
SELECT
    NULLIF(category_code, ''),
    official_category_name,
    max(normalized_category_name),
    max(group_name),
    bool_or(lower(is_locked27) IN ('yes', 'true', '1')),
    NULLIF(string_agg(DISTINCT NULLIF(notes, ''), ' | '), '')
FROM exam_staging.pdf_asset_index
WHERE category_code <> '' AND official_category_name <> ''
GROUP BY category_code, official_category_name
ON CONFLICT (category_code, official_category_name) DO UPDATE
SET normalized_category_name = EXCLUDED.normalized_category_name,
    group_name = EXCLUDED.group_name,
    is_locked27 = EXCLUDED.is_locked27,
    notes = EXCLUDED.notes;

INSERT INTO exam.subjects (category_id, subject_code, official_subject_name, normalized_subject_name, canonical_subject_name, notes)
SELECT
    c.id,
    s.subject_code,
    s.official_subject_name,
    max(s.normalized_subject_name),
    NULL,
    NULLIF(string_agg(DISTINCT NULLIF(s.notes, ''), ' | '), '')
FROM exam_staging.pdf_asset_index s
JOIN exam.categories c
  ON c.category_code = s.category_code
 AND c.official_category_name = s.official_category_name
WHERE s.subject_code <> '' AND s.official_subject_name <> ''
GROUP BY c.id, s.subject_code, s.official_subject_name
ON CONFLICT (category_id, subject_code, official_subject_name) DO UPDATE
SET normalized_subject_name = EXCLUDED.normalized_subject_name,
    notes = EXCLUDED.notes;

INSERT INTO exam.official_documents (
    registry_key,
    exam_session_id,
    category_id,
    subject_id,
    question_set,
    document_role,
    source_url,
    official_category_name,
    official_subject_name
)
SELECT
    s.registry_key,
    es.id,
    c.id,
    subj.id,
    '1',
    s.document_role,
    s.source_url,
    s.official_category_name,
    s.official_subject_name
FROM exam_staging.pdf_asset_index s
JOIN exam.source_systems ss ON ss.code = 'moex'
JOIN exam.exam_sessions es ON es.source_system_id = ss.id AND es.exam_code = s.exam_code
JOIN exam.categories c ON c.category_code = s.category_code AND c.official_category_name = s.official_category_name
JOIN exam.subjects subj ON subj.category_id = c.id AND subj.subject_code = s.subject_code AND subj.official_subject_name = s.official_subject_name
WHERE s.registry_key <> ''
ON CONFLICT (registry_key) DO UPDATE
SET exam_session_id = EXCLUDED.exam_session_id,
    category_id = EXCLUDED.category_id,
    subject_id = EXCLUDED.subject_id,
    document_role = EXCLUDED.document_role,
    source_url = EXCLUDED.source_url,
    official_category_name = EXCLUDED.official_category_name,
    official_subject_name = EXCLUDED.official_subject_name;

INSERT INTO exam.assets (asset_key, asset_type, storage_backend, asset_path, relative_asset_path, sha256, bytes, mime_type)
SELECT
    registry_key || ':pdf',
    'pdf',
    'filesystem',
    asset_path,
    relative_asset_path,
    NULLIF(sha256, ''),
    NULLIF(bytes, '')::BIGINT,
    'application/pdf'
FROM exam_staging.pdf_asset_index
WHERE registry_key <> ''
ON CONFLICT (asset_key) DO UPDATE
SET asset_path = EXCLUDED.asset_path,
    relative_asset_path = EXCLUDED.relative_asset_path,
    sha256 = EXCLUDED.sha256,
    bytes = EXCLUDED.bytes,
    mime_type = EXCLUDED.mime_type;

INSERT INTO exam.document_assets (official_document_id, asset_id, role)
SELECT od.id, a.id, 'primary_pdf'
FROM exam_staging.pdf_asset_index s
JOIN exam.official_documents od ON od.registry_key = s.registry_key
JOIN exam.assets a ON a.asset_key = s.registry_key || ':pdf'
ON CONFLICT (official_document_id, asset_id, role) DO NOTHING;

INSERT INTO exam.question_answer_document_pairs (
    pair_key,
    pair_status,
    question_document_id,
    primary_answer_document_id,
    ans_document_id,
    mod_document_id,
    notes
)
SELECT
    p.pair_key,
    p.pair_status,
    q.id,
    primary_answer.id,
    ans.id,
    mod.id,
    NULLIF(p.notes, '')
FROM exam_staging.question_answer_pairs p
JOIN exam.official_documents q ON q.registry_key = p.question_registry_key
LEFT JOIN exam.official_documents primary_answer ON primary_answer.registry_key = NULLIF(p.answer_registry_key_primary, '')
LEFT JOIN exam.official_documents ans ON ans.registry_key = NULLIF(p.answer_registry_key_ans, '')
LEFT JOIN exam.official_documents mod ON mod.registry_key = NULLIF(p.answer_registry_key_mod, '')
ON CONFLICT (pair_key) DO UPDATE
SET pair_status = EXCLUDED.pair_status,
    question_document_id = EXCLUDED.question_document_id,
    primary_answer_document_id = EXCLUDED.primary_answer_document_id,
    ans_document_id = EXCLUDED.ans_document_id,
    mod_document_id = EXCLUDED.mod_document_id,
    notes = EXCLUDED.notes;
""",
    )


def apply_mineru_sample(args: argparse.Namespace) -> None:
    psql(
        args,
        """
INSERT INTO exam.assets (asset_key, asset_type, storage_backend, asset_path, relative_asset_path, sha256, bytes, mime_type)
SELECT
    asset_key,
    asset_type,
    'filesystem',
    asset_path,
    relative_asset_path,
    NULLIF(sha256, ''),
    NULLIF(bytes, '')::BIGINT,
    NULLIF(mime_type, '')
FROM exam_staging.mineru_assets
ON CONFLICT (asset_key) DO UPDATE
SET asset_path = EXCLUDED.asset_path,
    relative_asset_path = EXCLUDED.relative_asset_path,
    sha256 = EXCLUDED.sha256,
    bytes = EXCLUDED.bytes,
    mime_type = EXCLUDED.mime_type;

INSERT INTO exam.document_assets (official_document_id, asset_id, role)
SELECT DISTINCT od.id, a.id, a.asset_type
FROM exam_staging.mineru_results r
JOIN exam.assets pdf_asset ON pdf_asset.asset_path = r.pdf_path AND pdf_asset.asset_type = 'pdf'
JOIN exam.document_assets pdf_link ON pdf_link.asset_id = pdf_asset.id AND pdf_link.role = 'primary_pdf'
JOIN exam.official_documents od ON od.id = pdf_link.official_document_id
JOIN exam_staging.mineru_assets ma
  ON ma.asset_path = r.expected_md
  OR ma.asset_path LIKE r.output_parent || '/' || regexp_replace(regexp_replace(r.pdf_path, '^.*/', ''), '\\.pdf$', '') || '/%'
JOIN exam.assets a ON a.asset_key = ma.asset_key
ON CONFLICT (official_document_id, asset_id, role) DO NOTHING;

INSERT INTO exam.mineru_runs (
    official_document_id,
    input_asset_id,
    run_status,
    mineru_version,
    output_root,
    output_manifest,
    error_message,
    finished_at
)
SELECT
    od.id,
    pdf_asset.id,
    CASE WHEN r.status = 'ok' THEN 'succeeded' ELSE 'failed' END,
    'MinerU 3.1.7',
    r.output_parent,
    jsonb_build_object(
        'task_id', r.task_id,
        'status', r.status,
        'returncode', NULLIF(r.returncode, '')::INTEGER,
        'elapsed_seconds', NULLIF(r.elapsed_seconds, '')::NUMERIC,
        'md_count', NULLIF(r.md_count, '')::INTEGER,
        'image_count', NULLIF(r.image_count, '')::INTEGER,
        'expected_md', r.expected_md
    ),
    NULLIF(r.error_tail, ''),
    now()
FROM exam_staging.mineru_results r
JOIN exam.assets pdf_asset ON pdf_asset.asset_path = r.pdf_path AND pdf_asset.asset_type = 'pdf'
JOIN exam.document_assets pdf_link ON pdf_link.asset_id = pdf_asset.id AND pdf_link.role = 'primary_pdf'
JOIN exam.official_documents od ON od.id = pdf_link.official_document_id
WHERE NOT EXISTS (
    SELECT 1
    FROM exam.mineru_runs existing
    WHERE existing.official_document_id = od.id
      AND existing.input_asset_id = pdf_asset.id
      AND existing.output_manifest->>'expected_md' = r.expected_md
);
""",
    )


def print_summary(args: argparse.Namespace) -> None:
    result = psql(
        args,
        """
SELECT 'source_systems' AS table_name, count(*) FROM exam.source_systems
UNION ALL SELECT 'exam_sessions', count(*) FROM exam.exam_sessions
UNION ALL SELECT 'categories', count(*) FROM exam.categories
UNION ALL SELECT 'subjects', count(*) FROM exam.subjects
UNION ALL SELECT 'official_documents', count(*) FROM exam.official_documents
UNION ALL SELECT 'assets', count(*) FROM exam.assets
UNION ALL SELECT 'document_assets', count(*) FROM exam.document_assets
UNION ALL SELECT 'question_answer_document_pairs', count(*) FROM exam.question_answer_document_pairs
UNION ALL SELECT 'mineru_runs', count(*) FROM exam.mineru_runs
ORDER BY table_name;
""",
    )
    print(result.stdout)


def main() -> None:
    args = parse_args()
    pdf_rows = read_rows(args.pdf_index, PDF_FIELDS)
    pair_rows_source = read_rows(args.pair_index, read_rows(args.pair_index, PAIR_FIELDS)[0].keys() if False else PAIR_FIELDS)
    pair_rows = [{field: row.get(field, "") for field in PAIR_FIELDS} for row in pair_rows_source]
    mineru_rows, mineru_assets = mineru_sample_rows(args.mineru_results, args.mineru_limit)

    create_staging(args)
    copy_table(args, "exam_staging.pdf_asset_index", pdf_rows, PDF_FIELDS)
    copy_table(args, "exam_staging.question_answer_pairs", pair_rows, PAIR_FIELDS)
    copy_table(args, "exam_staging.mineru_results", mineru_rows, MINERU_FIELDS)
    copy_table(args, "exam_staging.mineru_assets", mineru_assets, ["asset_key", "asset_type", "asset_path", "relative_asset_path", "sha256", "bytes", "mime_type"])
    apply_upserts(args)
    apply_mineru_sample(args)

    print(json.dumps({
        "pdf_index": str(args.pdf_index),
        "pdf_rows": len(pdf_rows),
        "pair_index": str(args.pair_index),
        "pair_rows": len(pair_rows),
        "mineru_results": str(args.mineru_results) if args.mineru_results else "",
        "mineru_sample_rows": len(mineru_rows),
        "mineru_sample_assets": len(mineru_assets),
    }, ensure_ascii=False, indent=2))
    print_summary(args)


if __name__ == "__main__":
    main()
