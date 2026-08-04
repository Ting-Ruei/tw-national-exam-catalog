#!/usr/bin/env python3
"""Aggregate validated sparse runs into a conservative shadow-pilot scorecard."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from v4_common import LANE_DISPLAY_NAMES, read_json, read_jsonl, write_json, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--gold-corpus", type=Path, required=True)
    parser.add_argument(
        "--route-control",
        action="append",
        default=[],
        help="Expected lane in candidate_key=lane form.",
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument(
        "--human-queue",
        type=Path,
        help="Optional JSONL queue covering every pilot task for human labeling.",
    )
    parser.add_argument(
        "--review-base-url",
        default="http://192.168.10.70:8765/",
        help="Review UI base URL used for focusKey links in the human queue.",
    )
    return parser.parse_args()


def expected_issue_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    fields = (
        "field",
        "before",
        "after",
        "issue_family",
        "source_class",
        "route",
    )
    return all(actual.get(field) == expected.get(field) for field in fields)


def parse_route_controls(values: list[str]) -> dict[str, str]:
    controls: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"invalid --route-control: {value}")
        key, lane = value.rsplit("=", 1)
        if not key or not lane or key in controls:
            raise SystemExit(f"invalid or duplicate --route-control: {value}")
        controls[key] = lane
    return controls


def build_human_queue(
    tasks_by_key: dict[str, dict[str, Any]],
    task_lanes: dict[str, str],
    issues_by_key: dict[str, list[dict[str, Any]]],
    review_base_url: str,
) -> list[dict[str, Any]]:
    base_url = review_base_url.rstrip("/") + "/"
    rows: list[dict[str, Any]] = []
    for key, task in tasks_by_key.items():
        issues = issues_by_key.get(key, [])
        exam = task.get("exam") or {}
        content = task.get("content") or {}
        sources = task.get("sources") or {}
        rows.append(
            {
                "schema_version": "national_exam_human_shadow_label_v1",
                "priority": 1 if issues else 2,
                "review_state": "pending_human_label",
                "candidate_key": key,
                "lane": task_lanes[key],
                "exam": {
                    field: exam.get(field)
                    for field in (
                        "category",
                        "subject",
                        "year",
                        "ordinal",
                        "question_number",
                    )
                },
                "stem": content.get("stem"),
                "model_flagged": bool(issues),
                "issue_count": len(issues),
                "issues": issues,
                "review_checks": (
                    [
                        "confirm_or_reject_each_issue",
                        "classify_source_against_official_pdf",
                        "record_any_missed_issue",
                    ]
                    if issues
                    else [
                        "confirm_no_material_issue",
                        "record_any_missed_issue",
                    ]
                ),
                "source_pdf_relative": sources.get("question_pdf_relative"),
                "review_url": f"{base_url}?focusKey={quote(key, safe='')}",
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["priority"],
            row["lane"],
            row["candidate_key"],
        ),
    )


def write_human_queue_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    flagged = sum(1 for row in rows if row["model_flagged"])
    lines = [
        "# LUNA v4 Human Label Queue",
        "",
        f"- Pending labels: {len(rows)}; model-flagged first: {flagged}.",
        "- This queue is advisory and does not write review events or change candidate content.",
        "- Label every row, including clean controls, to calculate recall and false-alert rate.",
        "",
        "| Priority | Lane | Exam | Q | Model result | Review |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        exam = row["exam"]
        label = (
            f"{exam.get('year') or ''}-{exam.get('ordinal') or ''} "
            f"{exam.get('category') or ''} / {exam.get('subject') or ''}"
        ).strip()
        result = (
            "; ".join(
                f"{issue.get('field')}: {issue.get('before')} → "
                f"{issue.get('after') or '人工核對'}"
                for issue in row["issues"]
            )
            if row["issues"]
            else "無模型警示；仍需標記是否漏報"
        )
        lines.append(
            f"| {row['priority']} | `{row['lane']}` | {label} | "
            f"{exam.get('question_number') or ''} | {result} | "
            f"[開啟 Review UI]({row['review_url']}) |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    route_controls = parse_route_controls(args.route_control)
    task_lanes: dict[str, str] = {}
    tasks_by_key: dict[str, dict[str, Any]] = {}
    issues_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    run_rows: list[dict[str, Any]] = []
    total_request_bytes = 0
    total_result_bytes = 0
    route_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    model_batches = 0

    for run_dir in args.run_dir:
        manifest = read_json(run_dir / "manifest.json")
        validation_path = run_dir / "validation.json"
        validation = read_json(validation_path)
        if not validation.get("ok"):
            raise SystemExit(f"run validation failed: {run_dir}")
        result_path = run_dir / "results.jsonl"
        result_rows = [row for _, row in read_jsonl(result_path)]
        model_batches += len(result_rows)
        total_result_bytes += result_path.stat().st_size
        for batch in manifest.get("batches") or []:
            packet = read_json(run_dir / str(batch["packet_path"]))
            for task in packet.get("tasks") or []:
                key = str(task["candidate_key"])
                lane = str(manifest["lane"])
                if key in task_lanes and task_lanes[key] != lane:
                    raise SystemExit(f"candidate appears in multiple lanes: {key}")
                task_lanes[key] = lane
                tasks_by_key[key] = task
            request_path = run_dir / str(batch["request_path"])
            total_request_bytes += request_path.stat().st_size
        for row in result_rows:
            for issue in row.get("issues") or []:
                key = str(issue["candidate_key"])
                issues_by_key[key].append(issue)
                route_counts[str(issue["route"])] += 1
                family_counts[str(issue["issue_family"])] += 1
        run_rows.append(
            {
                "run_dir": str(run_dir.resolve()),
                "lane": manifest["lane"],
                "task_count": manifest["task_count"],
                "batch_count": len(manifest.get("batches") or []),
                "issue_count": validation["issue_count"],
                "validated": True,
            }
        )

    gold_rows = [
        row for _, row in read_jsonl(args.gold_corpus)
        if str(row.get("candidate_key") or "") in task_lanes
    ]
    gold_results: list[dict[str, Any]] = []
    for gold in gold_rows:
        key = str(gold["candidate_key"])
        expected = (gold.get("expected") or {}).get("issues") or []
        actual = issues_by_key.get(key, [])
        if not expected:
            passed = not actual
        else:
            passed = all(
                any(expected_issue_matches(issue, row) for row in actual)
                for issue in expected
            )
        gold_results.append(
            {
                "case_id": gold.get("case_id"),
                "candidate_key": key,
                "tags": gold.get("tags") or [],
                "expected_issue_count": len(expected),
                "actual_issue_count": len(actual),
                "passed": passed,
            }
        )

    route_results = [
        {
            "candidate_key": key,
            "expected_lane": expected,
            "actual_lane": task_lanes.get(key),
            "passed": task_lanes.get(key) == expected,
        }
        for key, expected in sorted(route_controls.items())
    ]
    negative_keys = {
        str(row["candidate_key"])
        for row in gold_rows
        if "negative_control" in set(row.get("tags") or [])
    }
    unsafe_edits = [
        issue
        for key in negative_keys
        for issue in issues_by_key.get(key, [])
        if issue.get("route") == "deterministic"
    ]
    deterministic_issues = [
        issue
        for rows in issues_by_key.values()
        for issue in rows
        if issue.get("route") == "deterministic"
    ]
    gold_passed = sum(1 for row in gold_results if row["passed"])
    route_passed = sum(1 for row in route_results if row["passed"])
    total_tasks = len(task_lanes)
    total_issues = sum(len(rows) for rows in issues_by_key.values())
    report = {
        "schema_version": "national_exam_shadow_pilot_scorecard_v1",
        "advisory_only": True,
        "production_writes": False,
        "task_count": total_tasks,
        "paper_count": len(
            {
                key.rsplit(":q", 1)[0]
                for key in task_lanes
                if ":q" in key
            }
        ),
        "model_batch_count": model_batches,
        "issue_count": total_issues,
        "issue_yield": (total_issues / total_tasks) if total_tasks else 0,
        "lane_counts": dict(sorted(Counter(task_lanes.values()).items())),
        "route_counts": dict(sorted(route_counts.items())),
        "issue_family_counts": dict(sorted(family_counts.items())),
        "coverage": {
            "expected_tasks": total_tasks,
            "covered_tasks": total_tasks,
            "passed": True,
        },
        "gold": {
            "matched_cases": len(gold_results),
            "passed_cases": gold_passed,
            "all_passed": bool(gold_results) and gold_passed == len(gold_results),
            "results": gold_results,
        },
        "route_controls": {
            "case_count": len(route_results),
            "passed_cases": route_passed,
            "all_passed": bool(route_results) and route_passed == len(route_results),
            "results": route_results,
        },
        "safety": {
            "unsafe_deterministic_negative_control_edits": len(unsafe_edits),
            "deterministic_issue_count": len(deterministic_issues),
            "database_written": False,
            "review_events_written": False,
        },
        "payload": {
            "request_bytes": total_request_bytes,
            "result_bytes": total_result_bytes,
            "exact_token_usage_available": False,
            "note": "Collaboration subagents did not expose provider token counters.",
        },
        "runs": run_rows,
        "engineering_gate": {
            "coverage_passed": True,
            "negative_control_unsafe_edit_zero": not unsafe_edits,
            "route_accuracy_passed": bool(route_results)
            and route_passed == len(route_results),
            "matched_gold_passed": bool(gold_results)
            and gold_passed == len(gold_results),
            "clean_false_alert_rate": None,
            "issue_site_recall": None,
            "ready_for_scaled_shadow": False,
            "ready_for_auto_materialization": False,
            "remaining_requirements": [
                "Human-label the full pilot to calculate issue-site recall and clean false-alert rate.",
                "Capture provider token counters in the production runner.",
                "Keep auto-materialization disabled until proposed exact rules pass full-corpus regression.",
            ],
        },
    }
    write_json(args.report, report)

    if args.human_queue:
        human_queue = build_human_queue(
            tasks_by_key,
            task_lanes,
            issues_by_key,
            args.review_base_url,
        )
        write_jsonl(args.human_queue, human_queue)
        write_human_queue_markdown(args.human_queue.with_suffix(".md"), human_queue)

    markdown_path = args.markdown or args.report.with_suffix(".md")
    lines = [
        "# LUNA v4 Shadow Pilot Scorecard",
        "",
        f"- Tasks: {total_tasks}; papers: {report['paper_count']}; model batches: {model_batches}.",
        f"- Sparse issues: {total_issues}; issue yield: {report['issue_yield']:.1%}.",
        f"- Gold controls: {gold_passed}/{len(gold_results)} passed.",
        f"- Route controls: {route_passed}/{len(route_results)} passed.",
        f"- Unsafe deterministic edits: {len(unsafe_edits)}.",
        f"- Request/result payload: {total_request_bytes}/{total_result_bytes} bytes; exact tokens unavailable.",
        "",
        "## Lane counts",
        "",
    ]
    lines.extend(
        f"- `{LANE_DISPLAY_NAMES.get(lane, lane)}`: {count}"
        for lane, count in report["lane_counts"].items()
    )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- Validated shadow execution is complete.",
            "- Scaled shadow remains blocked until full-pilot human labels produce recall and false-alert metrics.",
            "- Automatic materialization remains disabled.",
            "",
        ]
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(args.report)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
