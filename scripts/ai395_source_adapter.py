#!/usr/bin/env python3
"""Validate source/MinerU manifests without downloading or mutating inputs.

The live catalog scanner and MinerU workers remain separate components. This
adapter is the downstream boundary used while those stages are disabled: it
accepts an immutable, read-only manifest and proves that the files needed by
the parser are present and hash-identical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ContractError(ValueError):
    """Raised when an artifact manifest cannot safely enter the pipeline."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ContractError(f"manifest must be a JSON object: {path}")
    return payload


def resolve_manifest_path(manifest_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else manifest_path.parent / candidate


def verify_file_entry(manifest_path: Path, entry: dict[str, Any], *, label: str) -> dict[str, Any]:
    required = ("path", "sha256")
    missing = [key for key in required if not entry.get(key)]
    if missing:
        raise ContractError(f"{label} missing required keys: {', '.join(missing)}")
    path = resolve_manifest_path(manifest_path, str(entry["path"]))
    if not path.is_file():
        raise ContractError(f"{label} does not exist: {path}")
    actual = sha256_file(path)
    expected = str(entry["sha256"])
    if actual != expected:
        raise ContractError(f"{label} sha256 mismatch: expected {expected}, got {actual}")
    result = dict(entry)
    result["resolved_path"] = str(path.resolve())
    result["bytes"] = path.stat().st_size
    result["verified_sha256"] = actual
    return result


def validate_source_manifest(manifest_path: Path) -> dict[str, Any]:
    payload = load_json(manifest_path)
    artifacts = payload.get("source_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ContractError("source manifest requires a non-empty source_artifacts list")
    verified: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    for index, raw in enumerate(artifacts):
        if not isinstance(raw, dict):
            raise ContractError(f"source_artifacts[{index}] must be an object")
        artifact_id = str(raw.get("artifact_id") or "")
        if not artifact_id or artifact_id in artifact_ids:
            raise ContractError(f"duplicate or empty source artifact_id: {artifact_id!r}")
        artifact_ids.add(artifact_id)
        if raw.get("immutable") is not True or raw.get("read_only") is not True:
            raise ContractError(f"source artifact {artifact_id} must be immutable and read_only")
        verified.append(verify_file_entry(manifest_path, raw, label=f"source artifact {artifact_id}"))

    file_entries: dict[str, dict[str, Any]] = {}
    for key in ("candidate_jsonl", "issue_csv"):
        raw = payload.get(key)
        if not isinstance(raw, dict):
            raise ContractError(f"source manifest requires {key}")
        file_entries[key] = verify_file_entry(manifest_path, raw, label=key)

    return {
        "manifest_path": str(manifest_path.resolve()),
        "fixture_id": payload.get("fixture_id"),
        "source_artifacts": verified,
        "candidate_jsonl": file_entries["candidate_jsonl"],
        "issue_csv": file_entries["issue_csv"],
    }


def validate_mineru_manifest(manifest_path: Path, source_artifact_ids: set[str]) -> dict[str, Any]:
    payload = load_json(manifest_path)
    required = ("mineru_run_id", "source_artifact_id", "status", "input_sha256", "output_root", "read_only")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ContractError(f"MinerU manifest missing required keys: {', '.join(missing)}")
    if payload["source_artifact_id"] not in source_artifact_ids:
        raise ContractError(f"MinerU source artifact is not in source manifest: {payload['source_artifact_id']}")
    if payload["read_only"] is not True:
        raise ContractError("MinerU artifact must be read_only")
    output_root = resolve_manifest_path(manifest_path, str(payload["output_root"]))
    if not output_root.is_dir():
        raise ContractError(f"MinerU output_root does not exist: {output_root}")
    markdown = payload.get("markdown_path")
    if markdown:
        markdown_path = resolve_manifest_path(manifest_path, str(markdown))
        if not markdown_path.is_file():
            raise ContractError(f"MinerU markdown_path does not exist: {markdown_path}")
        expected = str((payload.get("output_manifest") or {}).get("markdown_sha256") or "")
        if expected and sha256_file(markdown_path) != expected:
            raise ContractError(f"MinerU markdown sha256 mismatch: {markdown_path}")
        payload["resolved_markdown_path"] = str(markdown_path.resolve())
    image_root = payload.get("image_root")
    if image_root:
        resolved_image_root = resolve_manifest_path(manifest_path, str(image_root))
        if not resolved_image_root.is_dir():
            raise ContractError(f"MinerU image_root does not exist: {resolved_image_root}")
        payload["resolved_image_root"] = str(resolved_image_root.resolve())
    payload["manifest_path"] = str(manifest_path.resolve())
    payload["resolved_output_root"] = str(output_root.resolve())
    return payload


def validate_manifests(source_manifest: Path, mineru_manifest: Path) -> dict[str, Any]:
    source = validate_source_manifest(source_manifest.resolve())
    mineru = validate_mineru_manifest(mineru_manifest.resolve(), {item["artifact_id"] for item in source["source_artifacts"]})
    return {"source": source, "mineru": mineru, "verified": True}


def describe() -> dict[str, Any]:
    return {
        "component_id": "ai395_source_adapter",
        "input": ["source_manifest.json", "mineru_manifest.json"],
        "output": ["verified source artifact records", "verified MinerU artifact record"],
        "config_keys": ["source_stage.mode", "source_stage.enabled", "mineru_stage.mode", "mineru_stage.enabled"],
        "side_effect_class": "read_only",
        "exit_codes": {"0": "verified", "2": "contract or hash failure"},
        "disabled_behavior": "live download and MinerU execution are never invoked by this adapter",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--mineru-manifest", type=Path)
    parser.add_argument("--describe", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.describe:
        print(json.dumps(describe(), ensure_ascii=False, sort_keys=True))
        return 0
    if not args.source_manifest or not args.mineru_manifest:
        raise SystemExit("--source-manifest and --mineru-manifest are required unless --describe is used")
    try:
        result = validate_manifests(args.source_manifest, args.mineru_manifest)
    except ContractError as exc:
        print(json.dumps({"verified": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
