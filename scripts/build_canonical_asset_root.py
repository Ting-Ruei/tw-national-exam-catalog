#!/usr/bin/env python3
"""Build a hash-bound, fail-closed canonical asset-root candidate.

The builder never edits either source root.  It first validates the two source
manifests, the independently generated comparison, the approved conflict
resolution manifest, Linux filename mappings, and safe symlink plans.  By
default it only writes a machine-readable plan.  ``--apply`` is accepted only
for a destination that does not already exist and leaves an incomplete marker
until every copied byte and rebuilt symlink has been verified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import posixpath
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CHUNK_BYTES = 4 * 1024 * 1024
HASH_LENGTH = 64
MANIFEST_FIELDS = (
    "root_label",
    "relative_path",
    "entry_type",
    "bytes",
    "mtime_ns",
    "sha256",
    "link_target",
)
COMPARISON_FIELDS = (
    "relative_path",
    "status",
    "left_bytes",
    "right_bytes",
    "left_sha256",
    "right_sha256",
    "left_type",
    "right_type",
)
RESOLUTION_FIELDS = (
    "schema_version",
    "decision_id",
    "approved_at",
    "comparison_sha256",
    "relative_path",
    "left_sha256",
    "right_sha256",
    "resolution",
    "canonical_action",
    "authority",
    "rebuild_source",
    "notes",
)
NAME_MAP_FIELDS = (
    "root_label",
    "source_relative_path",
    "storage_relative_path",
    "bytes",
    "sha256",
)
SYMLINK_PLAN_FIELDS = (
    "root_label",
    "source_relative_path",
    "target_logical_relative_path",
    "target_storage_relative_path",
    "normalized_link_target",
    "bytes",
    "sha256",
)
PLAN_FIELDS = (
    "logical_relative_path",
    "storage_relative_path",
    "action",
    "source_label",
    "source_relative_path",
    "source_storage_relative_path",
    "entry_type",
    "bytes",
    "sha256",
    "link_target",
    "comparison_status",
    "resolution",
    "rebuild_source",
)
REGEN_FIELDS = (
    "logical_relative_path",
    "resolution",
    "canonical_action",
    "authority",
    "rebuild_source",
    "decision_id",
)
ALLOWED_RESOLUTIONS = {
    ("exclude_both", "omit"),
    ("exclude_from_canonical", "omit"),
    ("regenerate_on_ai395", "omit_then_regenerate"),
    ("regenerate_from_final_catalog", "omit_then_regenerate"),
}
RESERVED_ROOTS = {".linux-name-map", ".canonical-build-incomplete.json"}


class BuildError(RuntimeError):
    """A validation failure that must stop the canonical build."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_hash(value: str) -> bool:
    return len(value) == HASH_LENGTH and all(char in "0123456789abcdef" for char in value)


def require_hash(path: Path, expected: str, label: str) -> str:
    if not valid_hash(expected):
        raise BuildError(f"{label} expected SHA-256 is not 64 lowercase hex characters")
    actual = sha256_file(path)
    if actual != expected:
        raise BuildError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def safe_relative(raw: str, label: str) -> PurePosixPath:
    if not raw or "\x00" in raw or raw.startswith("/"):
        raise BuildError(f"unsafe {label}: {raw!r}")
    relative = PurePosixPath(raw)
    if relative.as_posix() != raw or any(part in {"", ".", ".."} for part in relative.parts):
        raise BuildError(f"unsafe or non-canonical {label}: {raw!r}")
    return relative


def linux_safe_storage(raw: str, label: str) -> PurePosixPath:
    relative = safe_relative(raw, label)
    for part in relative.parts:
        if len(part.encode("utf-8")) > 255:
            raise BuildError(f"Linux filename component exceeds 255 bytes in {label}: {raw!r}")
    return relative


def root_path(root: Path, raw: str, label: str) -> Path:
    relative = safe_relative(raw, label)
    return root.joinpath(*relative.parts)


def read_delimited(path: Path, fields: Iterable[str], *, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.is_file():
        raise BuildError(f"evidence file not found: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        missing = [field for field in fields if field not in (reader.fieldnames or [])]
        if missing:
            raise BuildError(f"{path} is missing fields: {missing}")
        return list(reader)


def load_manifest(path: Path, label: str, expected_hash: str) -> dict[str, dict[str, str]]:
    require_hash(path, expected_hash, f"manifest {label}")
    result: dict[str, dict[str, str]] = {}
    for row in read_delimited(path, MANIFEST_FIELDS):
        if row["root_label"] != label:
            continue
        relative = safe_relative(row["relative_path"], "manifest relative_path").as_posix()
        if relative in result:
            raise BuildError(f"duplicate manifest path: {label}:{relative}")
        entry_type = row["entry_type"]
        if entry_type not in {"file", "symlink"}:
            raise BuildError(f"unsupported entry type: {label}:{relative}:{entry_type}")
        try:
            size = int(row["bytes"])
        except ValueError as exc:
            raise BuildError(f"invalid byte count: {label}:{relative}") from exc
        if size < 0:
            raise BuildError(f"negative byte count: {label}:{relative}")
        if entry_type == "file" and not valid_hash(row["sha256"]):
            raise BuildError(f"regular file is not SHA-256 bound: {label}:{relative}")
        if entry_type == "symlink" and row["sha256"]:
            raise BuildError(f"symlink unexpectedly has SHA-256 content hash: {label}:{relative}")
        result[relative] = row
    if not result:
        raise BuildError(f"manifest has no rows for label {label!r}: {path}")
    return result


def comparison_status(left: dict[str, str] | None, right: dict[str, str] | None) -> str:
    if left is None:
        return "right_only"
    if right is None:
        return "left_only"
    if left["entry_type"] != right["entry_type"] or left.get("link_target", "") != right.get("link_target", ""):
        return "content_conflict"
    left_hash = left.get("sha256", "")
    right_hash = right.get("sha256", "")
    if left_hash and right_hash:
        return "same_sha256" if left_hash == right_hash else "content_conflict"
    return "same_metadata" if left["bytes"] == right["bytes"] else "content_conflict"


def expected_comparison_row(
    relative: str,
    left: dict[str, str] | None,
    right: dict[str, str] | None,
) -> dict[str, str]:
    return {
        "relative_path": relative,
        "status": comparison_status(left, right),
        "left_bytes": left.get("bytes", "") if left else "",
        "right_bytes": right.get("bytes", "") if right else "",
        "left_sha256": left.get("sha256", "") if left else "",
        "right_sha256": right.get("sha256", "") if right else "",
        "left_type": left.get("entry_type", "") if left else "",
        "right_type": right.get("entry_type", "") if right else "",
    }


def load_comparison(
    path: Path,
    expected_hash: str,
    left: dict[str, dict[str, str]],
    right: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    require_hash(path, expected_hash, "comparison")
    rows: dict[str, dict[str, str]] = {}
    for row in read_delimited(path, COMPARISON_FIELDS):
        relative = safe_relative(row["relative_path"], "comparison relative_path").as_posix()
        if relative in rows:
            raise BuildError(f"duplicate comparison path: {relative}")
        rows[relative] = row
    expected_paths = set(left) | set(right)
    if set(rows) != expected_paths:
        missing = sorted(expected_paths - set(rows))[:5]
        extra = sorted(set(rows) - expected_paths)[:5]
        raise BuildError(f"comparison path set mismatch; missing={missing}, extra={extra}")
    for relative in sorted(expected_paths):
        expected = expected_comparison_row(relative, left.get(relative), right.get(relative))
        actual = {field: rows[relative].get(field, "") for field in COMPARISON_FIELDS}
        if actual != expected:
            raise BuildError(f"comparison row does not match bound manifests: {relative}")
        if expected["status"] == "same_metadata":
            raise BuildError(f"unhashed shared path is not eligible for canonical build: {relative}")
    return rows


def load_resolutions(
    path: Path,
    expected_hash: str,
    comparison_hash: str,
    comparison: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    require_hash(path, expected_hash, "approved resolution")
    result: dict[str, dict[str, str]] = {}
    for row in read_delimited(path, RESOLUTION_FIELDS):
        relative = safe_relative(row["relative_path"], "resolution relative_path").as_posix()
        if relative in result:
            raise BuildError(f"duplicate resolution path: {relative}")
        if not row["decision_id"]:
            raise BuildError(f"missing decision_id for resolution path: {relative}")
        if row["comparison_sha256"] != comparison_hash:
            raise BuildError(f"resolution is bound to a different comparison: {relative}")
        if (row["resolution"], row["canonical_action"]) not in ALLOWED_RESOLUTIONS:
            raise BuildError(f"unsupported resolution/action: {relative}")
        compared = comparison.get(relative)
        if not compared or compared["status"] != "content_conflict":
            raise BuildError(f"resolution path is not a current content conflict: {relative}")
        if row["left_sha256"] != compared["left_sha256"] or row["right_sha256"] != compared["right_sha256"]:
            raise BuildError(f"stale_resolution: source hash changed for {relative}")
        result[relative] = row
    conflicts = {path for path, row in comparison.items() if row["status"] == "content_conflict"}
    if set(result) != conflicts:
        missing = sorted(conflicts - set(result))[:5]
        extra = sorted(set(result) - conflicts)[:5]
        raise BuildError(f"content conflicts do not have exactly one resolution; missing={missing}, extra={extra}")
    return result


def parse_label_paths(values: list[str], option: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise BuildError(f"{option} must use LABEL=PATH: {value}")
        label, raw_path = value.split("=", 1)
        if not label or not raw_path or label in result:
            raise BuildError(f"invalid or duplicate {option}: {value}")
        result[label] = Path(raw_path).expanduser()
    return result


def load_name_map(
    path: Path,
    label: str,
    manifest: dict[str, dict[str, str]],
    expected_hash: str,
) -> dict[str, dict[str, str]]:
    require_hash(path, expected_hash, f"Linux filename map {label}")
    result: dict[str, dict[str, str]] = {}
    storage_hashes: dict[str, tuple[str, str]] = {}
    for row in read_delimited(path, NAME_MAP_FIELDS, delimiter="\t"):
        if row["root_label"] != label:
            continue
        logical = safe_relative(row["source_relative_path"], "name-map source path").as_posix()
        storage = linux_safe_storage(row["storage_relative_path"], "name-map storage path").as_posix()
        if not storage.startswith(".linux-name-map/objects/"):
            raise BuildError(f"name-map storage is outside the reserved object store: {storage}")
        if logical in result:
            raise BuildError(f"duplicate name-map source path: {label}:{logical}")
        source = manifest.get(logical)
        if not source or source["entry_type"] != "file":
            raise BuildError(f"name-map source is not a manifest file: {label}:{logical}")
        if row["bytes"] != source["bytes"] or row["sha256"] != source["sha256"]:
            raise BuildError(f"name-map evidence does not match manifest: {label}:{logical}")
        identity = (row["bytes"], row["sha256"])
        if storage in storage_hashes and storage_hashes[storage] != identity:
            raise BuildError(f"name-map storage collision with different content: {storage}")
        storage_hashes[storage] = identity
        result[logical] = row
    return result


def storage_for(logical: str, name_map: dict[str, dict[str, str]]) -> str:
    mapped = name_map.get(logical)
    storage = mapped["storage_relative_path"] if mapped else logical
    if not mapped and safe_relative(logical, "logical path").parts[0] in RESERVED_ROOTS:
        raise BuildError(f"source path collides with builder-reserved namespace: {logical}")
    return linux_safe_storage(storage, "storage path").as_posix()


def load_symlink_plan(
    path: Path,
    label: str,
    manifest: dict[str, dict[str, str]],
    name_map: dict[str, dict[str, str]],
    expected_hash: str,
) -> dict[str, dict[str, str]]:
    require_hash(path, expected_hash, f"safe symlink plan {label}")
    result: dict[str, dict[str, str]] = {}
    for row in read_delimited(path, SYMLINK_PLAN_FIELDS, delimiter="\t"):
        if row["root_label"] != label:
            continue
        source_logical = safe_relative(row["source_relative_path"], "symlink source path").as_posix()
        target_logical = safe_relative(row["target_logical_relative_path"], "symlink target logical path").as_posix()
        target_storage = linux_safe_storage(row["target_storage_relative_path"], "symlink target storage path").as_posix()
        if source_logical in result:
            raise BuildError(f"duplicate symlink plan path: {label}:{source_logical}")
        source = manifest.get(source_logical)
        target = manifest.get(target_logical)
        if not source or source["entry_type"] != "symlink":
            raise BuildError(f"symlink plan source is not a manifest symlink: {label}:{source_logical}")
        if not target or target["entry_type"] != "file":
            raise BuildError(f"symlink plan target is not a manifest file: {label}:{target_logical}")
        if target_storage != storage_for(target_logical, name_map):
            raise BuildError(f"symlink target storage disagrees with filename map: {label}:{source_logical}")
        if row["bytes"] != target["bytes"] or row["sha256"] != target["sha256"]:
            raise BuildError(f"symlink target hash/size disagrees with manifest: {label}:{source_logical}")
        manifest_target = source["link_target"]
        if not manifest_target or "\x00" in manifest_target:
            raise BuildError(f"manifest symlink target is empty or invalid: {label}:{source_logical}")
        if manifest_target.startswith("/"):
            normalized_manifest_target = posixpath.normpath(manifest_target)
            if not normalized_manifest_target.endswith("/" + target_logical):
                raise BuildError(f"safe plan changes legacy absolute symlink semantics: {label}:{source_logical}")
        else:
            resolved_logical = posixpath.normpath(
                posixpath.join(posixpath.dirname(source_logical), manifest_target)
            )
            if resolved_logical.startswith("../") or resolved_logical == ".." or resolved_logical != target_logical:
                raise BuildError(f"safe plan changes manifest symlink semantics: {label}:{source_logical}")
        source_storage = storage_for(source_logical, name_map)
        link_target = row["normalized_link_target"]
        if not link_target or link_target.startswith("/") or "\x00" in link_target:
            raise BuildError(f"unsafe normalized symlink target: {label}:{source_logical}")
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_storage), link_target))
        if resolved.startswith("../") or resolved == ".." or resolved != target_storage:
            raise BuildError(f"symlink escapes root or resolves to unexpected storage: {label}:{source_logical}")
        expected_link = posixpath.relpath(target_storage, posixpath.dirname(source_storage) or ".")
        if link_target != expected_link:
            raise BuildError(f"symlink target is not normalized: {label}:{source_logical}")
        result[source_logical] = row
    return result


def make_plan(
    comparison: dict[str, dict[str, str]],
    resolutions: dict[str, dict[str, str]],
    manifests: dict[str, dict[str, dict[str, str]]],
    name_maps: dict[str, dict[str, dict[str, str]]],
    symlink_plans: dict[str, dict[str, dict[str, str]]],
    left_label: str,
    right_label: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    plan: list[dict[str, str]] = []
    regeneration: list[dict[str, str]] = []
    physical: dict[str, tuple[str, str, str]] = {}
    selected_storage: set[str] = set()
    pending_symlinks: list[tuple[str, str]] = []

    for relative in sorted(comparison):
        compared = comparison[relative]
        status = compared["status"]
        if status == "content_conflict":
            resolution = resolutions[relative]
            plan.append(
                {
                    "logical_relative_path": relative,
                    "storage_relative_path": "",
                    "action": resolution["canonical_action"],
                    "source_label": "",
                    "source_relative_path": "",
                    "source_storage_relative_path": "",
                    "entry_type": "",
                    "bytes": "",
                    "sha256": "",
                    "link_target": "",
                    "comparison_status": status,
                    "resolution": resolution["resolution"],
                    "rebuild_source": resolution["rebuild_source"],
                }
            )
            if resolution["canonical_action"] == "omit_then_regenerate":
                regeneration.append({field: resolution[field] if field in resolution else relative for field in REGEN_FIELDS})
                regeneration[-1]["logical_relative_path"] = relative
            continue

        if status in {"same_sha256", "left_only"}:
            source_label = left_label
        elif status == "right_only":
            source_label = right_label
        else:
            raise BuildError(f"comparison status is not buildable: {relative}:{status}")
        source = manifests[source_label][relative]
        storage = storage_for(relative, name_maps[source_label])
        link_target = ""
        if source["entry_type"] == "symlink":
            symlink = symlink_plans.get(source_label, {}).get(relative)
            if not symlink:
                raise BuildError(f"selected symlink has no safe plan: {source_label}:{relative}")
            link_target = symlink["normalized_link_target"]
            pending_symlinks.append((storage, symlink["target_storage_relative_path"]))
        else:
            selected_storage.add(storage)
        identity = (source["entry_type"], source["bytes"], source["sha256"] or link_target)
        if storage in physical and physical[storage] != identity:
            raise BuildError(f"destination storage collision: {storage}")
        physical[storage] = identity
        plan.append(
            {
                "logical_relative_path": relative,
                "storage_relative_path": storage,
                "action": "copy" if source["entry_type"] == "file" else "rebuild_symlink",
                "source_label": source_label,
                "source_relative_path": relative,
                "source_storage_relative_path": storage,
                "entry_type": source["entry_type"],
                "bytes": source["bytes"],
                "sha256": source["sha256"],
                "link_target": link_target,
                "comparison_status": status,
                "resolution": "",
                "rebuild_source": "",
            }
        )
    for source_storage, target_storage in pending_symlinks:
        if target_storage not in selected_storage:
            raise BuildError(f"symlink target is not selected into candidate: {source_storage} -> {target_storage}")
    return plan, regeneration


def source_path_for(row: dict[str, str], roots: dict[str, Path]) -> Path:
    return root_path(roots[row["source_label"]], row["source_storage_relative_path"], "source storage path")


def verify_sources(
    plan: list[dict[str, str]],
    roots: dict[str, Path],
    conflict_rows: Iterable[tuple[str, dict[str, str], str]],
    name_maps: dict[str, dict[str, dict[str, str]]],
    manifests: dict[str, dict[str, dict[str, str]]],
    symlink_plans: dict[str, dict[str, dict[str, str]]],
    progress_every: int,
) -> int:
    checks: dict[tuple[str, str], tuple[str, str, str]] = {}
    symlinks: list[dict[str, str]] = []
    for row in plan:
        if row["action"] == "copy":
            checks[(row["source_label"], row["source_storage_relative_path"])] = (
                row["bytes"], row["sha256"], row["logical_relative_path"]
            )
        elif row["action"] == "rebuild_symlink":
            symlinks.append(row)
    for relative, manifest_row, label in conflict_rows:
        if manifest_row["entry_type"] != "file":
            raise BuildError(f"conflict source is not a regular file and cannot be hash rebound: {label}:{relative}")
        storage = storage_for(relative, name_maps[label])
        checks[(label, storage)] = (manifest_row["bytes"], manifest_row["sha256"], relative)
    checked = 0
    for (label, storage), (expected_bytes, expected_hash, logical) in sorted(checks.items()):
        source = root_path(roots[label], storage, "source storage path")
        if not source.is_file() or source.is_symlink():
            raise BuildError(f"source file missing or wrong type: {label}:{logical} via {storage}")
        if source.stat().st_size != int(expected_bytes):
            raise BuildError(f"source file size mismatch: {label}:{logical}")
        actual = sha256_file(source)
        if actual != expected_hash:
            raise BuildError(f"source file SHA-256 mismatch: {label}:{logical}")
        checked += 1
        if progress_every and checked % progress_every == 0:
            print(f"source verification: {checked:,} files", file=sys.stderr, flush=True)
    for row in symlinks:
        source = source_path_for(row, roots)
        if not source.is_symlink():
            raise BuildError(f"staged symlink missing: {row['source_label']}:{row['logical_relative_path']}")
        expected = symlink_plans[row["source_label"]][row["logical_relative_path"]]["normalized_link_target"]
        if os.readlink(source) != expected:
            raise BuildError(f"staged symlink target differs from safe plan: {row['source_label']}:{row['logical_relative_path']}")
    return checked


def write_csv_atomic(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]], *, delimiter: str = ",") -> str:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=tuple(fields), delimiter=delimiter, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return sha256_file(path)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def canonical_map_rows(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    rows = []
    for row in plan:
        if row["action"] == "copy" and row["logical_relative_path"] != row["storage_relative_path"]:
            rows.append(
                {
                    "root_label": "canonical-main",
                    "source_relative_path": row["logical_relative_path"],
                    "storage_relative_path": row["storage_relative_path"],
                    "bytes": row["bytes"],
                    "sha256": row["sha256"],
                }
            )
    return rows


def copy_verified(source: Path, destination: Path, expected_bytes: int, expected_hash: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    digest = hashlib.sha256()
    copied = 0
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            for chunk in iter(lambda: reader.read(CHUNK_BYTES), b""):
                writer.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if copied != expected_bytes or digest.hexdigest() != expected_hash:
            raise BuildError(f"copy verification failed: {source}")
        shutil.copystat(source, temporary, follow_symlinks=False)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def iter_physical_manifest(root: Path) -> Iterable[dict[str, str | int]]:
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        base = Path(directory)
        symlink_dirs = [name for name in dirnames if (base / name).is_symlink()]
        for name in symlink_dirs:
            dirnames.remove(name)
            filenames.append(name)
        for name in sorted(filenames):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if relative == ".canonical-build-incomplete.json":
                continue
            stat = path.lstat()
            if path.is_symlink():
                yield {
                    "root_label": "canonical-main",
                    "relative_path": relative,
                    "entry_type": "symlink",
                    "bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": "",
                    "link_target": os.readlink(path),
                }
            else:
                yield {
                    "root_label": "canonical-main",
                    "relative_path": relative,
                    "entry_type": "file",
                    "bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": sha256_file(path),
                    "link_target": "",
                }


def apply_plan(
    plan: list[dict[str, str]],
    roots: dict[str, Path],
    output_root: Path,
    report_dir: Path,
    progress_every: int,
) -> tuple[int, str]:
    if output_root.exists() or output_root.is_symlink():
        raise BuildError(f"output root already exists; refusing in-place build: {output_root}")
    if not output_root.parent.is_dir():
        raise BuildError(f"output root parent does not exist: {output_root.parent}")
    output_root.mkdir(mode=0o750)
    marker = output_root / ".canonical-build-incomplete.json"
    write_json_atomic(
        marker,
        {
            "schema_version": "tw_exam_canonical_build_marker_v1",
            "created_at": datetime.now().astimezone().isoformat(),
            "state": "incomplete",
        },
    )
    copied_storage: dict[str, tuple[int, str]] = {}
    copied = 0
    for row in plan:
        if row["action"] != "copy":
            continue
        storage = row["storage_relative_path"]
        identity = (int(row["bytes"]), row["sha256"])
        destination = root_path(output_root, storage, "destination storage path")
        if storage in copied_storage:
            if copied_storage[storage] != identity:
                raise BuildError(f"duplicate storage path changed identity: {storage}")
            continue
        source = source_path_for(row, roots)
        copy_verified(source, destination, *identity)
        copied_storage[storage] = identity
        copied += 1
        if progress_every and copied % progress_every == 0:
            print(f"candidate copy: {copied:,} files", file=sys.stderr, flush=True)
    for row in plan:
        if row["action"] != "rebuild_symlink":
            continue
        destination = root_path(output_root, row["storage_relative_path"], "destination symlink path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise BuildError(f"symlink destination already exists: {row['storage_relative_path']}")
        os.symlink(row["link_target"], destination)
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(row["storage_relative_path"]), row["link_target"])
        )
        target = root_path(output_root, resolved, "resolved symlink target")
        if not target.is_file() or target.is_symlink():
            raise BuildError(f"rebuilt symlink target is missing or not a regular file: {row['logical_relative_path']}")

    map_path = output_root / ".linux-name-map" / "canonical-filename-map.tsv"
    map_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv_atomic(map_path, NAME_MAP_FIELDS, canonical_map_rows(plan), delimiter="\t")

    for storage, (expected_bytes, expected_hash) in copied_storage.items():
        destination = root_path(output_root, storage, "destination verification path")
        if destination.stat().st_size != expected_bytes or sha256_file(destination) != expected_hash:
            raise BuildError(f"post-copy candidate verification failed: {storage}")

    physical_manifest = report_dir / "canonical-physical-manifest.csv"
    physical_hash = write_csv_atomic(physical_manifest, MANIFEST_FIELDS, iter_physical_manifest(output_root))
    return copied, physical_hash


def ensure_report_dir(report_dir: Path, roots: dict[str, Path], output_root: Path) -> None:
    resolved = report_dir.resolve(strict=False)
    for label, root in roots.items():
        source = root.resolve(strict=False)
        if resolved == source or source in resolved.parents:
            raise BuildError(f"report directory is inside source root {label}: {report_dir}")
    target = output_root.resolve(strict=False)
    if resolved == target or target in resolved.parents:
        raise BuildError(f"report directory is inside output root: {report_dir}")
    report_dir.mkdir(parents=True, exist_ok=True)
    for name in ("canonical-build-plan.csv", "regeneration-queue.csv", "canonical-build-report.json"):
        if (report_dir / name).exists():
            raise BuildError(f"report output already exists; use a new report directory: {report_dir / name}")


def run(args: argparse.Namespace) -> int:
    left_manifest = args.left_manifest.expanduser()
    right_manifest = args.right_manifest.expanduser()
    comparison_path = args.comparison.expanduser()
    resolution_path = args.resolution.expanduser()
    output_root = args.output_root.expanduser()
    report_dir = args.report_dir.expanduser()
    roots = {
        args.left_label: args.left_root.expanduser(),
        args.right_label: args.right_root.expanduser(),
    }
    if args.left_label == args.right_label:
        raise BuildError("left and right labels must be distinct")
    for label, root in roots.items():
        if not root.is_dir():
            raise BuildError(f"source root is not a directory: {label}={root}")
    ensure_report_dir(report_dir, roots, output_root)

    left = load_manifest(left_manifest, args.left_label, args.expect_left_manifest_sha256)
    right = load_manifest(right_manifest, args.right_label, args.expect_right_manifest_sha256)
    comparison = load_comparison(comparison_path, args.expect_comparison_sha256, left, right)
    resolutions = load_resolutions(
        resolution_path,
        args.expect_resolution_sha256,
        args.expect_comparison_sha256,
        comparison,
    )
    map_paths = parse_label_paths(args.name_map, "--name-map")
    symlink_paths = parse_label_paths(args.safe_symlink_plan, "--safe-symlink-plan")
    map_hashes = parse_label_paths(args.expect_name_map_sha256, "--expect-name-map-sha256")
    symlink_hashes = parse_label_paths(
        args.expect_safe_symlink_plan_sha256,
        "--expect-safe-symlink-plan-sha256",
    )
    map_hash_values = {label: str(value) for label, value in map_hashes.items()}
    symlink_hash_values = {label: str(value) for label, value in symlink_hashes.items()}
    for option, labelled in (
        ("--name-map", map_paths),
        ("--safe-symlink-plan", symlink_paths),
    ):
        unknown = sorted(set(labelled) - set(roots))
        if unknown:
            raise BuildError(f"{option} has unknown source labels: {unknown}")
    if set(map_hash_values) != set(map_paths):
        raise BuildError("every --name-map must have exactly one --expect-name-map-sha256")
    if set(symlink_hash_values) != set(symlink_paths):
        raise BuildError("every --safe-symlink-plan must have exactly one --expect-safe-symlink-plan-sha256")
    manifests = {args.left_label: left, args.right_label: right}
    name_maps: dict[str, dict[str, dict[str, str]]] = {}
    symlink_plans: dict[str, dict[str, dict[str, str]]] = {}
    for label in roots:
        name_maps[label] = (
            load_name_map(map_paths[label], label, manifests[label], map_hash_values[label])
            if label in map_paths else {}
        )
        symlink_plans[label] = (
            load_symlink_plan(
                symlink_paths[label],
                label,
                manifests[label],
                name_maps[label],
                symlink_hash_values[label],
            )
            if label in symlink_paths else {}
        )
    plan, regeneration = make_plan(
        comparison,
        resolutions,
        manifests,
        name_maps,
        symlink_plans,
        args.left_label,
        args.right_label,
    )
    plan_hash = write_csv_atomic(report_dir / "canonical-build-plan.csv", PLAN_FIELDS, plan)
    regeneration_hash = write_csv_atomic(report_dir / "regeneration-queue.csv", REGEN_FIELDS, regeneration)

    conflict_rows = []
    for relative in resolutions:
        conflict_rows.append((relative, left[relative], args.left_label))
        conflict_rows.append((relative, right[relative], args.right_label))
    verified_sources = 0
    if args.verify_source_files or args.apply:
        verified_sources = verify_sources(
            plan,
            roots,
            conflict_rows,
            name_maps,
            manifests,
            symlink_plans,
            args.progress_every,
        )

    counts = Counter(row["action"] for row in plan)
    status_counts = Counter(row["comparison_status"] for row in plan)
    report: dict[str, Any] = {
        "schema_version": "tw_exam_canonical_build_report_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "state": "dry_run_complete",
        "apply_requested": bool(args.apply),
        "ready_for_promotion": False,
        "output_root": str(output_root.resolve(strict=False)),
        "source_roots": {label: str(path.resolve()) for label, path in roots.items()},
        "evidence": {
            "left_manifest": str(left_manifest.resolve()),
            "left_manifest_sha256": args.expect_left_manifest_sha256,
            "right_manifest": str(right_manifest.resolve()),
            "right_manifest_sha256": args.expect_right_manifest_sha256,
            "comparison": str(comparison_path.resolve()),
            "comparison_sha256": args.expect_comparison_sha256,
            "resolution": str(resolution_path.resolve()),
            "resolution_sha256": args.expect_resolution_sha256,
            "plan_sha256": plan_hash,
            "regeneration_queue_sha256": regeneration_hash,
            "linux_filename_maps": {
                label: {"path": str(map_paths[label].resolve()), "sha256": map_hash_values[label]}
                for label in sorted(map_paths)
            },
            "safe_symlink_plans": {
                label: {"path": str(symlink_paths[label].resolve()), "sha256": symlink_hash_values[label]}
                for label in sorted(symlink_paths)
            },
        },
        "counts": {
            "logical_rows": len(plan),
            "actions": dict(sorted(counts.items())),
            "comparison_statuses": dict(sorted(status_counts.items())),
            "approved_conflicts": len(resolutions),
            "regeneration_queue": len(regeneration),
            "source_files_rehashed": verified_sources,
            "canonical_name_mappings": len(canonical_map_rows(plan)),
        },
        "gates": {
            "evidence_hashes_match": True,
            "comparison_rebuilt_exactly": True,
            "conflicts_have_exactly_one_hash_bound_resolution": True,
            "filename_maps_valid": True,
            "selected_symlinks_have_safe_plans": True,
            "source_files_rehashed": bool(args.verify_source_files or args.apply),
            "regeneration_queue_empty": not regeneration,
            "production_authority_changed": False,
        },
    }
    write_json_atomic(report_dir / "canonical-build-report.json", report)

    if args.apply:
        try:
            copied, physical_hash = apply_plan(plan, roots, output_root, report_dir, args.progress_every)
        except (BuildError, OSError) as exc:
            report["state"] = "candidate_incomplete"
            report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            write_json_atomic(report_dir / "canonical-build-report.json", report)
            raise BuildError(f"candidate apply failed; incomplete marker retained: {exc}") from exc
        report["state"] = "candidate_complete"
        report["counts"]["unique_physical_files_copied"] = copied
        report["evidence"]["physical_manifest"] = str((report_dir / "canonical-physical-manifest.csv").resolve())
        report["evidence"]["physical_manifest_sha256"] = physical_hash
        report["gates"]["candidate_files_verified"] = True
        write_json_atomic(report_dir / "canonical-build-report.json", report)
        marker = output_root / ".canonical-build-incomplete.json"
        marker.unlink()

    print(f"state: {report['state']}")
    print(f"logical rows: {len(plan):,}")
    print(f"approved conflicts: {len(resolutions):,}")
    print(f"regeneration queue: {len(regeneration):,}")
    print(f"plan SHA-256: {plan_hash}")
    print(f"report: {report_dir / 'canonical-build-report.json'}")
    if args.apply:
        print(f"candidate: {output_root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left-manifest", type=Path, required=True)
    parser.add_argument("--left-label", required=True)
    parser.add_argument("--left-root", type=Path, required=True)
    parser.add_argument("--expect-left-manifest-sha256", required=True)
    parser.add_argument("--right-manifest", type=Path, required=True)
    parser.add_argument("--right-label", required=True)
    parser.add_argument("--right-root", type=Path, required=True)
    parser.add_argument("--expect-right-manifest-sha256", required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--expect-comparison-sha256", required=True)
    parser.add_argument("--resolution", type=Path, required=True)
    parser.add_argument("--expect-resolution-sha256", required=True)
    parser.add_argument("--name-map", action="append", default=[], metavar="LABEL=PATH")
    parser.add_argument("--expect-name-map-sha256", action="append", default=[], metavar="LABEL=SHA256")
    parser.add_argument("--safe-symlink-plan", action="append", default=[], metavar="LABEL=PATH")
    parser.add_argument(
        "--expect-safe-symlink-plan-sha256",
        action="append",
        default=[],
        metavar="LABEL=SHA256",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--verify-source-files", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--progress-every", type=int, default=1000)
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except BuildError as exc:
        print(f"canonical build blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
