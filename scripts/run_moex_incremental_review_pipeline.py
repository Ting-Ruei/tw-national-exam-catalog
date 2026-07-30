#!/usr/bin/env python3
"""Run the incremental MOEX -> MinerU -> Review UI ingestion workflow.

The script is the deterministic worker boundary intended for both manual use
and a future n8n scheduler.  Dify/LLM work stays downstream and advisory; this
worker owns official-site scanning, file lineage, OCR, parser output, and SQL
staging ingestion.

Large PDFs, MinerU output, checkpoints, and run logs are written below the
configured ASSET_ROOT and therefore remain outside Git.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾")).expanduser()
DEFAULT_BASELINE_CATALOG = PROJECT_ROOT / "catalogs" / "moex_subject_catalog__y100-115.csv"
DEFAULT_LOCKED_CATEGORIES = PROJECT_ROOT / "catalogs" / "locked_27_canonical_category_names.csv"
DEFAULT_MINERU_BIN = Path(
    os.environ.get(
        "MINERU_BIN",
        Path.home() / "AI workspace" / "OCR_model" / "MinerU" / "venv_mineru" / "bin" / "mineru",
    )
).expanduser()

ROLE_URL_FIELDS = {
    "question": "question_url",
    "answer": "answer_url",
    "correction": "correction_url",
}


def roc_year_today() -> int:
    return dt.datetime.now().year - 1911


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", "", value).strip()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str], *, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8-sig" if bom else "utf-8"
    with path.open("w", newline="", encoding=encoding) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def acquire_pipeline_lock(asset_root: Path) -> TextIO:
    """Prevent scheduled and manual production runs from overlapping."""
    lock_path = asset_root / "Registry" / "incremental_pipeline" / "production.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        owner = handle.read().strip() or "owner metadata unavailable"
        handle.close()
        raise RuntimeError(f"Another incremental pipeline is already running: {owner}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(
        json.dumps(
            {"pid": os.getpid(), "started_at": dt.datetime.now().astimezone().isoformat()},
            ensure_ascii=False,
        )
        + "\n"
    )
    handle.flush()
    return handle


def catalog_key(row: dict[str, str]) -> str:
    return row.get("registry_key", "")


def document_changes(
    baseline_rows: list[dict[str, str]],
    live_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Return URL additions/changes without treating missing live rows as deletion."""
    old_by_key = {catalog_key(row): row for row in baseline_rows if catalog_key(row)}
    changes: list[dict[str, str]] = []
    for live in live_rows:
        key = catalog_key(live)
        old = old_by_key.get(key, {})
        for role, field in ROLE_URL_FIELDS.items():
            new_url = live.get(field, "")
            old_url = old.get(field, "")
            if not new_url or new_url == old_url:
                continue
            changes.append(
                {
                    "change_type": "document_added" if not old_url else "document_url_changed",
                    "document_role": role,
                    "year": live.get("year", ""),
                    "exam_code": live.get("exam_code", ""),
                    "exam_label": live.get("exam_label", ""),
                    "category_code": live.get("category_code", ""),
                    "category_name": live.get("category_name", ""),
                    "subject_code": live.get("subject_code", ""),
                    "subject_name": live.get("subject_name", ""),
                    "registry_key": key,
                    "old_url": old_url,
                    "new_url": new_url,
                }
            )
    return sorted(
        changes,
        key=lambda item: (
            item["exam_code"],
            item["category_code"],
            item["subject_code"],
            item["document_role"],
        ),
    )


def merge_catalog_rows(
    baseline_rows: list[dict[str, str]],
    live_rows: list[dict[str, str]],
    *,
    commit_exam_codes: set[str] | None = None,
) -> list[dict[str, str]]:
    """Upsert live rows while preserving history and baseline row ordering."""
    live_by_key: dict[str, dict[str, str]] = {}
    for row in live_rows:
        if commit_exam_codes is not None and row.get("exam_code") not in commit_exam_codes:
            continue
        key = catalog_key(row)
        if key:
            live_by_key[key] = dict(row)

    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in baseline_rows:
        key = catalog_key(row)
        if not key or key in seen:
            continue
        merged.append(live_by_key.get(key, dict(row)))
        seen.add(key)
    for row in live_rows:
        if commit_exam_codes is not None and row.get("exam_code") not in commit_exam_codes:
            continue
        key = catalog_key(row)
        if key and key not in seen:
            merged.append(dict(row))
            seen.add(key)
    return merged


def read_locked_categories(path: Path) -> set[str]:
    rows, _ = read_csv(path)
    return {normalize_name(row.get("canonical_category_name", "")) for row in rows}


def choose_changes(
    changes: list[dict[str, str]],
    *,
    profile: str,
    locked_categories: set[str],
    exam_codes: set[str],
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    for item in changes:
        if exam_codes and item["exam_code"] not in exam_codes:
            continue
        if profile == "locked27" and normalize_name(item["category_name"]) not in locked_categories:
            continue
        selected.append(item)
    return selected


def target_rows_for_changes(
    live_rows: list[dict[str, str]],
    selected_changes: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Select complete question/answer pairs; keep incomplete rows in waiting."""
    wanted = {item["registry_key"] for item in selected_changes}
    targets: list[dict[str, str]] = []
    waiting: list[dict[str, str]] = []
    for row in live_rows:
        if catalog_key(row) not in wanted:
            continue
        if row.get("question_url") and (row.get("correction_url") or row.get("answer_url")):
            targets.append(row)
        else:
            waiting.append(row)
    return targets, waiting


def change_summary(changes: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "count": len(changes),
        "exam_codes": dict(sorted(Counter(item["exam_code"] for item in changes).items())),
        "roles": dict(sorted(Counter(item["document_role"] for item in changes).items())),
        "categories": dict(sorted(Counter(item["category_name"] for item in changes).items())),
    }


def run_command(
    command: list[str],
    *,
    log_path: Path,
    env: dict[str, str] | None = None,
    cwd: Path = PROJECT_ROOT,
) -> str:
    """Stream subprocess output and retain an inspectable per-stage log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    shown = " ".join(command)
    print(f"[RUN] {shown}", flush=True)
    lines: list[str] = []
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {shown}\n")
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            lines.append(line)
        return_code = process.wait()
        if return_code:
            raise RuntimeError(f"Command failed with exit code {return_code}: {shown}")
    return "".join(lines)


def latest_new_path(before: set[Path], directory: Path, pattern: str) -> Path:
    after = set(directory.glob(pattern))
    created = sorted(after - before)
    if created:
        return created[-1]
    existing = sorted(after)
    if not existing:
        raise RuntimeError(f"Expected output not found: {directory}/{pattern}")
    return existing[-1]


def validate_live_catalog(live_rows: list[dict[str, str]], year: int) -> None:
    if not live_rows:
        raise RuntimeError("Official scan returned no catalog rows")
    wrong_year = sorted({row.get("year", "") for row in live_rows if row.get("year") != str(year)})
    if wrong_year:
        raise RuntimeError(f"Official scan contains unexpected ROC years: {wrong_year}")
    missing_keys = sum(1 for row in live_rows if not catalog_key(row))
    if missing_keys:
        raise RuntimeError(f"Official scan contains {missing_keys} rows without registry_key")


def scan_catalog(args: argparse.Namespace, run_dir: Path) -> Path:
    if args.scan_catalog:
        return args.scan_catalog.expanduser().resolve()
    catalog_dir = run_dir / "catalog"
    run_command(
        [
            sys.executable,
            "scripts/export_moex_subject_catalog.py",
            "--year",
            str(args.year),
            "--delay",
            str(args.scan_delay),
            "--output-dir",
            str(catalog_dir),
        ],
        log_path=run_dir / "logs" / "01_scan.log",
    )
    return catalog_dir / f"moex_subject_catalog__y{args.year}-{args.year}.csv"


def download_affected_categories(
    args: argparse.Namespace,
    run_dir: Path,
    merged_catalog: Path,
    target_rows: list[dict[str, str]],
    minimum_year: int,
) -> list[Path]:
    manifest_dir = args.asset_root / "Registry" / "asset_manifests"
    log_dir = args.asset_root / "Registry" / "processing_logs"
    output_root = args.asset_root / "10_official_pdf" / "by_official_catalog"
    manifests: list[Path] = []
    for index, category in enumerate(sorted({row["category_name"] for row in target_rows}), start=1):
        before = set(manifest_dir.glob(f"moex_pdf_download__{category}__*.csv"))
        run_command(
            [
                sys.executable,
                "scripts/download_moex_pdfs_from_catalog.py",
                "--catalog",
                str(merged_catalog),
                "--category",
                category,
                "--year-start",
                str(args.year),
                "--year-end",
                str(minimum_year),
                "--output-root",
                str(output_root),
                "--manifest-dir",
                str(manifest_dir),
                "--log-dir",
                str(log_dir),
                "--sleep",
                str(args.download_sleep),
            ],
            log_path=run_dir / "logs" / f"02_download_{index:02d}_{normalize_name(category)}.log",
        )
        manifest = latest_new_path(before, manifest_dir, f"moex_pdf_download__{category}__*.csv")
        rows, _ = read_csv(manifest)
        errors = [row for row in rows if row.get("status", "").startswith("error:")]
        if errors:
            raise RuntimeError(f"{category} download manifest contains {len(errors)} errors: {manifest}")
        manifests.append(manifest)
    return manifests


def rebuild_indexes(args: argparse.Namespace, run_dir: Path) -> tuple[Path, Path]:
    manifest_dir = args.asset_root / "Registry" / "asset_manifests"
    pdf_dir = args.asset_root / "Registry" / "pdf_indexes"
    pair_dir = args.asset_root / "Registry" / "paired_indexes"
    before_pdf = set(pdf_dir.glob("pdf_asset_index_detail__*.csv"))
    before_pair = set(pair_dir.glob("question_answer_pairs_detail__*.csv"))
    run_command(
        [
            sys.executable,
            "scripts/build_pdf_asset_index.py",
            "--asset-root",
            str(args.asset_root),
            "--manifest-dir",
            str(manifest_dir),
            "--output-dir",
            str(pdf_dir),
        ],
        log_path=run_dir / "logs" / "03_pdf_index.log",
    )
    run_command(
        [
            sys.executable,
            "scripts/build_question_answer_pairs.py",
            "--asset-root",
            str(args.asset_root),
            "--manifest-dir",
            str(manifest_dir),
            "--output-dir",
            str(pair_dir),
        ],
        log_path=run_dir / "logs" / "04_pair_index.log",
    )
    return (
        latest_new_path(before_pdf, pdf_dir, "pdf_asset_index_detail__*.csv"),
        latest_new_path(before_pair, pair_dir, "question_answer_pairs_detail__*.csv"),
    )


def build_incremental_pair_index(run_dir: Path, full_pair_index: Path, target_rows: list[dict[str, str]]) -> Path:
    rows, fields = read_csv(full_pair_index)
    wanted = {f"{catalog_key(row)}:question" for row in target_rows}
    selected = [row for row in rows if row.get("question_registry_key") in wanted]
    found = {row.get("question_registry_key", "") for row in selected}
    missing = sorted(wanted - found)
    if missing:
        raise RuntimeError(f"Pair index is missing {len(missing)} target questions: {missing[:5]}")
    incomplete = [row for row in selected if row.get("pair_status") not in {"paired_ans_only", "paired_mod_primary"}]
    if incomplete:
        raise RuntimeError(f"Incremental pair index contains {len(incomplete)} incomplete pairs")
    out = run_dir / "incremental_pair_index.csv"
    write_csv(out, selected, fields, bom=True)
    return out


def run_mineru(
    args: argparse,
    run_dir: Path,
    pdf_index: Path,
    incremental_pairs: Path,
) -> tuple[Path, dict[str, int]]:
    mineru_run_dir = args.asset_root / "Registry" / "mineru_runs"
    before = set(mineru_run_dir.glob("*/mineru_results__paired-primary__*.csv"))
    env = dict(os.environ)
    env.update(
        {
            "ASSET_ROOT": str(args.asset_root),
            "MINERU_METHOD": "ocr",
            "MINERU_BACKEND": "vlm-engine",
            "MINERU_IMAGE_ANALYSIS": "false",
        }
    )
    run_command(
        [
            sys.executable,
            "scripts/run_mineru_pdf_batch.py",
            "--scope",
            "paired-primary",
            "--workers",
            str(args.workers),
            "--mineru-bin",
            str(args.mineru_bin),
            "--output-root",
            str(args.asset_root / "20_mineru_output" / "by_official_catalog"),
            "--pdf-index",
            str(pdf_index),
            "--pair-index",
            str(incremental_pairs),
            "--timeout-seconds",
            str(args.mineru_timeout),
            "--batch-by-output-parent",
        ],
        log_path=run_dir / "logs" / "05_mineru.log",
        env=env,
    )
    result_path = latest_new_path(before, mineru_run_dir, "*/mineru_results__paired-primary__*.csv")
    rows, _ = read_csv(result_path)
    statuses = Counter(row.get("status", "") for row in rows)
    failures = sum(count for status, count in statuses.items() if status not in {"ok", "skipped_existing"})
    if failures:
        raise RuntimeError(f"MinerU batch contains {failures} failed results: {dict(statuses)}")
    return result_path, dict(sorted(statuses.items()))


def build_candidates(args: argparse, run_dir: Path, incremental_pairs: Path) -> tuple[Path, Path, dict[str, Any]]:
    candidate_root = args.asset_root / "30_normalized_items" / "question_candidates"
    before = set(path for path in candidate_root.iterdir() if path.is_dir()) if candidate_root.exists() else set()
    env = dict(os.environ)
    env["ASSET_ROOT"] = str(args.asset_root)
    run_command(
        [
            sys.executable,
            "scripts/build_question_candidates_from_mineru.py",
            "--pair-index",
            str(incremental_pairs),
            "--output-dir",
            str(args.asset_root / "30_normalized_items"),
            "--include-needs-review",
        ],
        log_path=run_dir / "logs" / "06_candidates.log",
        env=env,
    )
    candidate_dir = latest_new_path(before, candidate_root, "*")
    summary_files = sorted(candidate_dir.glob("question_candidate_summary__*.json"))
    if not summary_files:
        raise RuntimeError(f"Candidate summary missing from {candidate_dir}")
    summary = json.loads(summary_files[-1].read_text(encoding="utf-8"))
    candidate_jsonl = Path(summary["candidate_jsonl"])
    issue_csv = Path(summary["issue_csv"])
    if int(summary.get("candidate_count") or 0) <= 0:
        raise RuntimeError("Parser produced zero candidates")
    validate_candidate_integrity_summary(
        summary.get("automation_blocking_issue_counts") or {}
    )
    validate_group_candidate_summary(summary.get("group_candidate_summary") or {})
    return candidate_jsonl, issue_csv, summary


def validate_candidate_integrity_summary(blocking_issue_counts: dict[str, Any]) -> None:
    """Stop before SQL when parser output has missing or ambiguous question boundaries."""
    failures = {
        str(code): int(count or 0)
        for code, count in blocking_issue_counts.items()
        if int(count or 0)
    }
    if failures:
        raise RuntimeError(
            "Candidate structural integrity check failed: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )


def validate_group_candidate_summary(group_summary: dict[str, Any]) -> None:
    """Block automation only when a high-confidence group cue was lost."""
    failures = {
        "unbound_explicit_group_cue_count": int(
            group_summary.get("unbound_explicit_group_cue_count") or 0
        ),
        "incomplete_explicit_group_count": int(
            group_summary.get("incomplete_explicit_group_count") or 0
        ),
        "continuation_without_anchor_count": int(
            group_summary.get("continuation_without_anchor_count") or 0
        ),
    }
    failures = {key: value for key, value in failures.items() if value}
    if failures:
        raise RuntimeError(
            "Question-group candidate integrity check failed: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )


def ingest_postgres(
    args: argparse,
    run_dir: Path,
    pdf_index: Path,
    pair_index: Path,
    mineru_results: Path,
    candidate_jsonl: Path,
    issue_csv: Path,
) -> None:
    reviewed_change_report = run_dir / "reviewed_candidate_changes.json"
    run_command(
        ["docker", "compose", "up", "-d", "--wait", "postgres"],
        log_path=run_dir / "logs" / "07_postgres_up.log",
    )
    run_command(
        ["bash", "scripts/postgres_apply_schema.sh"],
        log_path=run_dir / "logs" / "08_schema.log",
    )
    mineru_rows, _ = read_csv(mineru_results)
    run_command(
        [
            sys.executable,
            "scripts/ingest_indexes_to_postgres.py",
            "--pdf-index",
            str(pdf_index),
            "--pair-index",
            str(pair_index),
            "--mineru-results",
            str(mineru_results),
            "--mineru-limit",
            str(len(mineru_rows)),
            "--postgres-db",
            args.postgres_db,
            "--postgres-user",
            args.postgres_user,
        ],
        log_path=run_dir / "logs" / "09_ingest_indexes.log",
    )
    run_command(
        [
            sys.executable,
            "scripts/ingest_question_candidates_to_postgres.py",
            "--candidate-jsonl",
            str(candidate_jsonl),
            "--issue-csv",
            str(issue_csv),
            "--sync-mode",
            "merge",
            "--reviewed-change-report",
            str(reviewed_change_report),
            "--postgres-db",
            args.postgres_db,
            "--postgres-user",
            args.postgres_user,
        ],
        log_path=run_dir / "logs" / "10_ingest_candidates.log",
    )
    if not args.no_start_review_ui:
        run_command(
            ["docker", "compose", "up", "-d", "review-ui"],
            log_path=run_dir / "logs" / "11_review_ui_up.log",
        )


def update_baseline(source_catalog: Path, baseline_catalog: Path) -> None:
    baseline_catalog.parent.mkdir(parents=True, exist_ok=True)
    temporary = baseline_catalog.with_suffix(baseline_catalog.suffix + ".tmp")
    shutil.copyfile(source_catalog, temporary)
    temporary.replace(baseline_catalog)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=roc_year_today(), help="ROC year to scan. Default: current ROC year.")
    parser.add_argument("--exam-code", action="append", default=[], help="Only process selected exam code(s). Repeatable.")
    parser.add_argument("--profile", choices=("locked27", "all"), default="locked27")
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--baseline-catalog", type=Path, default=DEFAULT_BASELINE_CATALOG)
    parser.add_argument("--locked-categories", type=Path, default=DEFAULT_LOCKED_CATEGORIES)
    parser.add_argument("--scan-catalog", type=Path, help="Use an already-exported current-year CSV instead of network scanning.")
    parser.add_argument("--scan-delay", type=float, default=0.3)
    parser.add_argument("--download-sleep", type=float, default=0.15)
    parser.add_argument("--mineru-bin", type=Path, default=DEFAULT_MINERU_BIN)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--mineru-timeout",
        type=int,
        default=0,
        help="Timeout for each category-level grouped MinerU invocation; 0 means no timeout (default: 0).",
    )
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument(
        "--review-ui-url",
        default=os.environ.get("REVIEW_PRIMARY_UI_URL", "http://127.0.0.1:8765/"),
        help="Authoritative Review UI URL recorded in the run state.",
    )
    parser.add_argument(
        "--no-start-review-ui",
        action="store_true",
        help="Do not start the local Review UI when review is owned by another host.",
    )
    parser.add_argument("--scan-only", action="store_true", help="Write the diff report without downloads or checkpoint commit.")
    parser.add_argument("--no-postgres", action="store_true", help="Stop after candidate generation and do not commit the catalog checkpoint.")
    parser.add_argument("--dry-run", action="store_true", help="Alias for --scan-only; useful to n8n preflight branches.")
    parser.add_argument("--keep-baseline", action="store_true", help="Do not update the canonical catalog after a successful full run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.asset_root = args.asset_root.expanduser().resolve()
    args.baseline_catalog = args.baseline_catalog.expanduser().resolve()
    args.locked_categories = args.locked_categories.expanduser().resolve()
    args.mineru_bin = args.mineru_bin.expanduser().resolve()
    args.scan_only = args.scan_only or args.dry_run
    pipeline_lock = acquire_pipeline_lock(args.asset_root)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.asset_root / "Registry" / "incremental_pipeline" / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    state_path = run_dir / "run_state.json"
    state: dict[str, Any] = {
        "run_id": stamp,
        "started_at": dt.datetime.now().astimezone().isoformat(),
        "status": "running",
        "stage": "scan",
        "profile": args.profile,
        "year": args.year,
        "exam_codes": sorted(set(args.exam_code)),
        "asset_root": str(args.asset_root),
        "baseline_catalog": str(args.baseline_catalog),
        "run_dir": str(run_dir),
    }
    write_json(state_path, state)

    try:
        baseline_rows, baseline_fields = read_csv(args.baseline_catalog)
        live_catalog = scan_catalog(args, run_dir)
        live_rows, live_fields = read_csv(live_catalog)
        validate_live_catalog(live_rows, args.year)
        fields = baseline_fields or live_fields
        changes = document_changes(baseline_rows, live_rows)
        selected = choose_changes(
            changes,
            profile=args.profile,
            locked_categories=read_locked_categories(args.locked_categories),
            exam_codes=set(args.exam_code),
        )
        targets, waiting = target_rows_for_changes(live_rows, selected)

        change_fields = [
            "change_type",
            "document_role",
            "year",
            "exam_code",
            "exam_label",
            "category_code",
            "category_name",
            "subject_code",
            "subject_name",
            "registry_key",
            "old_url",
            "new_url",
        ]
        write_csv(run_dir / "all_document_changes.csv", changes, change_fields, bom=True)
        write_csv(run_dir / "selected_document_changes.csv", selected, change_fields, bom=True)
        write_csv(run_dir / "target_catalog_rows.csv", targets, live_fields, bom=True)
        write_csv(run_dir / "waiting_catalog_rows.csv", waiting, live_fields, bom=True)

        commit_codes = set(args.exam_code) or None
        merged_rows = merge_catalog_rows(baseline_rows, live_rows, commit_exam_codes=commit_codes)
        merged_catalog = run_dir / args.baseline_catalog.name
        write_csv(merged_catalog, merged_rows, fields, bom=True)

        state.update(
            {
                "stage": "scan_complete",
                "live_catalog": str(live_catalog),
                "merged_catalog": str(merged_catalog),
                "all_changes": change_summary(changes),
                "selected_changes": change_summary(selected),
                "target_pair_count": len(targets),
                "waiting_pair_count": len(waiting),
            }
        )
        write_json(state_path, state)
        print(json.dumps({key: state[key] for key in ("all_changes", "selected_changes", "target_pair_count", "waiting_pair_count")}, ensure_ascii=False, indent=2))

        if args.scan_only:
            state.update({"status": "scan_only_complete", "stage": "complete", "finished_at": dt.datetime.now().astimezone().isoformat()})
            write_json(state_path, state)
            return 0

        if not targets:
            if not args.keep_baseline and not waiting:
                update_baseline(merged_catalog, args.baseline_catalog)
                state["baseline_updated"] = True
            state.update({"status": "no_action", "stage": "complete", "finished_at": dt.datetime.now().astimezone().isoformat()})
            write_json(state_path, state)
            return 0

        if not args.mineru_bin.exists():
            raise RuntimeError(f"MinerU executable not found: {args.mineru_bin}")

        minimum_year = min(int(row["year"]) for row in baseline_rows if row.get("year"))
        state["stage"] = "download"
        write_json(state_path, state)
        manifests = download_affected_categories(args, run_dir, merged_catalog, targets, minimum_year)
        state["download_manifests"] = [str(path) for path in manifests]

        state["stage"] = "index"
        write_json(state_path, state)
        pdf_index, pair_index = rebuild_indexes(args, run_dir)
        incremental_pairs = build_incremental_pair_index(run_dir, pair_index, targets)
        state.update(
            {
                "pdf_index": str(pdf_index),
                "pair_index": str(pair_index),
                "incremental_pair_index": str(incremental_pairs),
            }
        )

        state["stage"] = "mineru"
        write_json(state_path, state)
        mineru_results, mineru_statuses = run_mineru(args, run_dir, pdf_index, incremental_pairs)
        state.update({"mineru_results": str(mineru_results), "mineru_statuses": mineru_statuses})

        state["stage"] = "parse_candidates"
        write_json(state_path, state)
        candidate_jsonl, issue_csv, candidate_summary = build_candidates(args, run_dir, incremental_pairs)
        state.update(
            {
                "candidate_jsonl": str(candidate_jsonl),
                "issue_csv": str(issue_csv),
                "candidate_count": candidate_summary.get("candidate_count", 0),
                "candidate_quality": candidate_summary.get("quality_status_counts", {}),
                "automation_blocking_issue_counts": candidate_summary.get(
                    "automation_blocking_issue_counts", {}
                ),
                "group_candidate_summary": candidate_summary.get(
                    "group_candidate_summary", {}
                ),
            }
        )

        if args.no_postgres:
            state.update(
                {
                    "status": "candidates_ready_not_ingested",
                    "stage": "complete",
                    "baseline_updated": False,
                    "finished_at": dt.datetime.now().astimezone().isoformat(),
                }
            )
            write_json(state_path, state)
            return 0

        state["stage"] = "postgres_ingest"
        state["reviewed_candidate_change_report"] = str(
            run_dir / "reviewed_candidate_changes.json"
        )
        write_json(state_path, state)
        ingest_postgres(args, run_dir, pdf_index, pair_index, mineru_results, candidate_jsonl, issue_csv)

        if not args.keep_baseline:
            update_baseline(merged_catalog, args.baseline_catalog)
            state["baseline_updated"] = True
        else:
            state["baseline_updated"] = False
        state.update(
            {
                "status": "complete",
                "stage": "complete",
                "review_ui_url": args.review_ui_url,
                "finished_at": dt.datetime.now().astimezone().isoformat(),
            }
        )
        write_json(state_path, state)
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        state.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": dt.datetime.now().astimezone().isoformat(),
            }
        )
        write_json(state_path, state)
        print(state["error"], file=sys.stderr)
        print(f"Checkpoint: {state_path}", file=sys.stderr)
        return 1
    finally:
        pipeline_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
