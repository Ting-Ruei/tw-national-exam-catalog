#!/usr/bin/env python3
"""
Summarize the latest MinerU batch run for local monitoring and cron snapshots.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
RUN_ROOT = ASSET_ROOT / "Registry" / "mineru_runs"
BACKGROUND_LOG_ROOT = RUN_ROOT / "background_logs"
STATUS_ROOT = RUN_ROOT / "status_snapshots"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report status for the latest MinerU batch run.")
    parser.add_argument("--write-snapshot", action="store_true", help="Write a timestamped JSON snapshot.")
    parser.add_argument("--write-latest", action="store_true", help="Write latest JSON and text status files.")
    return parser.parse_args()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def newest_path(paths: list[Path]) -> Path | None:
    return sorted(paths)[-1] if paths else None


def read_text_tail(path: Path, max_chars: int = 1200) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_table() -> list[tuple[int, int, str]]:
    try:
        result = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, check=True)
    except Exception:
        return []

    rows: list[tuple[int, int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append((pid, ppid, parts[2]))
    return rows


def find_running_batch() -> dict[str, object]:
    rows = process_table()
    if not rows:
        return {
            "batch_pid": None,
            "batch_ppid": None,
            "batch_running": False,
            "worker_pids": [],
        }

    batch_rows = [row for row in rows if "scripts/run_mineru_pdf_batch.py" in row[2]]
    if not batch_rows:
        return {
            "batch_pid": None,
            "batch_ppid": None,
            "batch_running": False,
            "worker_pids": [],
        }

    batch_pid, batch_ppid, _ = max(batch_rows, key=lambda row: row[0])
    worker_pids = sorted(
        pid for pid, ppid, command in rows
        if ppid == batch_pid or (ppid == batch_ppid and "mineru.cli.fast_api" in command)
    )
    return {
        "batch_pid": batch_pid,
        "batch_ppid": batch_ppid,
        "batch_running": pid_is_running(batch_pid),
        "worker_pids": worker_pids,
    }


def find_latest_launcher() -> dict[str, object]:
    log_path = newest_path(list(BACKGROUND_LOG_ROOT.glob("mineru_batch__*.log")))
    pid_path = newest_path(list(BACKGROUND_LOG_ROOT.glob("mineru_batch__*.pid")))
    pid = None
    pid_running = False
    if pid_path and pid_path.exists():
        raw = pid_path.read_text(encoding="utf-8").strip()
        if raw.isdigit():
            pid = int(raw)
            pid_running = pid_is_running(pid)
    return {
        "launcher_log": str(log_path) if log_path else "",
        "launcher_pid_file": str(pid_path) if pid_path else "",
        "pid": pid,
        "pid_running": pid_running,
        "launcher_log_tail": read_text_tail(log_path) if log_path else "",
    }


def find_latest_run() -> dict[str, object]:
    run_dirs = sorted(
        path for path in RUN_ROOT.iterdir()
        if path.is_dir() and re.fullmatch(r"\d{8}-\d{6}", path.name)
    ) if RUN_ROOT.exists() else []
    run_dir = None
    for candidate in reversed(run_dirs):
        result_csv = newest_path(list(candidate.glob("mineru_results__*.csv")))
        summary_json = newest_path(list(candidate.glob("mineru_summary__*.json")))
        if result_csv:
            run_dir = candidate
            break
        if summary_json and summary_json.exists():
            summary = json.loads(summary_json.read_text(encoding="utf-8"))
            if not summary.get("dry_run"):
                run_dir = candidate
                break
    if run_dir is None:
        return {
            "run_dir": "",
            "scope": "",
            "task_csv": "",
            "result_csv": "",
            "summary_json": "",
            "tasks_total": 0,
            "results_total": 0,
            "status_counts": {},
            "document_role_counts": {},
            "group_counts_top10": {},
            "completed_ratio": 0.0,
            "last_result_at": "",
            "last_errors": [],
        }

    task_csv = newest_path(list(run_dir.glob("mineru_tasks__*.csv")))
    result_csv = newest_path(list(run_dir.glob("mineru_results__*.csv")))
    summary_json = newest_path(list(run_dir.glob("mineru_summary__*.json")))

    task_rows = read_csv_rows(task_csv) if task_csv else []
    result_rows = read_csv_rows(result_csv) if result_csv else []

    status_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    last_errors: list[dict[str, str]] = []

    for row in task_rows:
        role = row.get("document_role", "")
        group = row.get("group_name", "")
        role_counts[role] = role_counts.get(role, 0) + 1
        group_counts[group] = group_counts.get(group, 0) + 1

    for row in result_rows:
        status = row.get("status", "")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in {"error", "timeout"}:
            last_errors.append(
                {
                    "task_id": row.get("task_id", ""),
                    "status": status,
                    "pdf_path": row.get("pdf_path", ""),
                    "error_tail": row.get("error_tail", "")[-300:],
                }
            )

    summary = {}
    if summary_json and summary_json.exists():
        summary = json.loads(summary_json.read_text(encoding="utf-8"))

    completed = sum(status_counts.values())
    total = len(task_rows) or int(summary.get("task_count", 0))
    latest_mtime = dt.datetime.fromtimestamp(result_csv.stat().st_mtime).isoformat() if result_csv and result_csv.exists() else ""
    top_groups = dict(sorted(group_counts.items(), key=lambda item: (-item[1], item[0]))[:10])
    scope = ""
    if task_csv:
        match = re.match(r"mineru_tasks__(.+)__\d{8}-\d{6}\.csv$", task_csv.name)
        if match:
            scope = match.group(1)

    return {
        "run_dir": str(run_dir),
        "scope": scope,
        "task_csv": str(task_csv) if task_csv else "",
        "result_csv": str(result_csv) if result_csv else "",
        "summary_json": str(summary_json) if summary_json else "",
        "tasks_total": total,
        "results_total": len(result_rows),
        "status_counts": status_counts,
        "document_role_counts": role_counts,
        "group_counts_top10": top_groups,
        "completed_ratio": round((completed / total), 4) if total else 0.0,
        "last_result_at": latest_mtime,
        "last_errors": last_errors[-5:],
    }


def build_report() -> dict[str, object]:
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    launcher = find_latest_launcher()
    batch = find_running_batch()
    run = find_latest_run()
    return {
        "generated_at": now.isoformat(),
        "launcher": launcher,
        "batch": batch,
        "run": run,
    }


def format_text(report: dict[str, object]) -> str:
    launcher = report["launcher"]
    batch = report["batch"]
    run = report["run"]
    lines = [
        f"generated_at: {report['generated_at']}",
        f"pid: {launcher.get('pid') or ''}",
        f"pid_running: {launcher.get('pid_running')}",
        f"batch_pid: {batch.get('batch_pid') or ''}",
        f"batch_running: {batch.get('batch_running')}",
        f"scope: {run.get('scope', '')}",
        f"run_dir: {run.get('run_dir', '')}",
        f"tasks_total: {run.get('tasks_total', 0)}",
        f"results_total: {run.get('results_total', 0)}",
        f"completed_ratio: {run.get('completed_ratio', 0.0)}",
        f"status_counts: {json.dumps(run.get('status_counts', {}), ensure_ascii=False)}",
        f"document_role_counts: {json.dumps(run.get('document_role_counts', {}), ensure_ascii=False)}",
        f"worker_pids: {json.dumps(batch.get('worker_pids', []), ensure_ascii=False)}",
        f"last_result_at: {run.get('last_result_at', '')}",
        f"launcher_log: {launcher.get('launcher_log', '')}",
    ]
    if run.get("last_errors"):
        lines.append(f"last_errors: {json.dumps(run['last_errors'], ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def write_outputs(report: dict[str, object]) -> None:
    STATUS_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    latest_json = STATUS_ROOT / "mineru_status__latest.json"
    latest_txt = STATUS_ROOT / "mineru_status__latest.txt"
    snapshot_json = STATUS_ROOT / f"mineru_status__{stamp}.json"
    latest_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest_txt.write_text(format_text(report), encoding="utf-8")
    snapshot_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    report = build_report()
    if args.write_snapshot or args.write_latest:
        write_outputs(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
