#!/usr/bin/env python3
"""Extract and audit every base medical-technologist question PDF without LLMs.

The corpus lives under the caller-selected output directory (normally ``tmp``)
because it contains official exam text.  This command is read-only with respect
to candidates, review events, manual assets, and PostgreSQL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from build_pdf_reference_source import ENGINE_FAMILIES, build_reference


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "國考題資料夾/10_official_pdf/by_official_catalog/醫事檢驗師"
QUESTION_MARKER = re.compile(r"(?m)^\s*(\d{1,3})\s*[\.．、]\s*")
LEGACY_QUESTION_MARKER = re.compile(r"(?m)^\s*(\d{1,3})\s+(?=[^\d\s])")
PRIVATE_USE = re.compile(r"[\ue000-\uf8ff]")
STACKED_TOKEN = re.compile(r"(?<!\S)([^\s]{1,60})(?:\s+\1){2,}(?!\S)")
STACKED_CJK = re.compile(r"([\u3400-\u9fff])\1{2,}")
STRUCTURAL_GLYPH_MAP = str.maketrans(
    {
        "\ue18c": "A.",
        "\ue18d": "B.",
        "\ue18e": "C.",
        "\ue18f": "D.",
        "\ue129": "①",
        "\ue12a": "②",
        "\ue12b": "③",
        "\ue000": "①",
        "\ue001": "②",
        "\ue002": "③",
        "\ue003": "④",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--expected-questions", type=int, default=80)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def question_pdfs(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.pdf")
        if not path.stem.endswith("_ANS") and not path.stem.endswith("_MOD")
    )


def normalized_exact(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00ad", "").replace("\x00", "")
    value = value.translate(STRUCTURAL_GLYPH_MAP)
    return re.sub(r"\s+", "", value)


def unresolved_private_use_count(value: str) -> int:
    return len(PRIVATE_USE.findall((value or "").translate(STRUCTURAL_GLYPH_MAP)))


def stacked_text_signals(value: str) -> dict[str, Any]:
    """Find 3x/4x text emitted by overlapping PDF text layers."""

    examples: list[str] = []
    token_runs = 0
    cjk_token_runs = 0
    cjk_runs = 0
    tandem_lines = 0
    for line in (value or "").splitlines():
        for match in STACKED_TOKEN.finditer(line):
            token_runs += 1
            if re.search(r"[\u3400-\u9fff]", match.group(1)):
                cjk_token_runs += 1
            if len(examples) < 8:
                examples.append(match.group(0)[:180])
        for match in STACKED_CJK.finditer(line):
            cjk_runs += 1
            if len(examples) < 8:
                examples.append(match.group(0)[:180])
        compact = "".join(line.split())
        for copies in (4, 3):
            if len(compact) < copies * 4 or len(compact) % copies:
                continue
            unit = compact[: len(compact) // copies]
            if unit * copies == compact:
                tandem_lines += 1
                if len(examples) < 8:
                    examples.append(f"{copies}x:{unit[:160]}")
                break
    high_confidence = bool(cjk_runs >= 3 or tandem_lines >= 2 or cjk_token_runs >= 3)
    return {
        "token_runs": token_runs,
        "cjk_token_runs": cjk_token_runs,
        "cjk_character_runs": cjk_runs,
        "tandem_lines": tandem_lines,
        "examples": examples,
        "detected": bool(token_runs or cjk_runs or tandem_lines),
        "high_confidence": high_confidence,
    }


def marker_sequence(text: str) -> list[int]:
    marker = marker_pattern(text)
    return [int(match.group(1)) for match in marker.finditer(text or "")]


def marker_pattern(text: str) -> re.Pattern[str]:
    # Old MOEX PDFs use four stable PUA glyphs for A-D and omit the period
    # after question numbers.  Limit the relaxed pattern to that signature so
    # modern lines beginning with a numeric value are not misread as questions.
    value = text or ""
    legacy_signature = sum(value.count(char) for char in "\ue18c\ue18d\ue18e\ue18f")
    return LEGACY_QUESTION_MARKER if legacy_signature >= 40 else QUESTION_MARKER


def split_questions(text: str) -> dict[int, str]:
    matches = list(marker_pattern(text).finditer(text or ""))
    result: dict[int, str] = {}
    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = (text[match.start() : end] or "").strip()
        if segment and len(segment) > len(result.get(number, "")):
            result[number] = segment[:12_000]
    return result


def family_documents(pages: list[dict[str, Any]]) -> dict[str, str]:
    candidates: dict[str, list[tuple[str, str]]] = {}
    engine_names = sorted(
        {
            name
            for page in pages
            for name, engine in (page.get("engines") or {}).items()
            if isinstance(engine, dict)
        }
    )
    for name in engine_names:
        family = ENGINE_FAMILIES.get(name, name)
        text = "\n".join(str(((page.get("engines") or {}).get(name) or {}).get("text") or "") for page in pages)
        candidates.setdefault(family, []).append((name, text))
    documents: dict[str, str] = {}
    for family, values in candidates.items():
        # Raw Poppler text usually preserves question marker order more reliably
        # than layout mode; for other ties keep the version with more markers.
        values.sort(
            key=lambda row: (
                1 if row[0] == "pdftotext_raw" else 0,
                len(marker_sequence(row[1])),
                len(normalized_exact(row[1])),
            ),
            reverse=True,
        )
        documents[family] = values[0][1]
    return documents


def sequence_flags(sequence: list[int], expected: int) -> list[str]:
    flags: list[str] = []
    relevant = [number for number in sequence if 1 <= number <= expected]
    if not relevant:
        return ["no_question_markers"]
    if len(set(relevant)) < expected:
        flags.append("missing_question_markers")
    if any(right < left for left, right in zip(relevant, relevant[1:])):
        flags.append("non_monotonic_question_markers")
    if any(count > 1 for count in Counter(relevant).values()):
        flags.append("duplicate_question_markers")
    return flags


def analyze_reference(pdf: Path, pdf_root: Path, manifest: dict[str, Any], expected: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pages_path = Path(manifest["output"]["pages_jsonl"])
    pages = [json.loads(line) for line in pages_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    documents = family_documents(pages)
    questions_by_family = {family: split_questions(text) for family, text in documents.items()}
    question_rows: list[dict[str, Any]] = []
    for number in range(1, expected + 1):
        texts = {
            family: questions.get(number, "")
            for family, questions in questions_by_family.items()
            if questions.get(number, "").strip()
        }
        hashes = {family: hashlib.sha256(normalized_exact(text).encode("utf-8")).hexdigest() for family, text in texts.items()}
        unresolved = {family: unresolved_private_use_count(text) for family, text in texts.items()}
        if len(texts) < 2:
            status = "insufficient_question_text"
        elif any(unresolved.values()):
            status = "question_text_unresolved_glyphs"
        elif len(set(hashes.values())) == 1:
            status = "usable_question_consensus"
        else:
            status = "question_text_disagreement"
        question_rows.append(
            {
                "pdf_sha256": manifest["source_pdf_sha256"],
                "pdf_relative": str(pdf.relative_to(pdf_root)),
                "question_number": number,
                "status": status,
                "families": sorted(texts),
                "normalized_hashes": hashes,
                "unresolved_private_use_counts": unresolved,
                "texts": texts,
            }
        )

    sequence_by_family = {family: marker_sequence(text) for family, text in documents.items()}
    family_flags = {family: sequence_flags(sequence, expected) for family, sequence in sequence_by_family.items()}
    weird_counts = {
        family: {
            "replacement": text.count("�"),
            "private_use": len(PRIVATE_USE.findall(text)),
            "unresolved_private_use": unresolved_private_use_count(text),
            "nul": text.count("\x00"),
        }
        for family, text in documents.items()
    }
    stacked_by_family = {family: stacked_text_signals(text) for family, text in documents.items()}
    legacy_font_encoding = any(
        sum(text.count(char) for char in "\ue18c\ue18d\ue18e\ue18f") >= expected * 2
        for text in documents.values()
    )
    question_counts = Counter(row["status"] for row in question_rows)
    page_flags = Counter(flag for page in pages for flag in (page.get("flags") or []))
    anomaly_flags = sorted(
        {
            flag
            for flags in family_flags.values()
            for flag in flags
        }
        | {
            "question_text_gap"
            for _ in [0]
            if question_counts["insufficient_question_text"]
        }
        | {
            "question_text_disagreement"
            for _ in [0]
            if question_counts["question_text_disagreement"]
        }
        | {
            "invalid_unicode_mapping"
            for _ in [0]
            if any(
                counts["replacement"] or counts["nul"] or counts["unresolved_private_use"]
                for counts in weird_counts.values()
            )
        }
        | {
            "stacked_text_layer_suspect"
            for _ in [0]
            if any(value["high_confidence"] for value in stacked_by_family.values())
        }
        | ({"legacy_private_use_font_encoding"} if legacy_font_encoding else set())
    )
    summary = {
        "pdf_sha256": manifest["source_pdf_sha256"],
        "pdf_relative": str(pdf.relative_to(pdf_root)),
        "bytes": manifest.get("source_pdf_bytes"),
        "page_count": manifest.get("page_count"),
        "engine_errors": manifest.get("engine_errors") or {},
        "page_status_counts": manifest.get("page_status_counts") or {},
        "manual_source_review_pages": manifest.get("manual_source_review_pages") or [],
        "page_flag_counts": dict(page_flags),
        "question_status_counts": dict(question_counts),
        "question_marker_counts": {family: len(set(number for number in values if 1 <= number <= expected)) for family, values in sequence_by_family.items()},
        "question_marker_flags": family_flags,
        "weird_character_counts": weird_counts,
        "stacked_text_signals": stacked_by_family,
        "legacy_private_use_font_encoding": legacy_font_encoding,
        "anomaly_flags": anomaly_flags,
    }
    return summary, question_rows


def process_pdf(pdf: Path, pdf_root: Path, output_dir: Path, expected: int, resume: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    relative = str(pdf.relative_to(pdf_root))
    identifier = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]
    reference_dir = output_dir / "references" / identifier
    manifest_path = reference_dir / "manifest.json"
    if resume and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = build_reference(pdf, reference_dir)
    return analyze_reference(pdf, pdf_root, manifest, expected)


def main() -> int:
    args = parse_args()
    if args.workers < 1 or args.expected_questions < 1:
        raise SystemExit("workers and expected-questions must be positive")
    pdf_root = args.pdf_root.resolve()
    pdfs = question_pdfs(pdf_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_pdf, pdf, pdf_root, args.output_dir, args.expected_questions, args.resume): pdf
            for pdf in pdfs
        }
        for index, future in enumerate(as_completed(futures), start=1):
            pdf = futures[future]
            try:
                summary, rows = future.result()
                summaries.append(summary)
                questions.extend(rows)
            except Exception as exc:
                failures.append({"pdf_relative": str(pdf.relative_to(pdf_root)), "error": f"{type(exc).__name__}: {exc}"})
            if index % 12 == 0 or index == len(futures):
                print(f"processed {index}/{len(futures)} PDFs; failures={len(failures)}", flush=True)
    summaries.sort(key=lambda row: row["pdf_relative"])
    questions.sort(key=lambda row: (row["pdf_relative"], row["question_number"]))
    anomalies = [row for row in summaries if row["anomaly_flags"] or row["engine_errors"]]
    write_jsonl(args.output_dir / "pdf-summary.jsonl", summaries)
    write_jsonl(args.output_dir / "question-evidence.jsonl", questions)
    write_jsonl(args.output_dir / "anomalies.jsonl", anomalies)
    write_jsonl(args.output_dir / "failures.jsonl", failures)
    manifest = {
        "schema_version": "medtech_pdf_text_corpus_audit_v1",
        "advisory_only": True,
        "pdf_root": str(pdf_root),
        "selection": "base question PDFs; _ANS and _MOD excluded",
        "pdf_count": len(pdfs),
        "processed_pdf_count": len(summaries),
        "failure_count": len(failures),
        "expected_questions_per_pdf": args.expected_questions,
        "question_row_count": len(questions),
        "pdf_with_anomalies": len(anomalies),
        "anomaly_reason_counts": dict(Counter(flag for row in anomalies for flag in row["anomaly_flags"])),
        "question_status_counts": dict(Counter(row["status"] for row in questions)),
        "outputs": {
            "pdf_summary": str((args.output_dir / "pdf-summary.jsonl").resolve()),
            "question_evidence": str((args.output_dir / "question-evidence.jsonl").resolve()),
            "anomalies": str((args.output_dir / "anomalies.jsonl").resolve()),
            "failures": str((args.output_dir / "failures.jsonl").resolve()),
        },
        "safety": {
            "llm_tokens": 0,
            "candidate_writes": False,
            "review_event_writes": False,
            "database_writes": False,
            "official_text_kept_out_of_git": True,
        },
    }
    write_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
