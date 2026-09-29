#!/usr/bin/env python3
"""Build offline, extractor-consensus evidence for official answer PDFs.

This module is deliberately advisory: it compares an existing candidate answer
with an official answer table and never infers or changes an answer.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import build_pdf_reference_source as reference


FAMILIES = ("poppler", "pypdf", "pdfplumber")
ANSWER_LABELS = {"A", "B", "C", "D", "E", "F", "G", "#", "＊", "*", "X"}
MAX_EXCERPT_CHARS = 800


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    return value if isinstance(value, dict) else row


def _is_special_answer_pdf(path: Path, row: dict[str, Any]) -> bool:
    metadata = _metadata(row)
    role = str(metadata.get("answer_role_primary") or "").lower()
    relative = str(metadata.get("answer_pdf_primary_relative") or path.name).lower()
    return bool(
        metadata.get("is_special_correction")
        or role in {"correction", "mod", "special_correction"}
        or re.search(r"(?:^|[_-])(mod|correction)(?:[_.-]|$)", relative)
    )


def _normal_answer(value: str) -> str | None:
    value = unicodedata.normalize("NFKC", value or "").strip().upper()
    value = value.strip("()[]{}:：、，,.;．。")
    if value in ANSWER_LABELS:
        return value
    # Circled Latin labels occur in a few official PDFs.
    circled = {"Ⓐ": "A", "Ⓑ": "B", "Ⓒ": "C", "Ⓓ": "D", "Ⓔ": "E"}
    return circled.get(value)


def _candidate_answer(value: Any) -> str | None:
    if isinstance(value, str):
        return _normal_answer(value)
    if isinstance(value, (int, float)):
        return _normal_answer(str(value))
    return None


def _tokens(line: str) -> list[str]:
    return [token for token in re.split(r"\s+", line.strip()) if token]


def _question_numbers(line: str, expected_count: int | None) -> list[int] | None:
    if "題號" not in line:
        return None
    tail = line.split("題號", 1)[1]
    raw = re.findall(r"\d+", tail)
    if not raw:
        return []
    values: list[int] = []
    for token in raw:
        if len(token) == 4 and expected_count is not None and expected_count <= 99:
            # pypdf can concatenate adjacent fixed-width labels.  Splitting is
            # accepted only if the complete table later validates as 1..N.
            values.extend((int(token[:2]), int(token[2:])))
        else:
            values.append(int(token))
    return values


def _expected_count(text: str) -> int | None:
    matches = re.findall(r"題\s*數\s*[：:]?\s*(\d+)\s*題", text or "")
    return int(matches[0]) if matches else None


def _answer_lines(lines: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if "題號" not in line or not _tokens(line.split("題號", 1)[1]):
            continue
        answer_line = next((candidate for candidate in lines[index + 1 : index + 4] if "答案" in candidate), None)
        if answer_line is not None:
            pairs.append((line, answer_line))
    return pairs


def parse_family_answers(text: str) -> tuple[int | None, dict[int, str], str | None]:
    """Parse one extractor family, returning count, qnum->label, and error."""
    expected = _expected_count(text)
    parsed: dict[int, str] = {}
    for question_line, answer_line in _answer_lines((text or "").splitlines()):
        numbers = _question_numbers(question_line, expected)
        if numbers is None or not numbers:
            continue
        answer_tail = answer_line.split("答案", 1)[1]
        answers = [_normal_answer(token) for token in _tokens(answer_tail)]
        answers = [answer for answer in answers if answer is not None]
        if len(numbers) != len(answers):
            return expected, parsed, "question_answer_row_length_mismatch"
        for number, answer in zip(numbers, answers):
            if number in parsed:
                return expected, parsed, "duplicate_question_number"
            parsed[number] = answer

    if expected is None:
        return expected, parsed, "missing_declared_question_count"
    expected_numbers = set(range(1, expected + 1))
    if set(parsed) != expected_numbers:
        return expected, parsed, "incomplete_or_nonsequential_table"
    return expected, parsed, None


def _pages_by_family(manifest: dict[str, Any], family: str) -> str:
    pages_path = Path(str(manifest.get("output", {}).get("pages_jsonl") or ""))
    if not pages_path.is_file():
        return ""
    chunks: list[str] = []
    for line in pages_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        for engine, payload in (row.get("engines") or {}).items():
            if payload.get("source_family") == family and (family != "poppler" or engine == "pdftotext_layout"):
                chunks.append(str(payload.get("text") or ""))
    return "\n\f\n".join(chunks)


def _excerpt(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    marker = compact.find("題號")
    if marker >= 0:
        compact = compact[max(0, marker - 80) :]
    return compact[:MAX_EXCERPT_CHARS]


def _reference_for(pdf: Path, output_dir: Path, cache: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    digest = _sha256(pdf)
    if digest in cache:
        return digest, cache[digest]
    cache_dir = output_dir / "pdf_reference_cache" / digest
    manifest_path = cache_dir / "manifest.json"
    if manifest_path.is_file() and (cache_dir / "pages.jsonl").is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = reference.build_reference(pdf, cache_dir)
    if manifest.get("source_pdf_sha256") != digest:
        raise ValueError("reference manifest hash does not match source PDF")
    cache[digest] = manifest
    return digest, manifest


def build_answer_evidence(rows: list[dict[str, Any]], asset_root: Path, output_dir: Path) -> dict[str, dict[str, Any]]:
    """Return ``candidate_key -> assessment`` using only local PDF evidence."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict[str, Any]] = {}
    manifest_cache: dict[str, dict[str, Any]] = {}
    parsed_cache: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = str(row.get("candidate_key") or "")
        metadata = _metadata(row)
        relative = metadata.get("answer_pdf_primary_relative")
        assessment: dict[str, Any] = {"candidate_key": key, "status": "source_unverified"}
        if not relative:
            assessment["reason"] = "missing_answer_pdf_primary_relative"
            result[key] = assessment
            continue
        pdf = asset_root / str(relative)
        assessment["source_pdf_relative"] = str(relative)
        if not pdf.is_file():
            assessment["reason"] = "answer_pdf_not_found"
            result[key] = assessment
            continue
        try:
            digest, manifest = _reference_for(pdf, output_dir, manifest_cache)
            assessment["source_pdf_sha256"] = digest
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            assessment["reason"] = f"reference_build_failed:{type(exc).__name__}"
            result[key] = assessment
            continue
        qnum_raw = row.get("question_number")
        try:
            qnum = int(qnum_raw)
        except (TypeError, ValueError):
            qnum = None
        assessment["qnum"] = qnum
        if _is_special_answer_pdf(pdf, row):
            assessment["reason"] = "special_or_correction_answer_pdf"
            result[key] = assessment
            continue

        if digest not in parsed_cache:
            parsed: dict[str, Any] = {"families": {}, "excerpts": {}}
            for family in FAMILIES:
                text = _pages_by_family(manifest, family)
                count, answers, error = parse_family_answers(text)
                parsed["families"][family] = {"expected_count": count, "answers": answers, "error": error}
                parsed["excerpts"][family] = _excerpt(text)
            parsed_cache[digest] = parsed
        parsed = parsed_cache[digest]
        assessment["family_answers"] = {
            family: data["answers"] for family, data in parsed["families"].items()
        }
        assessment["expected_count_by_family"] = {
            family: data["expected_count"] for family, data in parsed["families"].items()
        }
        errors = {family: data["error"] for family, data in parsed["families"].items() if data["error"]}
        assessment["excerpts"] = parsed["excerpts"]
        if errors:
            assessment["reason"] = "invalid_answer_table"
            assessment["parse_errors"] = errors
        elif qnum is None or not all(qnum in data["answers"] for data in parsed["families"].values()):
            assessment["reason"] = "question_number_not_in_complete_table"
        else:
            family_values = [data["answers"][qnum] for data in parsed["families"].values()]
            assessment["source_answer"] = family_values[0]
            candidate_answer = _candidate_answer(row.get("answer"))
            assessment["candidate_answer"] = candidate_answer
            if len(set(family_values)) == 1:
                if candidate_answer is None:
                    assessment["reason"] = "candidate_answer_missing_or_unparseable"
                elif candidate_answer == family_values[0]:
                    assessment["status"] = "verified"
                else:
                    assessment["reason"] = "candidate_answer_disagrees_with_source"
            else:
                assessment["reason"] = "extractor_answer_disagreement"
                assessment["family_answer_at_qnum"] = {
                    family: data["answers"][qnum] for family, data in parsed["families"].items()
                }
        result[key] = assessment
    return result
