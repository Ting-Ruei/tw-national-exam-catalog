#!/usr/bin/env python3
"""Shared helpers for curriculum taxonomy scripts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMIES = (
    SKILL_ROOT / "references" / "taxonomy-v1.json",
    SKILL_ROOT / "references" / "biochem-taxonomy-v1.json",
)


def taxonomy_paths(paths: Iterable[Path] | None = None) -> list[Path]:
    return [Path(path) for path in (paths or DEFAULT_TAXONOMIES)]


def load_taxonomies(paths: Iterable[Path] | None = None) -> list[tuple[Path, dict[str, Any]]]:
    loaded: list[tuple[Path, dict[str, Any]]] = []
    seen_versions: set[str] = set()
    for path in taxonomy_paths(paths):
        taxonomy = json.loads(path.read_text(encoding="utf-8"))
        version = str(taxonomy.get("taxonomy_version") or "")
        if not taxonomy.get("taxonomy_code") or not version:
            raise SystemExit(f"taxonomy_code and taxonomy_version are required: {path}")
        if version in seen_versions:
            raise SystemExit(f"Duplicate taxonomy_version {version!r}")
        seen_versions.add(version)
        loaded.append((path, taxonomy))
    return loaded


def find_subject(
    taxonomies: list[tuple[Path, dict[str, Any]]],
    name: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    for path, taxonomy in taxonomies:
        for subject in taxonomy.get("subjects", []):
            if name in subject.get("names", []):
                return path, taxonomy, subject
    allowed = sorted(
        subject_name
        for _path, taxonomy in taxonomies
        for subject in taxonomy.get("subjects", [])
        for subject_name in subject.get("names", [])
    )
    raise SystemExit(f"Unknown subject {name!r}; allowed: {', '.join(allowed)}")


def subject_taxonomy_map(
    taxonomies: list[tuple[Path, dict[str, Any]]],
) -> dict[str, tuple[Path, dict[str, Any], dict[str, Any]]]:
    result: dict[str, tuple[Path, dict[str, Any], dict[str, Any]]] = {}
    for path, taxonomy in taxonomies:
        for subject in taxonomy.get("subjects", []):
            for name in subject.get("names", []):
                if name in result:
                    raise SystemExit(f"Subject alias appears in multiple taxonomies: {name}")
                result[name] = (path, taxonomy, subject)
    return result


def flatten_taxonomy(taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    order = 0
    for subject in taxonomy.get("subjects", []):
        order += 1
        subject_code = str(subject["subject_code"])
        canonical_name = str(subject["names"][0])
        rows.append(
            {
                "node_code": subject_code,
                "parent_node_code": None,
                "node_kind": "subject",
                "subject_name": canonical_name,
                "subject_aliases": list(subject.get("names", [])),
                "label": canonical_name,
                "definition": str(subject.get("definition") or f"{canonical_name} 課程分類根節點。"),
                "depth": 0,
                "sort_order": order,
                "is_selectable": False,
                "decision_rules": {"boundary_rules": subject.get("boundary_rules", [])},
            }
        )
        for domain in subject.get("domains", []):
            order += 1
            rows.append(
                {
                    "node_code": domain["code"],
                    "parent_node_code": subject_code,
                    "node_kind": "domain",
                    "subject_name": canonical_name,
                    "subject_aliases": list(subject.get("names", [])),
                    "label": domain["label"],
                    "definition": domain["definition"],
                    "depth": 1,
                    "sort_order": order,
                    "is_selectable": True,
                    "decision_rules": {
                        "positive_cues": domain.get("positive_cues", []),
                        "exclusions": domain.get("exclusions", []),
                    },
                }
            )
            for chapter in domain.get("chapters", []):
                order += 1
                rows.append(
                    {
                        "node_code": chapter["code"],
                        "parent_node_code": domain["code"],
                        "node_kind": "chapter",
                        "subject_name": canonical_name,
                        "subject_aliases": list(subject.get("names", [])),
                        "label": chapter["label"],
                        "definition": chapter["definition"],
                        "depth": 2,
                        "sort_order": order,
                        "is_selectable": True,
                        "decision_rules": {"positive_cues": chapter.get("positive_cues", [])},
                    }
                )
    return rows


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
