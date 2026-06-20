#!/usr/bin/env python3
"""
Build question candidate JSONL and QA flags from completed MinerU markdown.

This is an ingestion preflight tool. It does not write to PostgreSQL and does
not mutate official PDFs or MinerU output. The output is intended for the
human-in-the-loop review UI and later formal ingestion.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
PAIR_INDEX_DIR = ASSET_ROOT / "Registry" / "paired_indexes"
OUTPUT_ROOT = ASSET_ROOT / "30_normalized_items"
MINERU_ROOT = ASSET_ROOT / "20_mineru_output"
PARSER_VERSION = "moex_mineru_candidate_v0.3"

OPTION_RE = re.compile(r"(?m)^\s*(?:[（(]([A-E])[\)）]|([A-E])[\.\、．])\s*")
QUESTION_START_RE_MODERN = re.compile(r"(?m)^(\d{1,3})[\.、．]\s*(\S.*)$")
QUESTION_START_RE_LEGACY = re.compile(r"(?m)^(\d{1,3})(?:[\.、．]\s*|\s+)(\S.*)$")
IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HTML_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
DETAILS_BLOCK_RE = re.compile(r"<details\b.*?</details>", re.S | re.I)
STANDALONE_IMAGE_RE = re.compile(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*$")
GROUP_RANGE_RE = re.compile(r"第\s*(\d{1,3})\s*(?:至|到|~|～|-|－)\s*(\d{1,3})\s*題")
IMAGE_HINT_RE = re.compile(r"(下列圖|如圖|如附圖|附圖|圖示|圖中|圖片|照片|影像如下|X光片|x光片|切片圖|表格如下|下表|如下表)")
SUSPICIOUS_RE = re.compile(r"(�|□|▯|_{3,}|\.{6,}|。{3,})")
MARKUP_HINT_RE = re.compile(r"(<sub>|<sup>|\\[a-zA-Z]+|[α-ωΑ-ΩⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|[A-Za-z][0-9][A-Za-z0-9]*|\^[0-9+-]+)")
OCR_CHAR_MAP = str.maketrans(
    {
        "羟": "羥",
        "钙": "鈣",
        "减": "減",
    }
)


@dataclass
class Issue:
    candidate_key: str
    source_registry_key: str
    question_number: str
    issue_code: str
    severity: str
    message: str
    issue_json: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build question candidate JSONL and QA flags from MinerU output.")
    parser.add_argument("--pair-index", type=Path, default=latest_path(PAIR_INDEX_DIR, "question_answer_pairs_detail__*.csv"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--limit", type=int, default=0, help="Limit paired documents for smoke tests. 0 means no limit.")
    parser.add_argument("--registry-key", action="append", default=[], help="Only process selected question registry key(s).")
    parser.add_argument("--group-name", action="append", default=[], help="Only process selected group_name values.")
    parser.add_argument("--include-needs-review", action="store_true", help="Keep candidates even when they have warning issues.")
    return parser.parse_args()


def latest_path(directory: Path, pattern: str) -> Path:
    paths = sorted(directory.glob(pattern))
    if not paths:
        raise SystemExit(f"No file found: {directory}/{pattern}")
    return paths[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if value.startswith("國考題資料夾/"):
        return PROJECT_ROOT / value
    return ASSET_ROOT / value


def relative_to_project(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def mineru_md_for_pdf(pdf_value: str) -> Path | None:
    if not pdf_value:
        return None
    pdf_path = project_path(pdf_value)
    try:
        rel = pdf_path.resolve().relative_to((ASSET_ROOT / "10_official_pdf").resolve())
    except ValueError:
        rel = Path(str(pdf_value).replace("10_official_pdf/", "", 1))
    parent = MINERU_ROOT / rel.with_suffix("")
    stem = pdf_path.stem
    for mode in ("vlm", "hybrid_auto", "ocr"):
        candidate = parent / mode / f"{stem}.md"
        if candidate.exists():
            return candidate
    matches = sorted(parent.glob(f"**/{stem}.md"))
    return matches[0] if matches else None


def normalize_text(value: str) -> str:
    value = DETAILS_BLOCK_RE.sub("", value)
    value = STANDALONE_IMAGE_RE.sub("", value)
    value = value.translate(OCR_CHAR_MAP)
    lines = [line.strip() for line in value.replace("\u3000", " ").splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def collect_image_refs(text: str, md_path: Path) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for raw in IMAGE_REF_RE.findall(text) + HTML_IMG_RE.findall(text):
        image_path = (md_path.parent / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        refs.append(
            {
                "raw_ref": raw,
                "path": str(image_path),
                "relative_path": relative_to_project(image_path),
                "exists": image_path.exists(),
                "bytes": image_path.stat().st_size if image_path.exists() else None,
            }
        )
    return refs


def parse_question_block(number: str, body: str, md_path: Path) -> dict[str, Any]:
    markers = list(OPTION_RE.finditer(body))
    stem = normalize_text(body[: markers[0].start()] if markers else body)
    options: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        label = marker.group(1) or marker.group(2)
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        option_text = normalize_text(body[start:end])
        options.append(
            {
                "key": label,
                "text": option_text,
                "image": None,
                "markup": markup_payload(option_text),
            }
        )
    image_refs = collect_image_refs(body, md_path)
    group_ref = infer_group_ref(stem, number)
    return {
        "question_number": number,
        "stem": stem,
        "stem_markup": markup_payload(stem),
        "options": options,
        "image_refs": image_refs,
        "question_type": "multiple_choice" if options else "unknown",
        "group_ref": group_ref,
        "raw_block": body.strip(),
    }


def question_start_re_for_year(year: str | None) -> re.Pattern[str]:
    try:
        roc_year = int(year or "")
    except ValueError:
        roc_year = 999
    if roc_year <= 105:
        return QUESTION_START_RE_LEGACY
    return QUESTION_START_RE_MODERN


def parse_questions(markdown: str, md_path: Path, year: str | None = None) -> list[dict[str, Any]]:
    starts = list(question_start_re_for_year(year).finditer(markdown))
    questions: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        number = start.group(1)
        body_start = start.start(2)
        body_end = starts[index + 1].start() if index + 1 < len(starts) else len(markdown)
        body = markdown[body_start:body_end]
        try:
            int(number)
        except ValueError:
            continue
        questions.append(parse_question_block(number, body, md_path))
    return questions


def table_cells(row_html: str) -> list[str]:
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.S | re.I)
    clean_cells: list[str] = []
    for cell in cells:
        clean = re.sub(r"<[^>]+>", "", cell)
        clean_cells.append(normalize_text(clean))
    return clean_cells


def parse_correction_notes(markdown: str) -> dict[str, list[str]]:
    notes: dict[str, list[str]] = {}
    for number, values in re.findall(r"第\s*(\d+)\s*題答\s*([A-E/或、]+)\s*者均給分", markdown):
        notes[str(int(number))] = [item for item in re.split(r"或|/|、", values) if item]
    return notes


def normalize_question_number(value: str) -> str | None:
    match = re.search(r"\d{1,3}", str(value))
    if not match:
        return None
    return str(int(match.group(0)))


def parse_answers(markdown: str) -> dict[str, dict[str, Any]]:
    corrections = parse_correction_notes(markdown)
    answers: dict[str, dict[str, Any]] = {}
    for table in re.findall(r"<table.*?>(.*?)</table>", markdown, flags=re.S | re.I):
        rows = re.findall(r"<tr.*?>(.*?)</tr>", table, flags=re.S | re.I)
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
            number_key = normalize_question_number(number)
            if number_key is None:
                continue
            if answer == "#" and number_key in corrections:
                accepted = corrections[number_key]
                answers[number_key] = {
                    "answer": "|".join(accepted),
                    "accepted_values": accepted,
                    "raw_answer": answer,
                    "is_special_correction": True,
                }
            else:
                answers[number_key] = {
                    "answer": answer,
                    "accepted_values": [answer] if answer else [],
                    "raw_answer": answer,
                    "is_special_correction": False,
                }
    return answers


def infer_group_ref(stem: str, number: str) -> str | None:
    for start, end in GROUP_RANGE_RE.findall(stem):
        try:
            n = int(number)
            a = int(start)
            b = int(end)
        except ValueError:
            continue
        if a <= n <= b:
            return f"q{a:03d}-q{b:03d}"
    return None


def markup_payload(text: str) -> dict[str, Any] | None:
    if not text or not MARKUP_HINT_RE.search(text):
        return None
    return {
        "plain": text,
        "markup": text,
        "format": "plain-or-mineru-markdown",
        "needs_review": bool(re.search(r"(<sub>|<sup>|\\[a-zA-Z]+|\^[0-9+-]+)", text)),
    }


def add_issue(
    issues: list[Issue],
    candidate_key: str,
    source_registry_key: str,
    question_number: str,
    issue_code: str,
    severity: str,
    message: str,
    issue_json: dict[str, Any] | None = None,
) -> None:
    issues.append(
        Issue(
            candidate_key=candidate_key,
            source_registry_key=source_registry_key,
            question_number=question_number,
            issue_code=issue_code,
            severity=severity,
            message=message,
            issue_json=issue_json or {},
        )
    )


def candidate_issues(candidate: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    key = candidate["candidate_key"]
    source = candidate["source_registry_key"]
    number = str(candidate["question_number"])
    stem = candidate["stem"]
    options = candidate["options"]
    if not stem:
        add_issue(issues, key, source, number, "empty_stem", "error", "題幹為空。")
    elif len(stem) < 8:
        add_issue(issues, key, source, number, "short_stem", "warning", "題幹過短，可能切題錯誤。", {"length": len(stem)})
    if SUSPICIOUS_RE.search(stem):
        add_issue(issues, key, source, number, "suspicious_ocr_chars", "warning", "題幹含疑似 OCR 亂碼或佔位符。")
    labels = [item["key"] for item in options]
    if len(options) < 4:
        add_issue(issues, key, source, number, "too_few_options", "error", "選項少於 4 個。", {"option_labels": labels})
    if len(labels) != len(set(labels)):
        add_issue(issues, key, source, number, "duplicate_option_label", "error", "選項標籤重複。", {"option_labels": labels})
    for option in options:
        if not option["text"]:
            add_issue(issues, key, source, number, "empty_option", "error", f"選項 {option['key']} 為空。")
    answer = candidate.get("answer")
    if answer is None:
        add_issue(issues, key, source, number, "missing_answer", "error", "答案 PDF 未找到對應題號。")
    elif isinstance(answer, str) and answer and not re.fullmatch(r"[A-E#|/或、]+", answer):
        add_issue(issues, key, source, number, "unexpected_answer_value", "warning", "答案值格式不常見。", {"answer": answer})
    image_refs = candidate.get("image_refs", [])
    if IMAGE_HINT_RE.search(stem) and not image_refs:
        add_issue(issues, key, source, number, "image_hint_without_asset", "warning", "題幹提到圖表或影像，但未偵測到圖片引用。")
    for ref in image_refs:
        if not ref["exists"]:
            add_issue(issues, key, source, number, "missing_image_asset", "error", "Markdown 引用的圖片不存在。", ref)
        elif ref.get("bytes") == 0:
            add_issue(issues, key, source, number, "empty_image_asset", "error", "圖片檔案大小為 0。", ref)
    stem_markup = candidate.get("stem_markup") or {}
    if stem_markup.get("needs_review"):
        add_issue(issues, key, source, number, "markup_needs_review", "warning", "題幹含公式、上下標或 markup，建議人工預覽。")
    return issues


def document_issues(candidates: list[dict[str, Any]], source_registry_key: str) -> list[Issue]:
    issues: list[Issue] = []
    numbers: list[int] = []
    by_number: dict[int, list[str]] = {}
    for candidate in candidates:
        try:
            number = int(candidate["question_number"])
        except ValueError:
            continue
        numbers.append(number)
        by_number.setdefault(number, []).append(candidate["candidate_key"])
    if not numbers:
        return issues
    for number, keys in by_number.items():
        if len(keys) > 1:
            for key in keys:
                add_issue(issues, key, source_registry_key, str(number), "duplicate_question_number", "error", "同一份考卷內題號重複。", {"candidate_keys": keys})
    expected = set(range(min(numbers), max(numbers) + 1))
    missing = sorted(expected - set(numbers))
    if missing:
        key = candidates[0]["candidate_key"]
        add_issue(issues, key, source_registry_key, "", "question_number_gap", "warning", "題號不連續，可能有缺題或 parser 未切到。", {"missing_numbers": missing[:50]})
    return issues


def quality_status(issues: list[Issue]) -> str:
    severities = {issue.severity for issue in issues}
    if "blocked" in severities or "error" in severities:
        return "blocked"
    if "warning" in severities:
        return "needs_review"
    return "pass"


def build_candidates_for_pair(row: dict[str, str]) -> tuple[list[dict[str, Any]], list[Issue], dict[str, Any]]:
    q_md = mineru_md_for_pdf(row.get("question_pdf") or row.get("question_pdf_relative", ""))
    a_md = mineru_md_for_pdf(row.get("answer_pdf_primary") or row.get("answer_pdf_primary_relative", ""))
    source_registry_key = row["question_registry_key"]
    meta = {
        "pair_key": row["pair_key"],
        "source_registry_key": source_registry_key,
        "question_markdown": str(q_md) if q_md else None,
        "answer_markdown": str(a_md) if a_md else None,
        "status": "planned",
    }
    if q_md is None:
        issue = Issue("", source_registry_key, "", "missing_question_markdown", "blocked", "找不到題目 MinerU markdown。", {})
        meta["status"] = "missing_question_markdown"
        return [], [issue], meta
    if a_md is None:
        issue = Issue("", source_registry_key, "", "missing_answer_markdown", "blocked", "找不到 primary answer MinerU markdown。", {})
        meta["status"] = "missing_answer_markdown"
        return [], [issue], meta
    q_text = q_md.read_text(encoding="utf-8", errors="replace")
    a_text = a_md.read_text(encoding="utf-8", errors="replace")
    parsed_questions = parse_questions(q_text, q_md, row.get("year"))
    answers = parse_answers(a_text)
    candidates: list[dict[str, Any]] = []
    issues: list[Issue] = []
    number_occurrences: dict[str, int] = {}
    for parsed in parsed_questions:
        number = str(int(parsed["question_number"]))
        number_occurrences[number] = number_occurrences.get(number, 0) + 1
        candidate_key = f"{source_registry_key}:q{int(number):03d}"
        if number_occurrences[number] > 1:
            candidate_key = f"{candidate_key}:dup{number_occurrences[number]:02d}"
        answer_payload = answers.get(number)
        candidate = {
            "candidate_key": candidate_key,
            "source_registry_key": source_registry_key,
            "canonical_question_key": f"{source_registry_key}:q{int(number):03d}",
            "question_number_occurrence": number_occurrences[number],
            "answer_source_registry_key": row.get("answer_registry_key_primary") or None,
            "question_number": number,
            "stem": parsed["stem"],
            "stem_markup": parsed["stem_markup"],
            "stem_image": None,
            "options": parsed["options"],
            "answer": answer_payload["answer"] if answer_payload else None,
            "answer_payload": answer_payload,
            "explanation": None,
            "question_type": parsed["question_type"],
            "group_ref": parsed["group_ref"],
            "image_refs": parsed["image_refs"],
            "metadata": {
                "parser_version": PARSER_VERSION,
                "group_name": row.get("group_name"),
                "year": row.get("year"),
                "exam_ordinal": row.get("exam_ordinal"),
                "exam_code": row.get("exam_code"),
                "category_code": row.get("category_code"),
                "subject_code": row.get("subject_code"),
                "official_category_name": row.get("official_category_name"),
                "normalized_category_name": row.get("normalized_category_name"),
                "official_subject_name": row.get("official_subject_name"),
                "normalized_subject_name": row.get("normalized_subject_name"),
                "question_pdf": row.get("question_pdf"),
                "question_pdf_relative": row.get("question_pdf_relative"),
                "answer_pdf_primary": row.get("answer_pdf_primary"),
                "answer_pdf_primary_relative": row.get("answer_pdf_primary_relative"),
                "answer_role_primary": row.get("answer_role_primary"),
                "question_markdown": str(q_md),
                "question_markdown_relative": relative_to_project(q_md),
                "answer_markdown": str(a_md),
                "answer_markdown_relative": relative_to_project(a_md),
                "raw_block": parsed["raw_block"],
            },
        }
        own_issues = candidate_issues(candidate)
        candidate["quality_status"] = quality_status(own_issues)
        candidate["issue_count"] = len(own_issues)
        candidates.append(candidate)
        issues.extend(own_issues)
    if not candidates:
        issues.append(Issue("", source_registry_key, "", "no_questions_parsed", "blocked", "題目 markdown 未解析出任何題目。", {"markdown": str(q_md)}))
    doc_issues = document_issues(candidates, source_registry_key)
    issues.extend(doc_issues)
    issues_by_key: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.candidate_key:
            issues_by_key.setdefault(issue.candidate_key, []).append(issue)
    for candidate in candidates:
        related = issues_by_key.get(candidate["candidate_key"], [])
        candidate["quality_status"] = quality_status(related)
        candidate["issue_count"] = len(related)
    meta["status"] = "ok"
    meta["candidate_count"] = len(candidates)
    meta["issue_count"] = len(issues)
    return candidates, issues, meta


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_issues_csv(path: Path, issues: list[Issue]) -> None:
    fields = ["candidate_key", "source_registry_key", "question_number", "issue_code", "severity", "message", "issue_json"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for issue in issues:
            writer.writerow(
                {
                    "candidate_key": issue.candidate_key,
                    "source_registry_key": issue.source_registry_key,
                    "question_number": issue.question_number,
                    "issue_code": issue.issue_code,
                    "severity": issue.severity,
                    "message": issue.message,
                    "issue_json": json.dumps(issue.issue_json, ensure_ascii=False, sort_keys=True),
                }
            )


def main() -> None:
    args = parse_args()
    rows = read_csv(args.pair_index)
    if args.registry_key:
        wanted = set(args.registry_key)
        rows = [row for row in rows if row.get("question_registry_key") in wanted]
    if args.group_name:
        wanted_groups = set(args.group_name)
        rows = [row for row in rows if row.get("group_name") in wanted_groups]
    rows = [row for row in rows if row.get("pair_status") in {"paired_ans_only", "paired_mod_primary"}]
    if args.limit > 0:
        rows = rows[: args.limit]

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / "question_candidates" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = run_dir / f"question_candidates__{timestamp}.jsonl"
    issue_path = run_dir / f"question_parse_issues__{timestamp}.csv"
    summary_path = run_dir / f"question_candidate_summary__{timestamp}.json"

    all_candidates: list[dict[str, Any]] = []
    all_issues: list[Issue] = []
    document_summaries: list[dict[str, Any]] = []
    for row in rows:
        candidates, issues, meta = build_candidates_for_pair(row)
        all_candidates.extend(candidates)
        all_issues.extend(issues)
        document_summaries.append(meta)

    write_jsonl(candidate_path, all_candidates)
    write_issues_csv(issue_path, all_issues)
    summary = {
        "parser_version": PARSER_VERSION,
        "pair_index": str(args.pair_index),
        "run_dir": str(run_dir),
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "paired_documents_seen": len(rows),
        "candidate_count": len(all_candidates),
        "issue_count": len(all_issues),
        "quality_status_counts": {
            status: sum(1 for item in all_candidates if item.get("quality_status") == status)
            for status in ("pass", "needs_review", "blocked")
        },
        "document_status_counts": {
            status: sum(1 for item in document_summaries if item.get("status") == status)
            for status in sorted({item.get("status") for item in document_summaries})
        },
        "documents": document_summaries,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
