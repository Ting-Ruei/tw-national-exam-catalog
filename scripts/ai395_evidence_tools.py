#!/usr/bin/env python3
"""Allow-listed, bounded evidence tools for the local audit agent.

The model can request an evidence *kind*, never a path or command.  This
module resolves paths from the immutable candidate metadata, reads only a
bounded question-sized segment, and returns redacted source labels rather
than filesystem paths.  PDF reference generation is cached below the caller
selected staging artifact directory and never writes candidates or review
events.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUESTION_MARKER = re.compile(r"^\s*(?:#{1,6}\s*)?(\d{1,3})(?:\s*[.)、．]\s*|\s+)")
MAX_TOOL_CHARS = 8_000


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)] + "\n[…evidence truncated…]"


def resolve_candidate_path(value: Any) -> Path | None:
    """Resolve known project-relative or stale project-absolute paths."""

    if not value:
        return None
    raw = str(value)
    path = Path(raw)
    if path.is_absolute() and path.is_file():
        try:
            resolved = path.resolve()
            if PROJECT_ROOT.resolve() in resolved.parents or resolved == PROJECT_ROOT.resolve():
                return resolved
        except OSError:
            return None
    marker = "tw-national-exam-catalog"
    if marker in path.parts:
        index = len(path.parts) - 1 - path.parts[::-1].index(marker)
        rebased = PROJECT_ROOT.joinpath(*path.parts[index + 1 :])
        if rebased.is_file():
            return rebased.resolve()
    if raw.startswith("國考題資料夾/"):
        rebased = PROJECT_ROOT / raw
    elif raw.startswith(("10_official_pdf/", "20_mineru_output/", "30_normalized_items/")):
        rebased = PROJECT_ROOT / "國考題資料夾" / raw
    else:
        rebased = PROJECT_ROOT / raw
    try:
        resolved = rebased.resolve()
    except OSError:
        return None
    if resolved.is_file() and (resolved == PROJECT_ROOT.resolve() or PROJECT_ROOT.resolve() in resolved.parents):
        return resolved
    return None


def _question_segments(text: str) -> dict[int, str]:
    lines = (text or "").splitlines()
    starts: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = QUESTION_MARKER.match(line)
        if match:
            starts.append((index, int(match.group(1))))
    segments: dict[int, str] = {}
    for position, (start, number) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        segments.setdefault(number, "\n".join(lines[start:end]).strip())
    return segments


def _markdown_evidence(path: Path | None, question_number: str, *, kind: str, max_chars: int) -> dict[str, Any]:
    if path is None:
        return {"kind": kind, "status": "unavailable", "error": "source path is unavailable"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"kind": kind, "status": "unavailable", "error": f"source read failed: {type(exc).__name__}"}
    try:
        number = int(str(question_number))
    except ValueError:
        number = -1
    segment = _question_segments(text).get(number, "")
    if not segment:
        segment = text[:max_chars]
    return {
        "kind": kind,
        "source": "mineru_markdown" if kind != "pdf_reference" else "official_pdf_reference",
        "status": "available",
        "locator": f"question:{question_number}",
        "text": _truncate(segment, max_chars),
    }


def _adjacent_markdown(path: Path | None, question_number: str, max_chars: int) -> dict[str, Any]:
    if path is None:
        return {"kind": "adjacent_questions", "status": "unavailable", "error": "question markdown is unavailable"}
    try:
        number = int(str(question_number))
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        return {"kind": "adjacent_questions", "status": "unavailable", "error": f"source read failed: {type(exc).__name__}"}
    segments = _question_segments(text)
    selected = [segments[n] for n in range(max(1, number - 2), number + 3) if n in segments]
    return {
        "kind": "adjacent_questions",
        "source": "mineru_markdown",
        "status": "available" if selected else "unavailable",
        "locator": f"questions:{max(1, number - 2)}-{number + 2}",
        "text": _truncate("\n\n".join(selected), max_chars),
    }


def _pdf_reference(
    path: Path | None,
    question_number: str,
    stem: str,
    cache_dir: Path | None,
    max_chars: int,
) -> list[dict[str, Any]]:
    if path is None:
        return [{"kind": "pdf_reference", "status": "unavailable", "error": "official PDF is unavailable"}]
    if cache_dir is None:
        return [{"kind": "pdf_reference", "status": "unavailable", "error": "PDF evidence cache is not configured"}]
    try:
        digest = _sha256_file(path)
        output_dir = cache_dir / "pdf_reference" / digest
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.exists():
            from build_pdf_reference_source import build_reference

            build_reference(path, output_dir, render_dir=None)
        pages_path = output_dir / "pages.jsonl"
        if not pages_path.is_file():
            return [{"kind": "pdf_reference", "status": "unavailable", "error": "PDF reference pages are missing"}]
        try:
            number_text = str(int(str(question_number)))
        except ValueError:
            number_text = str(question_number)
        anchor = re.sub(r"\s+", "", str(stem or ""))[:24]
        matches: list[dict[str, Any]] = []
        for line in pages_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            page = json.loads(line)
            engines = page.get("engines") or {}
            page_text = " ".join(str((entry or {}).get("text") or "") for entry in engines.values())
            compact = re.sub(r"\s+", "", page_text)
            if re.search(rf"(?<!\d){re.escape(number_text)}(?:[.)、．]|\s)", page_text) or (anchor and anchor in compact):
                families: dict[str, str] = {}
                for engine, entry in engines.items():
                    if not isinstance(entry, dict):
                        continue
                    family = str(entry.get("source_family") or engine)
                    text = str(entry.get("text") or "").strip()
                    if text and family not in families:
                        families[family] = text
                for family, text in sorted(families.items()):
                    matches.append({
                        "kind": "pdf_reference",
                        "source": "official_pdf_reference",
                        "status": "available",
                        "locator": f"page:{page.get('page')}",
                        "page": page.get("page"),
                        "family": family,
                        "text": _truncate(text, max(512, max_chars // 3)),
                    })
                if len(matches) >= 3:
                    break
        if not matches:
            return [{"kind": "pdf_reference", "status": "unavailable", "error": "question was not located in PDF reference"}]
        return matches
    except Exception as exc:  # pragma: no cover - PDF backends vary by host
        return [{"kind": "pdf_reference", "status": "error", "error": f"PDF reference tool failed: {type(exc).__name__}"}]


def collect_evidence(
    requests: list[dict[str, Any]],
    item: dict[str, Any],
    *,
    cache_dir: Path | None = None,
    compact_level: int = 0,
) -> list[dict[str, Any]]:
    """Execute only allow-listed evidence requests against local artifacts."""

    metadata = item.get("metadata") or {}
    question_path = resolve_candidate_path(metadata.get("question_markdown") or metadata.get("question_markdown_relative"))
    answer_path = resolve_candidate_path(metadata.get("answer_markdown") or metadata.get("answer_markdown_relative"))
    pdf_path = resolve_candidate_path(metadata.get("question_pdf") or metadata.get("question_pdf_relative"))
    max_chars_default = 4_000 if compact_level else MAX_TOOL_CHARS
    results: list[dict[str, Any]] = []
    for request in requests[:4]:
        kind = str(request.get("kind") or "")
        max_chars = max(256, min(max_chars_default, int(request.get("max_chars") or max_chars_default)))
        if kind == "question_markdown":
            results.append(_markdown_evidence(question_path, str(item.get("question_number") or ""), kind=kind, max_chars=max_chars))
        elif kind == "answer_markdown":
            results.append(_markdown_evidence(answer_path, str(item.get("question_number") or ""), kind=kind, max_chars=max_chars))
        elif kind == "adjacent_questions":
            results.append(_adjacent_markdown(question_path, str(item.get("question_number") or ""), max_chars))
        elif kind == "pdf_reference":
            results.extend(_pdf_reference(pdf_path, str(item.get("question_number") or ""), str(item.get("stem") or ""), cache_dir, max_chars))
        elif kind == "image_manifest":
            refs = item.get("image_refs") or []
            results.append({
                "kind": kind,
                "source": "mineru_image_manifest",
                "status": "available",
                "locator": "image_refs",
                "text": json.dumps(
                    [{"slot": index + 1, "exists": bool(ref.get("exists")), "bytes": int(ref.get("bytes") or 0)} for index, ref in enumerate(refs) if isinstance(ref, dict)],
                    ensure_ascii=False,
                ),
            })
        else:
            results.append({"kind": kind or "unknown", "status": "rejected", "error": "evidence kind is not allow-listed"})
    return results
