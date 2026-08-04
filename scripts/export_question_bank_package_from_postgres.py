#!/usr/bin/env python3
"""
Export reviewed formal PostgreSQL questions as an AI Learning Platform package.

The source of truth is the SQL formal layer:
- exam.questions
- exam.question_options
- exam.answers
- exam.question_assets
- exam.question_groups

The JSONL files produced by this script are package-delivery files, not review
logs and not the Review UI backend.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT_ROOT = PROJECT_ROOT / "國考題資料夾" / "40_exports" / "question_bank_packages"
EXTERNAL_SOURCE = "tw-national-exam-catalog"
EXTERNAL_SCHEMA_VERSION = "postgres-formal-v1"
PACKAGE_SCHEMA_VERSION = "question-bank-data-package/2026.07"
ADAPTER_VERSION = "tw_catalog_postgres_package_exporter_v0.1"
VISUAL_DEPENDENCY_RE = re.compile(
    r"(下圖|附圖|圖中|圖示|如圖|圖片|影像|照片|箭頭|表中|下表|附表|心電圖|X\s*光|X光|超音波|切片圖|染色圖|鏡檢圖|尿沉渣圖|電泳圖|曲線圖|流程圖|家系圖)",
    re.I,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a reviewed SQL question-bank package.")
    parser.add_argument("--category", default="醫事檢驗師", help="Normalized category name to export.")
    parser.add_argument("--slug", default="medtech", help="Package and asset slug.")
    parser.add_argument("--package-version", default="", help="Defaults to tw-national-exam-<slug>-vYYYY.MM.DD.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_EXPORT_ROOT)
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--include-non-accepted", action="store_true")
    parser.add_argument("--force", action="store_true", help="Replace an existing package directory.")
    parser.add_argument("--no-copy-assets", action="store_true", help="Emit asset references but do not copy files.")
    return parser.parse_args()


def json_dump(value: Any, *, pretty: bool = False) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql_json_lines(args: argparse.Namespace, sql: str) -> list[dict[str, Any]]:
    docker = os.environ.get("DOCKER_BIN") or shutil.which("docker") or "/usr/local/bin/docker"
    cmd = [
        docker,
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
        "-P",
        "pager=off",
        "-At",
        "-c",
        sql,
    ]
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, check=True, capture_output=True)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(proc.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON from psql line {line_number}: {exc}") from exc
    return rows


def command_stdout(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, check=True, capture_output=True).stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sanitize_filename(value: str) -> str:
    value = value.replace(":", "_").replace("/", "_").replace("\\", "_")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value.strip("._") or "item"


def numeric_or_text(value: Any) -> int | str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text) if text.isdigit() else text


def answer_values(answer_value: Any, answer_json: dict[str, Any] | None) -> list[str]:
    if isinstance(answer_json, dict):
        accepted = answer_json.get("accepted_values")
        if isinstance(accepted, list) and accepted:
            return [str(item) for item in accepted if str(item).strip()]
        if answer_json.get("answer"):
            return [str(answer_json["answer"])]
    text = str(answer_value or "").strip()
    if not text:
        return []
    if "|" in text:
        return [part.strip() for part in text.split("|") if part.strip()]
    return [text]


def question_type(raw_type: str, answers: list[str], option_count: int) -> tuple[str, str | None]:
    if any("+" in answer for answer in answers):
        return "multiple_select", None
    if raw_type in {"", "multiple_choice", "single_choice"}:
        return "single_choice", None
    if raw_type == "unknown" and option_count >= 2 and answers:
        return "single_choice", None
    return raw_type, None


def source_path_key(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    if text.startswith(str(PROJECT_ROOT)):
        text = str(Path(text).relative_to(PROJECT_ROOT))
    return text


def resolve_source_path(path: Any) -> Path | None:
    text = str(path or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def image_ref_path(ref: Any) -> str:
    if isinstance(ref, str):
        return ref
    if not isinstance(ref, dict):
        return ""
    return str(ref.get("path_relative") or ref.get("relative_path") or ref.get("path") or ref.get("raw_ref") or "")


def relative_source_path(path: Any) -> str:
    key = source_path_key(path)
    if key:
        return key
    text = str(path or "").strip()
    if text.startswith(str(PROJECT_ROOT)):
        return str(Path(text).relative_to(PROJECT_ROOT))
    return text


def collect_asset_sources(row: dict[str, Any]) -> list[dict[str, Any]]:
    qjson = row.get("question_json") if isinstance(row.get("question_json"), dict) else {}
    collected: list[dict[str, Any]] = []

    def add(
        path: Any,
        role: str,
        option_key: str | None = None,
        asset_key: str | None = None,
        display_order: Any = None,
        asset_quality_status: Any = None,
        mime_type: Any = None,
        page_number: Any = None,
        bbox: Any = None,
        source_mineru_block_id: Any = None,
    ) -> None:
        key = source_path_key(path)
        if not key:
            return
        collected.append(
            {
                "source_key": key,
                "source_path": str(path),
                "role": role,
                "option_key": option_key,
                "asset_key": asset_key,
                "display_order": display_order,
                "asset_quality_status": asset_quality_status,
                "mime_type": mime_type,
                "page_number": page_number,
                "bbox": bbox,
                "source_mineru_block_id": source_mineru_block_id,
            }
        )

    stem_image = qjson.get("stem_image")
    if stem_image:
        add(image_ref_path(stem_image), "stem_figure")

    for option in qjson.get("options") or []:
        if not isinstance(option, dict):
            continue
        image = option.get("image")
        if image:
            add(image_ref_path(image), "option_image", str(option.get("key") or ""))

    for ref in qjson.get("image_refs") or []:
        add(image_ref_path(ref), "figure")

    for asset in row.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        add(
            asset.get("relative_asset_path") or asset.get("asset_path"),
            str(asset.get("role") or "figure"),
            None,
            str(asset.get("asset_key") or ""),
            asset.get("display_order"),
            asset.get("asset_quality_status"),
            asset.get("mime_type"),
            asset.get("page_number"),
            asset.get("bbox"),
            asset.get("source_mineru_block_id"),
        )

    by_source_key: dict[str, dict[str, Any]] = {}
    for item in collected:
        key = item["source_key"]
        if key not in by_source_key:
            by_source_key[key] = dict(item)
            continue
        existing = by_source_key[key]
        for merge_key, value in item.items():
            if existing.get(merge_key) in (None, "", []) and value not in (None, "", []):
                existing[merge_key] = value
    return list(by_source_key.values())


def package_asset_path(row: dict[str, Any], asset: dict[str, Any], index: int, slug: str) -> str:
    source = resolve_source_path(asset.get("source_path") or asset.get("source_key"))
    suffix = source.suffix.lower() if source and source.suffix else ".bin"
    year = str(row.get("roc_year") or "unknown")
    exam_number = str(row.get("exam_number") or "unknown")
    subject_code = sanitize_filename(str(row.get("subject_code") or "unknown"))
    qnum = sanitize_filename(str(row.get("question_number") or "q"))
    qkey = sanitize_filename(str(row.get("question_key") or qnum))
    role = sanitize_filename(str(asset.get("role") or "asset"))
    return f"assets/{slug}/{year}/exam-{exam_number}/{subject_code}/{qkey}__{role}_{index:03d}{suffix}"


def remap_image_ref(value: Any, path_map: dict[str, str]) -> str | None:
    path = image_ref_path(value)
    if not path:
        return None
    return path_map.get(source_path_key(path))


def cleaned_source_metadata(row: dict[str, Any], qjson: dict[str, Any], asset_records: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = qjson.get("metadata") if isinstance(qjson.get("metadata"), dict) else {}
    wanted_keys = [
        "exam_code",
        "category_code",
        "subject_code",
        "exam_ordinal",
        "year",
        "parser_version",
        "question_pdf_relative",
        "answer_pdf_primary_relative",
        "question_markdown_relative",
        "answer_markdown_relative",
        "answer_role_primary",
        "preflight_warnings",
    ]
    source_metadata = {key: metadata.get(key) for key in wanted_keys if metadata.get(key) not in (None, "")}
    visual_profile = build_visual_profile(row, asset_records)
    source_metadata.update(
        {
            "external_source": EXTERNAL_SOURCE,
            "external_schema_version": EXTERNAL_SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "external_registry_key": row.get("source_registry_key"),
            "external_question_key": row.get("question_key"),
            "exam_code": row.get("exam_code") or source_metadata.get("exam_code"),
            "category_code": row.get("category_code") or source_metadata.get("category_code"),
            "subject_code": row.get("subject_code") or source_metadata.get("subject_code"),
            "question_set": row.get("question_set"),
            "review_status": row.get("review_status"),
            "parser_version": row.get("parser_version") or source_metadata.get("parser_version"),
            "canonical_subject_name": row.get("canonical_subject_name"),
            "subject_mapping_note": row.get("subject_mapping_note"),
            "asset_refs": asset_records,
            "visual_profile": visual_profile,
            "feature_tags": visual_profile.get("feature_tags") or [],
        }
    )
    return {key: value for key, value in source_metadata.items() if value not in (None, "", [])}


def build_visual_profile(row: dict[str, Any], asset_records: list[dict[str, Any]]) -> dict[str, Any]:
    roles = sorted({str(item.get("role")) for item in asset_records if item.get("role")})
    quality_statuses = sorted({str(item.get("asset_quality_status")) for item in asset_records if item.get("asset_quality_status")})
    visual_review_status = str(row.get("visual_review_status") or "").strip()
    has_visual_asset = bool(asset_records)
    qjson = row.get("question_json") if isinstance(row.get("question_json"), dict) else {}
    metadata = qjson.get("metadata") if isinstance(qjson.get("metadata"), dict) else {}
    text = "\n".join([str(qjson.get("stem") or row.get("question_text") or ""), str(metadata.get("raw_block") or "")])
    no_visual_required = visual_review_status == "no_visual_required"
    has_visual_dependency = bool(VISUAL_DEPENDENCY_RE.search(text)) and not no_visual_required
    has_manual_asset = any(
        "manual" in str(item.get("role") or "").lower()
        or "manual_assets" in str(item.get("source_relative_path") or item.get("package_path") or "").lower()
        for item in asset_records
    )
    visual_reviewed = visual_review_status in {"no_visual_required", "visual_asset_ok", "visual_asset_problem"}

    tags: list[str] = []
    if has_visual_asset:
        tags.append("visual:has_asset")
    if has_visual_dependency:
        tags.append("visual:has_dependency")
    if has_visual_dependency and not has_visual_asset:
        tags.append("visual:needs_asset_review")
    if has_manual_asset:
        tags.append("visual:manual_asset")
    if no_visual_required:
        tags.append("visual:no_visual_required")
    if visual_reviewed:
        tags.append("visual:reviewed")
    elif has_visual_asset or has_visual_dependency:
        tags.append("visual:unreviewed")
    if visual_review_status:
        tags.append(f"visual_review:{visual_review_status}")
    for role in roles:
        tags.append(f"visual_role:{role}")
    if quality_statuses:
        tags.extend(f"visual_asset_quality:{status}" for status in quality_statuses)

    return {
        "has_visual_asset": has_visual_asset,
        "visual_asset_count": len(asset_records),
        "has_visual_dependency": has_visual_dependency,
        "needs_visual_asset_review": has_visual_dependency and not has_visual_asset,
        "has_manual_asset": has_manual_asset,
        "no_visual_required": no_visual_required,
        "visual_review_status": visual_review_status or None,
        "visual_reviewed": visual_reviewed,
        "asset_roles": roles,
        "visual_asset_roles": roles,
        "asset_quality_statuses": quality_statuses,
        "asset_count": len(asset_records),
        "feature_tags": tags,
    }


def build_question_record(
    row: dict[str, Any],
    package_version: str,
    slug: str,
    package_dir: Path,
    copy_assets: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    qjson = row.get("question_json") if isinstance(row.get("question_json"), dict) else {}
    raw_qjson = row.get("question_raw_json") if isinstance(row.get("question_raw_json"), dict) else {}
    formal_options = row.get("options") if isinstance(row.get("options"), list) else []
    assets = collect_asset_sources(row)
    path_map: dict[str, str] = {}
    asset_records: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for index, asset in enumerate(assets, start=1):
        package_path = package_asset_path(row, asset, index, slug)
        source = resolve_source_path(asset.get("source_path") or asset.get("source_key"))
        record = {
            "source_question_key": row.get("question_key"),
            "source_asset_key": asset.get("asset_key"),
            "source_relative_path": relative_source_path(asset.get("source_path") or asset.get("source_key")),
            "package_path": package_path,
            "role": asset.get("role"),
            "option_key": asset.get("option_key"),
            "display_order": asset.get("display_order"),
            "asset_quality_status": asset.get("asset_quality_status"),
            "mime_type": asset.get("mime_type"),
            "page_number": asset.get("page_number"),
            "bbox": asset.get("bbox"),
            "source_mineru_block_id": asset.get("source_mineru_block_id"),
        }
        if source and source.exists() and source.is_file():
            if copy_assets:
                destination = package_dir / package_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            record["sha256"] = sha256_file(source)
            record["bytes"] = source.stat().st_size
            path_map[asset["source_key"]] = package_path
        else:
            record["missing"] = True
            warnings.append(
                {
                    "source_question_key": row.get("question_key"),
                    "source_relative_path": record["source_relative_path"],
                    "warning": "asset_source_missing",
                }
            )
        asset_records.append(record)

    options: list[dict[str, Any]] = []
    source_options = formal_options or qjson.get("options") or []
    for option in source_options:
        if not isinstance(option, dict):
            continue
        option_json = option.get("option_json") if isinstance(option.get("option_json"), dict) else option
        option_image = remap_image_ref(option_json.get("image"), path_map) if option_json.get("image") else None
        options.append(
            {
                "key": str(option.get("key") or option.get("label") or option.get("option_label") or "").strip(),
                "text": option.get("text") or option.get("option_text") or option_json.get("text") or "",
                "image": option_image,
                "explanation": option.get("explanation") or option_json.get("explanation"),
                "explanation_image": remap_image_ref(option_json.get("explanation_image"), path_map)
                if option_json.get("explanation_image")
                else None,
            }
        )

    answer_list = answer_values(row.get("answer_value"), row.get("answer_json"))
    exported_question_type, type_warning = question_type(str(raw_qjson.get("question_type") or ""), answer_list, len(options))
    stem_image = remap_image_ref(qjson.get("stem_image"), path_map) if qjson.get("stem_image") else None
    if not stem_image:
        for asset in asset_records:
            if asset.get("package_path") and asset.get("role") in {"stem_figure", "table_manual_screenshot", "table", "figure"}:
                stem_image = asset["package_path"]
                break

    canonical_payload = {
        "stem": qjson.get("stem") or row.get("question_text") or "",
        "options": options,
        "answer": answer_list,
        "explanation": qjson.get("explanation"),
        "stem_image": stem_image,
        "group_ref": row.get("formal_group_ref") or None,
    }
    content_hash = sha256_text(json_dump(canonical_payload))
    metadata = cleaned_source_metadata(row, qjson, asset_records)
    visual_profile = metadata.get("visual_profile") if isinstance(metadata.get("visual_profile"), dict) else build_visual_profile(row, asset_records)
    feature_tags = list(visual_profile.get("feature_tags") or [])
    if type_warning:
        metadata.setdefault("export_warnings", []).append(
            {
                "code": type_warning,
                "raw_question_type": raw_qjson.get("question_type"),
                "exported_question_type": exported_question_type,
            }
        )
    record = {
        "external_source": EXTERNAL_SOURCE,
        "external_schema_version": EXTERNAL_SCHEMA_VERSION,
        "package_version": package_version,
        "source_question_key": row.get("question_key"),
        "source_registry_key": row.get("source_registry_key"),
        "official_category_name": row.get("official_category_name"),
        "normalized_category_name": row.get("normalized_category_name"),
        "official_subject_name": row.get("official_subject_name"),
        "normalized_subject_name": row.get("normalized_subject_name"),
        "roc_year": numeric_or_text(row.get("roc_year")),
        "exam_number": numeric_or_text(row.get("exam_number")),
        "question_number": numeric_or_text(row.get("question_number")),
        "question_type": exported_question_type,
        "stem": canonical_payload["stem"],
        "stem_image": stem_image,
        "options": options,
        "answer": answer_list,
        "explanation": canonical_payload["explanation"],
        "group_ref": canonical_payload["group_ref"],
        "visual_profile": visual_profile,
        "feature_tags": feature_tags,
        "metadata": metadata,
    }
    record["metadata"]["source_content_hash"] = content_hash
    if row.get("canonical_subject_name"):
        record["metadata"]["canonical_subject_name"] = row["canonical_subject_name"]
    return record, asset_records, warnings


def question_sql(args: argparse.Namespace) -> str:
    review_filter = "" if args.include_non_accepted else "AND q.review_status = 'accepted'"
    return f"""
SELECT jsonb_build_object(
    'question_key', q.question_key,
    'question_number', q.question_number,
    'question_text', q.question_text,
    'question_json', q.question_json,
    'question_raw_json', q.question_raw_json,
    'parser_version', q.parser_version,
    'review_status', q.review_status,
    'answer_value', ans.answer_value,
    'answer_json', ans.answer_json,
    'is_correction', ans.is_correction,
    'answer_source_registry_key', ans_od.registry_key,
    'formal_group_ref', g.group_key,
    'options', COALESCE((
        SELECT jsonb_agg(
            jsonb_build_object(
                'option_label', opt.option_label,
                'option_text', opt.option_text,
                'option_json', opt.option_json
            )
            ORDER BY opt.option_label
        )
        FROM exam.question_options opt
        WHERE opt.question_id = q.id
    ), '[]'::jsonb),
    'source_registry_key', od.registry_key,
    'question_set', od.question_set,
    'roc_year', es.roc_year,
    'exam_number', es.exam_ordinal,
    'exam_code', es.exam_code,
    'category_code', c.category_code,
    'subject_code', s.subject_code,
    'official_category_name', od.official_category_name,
    'normalized_category_name', c.normalized_category_name,
    'official_subject_name', od.official_subject_name,
    'normalized_subject_name', s.normalized_subject_name,
    'canonical_subject_name', s.canonical_subject_name,
    'subject_mapping_note', csm.change_note,
    'assets', COALESCE((
        SELECT jsonb_agg(
            jsonb_build_object(
                'asset_key', a.asset_key,
                'asset_type', a.asset_type,
                'asset_path', a.asset_path,
                'relative_asset_path', a.relative_asset_path,
                'mime_type', a.mime_type,
                'sha256', a.sha256,
                'bytes', a.bytes,
                'role', qa.role,
                'display_order', qa.display_order,
                'asset_quality_status', qa.asset_quality_status,
                'page_number', qa.page_number,
                'bbox', qa.bbox,
                'source_mineru_block_id', qa.source_mineru_block_id
            )
            ORDER BY qa.display_order NULLS LAST, a.asset_key
        )
        FROM exam.question_assets qa
        JOIN exam.assets a ON a.id = qa.asset_id
        WHERE qa.question_id = q.id
    ), '[]'::jsonb),
    'visual_review_status', NULLIF(COALESCE(q.human_corrected_json->>'visual_review', q.question_json->>'visual_review', ''), '')
)
FROM exam.questions q
JOIN exam.official_documents od ON od.id = q.official_document_id
JOIN exam.exam_sessions es ON es.id = od.exam_session_id
JOIN exam.categories c ON c.id = od.category_id
JOIN exam.subjects s ON s.id = od.subject_id
LEFT JOIN exam.question_groups g ON g.id = q.question_group_id
LEFT JOIN LATERAL (
    SELECT *
    FROM exam.answers item
    WHERE item.question_id = q.id
    ORDER BY item.id DESC
    LIMIT 1
) ans ON true
LEFT JOIN exam.official_documents ans_od ON ans_od.id = ans.answer_source_document_id
LEFT JOIN exam.canonical_subject_mappings csm
    ON csm.category_group_name = c.group_name
    AND csm.official_category_name = c.official_category_name
    AND csm.official_subject_name = s.official_subject_name
    AND csm.canonical_subject_name = s.canonical_subject_name
WHERE c.normalized_category_name = {sql_literal(args.category)}
{review_filter}
ORDER BY
    es.roc_year,
    es.exam_ordinal NULLS LAST,
    s.subject_code,
    CASE WHEN q.question_number ~ '^[0-9]+$' THEN q.question_number::integer ELSE NULL END NULLS LAST,
    q.question_key
"""


def subject_sql(args: argparse.Namespace) -> str:
    review_filter = "" if args.include_non_accepted else "AND q.review_status = 'accepted'"
    return f"""
SELECT jsonb_build_object(
    'category_code', c.category_code,
    'official_category_name', c.official_category_name,
    'normalized_category_name', c.normalized_category_name,
    'group_name', c.group_name,
    'subject_code', s.subject_code,
    'official_subject_name', s.official_subject_name,
    'normalized_subject_name', s.normalized_subject_name,
    'canonical_subject_name', s.canonical_subject_name,
    'subject_mapping_note', csm.change_note,
    'roc_year_min', min(es.roc_year),
    'roc_year_max', max(es.roc_year),
    'question_count', count(*)
)
FROM exam.questions q
JOIN exam.official_documents od ON od.id = q.official_document_id
JOIN exam.exam_sessions es ON es.id = od.exam_session_id
JOIN exam.categories c ON c.id = od.category_id
JOIN exam.subjects s ON s.id = od.subject_id
LEFT JOIN exam.canonical_subject_mappings csm
    ON csm.category_group_name = c.group_name
    AND csm.official_category_name = c.official_category_name
    AND csm.official_subject_name = s.official_subject_name
    AND csm.canonical_subject_name = s.canonical_subject_name
WHERE c.normalized_category_name = {sql_literal(args.category)}
{review_filter}
GROUP BY
    c.category_code,
    c.official_category_name,
    c.normalized_category_name,
    c.group_name,
    s.subject_code,
    s.official_subject_name,
    s.normalized_subject_name,
    s.canonical_subject_name,
    csm.change_note
ORDER BY s.subject_code, s.official_subject_name
"""


def group_sql(args: argparse.Namespace) -> str:
    review_filter = "" if args.include_non_accepted else "AND q.review_status = 'accepted'"
    return f"""
SELECT jsonb_build_object(
    'group_ref', g.group_key,
    'source_registry_keys', jsonb_agg(DISTINCT od.registry_key),
    'official_category_name', min(od.official_category_name),
    'normalized_category_name', min(c.normalized_category_name),
    'official_subject_names', jsonb_agg(DISTINCT od.official_subject_name),
    'normalized_subject_names', jsonb_agg(DISTINCT s.normalized_subject_name),
    'shared_stem_text', g.shared_stem_text,
    'shared_stem_json', g.shared_stem_json,
    'display_markup_json', g.display_markup_json,
    'group_question_range', g.group_question_range,
    'member_source_question_keys', COALESCE(jsonb_agg(q.question_key ORDER BY q.question_key), '[]'::jsonb),
    'metadata', jsonb_build_object(
        'external_source', {sql_literal(EXTERNAL_SOURCE)},
        'external_schema_version', {sql_literal(EXTERNAL_SCHEMA_VERSION)},
        'adapter_version', {sql_literal(ADAPTER_VERSION)},
        'source', 'exam_question_groups',
        'review_status', g.review_status
    )
)
FROM exam.questions q
JOIN exam.question_groups g ON g.id = q.question_group_id
JOIN exam.official_documents od ON od.id = q.official_document_id
JOIN exam.categories c ON c.id = od.category_id
JOIN exam.subjects s ON s.id = od.subject_id
WHERE c.normalized_category_name = {sql_literal(args.category)}
{review_filter}
GROUP BY g.id, g.group_key, g.shared_stem_text, g.shared_stem_json, g.display_markup_json, g.group_question_range, g.review_status
ORDER BY g.group_key
"""


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json_dump(row))
            f.write("\n")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json_dump(value, pretty=True) + "\n", encoding="utf-8")


def file_info(path: Path, package_dir: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(package_dir)),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def visual_summary(question_records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "questions_with_visual_asset": 0,
        "questions_with_visual_dependency": 0,
        "questions_needing_visual_asset_review": 0,
        "questions_with_manual_asset": 0,
        "questions_visual_reviewed": 0,
        "visual_asset_count": 0,
        "feature_tag_counts": {},
        "asset_role_counts": {},
        "asset_quality_status_counts": {},
    }
    feature_tag_counts: dict[str, int] = {}
    asset_role_counts: dict[str, int] = {}
    asset_quality_status_counts: dict[str, int] = {}
    for record in question_records:
        profile = record.get("visual_profile") if isinstance(record.get("visual_profile"), dict) else {}
        if profile.get("has_visual_asset"):
            summary["questions_with_visual_asset"] += 1
        if profile.get("has_visual_dependency"):
            summary["questions_with_visual_dependency"] += 1
        if profile.get("needs_visual_asset_review"):
            summary["questions_needing_visual_asset_review"] += 1
        if profile.get("has_manual_asset"):
            summary["questions_with_manual_asset"] += 1
        if profile.get("visual_reviewed"):
            summary["questions_visual_reviewed"] += 1
        summary["visual_asset_count"] += int(profile.get("visual_asset_count") or profile.get("asset_count") or 0)
        for tag in record.get("feature_tags") or []:
            feature_tag_counts[str(tag)] = feature_tag_counts.get(str(tag), 0) + 1
        for role in profile.get("asset_roles") or []:
            asset_role_counts[str(role)] = asset_role_counts.get(str(role), 0) + 1
        for status in profile.get("asset_quality_statuses") or []:
            asset_quality_status_counts[str(status)] = asset_quality_status_counts.get(str(status), 0) + 1
    summary["feature_tag_counts"] = dict(sorted(feature_tag_counts.items()))
    summary["asset_role_counts"] = dict(sorted(asset_role_counts.items()))
    summary["asset_quality_status_counts"] = dict(sorted(asset_quality_status_counts.items()))
    return summary


def main() -> None:
    args = parse_args()
    date_version = datetime.now().strftime("%Y.%m.%d")
    package_version = args.package_version or f"tw-national-exam-{args.slug}-v{date_version}"
    package_dir = args.output_root / package_version
    if package_dir.exists():
        if not args.force:
            raise SystemExit(f"Package directory exists, pass --force to replace: {package_dir}")
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    questions_raw = psql_json_lines(args, question_sql(args))
    subjects = psql_json_lines(args, subject_sql(args))
    groups = psql_json_lines(args, group_sql(args))
    if not questions_raw:
        raise SystemExit(f"No questions found for category: {args.category}")

    question_records: list[dict[str, Any]] = []
    asset_records: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for row in questions_raw:
        record, assets, row_warnings = build_question_record(
            row,
            package_version,
            args.slug,
            package_dir,
            copy_assets=not args.no_copy_assets,
        )
        question_records.append(record)
        asset_records.extend(assets)
        warnings.extend(row_warnings)

    subjects_doc = {
        "external_source": EXTERNAL_SOURCE,
        "external_schema_version": EXTERNAL_SCHEMA_VERSION,
        "package_version": package_version,
        "category": args.category,
        "mapping_policy": {
            "canonical_subject_source": "exam.subjects.canonical_subject_name",
            "official_name_policy": "official_subject_name and normalized_subject_name are preserved as source lineage.",
            "historical_name_policy": "older medical technologist subject names are mapped to current canonical subject names when they represent official historical naming or exam-outline evolution.",
        },
        "normalized_subject_count": len({item.get("normalized_subject_name") for item in subjects if item.get("normalized_subject_name")}),
        "canonical_subject_count": len({item.get("canonical_subject_name") for item in subjects if item.get("canonical_subject_name")}),
        "canonical_subjects": sorted({item.get("canonical_subject_name") for item in subjects if item.get("canonical_subject_name")}),
        "subjects": subjects,
    }

    questions_path = package_dir / "questions.jsonl"
    groups_path = package_dir / "groups.jsonl"
    subjects_path = package_dir / "subjects.json"
    asset_manifest_path = package_dir / "asset_manifest.jsonl"
    warnings_path = package_dir / "warnings.jsonl"
    manifest_path = package_dir / "manifest.json"

    write_jsonl(questions_path, question_records)
    write_jsonl(groups_path, groups)
    write_json(subjects_path, subjects_doc)
    write_jsonl(asset_manifest_path, asset_records)
    if warnings:
        write_jsonl(warnings_path, warnings)

    git_commit = command_stdout(["git", "rev-parse", "HEAD"])
    git_dirty = bool(command_stdout(["git", "status", "--porcelain"]))
    asset_files = sorted(path for path in (package_dir / "assets").rglob("*") if path.is_file()) if (package_dir / "assets").exists() else []
    visual_stats = visual_summary(question_records)
    manifest = {
        "package_version": package_version,
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "external_source": EXTERNAL_SOURCE,
        "external_schema_version": EXTERNAL_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_project": {
            "repository": PROJECT_ROOT.name,
            "git_commit": git_commit or None,
            "git_dirty": git_dirty,
        },
        "source_database": {
            "postgres_db": args.postgres_db,
            "compose_service": "postgres",
            "formal_tables": [
                "exam.questions",
                "exam.question_options",
                "exam.answers",
                "exam.question_assets",
                "exam.question_groups",
            ],
            "group_export_policy": "groups.jsonl and question group_ref are exported only from confirmed formal question_group_id links to exam.question_groups.",
            "visual_export_policy": "questions.jsonl exports top-level visual_profile and feature_tags derived from formal question_assets, asset_quality_status, visual_review, and visual dependency text markers; asset_manifest.jsonl is the package asset copy plan.",
        },
        "category": {
            "normalized_category_name": args.category,
            "slug": args.slug,
        },
        "counts": {
            "questions": len(question_records),
            "subjects": len(subjects),
            "groups": len(groups),
            "asset_references": len(asset_records),
            "asset_files": len(asset_files),
            "warnings": len(warnings),
        },
        "visual_summary": visual_stats,
        "files": {
            "questions": file_info(questions_path, package_dir),
            "subjects": file_info(subjects_path, package_dir),
            "groups": file_info(groups_path, package_dir),
            "asset_manifest": file_info(asset_manifest_path, package_dir),
            "warnings": file_info(warnings_path, package_dir) if warnings else None,
        },
    }
    manifest["package_content_sha256"] = sha256_text(
        json_dump(
            {
                "questions": manifest["files"]["questions"]["sha256"],
                "subjects": manifest["files"]["subjects"]["sha256"],
                "groups": manifest["files"]["groups"]["sha256"],
                "asset_manifest": manifest["files"]["asset_manifest"]["sha256"],
                "asset_files": [sha256_file(path) for path in asset_files],
            }
        )
    )
    write_json(manifest_path, manifest)

    print(
        json_dump(
            {
                "package_dir": str(package_dir),
                "package_version": package_version,
                "questions": len(question_records),
                "subjects": len(subjects),
                "groups": len(groups),
                "asset_references": len(asset_records),
                "asset_files": len(asset_files),
                "warnings": len(warnings),
            },
            pretty=True,
        )
    )


if __name__ == "__main__":
    main()
