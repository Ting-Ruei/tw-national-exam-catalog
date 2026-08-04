#!/usr/bin/env python3
"""Freeze complete, non-truncated Review UI backlog shards over the read-only API."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from v4_common import compact_json, read_json, write_json


FILTER_ORDER = (
    ("category", "categories"),
    ("year", "years"),
    ("subject", "subjects"),
    ("ordinal", "ordinals"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://192.168.10.70:8765")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review-status", default="unreviewed")
    parser.add_argument("--ai-review-status", default="unreviewed")
    parser.add_argument("--q", default="")
    parser.add_argument("--expect-ai-model")
    parser.add_argument("--expect-ai-provider")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def probe_name(
    filters: dict[str, str],
    base_scope: dict[str, str],
    ai_review_expectation: dict[str, str | None] | None = None,
) -> str:
    probe_scope = {
        "base_scope": base_scope,
        "ai_review_expectation": ai_review_expectation or {},
        "filters": filters,
    }
    digest = hashlib.sha256(compact_json(probe_scope).encode("utf-8")).hexdigest()[:16]
    return f"probe_{digest}.json"


class BacklogFreezer:
    def __init__(
        self,
        *,
        base_url: str,
        output_dir: Path,
        review_status: str,
        ai_review_status: str,
        q: str,
        limit: int,
        timeout: float,
        resume: bool,
        expect_ai_model: str | None = None,
        expect_ai_provider: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.output_dir = output_dir
        self.base_scope = {
            "reviewStatus": review_status,
            "aiReviewStatus": ai_review_status,
            "q": q,
        }
        self.ai_review_expectation = {
            "model": expect_ai_model,
            "provider": expect_ai_provider,
        }
        self.limit = limit
        self.timeout = timeout
        self.resume = resume
        self.probe_dir = output_dir / "probes"
        self.shard_dir = output_dir / "shards"
        self.leaves: list[dict[str, Any]] = []
        self.candidate_keys: set[str] = set()

    def fetch(self, filters: dict[str, str]) -> dict[str, Any]:
        probe_path = self.probe_dir / probe_name(
            filters,
            self.base_scope,
            self.ai_review_expectation,
        )
        if self.resume and probe_path.exists():
            payload = read_json(probe_path)
        else:
            params = {
                **self.base_scope,
                "limit": str(self.limit),
                **filters,
            }
            url = f"{self.base_url}/api/candidates?{urllib.parse.urlencode(params)}"
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            write_json(probe_path, payload)
        rows = payload.get("candidates") or []
        filtered = int(payload.get("filtered_count") or 0)
        returned = int(payload.get("returned_count") or len(rows))
        if returned != len(rows) or returned > filtered or returned > self.limit:
            raise RuntimeError(
                f"invalid Review UI response for {filters}: "
                f"filtered={filtered}, returned={returned}, rows={len(rows)}"
            )
        return payload

    def validate_leaf_ai_scope(
        self,
        filters: dict[str, str],
        rows: list[dict[str, Any]],
    ) -> None:
        q = self.base_scope["q"].strip().lower()
        expected_model = self.ai_review_expectation["model"]
        expected_provider = self.ai_review_expectation["provider"]
        for row in rows:
            candidate_key = str(row.get("candidate_key") or "")
            ai_review = row.get("ai_review")
            if not isinstance(ai_review, dict):
                ai_review = {}
            actual_model = ai_review.get("model")
            actual_provider = ai_review.get("provider")
            if q:
                ai_identity = f"{actual_provider or ''} {actual_model or ''}".lower()
                if q not in ai_identity:
                    raise RuntimeError(
                        f"q matched outside latest AI identity in leaf {filters}: "
                        f"candidate_key={candidate_key!r}, q={self.base_scope['q']!r}, "
                        f"provider={actual_provider!r}, model={actual_model!r}"
                    )
            if expected_model is not None and actual_model != expected_model:
                raise RuntimeError(
                    f"latest AI model mismatch in leaf {filters}: "
                    f"candidate_key={candidate_key!r}, expected={expected_model!r}, "
                    f"actual={actual_model!r}"
                )
            if expected_provider is not None and actual_provider != expected_provider:
                raise RuntimeError(
                    f"latest AI provider mismatch in leaf {filters}: "
                    f"candidate_key={candidate_key!r}, expected={expected_provider!r}, "
                    f"actual={actual_provider!r}"
                )

    def freeze_leaf(self, filters: dict[str, str], payload: dict[str, Any]) -> None:
        rows = payload.get("candidates") or []
        filtered = int(payload.get("filtered_count") or 0)
        returned = int(payload.get("returned_count") or len(rows))
        if filtered != returned or returned != len(rows):
            raise RuntimeError(f"refusing truncated leaf for {filters}")
        self.validate_leaf_ai_scope(filters, rows)
        duplicate_keys = sorted(
            str(row.get("candidate_key") or "")
            for row in rows
            if str(row.get("candidate_key") or "") in self.candidate_keys
        )
        if duplicate_keys:
            raise RuntimeError(f"duplicate candidate keys across shards: {duplicate_keys[:5]}")
        keys = {str(row.get("candidate_key") or "") for row in rows}
        if "" in keys:
            raise RuntimeError(f"empty candidate key in leaf {filters}")
        self.candidate_keys.update(keys)
        shard_path = self.shard_dir / f"shard_{len(self.leaves) + 1:04d}.json"
        write_json(shard_path, payload)
        self.leaves.append(
            {
                "filters": dict(filters),
                "filtered_count": filtered,
                "returned_count": returned,
                "path": str(shard_path.resolve()),
            }
        )

    def split(self, filters: dict[str, str], payload: dict[str, Any]) -> None:
        filtered = int(payload.get("filtered_count") or 0)
        if filtered == 0:
            return
        if filtered <= self.limit:
            self.freeze_leaf(filters, payload)
            return
        facets = payload.get("facets") or {}
        for filter_name, facet_name in FILTER_ORDER:
            if filter_name in filters:
                continue
            values = [str(value) for value in facets.get(facet_name) or [] if str(value)]
            if len(values) < 2:
                continue
            child_total = 0
            for value in values:
                child_filters = {**filters, filter_name: value}
                child_payload = self.fetch(child_filters)
                child_count = int(child_payload.get("filtered_count") or 0)
                child_total += child_count
                self.split(child_filters, child_payload)
            if child_total != filtered:
                raise RuntimeError(
                    f"unstable or incomplete split for {filters} by {filter_name}: "
                    f"parent={filtered}, children={child_total}"
                )
            return
        raise RuntimeError(
            f"cannot split {filtered} rows below limit {self.limit}: filters={filters}"
        )

    def run(self) -> dict[str, Any]:
        root = self.fetch({})
        total = int(root.get("filtered_count") or 0)
        categories = [
            str(value)
            for value in (root.get("facets") or {}).get("categories") or []
            if str(value)
        ]
        if not categories:
            raise RuntimeError("Review UI returned no category facets")
        category_total = 0
        for category in categories:
            filters = {"category": category}
            payload = self.fetch(filters)
            category_total += int(payload.get("filtered_count") or 0)
            self.split(filters, payload)
        leaf_total = sum(int(row["filtered_count"]) for row in self.leaves)
        if category_total != total or leaf_total != total or len(self.candidate_keys) != total:
            raise RuntimeError(
                "backlog snapshot coverage mismatch: "
                f"root={total}, categories={category_total}, leaves={leaf_total}, "
                f"unique_keys={len(self.candidate_keys)}"
            )
        manifest = {
            "schema_version": "review_ui_backlog_snapshot_v1",
            "advisory_only": True,
            "review_status": self.base_scope["reviewStatus"],
            "ai_review_status": self.base_scope["aiReviewStatus"],
            "q": self.base_scope["q"],
            "base_scope": dict(self.base_scope),
            "ai_review_expectation": dict(self.ai_review_expectation),
            "base_url": self.base_url,
            "frozen_at": datetime.now(timezone.utc).isoformat(),
            "filtered_count": total,
            "shard_count": len(self.leaves),
            "candidate_key_count": len(self.candidate_keys),
            "shards": self.leaves,
        }
        write_json(self.output_dir / "manifest.json", manifest)
        return manifest


def main() -> int:
    args = parse_args()
    if not 1 <= args.limit <= 1000:
        raise SystemExit("--limit must be between 1 and 1000")
    freezer = BacklogFreezer(
        base_url=args.base_url,
        output_dir=args.output_dir,
        review_status=args.review_status,
        ai_review_status=args.ai_review_status,
        q=args.q,
        limit=args.limit,
        timeout=args.timeout,
        resume=args.resume,
        expect_ai_model=args.expect_ai_model,
        expect_ai_provider=args.expect_ai_provider,
    )
    manifest = freezer.run()
    print(
        json.dumps(
            {
                "ok": True,
                "filtered_count": manifest["filtered_count"],
                "shard_count": manifest["shard_count"],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
