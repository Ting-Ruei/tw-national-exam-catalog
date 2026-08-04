#!/usr/bin/env python3
"""Prepare a narrowly scoped, path-rebased bundle for a remote Review UI host."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"


def latest_run_state() -> Path:
    paths = sorted((ASSET_ROOT / "Registry" / "incremental_pipeline").glob("*/run_state.json"))
    if not paths:
        raise SystemExit("No incremental pipeline run_state.json found")
    return paths[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-state", type=Path, default=latest_run_state())
    parser.add_argument("--exam-code", action="append", default=[])
    parser.add_argument("--destination-project-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def rebase_text(value: str, destination_root: Path) -> str:
    return value.replace(str(PROJECT_ROOT), str(destination_root))


def rebase_value(value: Any, destination_root: Path) -> Any:
    if isinstance(value, str):
        return rebase_text(value, destination_root)
    if isinstance(value, list):
        return [rebase_value(item, destination_root) for item in value]
    if isinstance(value, dict):
        return {key: rebase_value(item, destination_root) for key, item in value.items()}
    return value


def rebased_rows(rows: Iterable[dict[str, str]], destination_root: Path) -> list[dict[str, str]]:
    return [
        {key: rebase_text(value, destination_root) for key, value in row.items()}
        for row in rows
    ]


def project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError as exc:
        raise SystemExit(f"Artifact is outside project root: {path}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    run_state_path = args.run_state.resolve()
    state = json.loads(run_state_path.read_text(encoding="utf-8"))
    exam_codes = set(args.exam_code or state.get("exam_codes") or [])
    if not exam_codes:
        raise SystemExit("No exam code supplied or recorded in run state")
    if state.get("status") != "complete":
        raise SystemExit(f"Incremental run is not complete: {state.get('status')}")

    output_dir = args.output_dir or (
        ASSET_ROOT / "Registry" / "remote_review_sync" / str(state.get("run_id") or run_state_path.parent.name) / "bundle"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_rows, pdf_fields = read_csv(Path(state["pdf_index"]))
    pdf_rows = [row for row in pdf_rows if row.get("exam_code") in exam_codes]
    selected_pdf_paths = {str(Path(row["asset_path"]).resolve()) for row in pdf_rows}

    pair_rows, pair_fields = read_csv(Path(state["pair_index"]))
    pair_rows = [row for row in pair_rows if row.get("exam_code") in exam_codes]

    mineru_rows, mineru_fields = read_csv(Path(state["mineru_results"]))
    mineru_rows = [row for row in mineru_rows if str(Path(row["pdf_path"]).resolve()) in selected_pdf_paths]

    candidates: list[dict[str, Any]] = []
    with Path(state["candidate_jsonl"]).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if str((item.get("metadata") or {}).get("exam_code") or "") in exam_codes:
                candidates.append(item)
    candidate_keys = {str(item["candidate_key"]) for item in candidates}

    issue_rows, issue_fields = read_csv(Path(state["issue_csv"]))
    issue_rows = [row for row in issue_rows if row.get("candidate_key") in candidate_keys]

    write_csv(output_dir / "pdf_index.csv", rebased_rows(pdf_rows, args.destination_project_root), pdf_fields)
    write_csv(output_dir / "pair_index.csv", rebased_rows(pair_rows, args.destination_project_root), pair_fields)
    write_csv(output_dir / "mineru_results.csv", rebased_rows(mineru_rows, args.destination_project_root), mineru_fields)
    write_csv(output_dir / "question_parse_issues.csv", rebased_rows(issue_rows, args.destination_project_root), issue_fields)
    with (output_dir / "question_candidates.jsonl").open("w", encoding="utf-8") as handle:
        for item in candidates:
            handle.write(json.dumps(rebase_value(item, args.destination_project_root), ensure_ascii=False, sort_keys=True) + "\n")

    artifact_paths: set[Path] = set()
    for row in pdf_rows:
        artifact_paths.add(Path(row["asset_path"]).resolve())
    for row in mineru_rows:
        expected_md = Path(row["expected_md"]).resolve()
        output_dir_for_pdf = expected_md.parent.parent
        if not expected_md.exists():
            raise SystemExit(f"Missing MinerU Markdown: {expected_md}")
        artifact_paths.update(path for path in output_dir_for_pdf.rglob("*") if path.is_file())

    artifact_rows: list[dict[str, Any]] = []
    for path in sorted(artifact_paths):
        if not path.exists():
            raise SystemExit(f"Missing artifact: {path}")
        artifact_rows.append(
            {
                "relative_path": project_relative(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    write_csv(output_dir / "artifact_manifest.csv", artifact_rows, ["relative_path", "bytes", "sha256"])
    (output_dir / "artifacts.files-from.txt").write_text(
        "".join(f"{row['relative_path']}\n" for row in artifact_rows),
        encoding="utf-8",
    )
    (output_dir / "candidate_keys.txt").write_text("".join(f"{key}\n" for key in sorted(candidate_keys)), encoding="utf-8")
    (output_dir / "registry_keys.txt").write_text(
        "".join(f"{row['registry_key']}\n" for row in sorted(pdf_rows, key=lambda item: item["registry_key"])),
        encoding="utf-8",
    )
    (output_dir / "pair_keys.txt").write_text(
        "".join(f"{row['pair_key']}\n" for row in sorted(pair_rows, key=lambda item: item["pair_key"])),
        encoding="utf-8",
    )

    summary = {
        "schema_version": 1,
        "source_run_state": str(run_state_path),
        "source_project_root": str(PROJECT_ROOT),
        "destination_project_root": str(args.destination_project_root),
        "exam_codes": sorted(exam_codes),
        "pdf_rows": len(pdf_rows),
        "pair_rows": len(pair_rows),
        "mineru_rows": len(mineru_rows),
        "candidate_rows": len(candidates),
        "issue_rows": len(issue_rows),
        "artifact_files": len(artifact_rows),
        "artifact_bytes": sum(int(row["bytes"]) for row in artifact_rows),
        "automation_blocking_issue_counts": state.get(
            "automation_blocking_issue_counts"
        )
        or {},
        "group_candidate_summary": state.get("group_candidate_summary") or {},
        "output_dir": str(output_dir),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
