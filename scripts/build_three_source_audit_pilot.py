#!/usr/bin/env python3
"""Freeze MinerU, official-PDF text, and visual evidence for a shadow pilot.

The output is intentionally written below ``tmp/`` or another non-Git artifact
directory.  Human review and AI feedback are stored only in a separate holdout
file; model packets never contain answers, human decisions, or gold labels.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from build_pdf_reference_source import build_reference, normalize_text, resolve_binary


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
# Official exam PDFs vary between ``44. 題幹`` and ``44 題幹``.  Requiring
# punctuation made otherwise searchable pages fall back to a full-page crop,
# which both wastes vision tokens and mixes neighbouring questions.
QUESTION_MARKER = re.compile(r"^\s*(\d{1,3})(?:\s*[\.．、]\s*|\s+)")
MAX_REMOTE_ASSET_BYTES = 25 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--base-url",
        default=(
            os.environ.get("CATALOG_RUNTIME_UI_URL")
            or os.environ.get("REVIEW_PRIMARY_UI_URL")
            or "http://127.0.0.1:8875"
        ),
        help="Read-only Review UI URL (default: CATALOG_RUNTIME_UI_URL, then REVIEW_PRIMARY_UI_URL)",
    )
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--render-dpi", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(compact_json(row) + "\n" for row in rows), encoding="utf-8")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(compact_json(value).encode("utf-8")).hexdigest()


def fetch_candidate(
    base_url: str,
    candidate_key: str,
    timeout: float,
    category_scope: str = "",
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"q": candidate_key, "limit": "5", **({"category": category_scope} if category_scope else {})}
    )
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/candidates?{query}",
        headers={"Accept": "application/json", "User-Agent": "three-source-pilot/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    exact = [row for row in payload.get("candidates") or [] if row.get("candidate_key") == candidate_key]
    if len(exact) != 1:
        raise RuntimeError(f"expected one exact Review UI candidate for {candidate_key}, got {len(exact)}")
    return exact[0]


def local_path(value: str | None) -> Path | None:
    if not value:
        return None
    text = str(value)
    path = Path(text)
    if path.is_absolute():
        parts = path.parts
        if "tw-national-exam-catalog" in parts:
            index = parts.index("tw-national-exam-catalog")
            path = PROJECT_ROOT.joinpath(*parts[index + 1 :])
    elif text.startswith("國考題資料夾/"):
        path = PROJECT_ROOT / text
    elif re.match(r"^\d+_", text):
        path = ASSET_ROOT / text
    else:
        path = PROJECT_ROOT / text
    try:
        resolved = path.resolve()
    except OSError:
        return None
    roots = (PROJECT_ROOT.resolve(), ASSET_ROOT.resolve())
    if not any(resolved == root or root in resolved.parents for root in roots):
        return None
    return resolved


def clean_visible_text(value: Any) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    text = text.replace("\\(", "").replace("\\)", "")
    return normalize_text(text)


def candidate_content(candidate: dict[str, Any], *, original: bool) -> dict[str, Any]:
    source = candidate.get("parser_original") if original else candidate
    if not isinstance(source, dict):
        source = candidate
    options = [
        {"key": str(row.get("key") or ""), "text": str(row.get("text") or "")}
        for row in source.get("options") or []
        if isinstance(row, dict)
    ]
    return {
        "stem": str(source.get("stem") or ""),
        "options": options,
        "group_ref": source.get("group_ref"),
        "group_sequence_no": source.get("group_sequence_no"),
    }


def split_question_segment(text: str, question_number: int) -> str:
    lines = (text or "").splitlines()
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = QUESTION_MARKER.match(line)
        if not match:
            continue
        number = int(match.group(1))
        if start is None and number == question_number:
            start = index
            continue
        if start is not None and number != question_number:
            end = index
            break
    if start is None:
        return ""
    return "\n".join(lines[start:end]).strip()[:8000]


def grouped_lines(words: list[dict[str, Any]], tolerance: float = 3.0) -> list[dict[str, Any]]:
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda row: (float(row.get("top") or 0), float(row.get("x0") or 0))):
        top = float(word.get("top") or 0)
        if not rows or abs(top - float(rows[-1][0].get("top") or 0)) > tolerance:
            rows.append([word])
        else:
            rows[-1].append(word)
    result: list[dict[str, Any]] = []
    for row in rows:
        ordered = sorted(row, key=lambda word: float(word.get("x0") or 0))
        result.append(
            {
                "text": " ".join(str(word.get("text") or "") for word in ordered).strip(),
                "top": min(float(word.get("top") or 0) for word in ordered),
                "bottom": max(float(word.get("bottom") or 0) for word in ordered),
                "x0": min(float(word.get("x0") or 0) for word in ordered),
                "x1": max(float(word.get("x1") or 0) for word in ordered),
            }
        )
    return result


def _page_question_starts(page: Any) -> list[tuple[int, int, dict[str, Any]]]:
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    lines = grouped_lines(words)
    starts: list[tuple[int, int, dict[str, Any]]] = []
    for line_index, line in enumerate(lines):
        match = QUESTION_MARKER.match(str(line["text"]))
        if match:
            starts.append((line_index, int(match.group(1)), line))
    return starts


def locate_question_regions(pdf: Path, question_number: int, stem: str) -> list[dict[str, Any]]:
    """Locate a question from its marker through the next marker, across pages.

    A common exam layout starts a visual question at the foot of one page and
    places its option figures at the top of the next.  A one-page crop silently
    drops the most important evidence, so continuation pages are included until
    the next question marker.  The result is deliberately capped at three pages
    to avoid turning a failed locator into an unbounded visual packet.
    """
    import pdfplumber

    stem_anchor = re.sub(r"\s+", "", clean_visible_text(stem))[:24]
    fallback: dict[str, Any] | None = None
    with pdfplumber.open(str(pdf)) as document:
        for page_index, page in enumerate(document.pages, start=1):
            page_text = page.extract_text(layout=True) or ""
            compact_page = re.sub(r"\s+", "", clean_visible_text(page_text))
            if stem_anchor and len(stem_anchor) >= 8 and stem_anchor in compact_page:
                fallback = {
                    "page": page_index,
                    "bbox": [0.0, 0.0, float(page.width), float(page.height)],
                    "page_width": float(page.width),
                    "page_height": float(page.height),
                    "method": "stem_anchor_page",
                }
            starts = _page_question_starts(page)
            for start_pos, (_line_index, number, line) in enumerate(starts):
                if number != question_number:
                    continue
                top = max(0.0, float(line["top"]) - 12.0)
                bottom = float(page.height) - 20.0
                if start_pos + 1 < len(starts):
                    _next_line_index, _next_number, next_line = starts[start_pos + 1]
                    bottom = max(top + 40.0, float(next_line["top"]) - 10.0)
                regions = [{
                    "page": page_index,
                    "bbox": [20.0, top, float(page.width) - 20.0, bottom],
                    "page_width": float(page.width),
                    "page_height": float(page.height),
                    "method": "question_number_line",
                }]
                if start_pos + 1 < len(starts):
                    return regions

                for continuation_index in range(page_index + 1, min(len(document.pages), page_index + 2) + 1):
                    continuation = document.pages[continuation_index - 1]
                    continuation_starts = _page_question_starts(continuation)
                    continuation_bottom = float(continuation.height) - 20.0
                    reached_next_question = False
                    if continuation_starts:
                        _line_index, first_number, first_line = continuation_starts[0]
                        if first_number != question_number:
                            continuation_bottom = max(40.0, float(first_line["top"]) - 10.0)
                            reached_next_question = True
                    regions.append(
                        {
                            "page": continuation_index,
                            "bbox": [20.0, 0.0, float(continuation.width) - 20.0, continuation_bottom],
                            "page_width": float(continuation.width),
                            "page_height": float(continuation.height),
                            "method": "question_continuation_page",
                        }
                    )
                    if reached_next_question:
                        break
                return regions
    if fallback:
        return [fallback]
    raise RuntimeError(f"could not locate question {question_number} in {pdf}")


def locate_question(pdf: Path, question_number: int, stem: str) -> dict[str, Any]:
    """Backward-compatible single-region locator for callers that need it."""

    return locate_question_regions(pdf, question_number, stem)[0]


def leading_before_next_question(text: str, question_number: int) -> str:
    """Return continuation-page text before the next numbered question."""

    kept: list[str] = []
    for line in (text or "").splitlines():
        match = QUESTION_MARKER.match(line)
        if match and int(match.group(1)) != question_number:
            break
        kept.append(line)
    return "\n".join(kept).strip()[:8000]


def question_text_consensus(engine_segments: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Evaluate evidence at question level, de-duplicated by extractor family."""

    by_family: dict[str, str] = {}
    for engine in engine_segments.values():
        family = str(engine.get("source_family") or "").strip()
        text = str(engine.get("text") or "").strip()
        if not family or not text:
            continue
        normalized = re.sub(r"\s+", "", clean_visible_text(text))
        if normalized and (family not in by_family or len(normalized) > len(by_family[family])):
            by_family[family] = normalized
    hashes = {family: hashlib.sha256(text.encode("utf-8")).hexdigest() for family, text in by_family.items()}
    unique = set(hashes.values())
    if len(by_family) < 2:
        status = "insufficient_question_text"
    elif len(unique) == 1:
        status = "usable_question_consensus"
    else:
        status = "question_text_disagreement"
    return {
        "status": status,
        "family_count": len(by_family),
        "families": sorted(by_family),
        "normalized_hashes": hashes,
    }


def render_crop(pdf: Path, location: dict[str, Any], output: Path, dpi: int) -> Path:
    from PIL import Image

    pdftoppm = resolve_binary("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is unavailable")
    output.parent.mkdir(parents=True, exist_ok=True)
    full_prefix = output.with_name(output.stem + "__full")
    command = [
        pdftoppm,
        "-f",
        str(location["page"]),
        "-l",
        str(location["page"]),
        "-singlefile",
        "-png",
        "-r",
        str(dpi),
        str(pdf),
        str(full_prefix),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"pdftoppm exit={completed.returncode}")
    full_path = full_prefix.with_suffix(".png")
    with Image.open(full_path) as image:
        page_width = float(location["page_width"])
        page_height = float(location["page_height"])
        x0, top, x1, bottom = [float(value) for value in location["bbox"]]
        box = (
            max(0, round(x0 / page_width * image.width)),
            max(0, round(top / page_height * image.height)),
            min(image.width, round(x1 / page_width * image.width)),
            min(image.height, round(bottom / page_height * image.height)),
        )
        image.crop(box).save(output)
    full_path.unlink(missing_ok=True)
    return output


def fetch_remote_asset(
    reference: str,
    *,
    base_url: str,
    cache_dir: Path,
    timeout: float,
    cache: dict[str, Path | None],
) -> Path | None:
    """Resolve a Review UI asset locally without mutating the source data tree."""

    reference = str(reference or "").strip()
    if not reference:
        return None
    if reference in cache:
        return cache[reference]
    local = local_path(reference)
    if local and local.is_file():
        cache[reference] = local
        return local
    suffix = Path(urllib.parse.urlparse(reference).path).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".bin"
    destination = cache_dir / f"{hashlib.sha256(reference.encode('utf-8')).hexdigest()}{suffix}"
    if not destination.exists():
        url = base_url.rstrip("/") + "/file?" + urllib.parse.urlencode({"path": reference})
        request = urllib.request.Request(
            url,
            headers={"Accept": "image/*", "User-Agent": "three-source-pilot/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(MAX_REMOTE_ASSET_BYTES + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            cache[reference] = None
            return None
        if not data or len(data) > MAX_REMOTE_ASSET_BYTES:
            cache[reference] = None
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    cache[reference] = destination
    return destination


def asset_path(
    value: Any,
    *,
    base_url: str,
    cache_dir: Path,
    timeout: float,
    cache: dict[str, Path | None],
) -> Path | None:
    if not isinstance(value, dict):
        return None
    reference = value.get("path_relative") or value.get("relative_path") or value.get("path")
    return fetch_remote_asset(
        str(reference or ""),
        base_url=base_url,
        cache_dir=cache_dir,
        timeout=timeout,
        cache=cache,
    )


def walk_asset_values(
    candidate: dict[str, Any],
    *,
    base_url: str,
    cache_dir: Path,
    timeout: float,
    cache: dict[str, Path | None],
) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for option in candidate.get("options") or []:
        if not isinstance(option, dict) or not isinstance(option.get("image"), dict):
            continue
        path = asset_path(
            option["image"], base_url=base_url, cache_dir=cache_dir, timeout=timeout, cache=cache
        )
        if path and path.is_file() and path not in seen:
            found.append((f"CURRENT_OPTION_{option.get('key')}", path))
            seen.add(path)
    for index, asset in enumerate(candidate.get("image_refs") or [], start=1):
        if not isinstance(asset, dict):
            continue
        path = asset_path(asset, base_url=base_url, cache_dir=cache_dir, timeout=timeout, cache=cache)
        if path and path.is_file() and path not in seen:
            found.append((f"CURRENT_STEM_{index}", path))
            seen.add(path)
    stem_image = candidate.get("stem_image")
    if isinstance(stem_image, dict):
        path = asset_path(stem_image, base_url=base_url, cache_dir=cache_dir, timeout=timeout, cache=cache)
        if path and path.is_file() and path not in seen:
            found.append(("CURRENT_STEM_IMAGE", path))
            seen.add(path)
    return found


def old_asset_values(
    candidate: dict[str, Any],
    *,
    base_url: str,
    cache_dir: Path,
    timeout: float,
    cache: dict[str, Path | None],
) -> list[tuple[str, Path]]:
    text_parts = [str((candidate.get("review") or {}).get("notes") or "")]
    for option in candidate.get("options") or []:
        image = option.get("image") if isinstance(option, dict) else None
        if isinstance(image, dict):
            text_parts.append(str(image.get("description") or ""))
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for match in re.finditer(r"解除圖片綁定：([^\r\n]+)", "\n".join(text_parts)):
        path = fetch_remote_asset(
            match.group(1).strip(),
            base_url=base_url,
            cache_dir=cache_dir,
            timeout=timeout,
            cache=cache,
        )
        if path and path.is_file() and path not in seen:
            found.append((f"OLD_MINERU_{len(found) + 1}", path))
            seen.add(path)
    return found


def make_contact_sheet(items: list[tuple[str, Path]], output: Path) -> Path:
    from PIL import Image, ImageDraw, ImageOps

    tile_width, tile_height, label_height = 760, 520, 46
    columns = 2
    rows = (len(items) + columns - 1) // columns
    canvas = Image.new("RGB", (tile_width * columns, (tile_height + label_height) * rows), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (label, path) in enumerate(items):
        column, row = index % columns, index // columns
        x, y = column * tile_width, row * (tile_height + label_height)
        draw.rectangle((x, y, x + tile_width - 1, y + label_height - 1), fill="#e9eef5", outline="#1f2937")
        draw.text((x + 12, y + 12), label, fill="black")
        with Image.open(path) as source:
            rendered = ImageOps.contain(source.convert("RGB"), (tile_width - 24, tile_height - 24))
        px = x + (tile_width - rendered.width) // 2
        py = y + label_height + (tile_height - rendered.height) // 2
        canvas.paste(rendered, (px, py))
        draw.rectangle((x, y + label_height, x + tile_width - 1, y + label_height + tile_height - 1), outline="#1f2937")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    return output


def reference_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def holdout(candidate: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    ai = candidate.get("ai_review") if isinstance(candidate.get("ai_review"), dict) else {}
    review = candidate.get("review") if isinstance(candidate.get("review"), dict) else {}
    return {
        "id": case["id"],
        "candidate_key": candidate["candidate_key"],
        "expected": case.get("expected") or [],
        "human_review": {
            "action": review.get("action"),
            "notes": review.get("notes"),
            "correction": review.get("correction"),
            "updated_at": review.get("updated_at"),
        },
        "prior_ai": {
            "provider": ai.get("provider"),
            "model": ai.get("model"),
            "raw_audit_status": ai.get("raw_audit_status"),
            "summary": ai.get("summary"),
            "findings": ai.get("findings") or [],
            "feedback": ai.get("feedback"),
            "learning": ai.get("learning"),
        },
    }


def build_case(
    case: dict[str, Any],
    candidate: dict[str, Any],
    output_dir: Path,
    pdf_cache: dict[Path, tuple[dict[str, Any], list[dict[str, Any]]]],
    asset_cache: dict[str, Path | None],
    base_url: str,
    timeout: float,
    dpi: int,
) -> dict[str, Any]:
    source_files = candidate.get("source_files") if isinstance(candidate.get("source_files"), dict) else {}
    pdf = local_path(source_files.get("official_pdf"))
    if not pdf or not pdf.is_file():
        raise FileNotFoundError(f"official PDF is unavailable for {candidate['candidate_key']}: {source_files.get('official_pdf')}")
    if pdf not in pdf_cache:
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        reference_dir = output_dir / "pdf-reference" / digest[:16]
        manifest_path = reference_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = build_reference(pdf, reference_dir)
        pdf_cache[pdf] = (manifest, reference_rows(Path(manifest["output"]["pages_jsonl"])))
    reference_manifest, pages = pdf_cache[pdf]
    qn = int(candidate.get("question_number") or 0)
    effective = candidate_content(candidate, original=False)
    original = candidate_content(candidate, original=True)
    locations = locate_question_regions(pdf, qn, effective["stem"] or original["stem"])
    page_rows = [pages[int(location["page"]) - 1] for location in locations]
    engine_names = sorted(
        {
            name
            for page_row in page_rows
            for name, engine in (page_row.get("engines") or {}).items()
            if isinstance(engine, dict)
        }
    )
    engine_segments: dict[str, dict[str, Any]] = {}
    for name in engine_names:
        segments: list[str] = []
        source_family: str | None = None
        for index, page_row in enumerate(page_rows):
            engine = (page_row.get("engines") or {}).get(name) or {}
            source_family = source_family or engine.get("source_family")
            text = str(engine.get("text") or "")
            segment = split_question_segment(text, qn) if index == 0 else leading_before_next_question(text, qn)
            if segment:
                segments.append(segment)
        engine_segments[name] = {"source_family": source_family, "text": "\n".join(segments)[:8000]}
    field_consensus = question_text_consensus(engine_segments)
    case_dir = output_dir / "cases" / str(case["id"])
    crops = [
        render_crop(
            pdf,
            location,
            case_dir / ("official-question-region.png" if index == 0 else f"official-question-continuation-{index + 1}.png"),
            dpi,
        )
        for index, location in enumerate(locations)
    ]
    asset_cache_dir = output_dir / "asset-cache"
    current_assets = walk_asset_values(
        candidate,
        base_url=base_url,
        cache_dir=asset_cache_dir,
        timeout=timeout,
        cache=asset_cache,
    )
    old_assets = old_asset_values(
        candidate,
        base_url=base_url,
        cache_dir=asset_cache_dir,
        timeout=timeout,
        cache=asset_cache,
    )
    contact_items = [
        (f"OFFICIAL_PDF_REGION_{index + 1}_P{location['page']}", crop)
        for index, (location, crop) in enumerate(zip(locations, crops))
    ]
    contact_items.extend(current_assets)
    contact_items.extend(old_assets)
    contact_sheet = make_contact_sheet(contact_items, case_dir / "visual-contact-sheet.png")
    consensus_statuses = [(row.get("consensus") or {}).get("status") for row in page_rows]
    consensus_families = sorted(
        {family for row in page_rows for family in ((row.get("consensus") or {}).get("families") or [])}
    )
    reference_flags = sorted({flag for row in page_rows for flag in (row.get("flags") or [])})
    packet = {
        "schema_version": "national_exam_three_source_packet_v1",
        "advisory_only": True,
        "case_id": case["id"],
        "candidate_key": candidate["candidate_key"],
        "lane": case.get("lane"),
        "exam": {
            "category": (candidate.get("metadata") or {}).get("normalized_category_name"),
            "subject": (candidate.get("metadata") or {}).get("normalized_subject_name"),
            "year": (candidate.get("metadata") or {}).get("year"),
            "ordinal": (candidate.get("metadata") or {}).get("exam_ordinal"),
            "question_number": candidate.get("question_number"),
        },
        "mineru_or_parser": original,
        "effective_candidate": effective,
        "official_pdf_second_source": {
            "pdf_sha256": reference_manifest["source_pdf_sha256"],
            "page": locations[0]["page"],
            "pages": [location["page"] for location in locations],
            "location_method": locations[0]["method"],
            "location_methods": [location["method"] for location in locations],
            "consensus_status": (
                consensus_statuses[0]
                if len(set(consensus_statuses)) == 1
                else "mixed_page_consensus"
            ),
            "families": consensus_families,
            "flags": reference_flags,
            "question_consensus": field_consensus,
            "question_text_by_engine": engine_segments,
        },
        "visual_evidence": {
            "official_question_crop": str(crops[0].resolve()),
            "official_question_crops": [str(crop.resolve()) for crop in crops],
            "contact_sheet": str(contact_sheet.resolve()),
            "current_asset_count": len(current_assets),
            "old_mineru_asset_count": len(old_assets),
            "contact_labels": [label for label, _path in contact_items],
        },
        "contract": {
            "do_not_solve_answer": True,
            "text_repair_requires_pdf_family_support": 2,
            "group_marker_owner": "group",
            "vision_must_confirm_pixels": True,
            "output_is_advisory_only": True,
        },
    }
    write_json(case_dir / "blind-packet.json", packet)
    return packet


def main() -> int:
    args = parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    cases = spec.get("cases") or []
    if not cases:
        raise SystemExit("spec has no cases")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = args.output_dir / "mac-studio-snapshot.jsonl"
    snapshots: list[dict[str, Any]] = []
    if args.resume and snapshot_path.exists():
        snapshots = [json.loads(line) for line in snapshot_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_key = {str(row.get("candidate_key") or ""): row for row in snapshots}
    missing: list[tuple[dict[str, Any], str, str]] = []
    for case in cases:
        key = str(case.get("candidate_key") or "")
        if not key:
            raise RuntimeError(f"case has no candidate_key: {case}")
        if key not in by_key:
            prefix = str(case.get("id") or "").split("_", 1)[0]
            category_scope = "__pharmacist_track__" if prefix == "pharmacy" else "醫事檢驗師"
            missing.append((case, key, category_scope))
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(missing)))) as executor:
        futures = {
            executor.submit(fetch_candidate, args.base_url, key, args.timeout, category_scope): (case, key)
            for case, key, category_scope in missing
        }
        for future in as_completed(futures):
            case, key = futures[future]
            by_key[key] = future.result()
            print(f"fetched {case['id']}: {key}", flush=True)
    snapshots = [by_key[str(case["candidate_key"])] for case in cases]
    write_jsonl(snapshot_path, snapshots)

    pdf_cache: dict[Path, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    asset_cache: dict[str, Path | None] = {}
    packets = [
        build_case(
            case,
            candidate,
            args.output_dir,
            pdf_cache,
            asset_cache,
            args.base_url,
            args.timeout,
            args.render_dpi,
        )
        for case, candidate in zip(cases, snapshots)
    ]
    holdouts = [holdout(candidate, case) for case, candidate in zip(cases, snapshots)]
    write_jsonl(args.output_dir / "blind-packets.jsonl", packets)
    write_jsonl(args.output_dir / "human-ai-holdout.jsonl", holdouts)
    lane_counts: dict[str, int] = defaultdict(int)
    for packet in packets:
        lane_counts[str(packet.get("lane") or "unknown")] += 1
    manifest = {
        "schema_version": "national_exam_three_source_pilot_manifest_v1",
        "advisory_only": True,
        "base_url": args.base_url,
        "spec": str(args.spec.resolve()),
        "spec_hash": stable_hash(spec),
        "case_count": len(packets),
        "lane_counts": dict(sorted(lane_counts.items())),
        "candidate_snapshot_hash": stable_hash(snapshots),
        "pdf_count": len(pdf_cache),
        "outputs": {
            "blind_packets": str((args.output_dir / "blind-packets.jsonl").resolve()),
            "holdout": str((args.output_dir / "human-ai-holdout.jsonl").resolve()),
            "mac_studio_snapshot": str(snapshot_path.resolve()),
        },
        "safety": {
            "writes_review_events": False,
            "changes_candidates": False,
            "answers_excluded_from_blind_packets": True,
            "human_and_ai_outcomes_held_out": True,
        },
    }
    write_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
