#!/usr/bin/env python3
"""
Export MOEX official exam/category/subject catalog without downloading PDFs.

The MOEX result page exposes stable checkbox IDs:

  ctl00_holderContent_chk_{exam_code}_{category_code}
  ctl00_holderContent_chk_{exam_code}_{category_code}_{subject_code}

and PDF links:

  wHandExamQandA_File.ashx?t=Q|S|M&code={exam_code}&c={category_code}&s={subject_code}&q={question_set}

This script records those official references so later download/gap-scan work
does not depend on messy local folders or filenames.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "moex_official_catalog"

BASE_URL = "https://wwwq.moex.gov.tw/exam/"
SEARCH_URL = BASE_URL + "wFrmExamQandASearch.aspx"
ROC_OFFSET = 1911
EXAM_CODE_RE = re.compile(r"^(\d{2,3})(\d{3})$")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tw-national-exam-catalog/0.1)"}

TYPE_TO_ROLE = {
    "Q": "question",
    "S": "answer",
    "M": "correction",
}


@dataclass
class MoexSubjectRecord:
    year: int
    exam_code: str
    exam_label: str
    exam_level: str
    category_code: str
    category_label: str
    category_name: str
    subject_code: str
    subject_name: str
    question_set: str = "1"
    has_question: bool = False
    has_answer: bool = False
    has_correction: bool = False
    question_url: str = ""
    answer_url: str = ""
    correction_url: str = ""
    registry_key: str = ""
    source_url: str = SEARCH_URL
    notes: list[str] = field(default_factory=list)


def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


SSL_CTX = ssl_ctx()


def get(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as response:
        return response.read().decode("utf-8")


def post(url: str, fields: dict) -> str:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            **HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url,
        },
    )
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as response:
        return response.read().decode("utf-8")


def extract_form_fields(html_text: str) -> dict:
    def value(name: str) -> str:
        match = re.search(rf'{re.escape(name)}"[^>]*value="([^"]*)"', html_text)
        return match.group(1) if match else ""

    return {
        "__VIEWSTATE": value("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": value("__VIEWSTATEGENERATOR"),
        "__VIEWSTATEENCRYPTED": "",
        "__EVENTVALIDATION": value("__EVENTVALIDATION"),
    }


def parse_exam_codes_from_html(html_text: str, year_start: int, year_end: int) -> list[tuple[str, str]]:
    block_match = re.search(
        r'<select[^>]*id="ctl00_holderContent_ddlExamCode"[^>]*>(.*?)</select>',
        html_text,
        re.DOTALL,
    )
    if not block_match:
        return []
    options = re.findall(r'<option value="(\d+)">(.*?)</option>', block_match.group(1))
    result = []
    for code, label in options:
        match = EXAM_CODE_RE.match(code)
        if not match:
            continue
        code_year = int(match.group(1))
        if year_start <= code_year <= year_end:
            result.append((code, strip_tags(label)))
    return result


def load_exam_codes_for_year(form_fields: dict, year_roc: int) -> list[tuple[str, str]]:
    western_year = str(year_roc + ROC_OFFSET)
    fields = {
        **form_fields,
        "__EVENTTARGET": "ctl00$holderContent$wUctlExamYearStart$ddlExamYear",
        "__EVENTARGUMENT": "",
        "ctl00$holderContent$wUctlExamYearStart$ddlExamYear": western_year,
        "ctl00$holderContent$wUctlExamYearEnd$ddlExamYear": western_year,
        "ctl00$holderContent$ddlExamCode": "",
    }
    html_text = post(SEARCH_URL, fields)
    return parse_exam_codes_from_html(html_text, year_roc, year_roc)


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_exam_label(value: str) -> str:
    value = strip_tags(value)
    value = re.sub(r"\s*本考試所有測\s*驗題標準答案.*$", "", value)
    return value.strip()


def split_category_label(label: str) -> tuple[str, str]:
    label = strip_tags(label)
    if "_" in label:
        level, name = label.split("_", 1)
        return level.strip(), name.strip()
    return "", label.strip()


def load_exam_result_html(home_fields: dict, code: str) -> str:
    match = EXAM_CODE_RE.match(code)
    if not match:
        raise ValueError(f"Invalid MOEX exam code: {code}")

    year_roc = int(match.group(1))
    western_year = str(year_roc + ROC_OFFSET)

    fields = {
        **home_fields,
        "__EVENTTARGET": "ctl00$holderContent$wUctlExamYearStart$ddlExamYear",
        "__EVENTARGUMENT": "",
        "ctl00$holderContent$wUctlExamYearStart$ddlExamYear": western_year,
        "ctl00$holderContent$wUctlExamYearEnd$ddlExamYear": western_year,
        "ctl00$holderContent$ddlExamCode": "",
    }
    html1 = post(SEARCH_URL, fields)

    fields = {
        **extract_form_fields(html1),
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "ctl00$holderContent$wUctlExamYearStart$ddlExamYear": western_year,
        "ctl00$holderContent$wUctlExamYearEnd$ddlExamYear": western_year,
        "ctl00$holderContent$ddlExamCode": code,
        "ctl00$holderContent$btnSearch": "查詢",
    }
    return post(SEARCH_URL, fields)


def extract_exam_label(html_text: str, fallback: str) -> str:
    fallback = strip_tags(fallback)
    if fallback and fallback != "指定代號":
        return fallback
    title_match = re.search(
        r"(\d{2,3}年[^<]*?考試)(?:\s*</td>|\s*<a|\s*本考試所有)",
        strip_tags(html_text),
    )
    if title_match:
        return normalize_exam_label(title_match.group(1))
    return strip_tags(fallback)


def parse_catalog_from_html(year: int, code: str, exam_label: str, html_text: str) -> list[MoexSubjectRecord]:
    category_by_code: dict[str, tuple[str, str]] = {}
    subjects: dict[tuple[str, str], MoexSubjectRecord] = {}

    label_pattern = re.compile(
        rf'<label[^>]+for="ctl00_holderContent_chk_{re.escape(code)}_(\d+)(?:_(\d+))?"[^>]*>(.*?)</label>',
        re.DOTALL,
    )
    for match in label_pattern.finditer(html_text):
        category_code = match.group(1)
        subject_code = match.group(2)
        label = strip_tags(match.group(3))
        if not label:
            continue
        if subject_code is None:
            exam_level, category_name = split_category_label(label)
            category_by_code[category_code] = (label, category_name)
            continue

        category_label, category_name = category_by_code.get(category_code, ("", ""))
        exam_level, parsed_category_name = split_category_label(category_label)
        if parsed_category_name:
            category_name = parsed_category_name

        key = (category_code, subject_code)
        subjects[key] = MoexSubjectRecord(
            year=year,
            exam_code=code,
            exam_label=exam_label,
            exam_level=exam_level,
            category_code=category_code,
            category_label=category_label,
            category_name=category_name,
            subject_code=subject_code,
            subject_name=label,
            registry_key=f"moex:{code}:{category_code}:{subject_code}:1",
        )

    link_pattern = re.compile(r'href="(wHandExamQandA_File\.ashx\?[^"]+)"')
    for raw_link in link_pattern.findall(html_text):
        href = html.unescape(raw_link)
        url = urllib.parse.urljoin(BASE_URL, href)
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        file_type = params.get("t")
        if file_type not in TYPE_TO_ROLE:
            continue
        if params.get("code") != code:
            continue
        category_code = params.get("c", "")
        subject_code = params.get("s", "")
        if not category_code or not subject_code:
            continue
        question_set = params.get("q") or "1"
        record = subjects.get((category_code, subject_code))
        if record is None:
            category_label, category_name = category_by_code.get(category_code, ("", ""))
            exam_level, parsed_category_name = split_category_label(category_label)
            record = MoexSubjectRecord(
                year=year,
                exam_code=code,
                exam_label=exam_label,
                exam_level=exam_level,
                category_code=category_code,
                category_label=category_label,
                category_name=parsed_category_name or category_name,
                subject_code=subject_code,
                subject_name="",
                question_set=question_set,
                registry_key=f"moex:{code}:{category_code}:{subject_code}:{question_set}",
                notes=["subject_label_missing"],
            )
            subjects[(category_code, subject_code)] = record

        if record.question_set != question_set:
            record.notes.append(f"additional_question_set:{question_set}")
        if file_type == "Q":
            record.has_question = True
            record.question_url = url
        elif file_type == "S":
            record.has_answer = True
            record.answer_url = url
        elif file_type == "M":
            record.has_correction = True
            record.correction_url = url

    for record in subjects.values():
        if not record.category_name:
            record.notes.append("category_label_missing")
        if not record.has_question:
            record.notes.append("question_link_missing")
        if not record.has_answer:
            record.notes.append("answer_link_missing")

    return sorted(subjects.values(), key=lambda item: (item.category_code, item.subject_code, item.subject_name))


def record_to_row(record: MoexSubjectRecord) -> dict:
    row = asdict(record)
    row["has_question"] = "yes" if record.has_question else "no"
    row["has_answer"] = "yes" if record.has_answer else "no"
    row["has_correction"] = "yes" if record.has_correction else "no"
    row["notes"] = "; ".join(record.notes)
    return row


def write_outputs(records: list[MoexSubjectRecord], errors: list[dict], output_dir: Path, year_start: int, year_end: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": dt.datetime.now().isoformat(),
        "source": SEARCH_URL,
        "scope": {"year_start": year_start, "year_end": year_end},
        "summary": {
            "record_count": len(records),
            "exam_code_count": len({record.exam_code for record in records}),
            "category_count": len({(record.exam_code, record.category_code) for record in records}),
            "error_count": len(errors),
        },
        "items": [asdict(record) for record in records],
        "errors": errors,
    }
    json_path = output_dir / f"moex_subject_catalog__y{year_start}-{year_end}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = output_dir / f"moex_subject_catalog__y{year_start}-{year_end}.csv"
    fieldnames = list(record_to_row(records[0]).keys()) if records else list(MoexSubjectRecord.__dataclass_fields__.keys())
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record_to_row(record))

    summary_path = output_dir / f"moex_subject_catalog__y{year_start}-{year_end}.md"
    by_year = Counter(record.year for record in records)
    by_exam = defaultdict(list)
    for record in records:
        by_exam[(record.year, record.exam_code, record.exam_label)].append(record)

    lines = [
        "# MOEX Official Subject Catalog",
        "",
        f"Source: `{SEARCH_URL}`",
        "",
        f"Scope: ROC {year_start}-{year_end}",
        "",
        f"Generated at: `{payload['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Subject rows: {len(records)}",
        f"- Exam codes: {payload['summary']['exam_code_count']}",
        f"- Exam/category groups: {payload['summary']['category_count']}",
        f"- Errors: {len(errors)}",
        "",
        "## Year Counts",
        "",
        "| ROC year | Subject rows |",
        "|---:|---:|",
    ]
    for year in sorted(by_year, reverse=True):
        lines.append(f"| {year} | {by_year[year]} |")

    lines.extend(["", "## Exam Counts", "", "| ROC year | Exam code | Exam label | Categories | Subjects |", "|---:|---:|---|---:|---:|"])
    for (year, exam_code, exam_label), exam_records in sorted(by_exam.items(), reverse=True):
        category_count = len({record.category_code for record in exam_records})
        lines.append(f"| {year} | {exam_code} | {exam_label} | {category_count} | {len(exam_records)} |")

    if errors:
        lines.extend(["", "## Errors", "", "| ROC year | Exam code | Exam label | Error |", "|---:|---:|---|---|"])
        for error in errors:
            lines.append(f"| {error.get('year', '')} | {error.get('exam_code', '')} | {error.get('exam_label', '')} | {error.get('error', '')} |")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "json": str(json_path),
        "csv": str(csv_path),
        "summary": str(summary_path),
        **payload["summary"],
    }, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export MOEX official subject catalog without downloading PDFs.")
    parser.add_argument("--year", type=int, help="Single ROC year.")
    parser.add_argument("--year-start", type=int, default=100, help="Start ROC year. Default: 100.")
    parser.add_argument("--year-end", type=int, default=115, help="End ROC year. Default: 115.")
    parser.add_argument("--code", action="append", default=[], help="Only export selected exam code. Repeatable.")
    parser.add_argument("--delay", type=float, default=0.7, help="Delay between exam-code requests. Default: 0.7s.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.year:
        year_start = year_end = args.year
    else:
        year_start, year_end = args.year_start, args.year_end
    if year_start > year_end:
        year_start, year_end = year_end, year_start

    home_html = get(SEARCH_URL)
    home_fields = extract_form_fields(home_html)

    exam_codes: list[tuple[int, str, str]] = []
    if args.code:
        for code in args.code:
            match = EXAM_CODE_RE.match(code)
            if not match:
                raise SystemExit(f"Invalid exam code: {code}")
            year = int(match.group(1))
            exam_codes.append((year, code, "指定代號"))
    else:
        for year in range(year_end, year_start - 1, -1):
            try:
                year_codes = load_exam_codes_for_year(home_fields, year)
            except Exception as exc:
                print(f"[WARN] failed to load exam codes for {year}: {exc}", file=sys.stderr)
                continue
            for code, label in year_codes:
                exam_codes.append((year, code, strip_tags(label)))
            print(f"[INFO] {year}: {len(year_codes)} exam codes", file=sys.stderr)
            time.sleep(args.delay)

    records: list[MoexSubjectRecord] = []
    errors: list[dict] = []
    for index, (year, code, label) in enumerate(exam_codes, 1):
        try:
            print(f"[INFO] {index}/{len(exam_codes)} export {code} {label}", file=sys.stderr)
            result_html = load_exam_result_html(home_fields, code)
            exam_label = extract_exam_label(result_html, label)
            parsed = parse_catalog_from_html(year, code, exam_label, result_html)
            if not parsed:
                errors.append({"year": year, "exam_code": code, "exam_label": label, "error": "no_subject_rows"})
            records.extend(parsed)
        except Exception as exc:
            errors.append({"year": year, "exam_code": code, "exam_label": label, "error": str(exc)})
            print(f"[WARN] failed {code}: {exc}", file=sys.stderr)
        time.sleep(args.delay)

    write_outputs(records, errors, args.output_dir, year_start, year_end)


if __name__ == "__main__":
    main()
