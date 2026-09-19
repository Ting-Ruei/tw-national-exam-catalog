#!/usr/bin/env python3
"""Create a traceable PNG view of existing MinerU raster assets.

This is a deterministic, read-only-source preprocessing node for the GLM
vision transport contract.  It never overwrites the original MinerU output;
it creates a new candidate JSONL, source/MinerU manifests, PNG asset directory,
and conversion report under an explicitly empty output directory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Iterator

from ai395_source_adapter import ContractError, validate_manifests


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_SOURCE_BYTES = 24 * 1024 * 1024
MAX_OUTPUT_BYTES = 24 * 1024 * 1024
MAX_PIXELS = 80_000_000
CONVERSION_VERSION = "deterministic_png_v1"


class PNGNormalizationError(ValueError):
    """Raised when an image cannot safely enter the PNG view."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PNGNormalizationError(f"invalid candidate JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise PNGNormalizationError(f"candidate at {path}:{line_number} is not an object")
            rows.append(value)
    if not rows:
        raise PNGNormalizationError(f"candidate JSONL is empty: {path}")
    return rows


def iter_image_slots(row: dict[str, Any]) -> Iterator[tuple[dict[str, Any], str | int, dict[str, Any]]]:
    refs = row.get("image_refs")
    if isinstance(refs, list):
        for index, ref in enumerate(refs):
            if isinstance(ref, dict):
                yield refs, index, ref
    stem = row.get("stem_image")
    if isinstance(stem, dict):
        yield row, "stem_image", stem
    for option in row.get("options") or []:
        if not isinstance(option, dict):
            continue
        for key in ("image", "explanation_image"):
            ref = option.get(key)
            if isinstance(ref, dict):
                yield option, key, ref


def _portable_candidates(raw_value: str, source_root: Path) -> list[Path]:
    raw_path = Path(raw_value).expanduser()
    candidates = [raw_path] if raw_path.is_absolute() else [source_root / raw_path]
    for marker in ("國考題資料夾/20_mineru_output/", "20_mineru_output/"):
        if marker in raw_value:
            candidates.append(source_root / raw_value.split(marker, 1)[1])
            break
    return candidates


def resolve_source_path(ref: dict[str, Any], source_root: Path) -> Path:
    source_root = source_root.resolve()
    raw_values = [ref.get("resolved_path"), ref.get("path"), ref.get("relative_path"), ref.get("raw_ref")]
    escaped = False
    for raw in raw_values:
        if not raw:
            continue
        for candidate in _portable_candidates(str(raw), source_root):
            resolved = candidate.resolve()
            try:
                resolved.relative_to(source_root)
            except ValueError:
                escaped = True
                continue
            if resolved.is_file():
                return resolved
    if escaped:
        raise PNGNormalizationError("image reference escapes the immutable MinerU root")
    raise PNGNormalizationError(f"image asset is missing under {source_root}: {raw_values}")


def source_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def encode_png(source: Path, output: Path) -> tuple[bytes, dict[str, Any]]:
    data = source.read_bytes()
    if not data:
        raise PNGNormalizationError(f"image asset is empty: {source}")
    if len(data) > MAX_SOURCE_BYTES:
        raise PNGNormalizationError(f"image asset exceeds {MAX_SOURCE_BYTES} bytes: {source}")

    if data.startswith(PNG_SIGNATURE):
        output.write_bytes(data)
        return data, {
            "conversion": "identity_png",
            "source_mime_type": "image/png",
            "output_mime_type": "image/png",
            "source_bytes": len(data),
            "output_bytes": len(data),
        }

    try:
        from PIL import Image
    except ImportError as exc:
        raise PNGNormalizationError("Pillow is required to convert non-PNG MinerU assets") from exc

    try:
        with Image.open(source) as image:
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise PNGNormalizationError(f"image dimensions exceed safety limit: {source} ({width}x{height})")
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            converted = image.convert("RGBA" if has_alpha else "RGB")
            try:
                converted.save(output, format="PNG", optimize=False, compress_level=6)
            finally:
                converted.close()
    except PNGNormalizationError:
        raise
    except Exception as exc:  # noqa: BLE001 - image decoder boundary
        raise PNGNormalizationError(f"unable to decode image {source}: {type(exc).__name__}: {exc}") from exc

    output_data = output.read_bytes()
    if not output_data.startswith(PNG_SIGNATURE):
        raise PNGNormalizationError(f"PNG encoder produced invalid bytes: {output}")
    if len(output_data) > MAX_OUTPUT_BYTES:
        raise PNGNormalizationError(f"converted PNG exceeds {MAX_OUTPUT_BYTES} bytes: {output}")
    return output_data, {
        "conversion": CONVERSION_VERSION,
        "source_mime_type": source_mime(source),
        "output_mime_type": "image/png",
        "source_bytes": len(data),
        "output_bytes": len(output_data),
        "width": width,
        "height": height,
    }


def converted_ref(ref: dict[str, Any], source: Path, output_path: Path, output_data: bytes, details: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(ref)
    source_hash = sha256_file(source)
    output_hash = sha256_bytes(output_data)
    result.update({
        "path": output_path.name,
        "resolved_path": str(output_path.resolve()),
        "relative_path": f"assets/{output_path.name}",
        "exists": True,
        "bytes": len(output_data),
        "sha256": output_hash,
        "mime_type": "image/png",
        "conversion": CONVERSION_VERSION if details["conversion"] != "identity_png" else "identity_png",
        "source_asset": {
            "path": str(source),
            "sha256": source_hash,
            "bytes": source.stat().st_size,
            "mime_type": source_mime(source),
        },
    })
    return result


def normalize_scope(source_manifest: Path, mineru_manifest: Path, output_dir: Path) -> dict[str, Any]:
    source_manifest = source_manifest.resolve()
    mineru_manifest = mineru_manifest.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise PNGNormalizationError(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        verified = validate_manifests(source_manifest, mineru_manifest)
    except ContractError as exc:
        raise PNGNormalizationError(str(exc)) from exc
    candidate_path = Path(verified["source"]["candidate_jsonl"]["resolved_path"])
    issue_path = Path(verified["source"]["issue_csv"]["resolved_path"])
    source_root = Path(verified["mineru"].get("resolved_image_root") or verified["mineru"]["resolved_output_root"]).resolve()
    rows = read_jsonl(candidate_path)
    asset_dir = output_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    cache: dict[str, tuple[Path, bytes, dict[str, Any]]] = {}
    report_rows: list[dict[str, Any]] = []
    converted_rows: list[dict[str, Any]] = []
    reference_count = 0
    for row in rows:
        copied = copy.deepcopy(row)
        for container, key, ref in iter_image_slots(copied):
            reference_count += 1
            source = resolve_source_path(ref, source_root)
            source_hash = sha256_file(source)
            cached = cache.get(source_hash)
            if cached is None:
                output_path = asset_dir / f"{source_hash}.png"
                output_data, details = encode_png(source, output_path)
                cached = (output_path, output_data, details)
                cache[source_hash] = cached
                report_rows.append({
                    "source_path": str(source),
                    "source_sha256": source_hash,
                    "source_bytes": source.stat().st_size,
                    "output_path": str(output_path),
                    "output_sha256": sha256_bytes(output_data),
                    "output_bytes": len(output_data),
                    **details,
                })
            output_path, output_data, details = cached
            container[key] = converted_ref(ref, source, output_path, output_data, details)
        converted_rows.append(copied)

    candidate_output = output_dir / "candidates.png.jsonl"
    issue_output = output_dir / "issues.csv"
    write_jsonl(candidate_output, converted_rows)
    shutil.copyfile(issue_path, issue_output)

    source_payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    source_payload["candidate_jsonl"] = {
        "path": candidate_output.name,
        "sha256": sha256_file(candidate_output),
    }
    source_payload["issue_csv"] = {
        "path": issue_output.name,
        "sha256": sha256_file(issue_output),
    }
    source_payload["derived_from"] = {
        "source_manifest": str(source_manifest),
        "mineru_manifest": str(mineru_manifest),
        "conversion_version": CONVERSION_VERSION,
        "source_image_root": str(source_root),
    }
    source_output = output_dir / "source_manifest.json"
    write_json(source_output, source_payload)

    mineru_payload = json.loads(mineru_manifest.read_text(encoding="utf-8"))
    mineru_payload["output_root"] = str(verified["mineru"]["resolved_output_root"])
    mineru_payload["image_root"] = "assets"
    mineru_payload["resolved_source_image_root"] = str(source_root)
    mineru_payload["conversion_version"] = CONVERSION_VERSION
    mineru_payload["conversion_report"] = "asset_conversion_report.json"
    mineru_output = output_dir / "mineru_manifest.json"
    write_json(mineru_output, mineru_payload)

    report = {
        "schema_version": "ai395-vision-png-normalization-v1",
        "conversion_version": CONVERSION_VERSION,
        "source_manifest": str(source_manifest),
        "mineru_manifest": str(mineru_manifest),
        "source_image_root": str(source_root),
        "output_dir": str(output_dir),
        "candidate_count": len(converted_rows),
        "image_reference_count": reference_count,
        "unique_asset_count": len(report_rows),
        "converted_asset_count": sum(1 for row in report_rows if row["conversion"] != "identity_png"),
        "identity_png_count": sum(1 for row in report_rows if row["conversion"] == "identity_png"),
        "assets": report_rows,
        "source_read_only": True,
        "original_assets_modified": False,
        "transport_contract": "data:image/png;base64,...",
    }
    write_json(output_dir / "asset_conversion_report.json", report)
    return {
        "status": "pass",
        "output_dir": str(output_dir),
        "source_manifest": str(source_output),
        "mineru_manifest": str(mineru_output),
        "candidate_jsonl": str(candidate_output),
        "asset_conversion_report": str(output_dir / "asset_conversion_report.json"),
        "candidate_count": len(converted_rows),
        "image_reference_count": reference_count,
        "unique_asset_count": len(report_rows),
        "converted_asset_count": report["converted_asset_count"],
        "identity_png_count": report["identity_png_count"],
        "original_assets_modified": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--mineru-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = normalize_scope(args.source_manifest, args.mineru_manifest, args.output_dir)
    except (OSError, PNGNormalizationError) as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
