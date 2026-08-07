#!/usr/bin/env python3
"""Build or verify deterministic asset manifests for host migration.

Use SHA-256 mode for the final source snapshot and destination acceptance.  A
metadata-only manifest is useful for quick planning but is not cutover proof.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any


FIELDS = ("root_label", "relative_path", "entry_type", "bytes", "mtime_ns", "sha256", "link_target")
CHUNK_BYTES = 4 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_root_specs(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--root must use LABEL=PATH: {value}")
        label, raw_path = value.split("=", 1)
        if not label or not raw_path:
            raise SystemExit(f"--root must use LABEL=PATH: {value}")
        if label in roots:
            raise SystemExit(f"Duplicate root label: {label}")
        roots[label] = Path(raw_path).expanduser()
    if not roots:
        raise SystemExit("At least one --root LABEL=PATH is required.")
    return roots


def entry_row(label: str, root: Path, path: Path, *, with_sha256: bool) -> dict[str, str | int]:
    stat = path.lstat()
    relative = path.relative_to(root).as_posix()
    if path.is_symlink():
        return {
            "root_label": label,
            "relative_path": relative,
            "entry_type": "symlink",
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": "",
            "link_target": os.readlink(path),
        }
    return {
        "root_label": label,
        "relative_path": relative,
        "entry_type": "file",
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path) if with_sha256 else "",
        "link_target": "",
    }


def iter_entries(label: str, root: Path, *, with_sha256: bool) -> Iterator[dict[str, str | int]]:
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        base = Path(directory)
        symlink_dirs: list[str] = []
        for name in dirnames:
            if (base / name).is_symlink():
                symlink_dirs.append(name)
        for name in symlink_dirs:
            dirnames.remove(name)
            yield entry_row(label, root, base / name, with_sha256=False)
        for name in filenames:
            yield entry_row(label, root, base / name, with_sha256=with_sha256)


def output_is_inside_root(output: Path, roots: dict[str, Path]) -> bool:
    resolved_output = output.resolve(strict=False)
    for root in roots.values():
        resolved_root = root.resolve(strict=False)
        if resolved_output == resolved_root or resolved_root in resolved_output.parents:
            return True
    return False


def build_manifest(args: argparse.Namespace) -> int:
    roots = parse_root_specs(args.root)
    for label, root in roots.items():
        if not root.is_dir():
            raise SystemExit(f"Root does not exist or is not a directory: {label}={root}")
    output = args.output.expanduser()
    if output_is_inside_root(output, roots):
        raise SystemExit("Manifest output must be outside every inventoried root.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")

    summary: dict[str, Any] = {
        "schema_version": "tw_exam_asset_manifest_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "hash_algorithm": "sha256" if args.sha256 else "none",
        "roots": {},
    }
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            for label, root in roots.items():
                count = 0
                total_bytes = 0
                for row in iter_entries(label, root, with_sha256=args.sha256):
                    writer.writerow(row)
                    count += 1
                    total_bytes += int(row["bytes"])
                    if args.progress_every and count % args.progress_every == 0:
                        print(f"{label}: {count:,} entries", file=sys.stderr, flush=True)
                summary["roots"][label] = {
                    "source_path": str(root.resolve()),
                    "entries": count,
                    "bytes": total_bytes,
                }
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()

    summary["manifest"] = str(output.resolve())
    summary["manifest_sha256"] = sha256_file(output)
    summary_path = output.with_suffix(output.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"manifest: {output}")
    print(f"summary:  {summary_path}")
    print(f"sha256:  {summary['manifest_sha256']}")
    return 0


def verify_row(row: dict[str, str], roots: dict[str, Path], *, check_mtime: bool) -> str | None:
    label = row["root_label"]
    root = roots.get(label)
    if root is None:
        return f"unknown root label in manifest: {label}"
    relative = Path(row["relative_path"])
    if relative.is_absolute() or ".." in relative.parts:
        return f"unsafe relative path: {label}:{row['relative_path']}"
    path = root / relative
    expected_type = row["entry_type"]
    if not path.exists() and not path.is_symlink():
        return f"missing: {label}:{row['relative_path']}"
    if expected_type == "symlink":
        if not path.is_symlink():
            return f"type mismatch, expected symlink: {label}:{row['relative_path']}"
        target = os.readlink(path)
        if target != row["link_target"]:
            return f"symlink target mismatch: {label}:{row['relative_path']}"
        return None
    if not path.is_file() or path.is_symlink():
        return f"type mismatch, expected file: {label}:{row['relative_path']}"
    stat = path.stat()
    if stat.st_size != int(row["bytes"]):
        return f"size mismatch: {label}:{row['relative_path']}"
    if check_mtime and stat.st_mtime_ns != int(row["mtime_ns"]):
        return f"mtime mismatch: {label}:{row['relative_path']}"
    expected_hash = row.get("sha256", "")
    if expected_hash and sha256_file(path) != expected_hash:
        return f"sha256 mismatch: {label}:{row['relative_path']}"
    return None


def verify_manifest(args: argparse.Namespace) -> int:
    roots = parse_root_specs(args.root)
    manifest = args.manifest.expanduser()
    if not manifest.is_file():
        raise SystemExit(f"Manifest not found: {manifest}")
    expected_paths: dict[str, set[str]] = {label: set() for label in roots}
    mismatches: list[str] = []
    checked = 0
    hashed_rows = 0
    with manifest.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing_fields = [field for field in FIELDS if field not in (reader.fieldnames or [])]
        if missing_fields:
            raise SystemExit(f"Manifest is missing fields: {missing_fields}")
        for row in reader:
            checked += 1
            expected_paths.setdefault(row["root_label"], set()).add(row["relative_path"])
            if row.get("sha256"):
                hashed_rows += 1
            mismatch = verify_row(row, roots, check_mtime=args.check_mtime)
            if mismatch:
                mismatches.append(mismatch)
            if args.progress_every and checked % args.progress_every == 0:
                print(f"verified: {checked:,} entries", file=sys.stderr, flush=True)

    if args.require_sha256 and hashed_rows != checked:
        mismatches.append(f"manifest is not fully hashed: {hashed_rows}/{checked} entries have SHA-256")

    if args.check_extra:
        for label, root in roots.items():
            actual = {
                str(row["relative_path"])
                for row in iter_entries(label, root, with_sha256=False)
            }
            for relative in sorted(actual - expected_paths.get(label, set())):
                mismatches.append(f"unexpected: {label}:{relative}")

    print(f"checked: {checked:,}")
    print(f"hashed rows: {hashed_rows:,}")
    print(f"mismatches: {len(mismatches):,}")
    for mismatch in mismatches[:100]:
        print(mismatch)
    if len(mismatches) > 100:
        print(f"... {len(mismatches) - 100:,} additional mismatch(es) omitted")
    return 0 if not mismatches else 2


def load_manifest_label(path: Path, label: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing_fields = [field for field in FIELDS if field not in (reader.fieldnames or [])]
        if missing_fields:
            raise SystemExit(f"Manifest is missing fields: {path}: {missing_fields}")
        for row in reader:
            if row["root_label"] != label:
                continue
            relative = row["relative_path"]
            if relative in rows:
                raise SystemExit(f"Duplicate path in manifest: {path}: {label}:{relative}")
            rows[relative] = row
    if not rows:
        raise SystemExit(f"No rows found for label {label!r} in {path}")
    return rows


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


def compare_manifests(args: argparse.Namespace) -> int:
    left_path = args.left_manifest.expanduser()
    right_path = args.right_manifest.expanduser()
    left_rows = load_manifest_label(left_path, args.left_label)
    right_rows = load_manifest_label(right_path, args.right_label)
    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    fields = (
        "relative_path",
        "status",
        "left_bytes",
        "right_bytes",
        "left_sha256",
        "right_sha256",
        "left_type",
        "right_type",
    )
    counts: dict[str, int] = {}
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for relative in sorted(set(left_rows) | set(right_rows)):
                left = left_rows.get(relative)
                right = right_rows.get(relative)
                status = comparison_status(left, right)
                counts[status] = counts.get(status, 0) + 1
                writer.writerow(
                    {
                        "relative_path": relative,
                        "status": status,
                        "left_bytes": left.get("bytes", "") if left else "",
                        "right_bytes": right.get("bytes", "") if right else "",
                        "left_sha256": left.get("sha256", "") if left else "",
                        "right_sha256": right.get("sha256", "") if right else "",
                        "left_type": left.get("entry_type", "") if left else "",
                        "right_type": right.get("entry_type", "") if right else "",
                    }
                )
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"comparison: {output}")
    for status in sorted(counts):
        print(f"{status}: {counts[status]:,}")
    return 2 if counts.get("content_conflict", 0) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a deterministic CSV manifest.")
    build.add_argument("--root", action="append", default=[], metavar="LABEL=PATH", required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--sha256", action="store_true", help="Hash every regular file.")
    build.add_argument("--progress-every", type=int, default=1000)
    build.set_defaults(func=build_manifest)

    verify = subparsers.add_parser("verify", help="Verify destination roots against a manifest.")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--root", action="append", default=[], metavar="LABEL=PATH", required=True)
    verify.add_argument("--require-sha256", action="store_true")
    verify.add_argument("--check-mtime", action="store_true")
    verify.add_argument("--check-extra", action="store_true")
    verify.add_argument("--progress-every", type=int, default=1000)
    verify.set_defaults(func=verify_manifest)

    compare = subparsers.add_parser("compare", help="Compare one labelled root from two manifests.")
    compare.add_argument("--left-manifest", type=Path, required=True)
    compare.add_argument("--left-label", required=True)
    compare.add_argument("--right-manifest", type=Path, required=True)
    compare.add_argument("--right-label", required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.set_defaults(func=compare_manifests)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
