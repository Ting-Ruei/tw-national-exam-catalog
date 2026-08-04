#!/usr/bin/env python3
"""Build a reproducible, page-addressable PDF second-reference source.

MinerU remains the primary candidate/OCR source.  This command only reads the
official PDF with several independent text extractors and records where those
extractors disagree.  It never changes a candidate or writes a review event.

The output directory is intentionally caller-selected (normally ``tmp/`` or a
registry artifact directory) because extracted official text must not be
committed to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


NORMALIZATION_VERSION = "pdf_reference_normalization_v1"
ENGINE_FAMILIES = {
    "pdftotext_layout": "poppler",
    "pdftotext_raw": "poppler",
    "pypdf": "pypdf",
    "pdfplumber": "pdfplumber",
}
DEFAULT_BIN_CANDIDATES = {
    "pdfinfo": (
        "pdfinfo",
        "/opt/homebrew/bin/pdfinfo",
        "/usr/bin/pdfinfo",
        "/Users/tim/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdfinfo",
    ),
    "pdftotext": (
        "pdftotext",
        "/opt/homebrew/bin/pdftotext",
        "/usr/bin/pdftotext",
        "/Users/tim/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin/pdftotext",
    ),
    "pdftoppm": (
        "pdftoppm",
        "/opt/homebrew/bin/pdftoppm",
        "/usr/bin/pdftoppm",
        "/Users/tim/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdftoppm",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--pdfinfo-bin")
    parser.add_argument("--pdftotext-bin")
    parser.add_argument("--pdftoppm-bin")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_binary(name: str, explicit: str | None = None) -> str | None:
    candidates = ([explicit] if explicit else []) + list(DEFAULT_BIN_CANDIDATES[name])
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate) or candidate
        if Path(resolved).exists() and os.access(resolved, os.X_OK):
            return resolved
    return None


def normalize_text(value: str) -> str:
    """Normalize only for comparison; raw engine text is preserved separately."""
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00ad", "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\x00", "")
    return re.sub(r"\s+", " ", value).strip()


def text_quality(value: str) -> dict[str, Any]:
    lines = [line.strip() for line in (value or "").splitlines() if line.strip()]
    normalized_lines = [normalize_text(line) for line in lines]
    counts = Counter(normalized_lines)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    visible = sum(not char.isspace() for char in (value or ""))
    replacement = (value or "").count("�")
    controls = sum(ord(char) < 32 and char not in "\n\t\r" for char in (value or ""))
    cjk = sum("\u3400" <= char <= "\u9fff" for char in (value or ""))
    return {
        "char_count": len(value or ""),
        "visible_char_count": visible,
        "line_count": len(lines),
        "repeated_line_count": repeated,
        "repeated_line_ratio": round(repeated / max(1, len(lines)), 4),
        "replacement_char_count": replacement,
        "control_char_count": controls,
        "cjk_char_count": cjk,
        "nonempty": bool(normalize_text(value)),
    }


def similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _engine_quality_score(value: str) -> tuple[int, int, int, int]:
    quality = text_quality(value)
    return (
        1 if quality["nonempty"] else 0,
        -int(quality["replacement_char_count"]),
        -int(quality["control_char_count"]),
        int(quality["visible_char_count"]),
    )


def choose_consensus(engine_texts: dict[str, str]) -> dict[str, Any]:
    """Choose a source only when independent extractor families agree."""
    nonempty = {
        name: text for name, text in engine_texts.items() if normalize_text(text)
    }
    if not nonempty:
        return {
            "status": "no_text_layer",
            "text": "",
            "engine": None,
            "families": [],
            "confidence": 0.0,
            "pairwise_similarity": {},
        }

    pairwise: dict[str, float] = {}
    for left_name, left_text in nonempty.items():
        for right_name, right_text in nonempty.items():
            if left_name >= right_name:
                continue
            pairwise[f"{left_name}|{right_name}"] = round(similarity(left_text, right_text), 4)

    support: dict[str, set[str]] = {name: {name} for name in nonempty}
    for pair, score in pairwise.items():
        if score < 0.90:
            continue
        left_name, right_name = pair.split("|", 1)
        support[left_name].add(right_name)
        support[right_name].add(left_name)

    best_name = max(
        nonempty,
        key=lambda name: (
            len({ENGINE_FAMILIES.get(item, item) for item in support[name]}),
            len(support[name]),
            _engine_quality_score(nonempty[name]),
        ),
    )
    families = sorted({ENGINE_FAMILIES.get(item, item) for item in support[best_name]})
    consensus = len(families) >= 2
    quality = text_quality(nonempty[best_name])
    flags: list[str] = []
    if quality["replacement_char_count"]:
        flags.append("replacement_character")
    if quality["control_char_count"]:
        flags.append("control_character")
    if quality["repeated_line_ratio"] >= 0.25:
        flags.append("repeated_text_layer_suspect")
    if not consensus:
        flags.append("single_extractor_family")
    return {
        "status": "usable_consensus" if consensus and not flags else "needs_review",
        "text": nonempty[best_name],
        "engine": best_name,
        "families": families,
        "confidence": round(
            min(1.0, 0.55 + 0.15 * max(0, len(families) - 1) + 0.30 * similarity(nonempty[best_name], nonempty[best_name])),
            4,
        ),
        "pairwise_similarity": pairwise,
        "flags": flags,
    }


def run_command(command: list[str], *, timeout: int = 90) -> tuple[str, str | None]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        return completed.stdout or "", (completed.stderr or f"exit={completed.returncode}").strip()
    return completed.stdout, None


def split_pages(value: str, page_count: int) -> list[str]:
    pages = (value or "").split("\f")
    if pages and pages[-1] == "":
        pages.pop()
    pages = pages[:page_count]
    if len(pages) < page_count:
        pages.extend([""] * (page_count - len(pages)))
    return pages


def parse_pdfinfo(output: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in (output or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _object_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple)):
        return len(value)
    return 1


def page_layer_inventory(page: Any) -> dict[str, Any]:
    """Inspect PDF object structure without interpreting visual content."""
    result: dict[str, Any] = {
        "content_stream_count": 0,
        "font_count": 0,
        "xobject_count": 0,
        "annotation_count": 0,
        "text_operator_count": 0,
        "do_operator_count": 0,
        "likely_multiple_layers": False,
    }
    try:
        contents = page.get("/Contents")
        result["content_stream_count"] = _object_count(contents)
        resources = page.get("/Resources") or {}
        fonts = resources.get("/Font") if hasattr(resources, "get") else None
        xobjects = resources.get("/XObject") if hasattr(resources, "get") else None
        annotations = page.get("/Annots")
        result["font_count"] = _object_count(fonts)
        result["xobject_count"] = _object_count(xobjects)
        result["annotation_count"] = _object_count(annotations)
        raw_stream = page.get_contents().get_data() if page.get_contents() else b""
        if isinstance(raw_stream, bytes):
            decoded = raw_stream.decode("latin-1", errors="ignore")
            result["text_operator_count"] = len(re.findall(r"\bBT\b|\bET\b", decoded))
            result["do_operator_count"] = len(re.findall(r"\bDo\b", decoded))
    except Exception as exc:  # pragma: no cover - malformed PDFs vary by file
        result["inventory_error"] = f"{type(exc).__name__}: {exc}"
    result["likely_multiple_layers"] = bool(
        result["content_stream_count"] > 1
        or result["text_operator_count"] > 4
        or result["do_operator_count"] > 2
    )
    return result


def extract_pypdf(pdf: Path, max_pages: int | None) -> tuple[list[str], list[dict[str, Any]], str | None, str]:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - runtime-dependent
        return [], [], f"pypdf unavailable: {exc}", ""
    try:
        reader = PdfReader(str(pdf), strict=False)
        pages = list(reader.pages)
        if max_pages is not None:
            pages = pages[:max_pages]
        texts: list[str] = []
        inventory: list[dict[str, Any]] = []
        for page in pages:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                text = page.extract_text() or ""
            texts.append(text)
            inventory.append(page_layer_inventory(page))
        version = getattr(__import__("pypdf"), "__version__", "unknown")
        return texts, inventory, None, str(version)
    except Exception as exc:  # pragma: no cover - malformed PDFs vary by file
        return [], [], f"pypdf extraction failed: {type(exc).__name__}: {exc}", ""


def extract_pdfplumber(pdf: Path, max_pages: int | None) -> tuple[list[str], str | None, str]:
    try:
        import pdfplumber
    except Exception as exc:  # pragma: no cover - runtime-dependent
        return [], f"pdfplumber unavailable: {exc}", ""
    try:
        with pdfplumber.open(str(pdf)) as document:
            pages = document.pages[:max_pages] if max_pages is not None else document.pages
            texts: list[str] = []
            for page in pages:
                try:
                    text = page.extract_text(layout=True) or ""
                except TypeError:
                    text = page.extract_text() or ""
                texts.append(text)
        version = getattr(pdfplumber, "__version__", "unknown")
        return texts, None, str(version)
    except Exception as exc:  # pragma: no cover - malformed PDFs vary by file
        return [], f"pdfplumber extraction failed: {type(exc).__name__}: {exc}", ""


def render_pages(pdf: Path, render_dir: Path, page_count: int, binary: str | None) -> dict[str, Any]:
    if not binary:
        return {"requested": True, "status": "binary_missing", "binary": None}
    render_dir.mkdir(parents=True, exist_ok=True)
    command = [binary, "-png", "-r", "200", str(pdf), str(render_dir / "page")]
    _output, error = run_command(command, timeout=180)
    return {
        "requested": True,
        "status": "ok" if error is None else "error",
        "binary": binary,
        "error": error,
        "output_dir": str(render_dir),
        "page_count": page_count,
    }


def build_reference(
    pdf: Path,
    output_dir: Path,
    *,
    max_pages: int | None = None,
    pdfinfo_bin: str | None = None,
    pdftotext_bin: str | None = None,
    pdftoppm_bin: str | None = None,
    render_dir: Path | None = None,
) -> dict[str, Any]:
    if not pdf.is_file():
        raise FileNotFoundError(pdf)
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(pdf)
    info_bin = resolve_binary("pdfinfo", pdfinfo_bin)
    text_bin = resolve_binary("pdftotext", pdftotext_bin)
    info_text, info_error = run_command([info_bin, str(pdf)]) if info_bin else ("", "pdfinfo binary missing")
    info = parse_pdfinfo(info_text)
    try:
        page_count = int(info.get("Pages") or 0)
    except ValueError:
        page_count = 0

    engine_pages: dict[str, list[str]] = {}
    engine_errors: dict[str, str] = {}
    binaries = {"pdfinfo": info_bin, "pdftotext": text_bin}
    if text_bin:
        for engine, mode in (("pdftotext_layout", "-layout"), ("pdftotext_raw", "-raw")):
            text, error = run_command([text_bin, mode, str(pdf), "-"])
            if error:
                engine_errors[engine] = error
            engine_pages[engine] = split_pages(text, page_count)
    else:
        engine_errors["pdftotext_layout"] = "pdftotext binary missing"
        engine_errors["pdftotext_raw"] = "pdftotext binary missing"
        engine_pages["pdftotext_layout"] = [""] * page_count
        engine_pages["pdftotext_raw"] = [""] * page_count

    pypdf_pages, inventories, pypdf_error, pypdf_version = extract_pypdf(pdf, max_pages)
    if pypdf_error:
        engine_errors["pypdf"] = pypdf_error
    engine_pages["pypdf"] = pypdf_pages
    plumber_pages, plumber_error, plumber_version = extract_pdfplumber(pdf, max_pages)
    if plumber_error:
        engine_errors["pdfplumber"] = plumber_error
    engine_pages["pdfplumber"] = plumber_pages

    effective_page_count = min(
        page_count or max((len(value) for value in engine_pages.values()), default=0),
        max_pages if max_pages is not None else 10**9,
    )
    page_rows: list[dict[str, Any]] = []
    for index in range(effective_page_count):
        texts = {
            engine: (values[index] if index < len(values) else "")
            for engine, values in engine_pages.items()
        }
        consensus = choose_consensus(texts)
        quality = {engine: text_quality(text) for engine, text in texts.items()}
        inventory = inventories[index] if index < len(inventories) else {}
        flags = list(consensus.get("flags") or [])
        if inventory.get("likely_multiple_layers"):
            flags.append("pdf_object_layers_suspect")
        if any(quality[engine]["replacement_char_count"] for engine in quality):
            flags.append("replacement_character")
        if any(score < 0.90 for score in (consensus.get("pairwise_similarity") or {}).values()):
            flags.append("extractor_disagreement")
        page_rows.append(
            {
                "page": index + 1,
                "engines": {
                    engine: {
                        "source_family": ENGINE_FAMILIES.get(engine, engine),
                        "text": texts[engine],
                        "normalized_for_compare": normalize_text(texts[engine]),
                        "quality": quality[engine],
                        **({"error": engine_errors[engine]} if engine in engine_errors else {}),
                    }
                    for engine in sorted(texts)
                },
                "layer_inventory": inventory,
                "consensus": consensus,
                "flags": sorted(set(flags)),
                "needs_manual_source_review": bool(flags)
                or consensus.get("status") != "usable_consensus",
            }
        )

    pages_path = output_dir / "pages.jsonl"
    pages_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in page_rows),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "national_exam_pdf_reference_manifest_v1",
        "normalization_version": NORMALIZATION_VERSION,
        "source_pdf": str(pdf.resolve()),
        "source_pdf_sha256": digest,
        "source_pdf_bytes": pdf.stat().st_size,
        "page_count": page_count,
        "processed_page_count": effective_page_count,
        "pdfinfo": info,
        "pdfinfo_error": info_error,
        "binaries": binaries,
        "extractors": {
            "pdftotext_layout": {"family": "poppler", "available": bool(text_bin)},
            "pdftotext_raw": {"family": "poppler", "available": bool(text_bin)},
            "pypdf": {"family": "pypdf", "version": pypdf_version, "available": bool(pypdf_pages or not pypdf_error)},
            "pdfplumber": {"family": "pdfplumber", "version": plumber_version, "available": bool(plumber_pages or not plumber_error)},
        },
        "engine_errors": engine_errors,
        "page_status_counts": dict(Counter(str(row["consensus"]["status"]) for row in page_rows)),
        "manual_source_review_pages": [row["page"] for row in page_rows if row["needs_manual_source_review"]],
        "output": {"pages_jsonl": str(pages_path.resolve())},
        "policy": {
            "mineru_remains_primary": True,
            "pdf_reference_is_second_source": True,
            "no_candidate_or_review_event_writes": True,
            "consensus_requires_two_extractor_families": True,
            "disagreement_requires_manual_pdf_review": True,
            "visual_fallback_is_not_ocr": True,
        },
    }
    if render_dir is not None:
        manifest["render"] = render_pages(
            pdf,
            render_dir,
            effective_page_count,
            resolve_binary("pdftoppm", pdftoppm_bin),
        )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    args = parse_args()
    manifest = build_reference(
        args.pdf,
        args.output_dir,
        max_pages=args.max_pages,
        pdfinfo_bin=args.pdfinfo_bin,
        pdftotext_bin=args.pdftotext_bin,
        pdftoppm_bin=args.pdftoppm_bin,
        render_dir=args.render_dir,
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
