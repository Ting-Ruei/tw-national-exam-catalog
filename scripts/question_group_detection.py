#!/usr/bin/env python3
"""Deterministic question-group candidate detection shared by parser and UI.

The helpers in this module only identify candidates for human review. They do
not confirm groups, write review events, or change formal question links.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable


CHINESE_GROUP_COUNT_CHARS = "一二三四五六七八九十兩两"
GROUP_RANGE_RE = re.compile(r"第\s*(\d{1,3})\s*(?:至|到|~|～|-|－)\s*(\d{1,3})\s*題")
GROUP_PREFIX_RANGE_RE = re.compile(r"^\s*(\d{1,3})\s*(?:-|－|~|～|至|到)\s*(\d{1,3})\s*(?=\S)")
GROUP_COUNT_RE = re.compile(
    rf"(?:回答|作答)\s*(?:下列|以下)\s*(?:共\s*)?"
    rf"(\d{{1,2}}|[{CHINESE_GROUP_COUNT_CHARS}]{{1,3}})\s*(?:個\s*)?題"
)
GROUP_CONTINUATION_RE = re.compile(
    r"^\s*[（(]?\s*(承上題|呈上題|上題|前述)\s*[）)]?[，,、：:]?",
    re.I,
)

# PostgreSQL uses POSIX character classes. Keep this as the SQL-side broad
# selector; Python narrows the exact member range with group_count_from_text().
GROUP_COUNT_SQL_RE = (
    r"(回答|作答)[[:space:]]*(下列|以下)[[:space:]]*"
    r"(共[[:space:]]*)?([0-9]{1,2}|[一二三四五六七八九十兩两]{1,3})"
    r"[[:space:]]*(個[[:space:]]*)?題"
)


def _normalized(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def small_group_count(value: Any) -> int | None:
    """Parse a conservative 2-20 question count from Arabic or Chinese text."""
    token = _normalized(value).strip().replace("兩", "二").replace("两", "二")
    if token.isdigit():
        count = int(token)
        return count if 2 <= count <= 20 else None

    digits = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if token in digits:
        count = digits[token]
        return count if count >= 2 else None
    if token == "十":
        return 10
    if token.startswith("十") and token[1:] in digits:
        return 10 + digits[token[1:]]
    if token.endswith("十") and token[:-1] in digits:
        count = digits[token[:-1]] * 10
        return count if count <= 20 else None
    if "十" in token:
        left, right = token.split("十", 1)
        if left in digits and right in digits:
            count = digits[left] * 10 + digits[right]
            return count if count <= 20 else None
    return None


def group_count_from_text(text: Any) -> int | None:
    match = GROUP_COUNT_RE.search(_normalized(text))
    return small_group_count(match.group(1)) if match else None


def explicit_group_ref(text: Any, question_number: Any) -> str | None:
    """Return a high-confidence qNNN-qNNN range declared by one question."""
    normalized = _normalized(text)
    try:
        number = int(str(question_number))
    except (TypeError, ValueError):
        return None

    for start, end in GROUP_RANGE_RE.findall(normalized):
        a = int(start)
        b = int(end)
        if a <= number <= b <= a + 20:
            return f"q{a:03d}-q{b:03d}"

    prefix = GROUP_PREFIX_RANGE_RE.search(normalized)
    if prefix:
        a = int(prefix.group(1))
        b = int(prefix.group(2))
        if a <= number <= b <= a + 20:
            return f"q{a:03d}-q{b:03d}"

    count = group_count_from_text(normalized)
    if count:
        end = number + count - 1
        if end <= number + 20:
            return f"q{number:03d}-q{end:03d}"
    return None


def _question_number(item: dict[str, Any]) -> int:
    try:
        return int(str(item.get("question_number") or ""))
    except ValueError:
        return 0


def group_candidate_metrics(candidates: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize deterministic pending groups without creating review state."""
    candidate_rows = list(candidates)
    by_source: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    explicit_cue_count = 0
    unbound_explicit: list[str] = []
    incomplete_explicit: list[str] = []
    continuation_without_anchor: list[str] = []

    def ensure_record(source_key: str, group_ref: str) -> dict[str, Any]:
        return records.setdefault(
            (source_key, group_ref),
            {
                "source_registry_key": source_key,
                "group_ref": group_ref,
                "detection_sources": set(),
                "question_numbers": set(),
            },
        )

    for item in candidate_rows:
        source_key = str(item.get("source_registry_key") or "")
        number = _question_number(item)
        if source_key and number > 0:
            by_source[source_key][number] = item

        group_ref = str(item.get("group_ref") or "").strip()
        if source_key and group_ref:
            record = ensure_record(source_key, group_ref)
            record["detection_sources"].add("parser_group_ref")
            if number > 0:
                record["question_numbers"].add(number)

        expected_ref = explicit_group_ref(item.get("stem"), number)
        if not expected_ref:
            continue
        explicit_cue_count += 1
        record = ensure_record(source_key, expected_ref)
        record["detection_sources"].add("explicit_marker")
        if group_ref != expected_ref:
            unbound_explicit.append(str(item.get("candidate_key") or ""))

    for source_key, by_number in by_source.items():
        continuation_numbers = {
            number
            for number, item in by_number.items()
            if GROUP_CONTINUATION_RE.search(str(item.get("stem") or ""))
        }
        for number in sorted(continuation_numbers):
            start = number - 1
            while start in continuation_numbers:
                start -= 1
            end = number
            while end + 1 in continuation_numbers:
                end += 1
            if start not in by_number:
                continuation_without_anchor.append(
                    str(by_number[number].get("candidate_key") or "")
                )
                continue
            group_ref = f"q{start:03d}-q{end:03d}"
            record = ensure_record(source_key, group_ref)
            record["detection_sources"].add("continuation_marker")
            record["question_numbers"].update(range(start, end + 1))

    for (source_key, group_ref), record in records.items():
        match = re.fullmatch(r"q(\d{3})-q(\d{3})", group_ref)
        if not match or "explicit_marker" not in record["detection_sources"]:
            continue
        start = int(match.group(1))
        end = int(match.group(2))
        missing = [number for number in range(start, end + 1) if number not in by_source[source_key]]
        if missing:
            incomplete_explicit.append(
                f"{source_key}:{group_ref}:missing={','.join(str(number) for number in missing)}"
            )

    groups = [
        {
            **record,
            "detection_sources": sorted(record["detection_sources"]),
            "question_numbers": sorted(record["question_numbers"]),
        }
        for record in records.values()
    ]
    groups.sort(key=lambda item: (item["source_registry_key"], item["group_ref"]))
    return {
        "pending_group_count": len(groups),
        "pending_group_question_count": len(
            {
                (item["source_registry_key"], number)
                for item in groups
                for number in item["question_numbers"]
            }
        ),
        "parser_group_ref_count": sum(
            1 for item in groups if "parser_group_ref" in item["detection_sources"]
        ),
        "explicit_marker_group_count": sum(
            1 for item in groups if "explicit_marker" in item["detection_sources"]
        ),
        "continuation_group_count": sum(
            1 for item in groups if "continuation_marker" in item["detection_sources"]
        ),
        "explicit_group_cue_count": explicit_cue_count,
        "unbound_explicit_group_cue_count": len(unbound_explicit),
        "unbound_explicit_candidate_keys": sorted(set(unbound_explicit)),
        "incomplete_explicit_group_count": len(incomplete_explicit),
        "incomplete_explicit_groups": sorted(set(incomplete_explicit)),
        "continuation_without_anchor_count": len(continuation_without_anchor),
        "continuation_without_anchor_candidate_keys": sorted(
            set(continuation_without_anchor)
        ),
        "groups": groups,
    }
