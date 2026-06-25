#!/usr/bin/env python3
"""
Backfill the table tail of 101-2 medical technologist microbiology.

This is intentionally a one-document repair. The 101-2 microbiology MinerU
markdown contains questions 56-80 inside a single HTML table that the general
parser previously left attached to question 55 option D. Do not widen parser
rules from this script.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import build_question_candidates_from_mineru as builder


SOURCE_REGISTRY_KEY = "moex:101110:108:0503:1:question"
ANSWER_REGISTRY_KEY = "moex:101110:108:0503:1:correction"
TARGET_KEY_55 = f"{SOURCE_REGISTRY_KEY}:q055"
QUESTION_RANGE = range(56, 81)
BACKFILL_VERSION = "manual_table_tail_backfill_1012_microbiology_v1"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = PROJECT_ROOT / "國考題資料夾" / "30_normalized_items" / "question_candidates" / "20260620-213413"
DEFAULT_CANDIDATE_PATH = RUN_DIR / "question_candidates__20260620-213413.jsonl"
DEFAULT_ISSUE_PATH = RUN_DIR / "question_parse_issues__20260620-213413.csv"
DEFAULT_REVIEW_LOG = RUN_DIR / "question_review_events.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill q56-q80 for 101-2 microbiology table tail.")
    parser.add_argument("--candidate-path", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--issue-path", type=Path, default=DEFAULT_ISSUE_PATH)
    parser.add_argument("--review-log", type=Path, default=DEFAULT_REVIEW_LOG)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def clean_cell(cell_html: str) -> str:
    text = re.sub(r"<[^>]+>", "", cell_html)
    return builder.normalize_text(html.unescape(text))


def extract_target_table(markdown: str) -> str:
    for table in re.findall(r"<table.*?</table>", markdown, flags=re.S | re.I):
        if re.search(r"<td[^>]*>\s*56\s*</td>", table, flags=re.I):
            return table
    raise SystemExit("Could not find q56 table in question markdown.")


def parse_table_tail(markdown: str) -> dict[str, dict[str, Any]]:
    table = extract_target_table(markdown)
    rows = re.findall(r"<tr.*?>(.*?)</tr>", table, flags=re.S | re.I)
    parsed: dict[str, dict[str, Any]] = {}
    current_number: str | None = None
    current_stem = ""
    current_options: list[dict[str, str]] = []

    def flush() -> None:
        nonlocal current_number, current_stem, current_options
        if current_number is None:
            return
        by_label = {item["key"]: item["text"] for item in current_options}
        parsed[current_number] = {
            "stem": current_stem,
            "options": [{"key": label, "text": by_label.get(label, "")} for label in "ABCD"],
            "raw_table_fragment": "\n".join(
                [current_stem] + [f"({item['key']}) {item['text']}" for item in current_options]
            ),
        }
        current_number = None
        current_stem = ""
        current_options = []

    for row in rows:
        cells = [clean_cell(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)]
        if not cells:
            continue
        number_match = re.fullmatch(r"\d{1,3}", cells[0] or "")
        if number_match:
            flush()
            current_number = str(int(cells[0]))
            current_stem = " ".join(cell for cell in cells[1:] if cell)
            continue
        if current_number is None:
            continue
        for cell in cells[1:]:
            match = re.match(r"^\(([A-D])\)\s*(.*)$", cell)
            if match:
                current_options.append({"key": match.group(1), "text": match.group(2).strip()})
    flush()
    return parsed


def latest_review_by_key(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if key:
                latest[key] = event
    return latest


def strip_table_tail_from_q55(candidate: dict[str, Any]) -> bool:
    changed = False
    for option in candidate.get("options") or []:
        if option.get("key") != "D":
            continue
        text = str(option.get("text") or "")
        if "<table" not in text:
            continue
        clean_text = builder.normalize_text(re.sub(r"<table.*$", "", text, flags=re.S | re.I))
        option["text"] = clean_text
        option["markup"] = builder.markup_payload(clean_text)
        changed = True
    metadata = candidate.get("metadata") or {}
    raw_block = str(metadata.get("raw_block") or "")
    if "<table" in raw_block:
        metadata["raw_block"] = builder.normalize_text(re.sub(r"<table.*$", "", raw_block, flags=re.S | re.I))
        metadata["backfill_repair"] = BACKFILL_VERSION
        candidate["metadata"] = metadata
        changed = True
    return changed


def make_candidate(number: int, parsed: dict[str, Any], base: dict[str, Any], answer_payload: dict[str, Any] | None) -> dict[str, Any]:
    number_text = str(number)
    candidate_key = f"{SOURCE_REGISTRY_KEY}:q{number:03d}"
    metadata = dict(base.get("metadata") or {})
    metadata["parser_version"] = BACKFILL_VERSION
    metadata["raw_block"] = parsed["raw_table_fragment"]
    metadata["backfill_source"] = "101-2 microbiology HTML table tail in MinerU markdown"
    options = []
    for raw_order, option in enumerate(parsed["options"], start=1):
        text = builder.normalize_text(option["text"])
        options.append(
            {
                "key": option["key"],
                "raw_order": raw_order,
                "text": text,
                "image": None,
                "markup": builder.markup_payload(text),
            }
        )
    stem = builder.normalize_text(parsed["stem"])
    candidate = {
        "candidate_key": candidate_key,
        "source_registry_key": SOURCE_REGISTRY_KEY,
        "canonical_question_key": candidate_key,
        "question_number_occurrence": 1,
        "answer_source_registry_key": base.get("answer_source_registry_key") or ANSWER_REGISTRY_KEY,
        "question_number": number_text,
        "stem": stem,
        "stem_markup": builder.markup_payload(stem),
        "stem_image": None,
        "options": options,
        "answer": answer_payload["answer"] if answer_payload else None,
        "answer_payload": answer_payload,
        "explanation": None,
        "question_type": "multiple_choice",
        "group_ref": None,
        "image_refs": [],
        "metadata": metadata,
    }
    issues = builder.candidate_issues(candidate)
    candidate["quality_status"] = builder.quality_status(issues)
    candidate["issue_count"] = len(issues)
    return candidate


def recompute_issues(candidates: list[dict[str, Any]]) -> list[builder.Issue]:
    issues: list[builder.Issue] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        own_issues = builder.candidate_issues(candidate)
        candidate["quality_status"] = builder.quality_status(own_issues)
        candidate["issue_count"] = len(own_issues)
        issues.extend(own_issues)
        by_source[candidate["source_registry_key"]].append(candidate)
    doc_issues = []
    for source, source_candidates in by_source.items():
        doc_issues.extend(builder.document_issues(source_candidates, source))
    issues.extend(doc_issues)
    by_candidate_key: dict[str, list[builder.Issue]] = defaultdict(list)
    for issue in issues:
        if issue.candidate_key:
            by_candidate_key[issue.candidate_key].append(issue)
    for candidate in candidates:
        related = by_candidate_key.get(candidate["candidate_key"], [])
        candidate["quality_status"] = builder.quality_status(related)
        candidate["issue_count"] = len(related)
    return issues


def append_reset_event(review_log: Path, previous: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    event = {
        "action": "reset_review",
        "candidate_key": TARGET_KEY_55,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "previous_action": previous.get("action"),
        "previous_notes": previous.get("notes", ""),
        "previous_reviewed_at": previous.get("created_at"),
        "reset_notes": "101-2 微生物第 55 題 D 選項尾端誤含第 56-80 題 HTML 表格；已切除表格尾段並另行補匯入 56-80 題。",
        "reviewer": "backfill_1012_microbiology_table_tail",
    }
    if not dry_run:
        with review_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def make_backup(paths: list[Path]) -> Path:
    backup_dir = RUN_DIR / "_repair_backups" / f"{datetime.now():%Y%m%d-%H%M%S}-1012-microbiology-table-tail"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def main() -> None:
    args = parse_args()
    candidates = load_jsonl(args.candidate_path)
    by_key = {candidate["candidate_key"]: candidate for candidate in candidates}
    source_candidates = [item for item in candidates if item.get("source_registry_key") == SOURCE_REGISTRY_KEY]
    if not source_candidates:
        raise SystemExit(f"No candidates found for {SOURCE_REGISTRY_KEY}")
    base = by_key.get(TARGET_KEY_55) or source_candidates[-1]
    question_md = Path(base["metadata"]["question_markdown"])
    answer_md = Path(base["metadata"]["answer_markdown"])
    parsed_tail = parse_table_tail(question_md.read_text(encoding="utf-8", errors="replace"))
    answers = builder.parse_answers(answer_md.read_text(encoding="utf-8", errors="replace"))

    existing_numbers = {int(candidate["question_number"]) for candidate in source_candidates if str(candidate.get("question_number", "")).isdigit()}
    missing_numbers = [number for number in QUESTION_RANGE if number not in existing_numbers]
    bad_numbers = [number for number in QUESTION_RANGE if str(number) not in parsed_tail]
    if bad_numbers:
        raise SystemExit(f"Table parser did not find expected questions: {bad_numbers}")

    new_candidates = [
        make_candidate(number, parsed_tail[str(number)], base, answers.get(str(number)))
        for number in missing_numbers
    ]
    q55_changed = strip_table_tail_from_q55(base)

    latest_reviews = latest_review_by_key(args.review_log)
    previous_q55 = latest_reviews.get(TARGET_KEY_55, {})
    should_reset_q55 = q55_changed and previous_q55.get("action") not in {None, "reset_review", "unreviewed"}

    print(f"source candidates before: {len(source_candidates)}")
    print(f"existing numbers: {min(existing_numbers)}-{max(existing_numbers)}")
    print(f"missing tail candidates to add: {len(new_candidates)} ({missing_numbers[:3]}...{missing_numbers[-3:] if missing_numbers else []})")
    print(f"q55 table tail cleaned: {q55_changed}")
    print(f"q55 reset_review to append: {should_reset_q55}")
    if args.dry_run:
        for candidate in new_candidates[:3]:
            print(json.dumps({k: candidate[k] for k in ("candidate_key", "stem", "answer", "quality_status", "issue_count")}, ensure_ascii=False))
        return

    backup_dir = make_backup([args.candidate_path, args.issue_path, args.review_log])
    existing_keys = {candidate["candidate_key"] for candidate in candidates}
    additions = [candidate for candidate in new_candidates if candidate["candidate_key"] not in existing_keys]
    insert_after = max(index for index, candidate in enumerate(candidates) if candidate.get("source_registry_key") == SOURCE_REGISTRY_KEY)
    candidates[insert_after + 1 : insert_after + 1] = additions
    issues = recompute_issues(candidates)
    write_jsonl(args.candidate_path, candidates)
    builder.write_issues_csv(args.issue_path, issues)
    if should_reset_q55:
        append_reset_event(args.review_log, previous_q55, dry_run=False)
    print(f"backup: {backup_dir}")
    print(f"added candidates: {len(additions)}")
    print(f"total candidates now: {len(candidates)}")


if __name__ == "__main__":
    main()
