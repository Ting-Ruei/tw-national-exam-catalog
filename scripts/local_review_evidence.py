#!/usr/bin/env python3
"""Strict, offline validation of three-source review packets.

This module deliberately treats Poppler's layout/raw variants as one family,
does not apply Unicode compatibility normalization, and compares the complete
question (stem and every option).  It is intentionally independent from the
pilot's advisory/majority analysis so the staging runner has a fail-closed
contract for real-source runs.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PACKET_SCHEMA = "national_exam_three_source_packet_v1"
_QUESTION_PREFIX = re.compile(r"^\s*(?:#{1,6}\s*)?\d{1,4}\s*(?:[.)、．:]\s*|\s+)")
# The trailing punctuation belongs *outside* the alternation. Written inside, each branch consumed
# only its own label form and the punctuation after it stayed in the text, so
# `Ⓐ.甲` stripped to `.甲` and `（A）甲` stripped to `甲` while `A：甲` stripped clean - three
# readings of one question that the three-source check then reported as a disagreement. The
# punctuation is a property of *how the label is printed*, not of which branch matched.
_OPTION_TOKEN = re.compile(
    r"(?:[（(](?P<wkey>[A-Ha-hＡ-Ｈ])[）)]|(?P<circled>[Ⓐ-Ⓩ])"
    r"|(?:^|(?<=[\s\n]))\s*(?P<key>[A-Ha-hＡ-Ｈ])"
    r"(?=[\s.．、:：)]|[\u4e00-\u9fff]))"
    r"\s*[.．、:：)]?\s*"
)


class EvidenceContractError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_object(candidate: dict[str, Any]) -> dict[str, Any]:
    """The source-bound content identity: full stem and ordered options only."""

    options = candidate.get("options") or []
    return {
        "stem": str(candidate.get("stem") or ""),
        "options": [
            {"key": str(option.get("key") or ""), "text": str(option.get("text") or "")}
            if isinstance(option, dict)
            else {"key": "", "text": str(option)}
            for option in options
        ],
    }


def content_fingerprint(candidate: dict[str, Any]) -> str:
    return _sha256(canonical_json(content_object(candidate)))


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    return _sha256(canonical_json({
        "candidate_key": str(candidate.get("candidate_key") or ""),
        "content_fingerprint": content_fingerprint(candidate),
    }))


def _compact_exact(value: Any, *, remove_question_prefix: bool = False) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if remove_question_prefix:
        text = _QUESTION_PREFIX.sub("", text, count=1)
    # Whitespace is presentation noise; code points, compatibility glyphs,
    # subscripts, punctuation, and option order remain semantically exact.
    return re.sub(r"\s+", "", text)


def _option_key(value: str) -> str:
    value = str(value or "")
    if "Ⓐ" <= value <= "Ⓩ":
        return chr(ord("A") + ord(value) - ord("Ⓐ"))
    return value.upper().translate(str.maketrans("ＡＢＣＤＥＦＧＨ", "ABCDEFGH"))


def _strip_bounded_option_labels(text: str, option_keys: list[str]) -> str:
    """Strip labels only when one complete ordered option sequence is found."""

    matches = list(_OPTION_TOKEN.finditer(text))
    expected = [_option_key(key) for key in option_keys]
    if not expected or len(matches) < len(expected):
        return text
    labels = []
    for match in matches:
        labels.append(_option_key(match.group("wkey") or match.group("circled") or match.group("key") or ""))
    # A label-looking English A-H token is harmless unless the entire source
    # contains exactly the candidate's ordered option-label sequence.
    for start in range(0, len(labels) - len(expected) + 1):
        if labels[start : start + len(expected)] != expected:
            continue
        selected = matches[start : start + len(expected)]
        if start != 0 or len(matches) != len(expected):
            # Keep any earlier/later label-like prose intact; only an exact
            # complete sequence is safe to rewrite.
            continue
        output: list[str] = []
        cursor = 0
        for match in selected:
            output.append(text[cursor : match.start()])
            cursor = match.end()
        output.append(text[cursor:])
        return "".join(output)
    return text


def _compact_source_question(value: Any, option_keys: list[str] | None = None) -> str:
    """Compact a complete source question while ignoring bounded labels only."""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _QUESTION_PREFIX.sub("", text, count=1)
    if option_keys:
        text = _strip_bounded_option_labels(text, option_keys)
    return re.sub(r"\s+", "", text)


def candidate_full_text(candidate: dict[str, Any]) -> str:
    parts = [str(candidate.get("stem") or "")]
    for option in candidate.get("options") or []:
        if isinstance(option, dict):
            parts.append(f"{option.get('key', '')}{option.get('text', '')}")
        else:
            parts.append(str(option))
    return "".join(parts)


def _family_name(engine: str, row: dict[str, Any]) -> str:
    family = str(row.get("source_family") or "").strip().lower()
    if family in {"poppler", "pdftotext", "pdftotext_raw", "pdftotext_layout"}:
        return "poppler"
    return family or engine.strip().lower()


def family_texts(packet: dict[str, Any]) -> dict[str, str]:
    engines = ((packet.get("official_pdf_second_source") or {}).get("question_text_by_engine") or {})
    grouped: dict[str, list[str]] = {}
    for engine, raw in engines.items():
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        family = _family_name(str(engine), raw)
        if text and family:
            grouped.setdefault(family, []).append(text)
    # Pick a deterministic representative within a family.  Multiple Poppler
    # modes can never increase the independent-family count.
    return {family: max(values, key=lambda value: (_compact_exact(value), len(value))) for family, values in grouped.items()}


def _field_rows(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    source = packet.get("official_pdf_second_source") or {}
    rows = source.get("question_fields_by_family") or packet.get("question_fields_by_family") or {}
    return {str(family).lower(): row for family, row in rows.items() if isinstance(row, dict)}


def _source_identity_valid(packet: dict[str, Any], families: dict[str, str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    source = packet.get("official_pdf_second_source") or {}
    engines = source.get("question_text_by_engine") or {}
    common_locator = str(source.get("page") or source.get("pages") or source.get("location_method") or "").strip()
    common_hash = str(source.get("pdf_sha256") or source.get("source_sha256") or "").strip()
    if set(families) != {"poppler", "pypdf", "pdfplumber"}:
        reasons.append("family_whitelist_or_count_mismatch")
    for engine, row in engines.items():
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            continue
        family = _family_name(str(engine), row)
        if family not in families:
            continue
        if not str(row.get("locator") or "").strip() and not common_locator:
            reasons.append(f"missing_locator:{family}")
        if not str(row.get("sha256") or row.get("source_sha256") or "").strip() and not common_hash:
            reasons.append(f"missing_source_hash:{family}")
    # Require provenance on at least one representative for each family. This
    # prevents a synthetic text-only object from passing as a real packet.
    for family in families:
        representatives = [
            row for engine, row in engines.items()
            if isinstance(row, dict) and _family_name(str(engine), row) == family
        ]
        if not common_locator or not common_hash:
            has_provenance = any(
                str(row.get("locator") or "").strip() and str(row.get("sha256") or row.get("source_sha256") or "").strip()
                for row in representatives
            )
        else:
            has_provenance = True
        if not has_provenance:
            reasons.append(f"missing_family_provenance:{family}")
    return not reasons, reasons


def _family_matches(candidate: dict[str, Any], family: str, text: str, fields: dict[str, Any] | None) -> bool:
    if fields:
        stem = fields.get("stem")
        options = fields.get("options")
        if stem is None or not isinstance(options, list) or len(options) != len(candidate.get("options") or []):
            return False
        if _compact_exact(stem) != _compact_exact(candidate.get("stem")):
            return False
        for expected, actual in zip(candidate.get("options") or [], options):
            actual_text = actual.get("text") if isinstance(actual, dict) else actual
            actual_key = actual.get("key") if isinstance(actual, dict) else ""
            expected_key = expected.get("key") if isinstance(expected, dict) else ""
            if str(actual_key or "") != str(expected_key or ""):
                return False
            if _compact_exact(actual_text) != _compact_exact(expected.get("text") if isinstance(expected, dict) else expected):
                return False
        return True
    option_keys = [str(option.get("key") or "") for option in (candidate.get("options") or []) if isinstance(option, dict)]
    source_text = _compact_source_question(text, option_keys)
    # Keys and their presentation are labels, not question content.  The
    # stem and every option's actual text still have to match exactly.
    expected_text = _compact_exact(
        str(candidate.get("stem") or "")
        + "".join(
            str(option.get("text") or "") if isinstance(option, dict) else str(option)
            for option in (candidate.get("options") or [])
        )
    )
    return bool(expected_text) and source_text == expected_text


def _answer_source_verified(packet: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Validate answer evidence independently from question-text evidence."""

    evidence = packet.get("answer_source_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "verified":
        return False
    identity = str(evidence.get("candidate_key") or evidence.get("candidate_id") or "")
    if identity != str(candidate.get("candidate_key") or ""):
        return False
    expected_qnum = str(candidate.get("question_number") or "")
    actual_qnum = str(evidence.get("question_number") or evidence.get("qnum") or "")
    if not expected_qnum or actual_qnum != expected_qnum:
        return False
    source_hash = str(
        evidence.get("source_pdf_sha256")
        or evidence.get("source_sha256")
        or evidence.get("sha256")
        or evidence.get("source_hash")
        or evidence.get("answer_source_sha256")
        or evidence.get("answer_source_hash")
        or evidence.get("asset_sha256")
        or evidence.get("pdf_sha256")
        or ""
    ).strip()
    if not source_hash:
        return False
    # The answer builder already requires three-family agreement; retain the
    # candidate-answer check here so a malformed packet cannot turn a source
    # status into a pass for a different answer.
    candidate_answer = str(candidate.get("answer") or "").strip().upper()
    assessed_answer = str(evidence.get("candidate_answer") or "").strip().upper()
    source_answer = str(evidence.get("source_answer") or "").strip().upper()
    if candidate_answer not in set("ABCDEFG") or assessed_answer != candidate_answer:
        return False
    return not source_answer or source_answer == candidate_answer


def assess_packet(packet: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Return a fail-closed assessment suitable for a staging lane."""

    reasons: list[str] = []
    packet_identity_valid = True
    if packet.get("schema_version") not in {None, PACKET_SCHEMA}:
        reasons.append("unsupported_schema")
        packet_identity_valid = False
    if str(packet.get("candidate_key") or "") != str(candidate.get("candidate_key") or ""):
        reasons.append("candidate_key_mismatch")
        packet_identity_valid = False
    fingerprints = packet.get("fingerprints") or {}
    declared_content = str(
        packet.get("content_fingerprint")
        or packet.get("effective_content_fingerprint")
        or fingerprints.get("content")
        or fingerprints.get("content_fingerprint")
        or ""
    )
    declared_candidate = str(packet.get("candidate_fingerprint") or fingerprints.get("candidate") or "")
    actual_content = content_fingerprint(candidate)
    actual_candidate = candidate_fingerprint(candidate)
    if declared_content and declared_content != actual_content:
        reasons.append("content_fingerprint_mismatch")
        packet_identity_valid = False
    if declared_candidate and declared_candidate != actual_candidate:
        reasons.append("candidate_fingerprint_mismatch")
        packet_identity_valid = False
    # Real packets without either fingerprint are stale-unsafe.  The legacy
    # packet generator did not emit these fields; the caller may explicitly
    # choose compatibility only for fixtures, never for real-source mode.
    if not declared_content and not declared_candidate:
        reasons.append("missing_fingerprint")
        packet_identity_valid = False

    families = family_texts(packet)
    fields = _field_rows(packet)
    matched = {
        family: _family_matches(candidate, family, text, fields.get(family))
        for family, text in families.items()
    }
    identity_valid, identity_reasons = _source_identity_valid(packet, families)
    reasons.extend(identity_reasons)
    if len(families) != 3:
        reasons.append("partial_or_missing_families")
    if len(families) == 3 and not all(matched.values()):
        reasons.append("three_source_disagreement")
    source_available = packet_identity_valid and identity_valid and len(families) == 3
    verified = source_available and all(matched.values())
    answer_verified = _answer_source_verified(packet, candidate)
    if not answer_verified:
        reasons.append("answer_source_unverified")
    return {
        "status": "verified" if verified else "source_unverified",
        "verified": verified,
        "source_available": source_available,
        "answer_verified": answer_verified,
        "answer_evidence": packet.get("answer_source_evidence") if isinstance(packet.get("answer_source_evidence"), dict) else {},
        "candidate_key": str(candidate.get("candidate_key") or ""),
        "content_fingerprint": actual_content,
        "candidate_fingerprint": actual_candidate,
        "families": sorted(families),
        "family_count": len(families),
        "matched_families": sorted(family for family, ok in matched.items() if ok),
        "reasons": reasons,
        "evidence": [
            {"kind": "official_pdf_text", "family": family, "status": "available", "text": text[:8000]}
            for family, text in sorted(families.items())
        ],
    }


def load_packets(path: Path) -> dict[str, dict[str, Any]]:
    """Load JSONL or a JSON array/object packet file keyed by candidate_key."""

    try:
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".jsonl":
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        else:
            payload = json.loads(raw)
            rows = payload if isinstance(payload, list) else payload.get("packets", []) if isinstance(payload, dict) else []
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceContractError(f"invalid three-source packet file {path}: {exc}") from exc
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("candidate_key") or ""):
            raise EvidenceContractError(f"packet missing candidate_key: {path}")
        key = str(row["candidate_key"])
        if key in result:
            raise EvidenceContractError(f"duplicate packet candidate_key: {key}")
        result[key] = row
    return result
