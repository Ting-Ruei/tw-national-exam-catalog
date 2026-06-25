#!/usr/bin/env python3
"""
Run local MinerU parsing for official exam PDFs.

The output tree mirrors the official PDF tree, and MinerU's generated files keep
the original PDF stem. This makes question/answer pairing inspectable by filename.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾")).expanduser()
PDF_ROOT = ASSET_ROOT / "10_official_pdf" / "by_official_catalog"
OUTPUT_ROOT = ASSET_ROOT / "20_mineru_output" / "by_official_catalog"
REGISTRY_ROOT = ASSET_ROOT / "Registry"
PDF_INDEX_DIR = REGISTRY_ROOT / "pdf_indexes"
PAIR_INDEX_DIR = REGISTRY_ROOT / "paired_indexes"
RUN_LOG_DIR = REGISTRY_ROOT / "mineru_runs"
REMOTE_BATCH_ROOT = REGISTRY_ROOT / "mineru_remote_batches"
DEFAULT_MINERU_BIN = Path.home() / "AI workspace" / "OCR_model" / "MinerU" / "venv_mineru" / "bin" / "mineru"
MINERU_METHOD = os.environ.get("MINERU_METHOD", "ocr")
MINERU_BACKEND = os.environ.get("MINERU_BACKEND", "vlm-engine")
MINERU_IMAGE_ANALYSIS = os.environ.get("MINERU_IMAGE_ANALYSIS", "false").lower() in {"1", "true", "yes", "on"}
CATEGORY_DIR_ALIASES = {
    "藥師（一）": "藥師(一)",
    "藥師（二）": "藥師(二)",
}


@dataclass(frozen=True)
class MinerUTask:
    task_id: str
    scope: str
    group_name: str
    document_role: str
    pair_status: str
    pdf_path: str
    pdf_relative: str
    sha256: str
    output_parent: str
    expected_md: str


@dataclass
class MinerUResult:
    task_id: str
    status: str
    returncode: int | None
    elapsed_seconds: float
    md_count: int
    image_count: int
    pdf_path: str
    output_parent: str
    expected_md: str
    error_tail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MinerU parsing over official exam PDFs.")
    parser.add_argument("--scope", choices=["paired-primary", "all-official", "questions-only"], default="paired-primary")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--mineru-bin", type=Path, default=DEFAULT_MINERU_BIN)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--pdf-index", type=Path)
    parser.add_argument("--pair-index", type=Path)
    parser.add_argument("--group", action="append", help="Filter group_name. Repeatable.")
    parser.add_argument("--year-start", type=int)
    parser.add_argument("--year-end", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--force", action="store_true", help="Run even when expected markdown already exists.")
    parser.add_argument(
        "--exclude-remote-reserved",
        action="store_true",
        help="Skip PDFs already reserved by remote batch manifests under Registry/mineru_remote_batches.",
    )
    parser.add_argument(
        "--chain-all-official",
        action="store_true",
        help="After paired-primary finishes, automatically run all-official on remaining PDFs only.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def latest_csv(directory: Path, prefix: str) -> Path:
    paths = sorted(directory.glob(f"{prefix}*.csv"))
    if not paths:
        raise SystemExit(f"No CSV found: {directory}/{prefix}*.csv")
    return paths[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def reserved_pdf_paths() -> set[str]:
    reserved: set[str] = set()
    if not REMOTE_BATCH_ROOT.exists():
        return reserved
    for manifest_path in REMOTE_BATCH_ROOT.glob("**/batch_manifest.csv"):
        try:
            rows = read_csv(manifest_path)
        except UnicodeDecodeError:
            continue
        for row in rows:
            pdf_path = row.get("pdf_path", "")
            if pdf_path:
                reserved.add(str(Path(pdf_path).resolve()))
    return reserved


def within_filters(row: dict[str, str], groups: set[str] | None, year_start: int | None, year_end: int | None) -> bool:
    if groups and row.get("group_name") not in groups:
        return False
    if year_start is None and year_end is None:
        return True
    try:
        year = int(row.get("year", "0"))
    except ValueError:
        return False
    if year_start is not None and year > year_start:
        return False
    if year_end is not None and year < year_end:
        return False
    return True


def normalize_category_dir(name: str) -> str:
    return CATEGORY_DIR_ALIASES.get(name, name)


def normalize_official_catalog_relative(path: Path) -> Path:
    parts = list(path.parts)
    if parts:
        parts[0] = normalize_category_dir(parts[0])
    return Path(*parts)


def resolve_pdf_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.exists():
        return resolved
    try:
        rel = resolved.relative_to(PDF_ROOT.resolve())
    except ValueError:
        return resolved
    normalized = PDF_ROOT.resolve() / normalize_official_catalog_relative(rel)
    return normalized if normalized.exists() else resolved


def expected_paths(pdf_path: Path, output_root: Path) -> tuple[Path, Path]:
    rel = pdf_path.resolve().relative_to(PDF_ROOT.resolve())
    rel = normalize_official_catalog_relative(rel)
    output_parent = output_root / rel.parent
    expected_md = output_parent / pdf_path.stem / "vlm" / f"{pdf_path.stem}.md"
    return output_parent, expected_md


def task_from_pdf(
    *,
    scope: str,
    pdf_path: Path,
    group_name: str,
    document_role: str,
    pair_status: str,
    sha256: str,
    output_root: Path,
) -> MinerUTask:
    output_parent, expected_md = expected_paths(pdf_path, output_root)
    pdf_relative = str(pdf_path.resolve().relative_to(PROJECT_ROOT.resolve()))
    task_key = f"{scope}:{pdf_relative}"
    return MinerUTask(
        task_id=task_key,
        scope=scope,
        group_name=group_name,
        document_role=document_role,
        pair_status=pair_status,
        pdf_path=str(pdf_path),
        pdf_relative=pdf_relative,
        sha256=sha256,
        output_parent=str(output_parent),
        expected_md=str(expected_md),
    )


def build_tasks(args: argparse.Namespace, exclude_pdf_paths: set[str] | None = None) -> list[MinerUTask]:
    output_root = args.output_root.resolve()
    groups = set(args.group) if args.group else None
    tasks_by_pdf: dict[str, MinerUTask] = {}
    exclude_pdf_paths = exclude_pdf_paths or set()
    allowed_pdf_paths: set[str] | None = None
    if args.pdf_index:
        allowed_pdf_paths = {str(resolve_pdf_path(Path(row["asset_path"]))) for row in read_csv(args.pdf_index) if row.get("asset_path")}
    if args.exclude_remote_reserved:
        exclude_pdf_paths = set(exclude_pdf_paths) | reserved_pdf_paths()

    if args.scope in {"paired-primary", "questions-only"}:
        pair_index = args.pair_index or latest_csv(PAIR_INDEX_DIR, "question_answer_pairs_detail__")
        for row in read_csv(pair_index):
            if not within_filters(row, groups, args.year_start, args.year_end):
                continue

            question_pdf = resolve_pdf_path(Path(row["question_pdf"]))
            if (allowed_pdf_paths is None or str(question_pdf) in allowed_pdf_paths) and str(question_pdf) not in exclude_pdf_paths:
                task = task_from_pdf(
                    scope=args.scope,
                    pdf_path=question_pdf,
                    group_name=row["group_name"],
                    document_role="question",
                    pair_status=row["pair_status"],
                    sha256=row["question_sha256"],
                    output_root=output_root,
                )
                tasks_by_pdf[task.pdf_path] = task

            if args.scope == "paired-primary" and row["answer_pdf_primary"]:
                answer_pdf = resolve_pdf_path(Path(row["answer_pdf_primary"]))
                if (allowed_pdf_paths is None or str(answer_pdf) in allowed_pdf_paths) and str(answer_pdf) not in exclude_pdf_paths:
                    task = task_from_pdf(
                        scope=args.scope,
                        pdf_path=answer_pdf,
                        group_name=row["group_name"],
                        document_role=row["answer_role_primary"] or "answer_primary",
                        pair_status=row["pair_status"],
                        sha256=row["answer_sha256_primary"],
                        output_root=output_root,
                    )
                    tasks_by_pdf[task.pdf_path] = task

    elif args.scope == "all-official":
        pdf_index = args.pdf_index or latest_csv(PDF_INDEX_DIR, "pdf_asset_index_detail__")
        for row in read_csv(pdf_index):
            if not within_filters(row, groups, args.year_start, args.year_end):
                continue
            pdf_path = resolve_pdf_path(Path(row["asset_path"]))
            if allowed_pdf_paths is not None and str(pdf_path) not in allowed_pdf_paths:
                continue
            if str(pdf_path) in exclude_pdf_paths:
                continue
            task = task_from_pdf(
                scope=args.scope,
                pdf_path=pdf_path,
                group_name=row["group_name"],
                document_role=row["document_role"],
                pair_status="",
                sha256=row["sha256"],
                output_root=output_root,
            )
            tasks_by_pdf[task.pdf_path] = task

    tasks = sorted(tasks_by_pdf.values(), key=lambda task: task.pdf_relative)
    if args.limit:
        tasks = tasks[: args.limit]
    return tasks


def run_batch(
    args: argparse.Namespace,
    *,
    scope: str,
    exclude_pdf_paths: set[str] | None = None,
) -> dict[str, object]:
    batch_args = argparse.Namespace(**vars(args))
    batch_args.scope = scope

    tasks = build_tasks(batch_args, exclude_pdf_paths=exclude_pdf_paths)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUN_LOG_DIR / stamp
    task_csv = run_dir / f"mineru_tasks__{scope}__{stamp}.csv"
    result_csv = run_dir / f"mineru_results__{scope}__{stamp}.csv"
    summary_json = run_dir / f"mineru_summary__{scope}__{stamp}.json"

    task_rows = [asdict(task) for task in tasks]
    write_csv(task_csv, task_rows)
    results: list[MinerUResult] = []

    print(
        json.dumps(
            {
                "status": "planned",
                "scope": scope,
                "workers": batch_args.workers,
                "tasks": len(tasks),
                "task_csv": str(task_csv),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if batch_args.dry_run:
        summary = summarize(tasks, results)
        summary.update({"dry_run": True, "task_csv": str(task_csv)})
        summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return {"tasks": tasks, "results": results, "task_csv": task_csv, "result_csv": result_csv, "summary_json": summary_json, "summary": summary}

    try:
        with ThreadPoolExecutor(max_workers=batch_args.workers) as executor:
            futures = [executor.submit(run_one, batch_args.mineru_bin, task, batch_args.timeout_seconds, batch_args.force) for task in tasks]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
                write_csv(result_csv, [asdict(item) for item in results])
    finally:
        summary = summarize(tasks, results)
        summary.update({"dry_run": False, "task_csv": str(task_csv), "result_csv": str(result_csv)})
        summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "run_summary", **summary}, ensure_ascii=False), flush=True)

    return {"tasks": tasks, "results": results, "task_csv": task_csv, "result_csv": result_csv, "summary_json": summary_json, "summary": summary}


def count_outputs(output_parent: Path) -> tuple[int, int]:
    md_count = sum(1 for _ in output_parent.glob("**/*.md"))
    image_count = sum(1 for p in output_parent.glob("**/*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    return md_count, image_count


def run_one(mineru_bin: Path, task: MinerUTask, timeout_seconds: int, force: bool) -> MinerUResult:
    output_parent = Path(task.output_parent)
    expected_md = Path(task.expected_md)
    if expected_md.exists() and not force:
        md_count, image_count = count_outputs(output_parent / Path(task.pdf_path).stem)
        return MinerUResult(
            task_id=task.task_id,
            status="skipped_existing",
            returncode=0,
            elapsed_seconds=0.0,
            md_count=md_count,
            image_count=image_count,
            pdf_path=task.pdf_path,
            output_parent=task.output_parent,
            expected_md=task.expected_md,
            error_tail="",
        )

    output_parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(mineru_bin),
        "-p",
        task.pdf_path,
        "-o",
        str(output_parent),
        "-m",
        MINERU_METHOD,
        "-b",
        MINERU_BACKEND,
        "--image-analysis",
        str(MINERU_IMAGE_ANALYSIS).lower(),
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
        elapsed = round(time.monotonic() - started, 3)
        md_count, image_count = count_outputs(output_parent / Path(task.pdf_path).stem)
        status = "ok" if result.returncode == 0 and expected_md.exists() else "error"
        return MinerUResult(
            task_id=task.task_id,
            status=status,
            returncode=result.returncode,
            elapsed_seconds=elapsed,
            md_count=md_count,
            image_count=image_count,
            pdf_path=task.pdf_path,
            output_parent=task.output_parent,
            expected_md=task.expected_md,
            error_tail=(result.stderr or result.stdout)[-1200:],
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.monotonic() - started, 3)
        md_count, image_count = count_outputs(output_parent / Path(task.pdf_path).stem)
        return MinerUResult(
            task_id=task.task_id,
            status="timeout",
            returncode=None,
            elapsed_seconds=elapsed,
            md_count=md_count,
            image_count=image_count,
            pdf_path=task.pdf_path,
            output_parent=task.output_parent,
            expected_md=task.expected_md,
            error_tail=str(exc)[-1200:],
        )


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summarize(tasks: list[MinerUTask], results: list[MinerUResult]) -> dict[str, object]:
    task_roles: dict[str, int] = {}
    task_groups: dict[str, int] = {}
    for task in tasks:
        task_roles[task.document_role] = task_roles.get(task.document_role, 0) + 1
        task_groups[task.group_name] = task_groups.get(task.group_name, 0) + 1

    statuses: dict[str, int] = {}
    for result in results:
        statuses[result.status] = statuses.get(result.status, 0) + 1

    return {
        "task_count": len(tasks),
        "result_count": len(results),
        "task_roles": task_roles,
        "task_groups": task_groups,
        "statuses": statuses,
    }


def main() -> None:
    args = parse_args()
    mineru_bin = args.mineru_bin.expanduser().resolve()
    if not mineru_bin.exists():
        raise SystemExit(f"MinerU executable not found: {mineru_bin}")
    paired_phase = run_batch(args, scope=args.scope)
    if args.chain_all_official and args.scope == "paired-primary" and not args.dry_run:
        paired_pdf_paths = {task.pdf_path for task in paired_phase["tasks"]}
        print(
            json.dumps(
                {
                    "status": "chain_next",
                    "next_scope": "all-official",
                    "excluded_pdf_count": len(paired_pdf_paths),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        run_batch(args, scope="all-official", exclude_pdf_paths=paired_pdf_paths)


if __name__ == "__main__":
    main()
