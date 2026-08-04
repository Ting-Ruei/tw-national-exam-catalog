#!/usr/bin/env python3
"""Verify that reset_review preserves every earlier human content correction."""

from __future__ import annotations

import json
import hashlib
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from serve_question_review_ui import ReviewState, strip_structured_tables  # noqa: E402


def manual_assets(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("manual_asset") is True:
            found.append(value)
        for nested in value.values():
            found.extend(manual_assets(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(manual_assets(nested))
    return found


def main() -> int:
    state = ReviewState(
        Path("/tmp/reset-projection-candidates.jsonl"),
        None,
        Path("/tmp/reset-projection-review-events.jsonl"),
        review_backend="sql",
    )
    if not state.sql_review_enabled:
        print(json.dumps({"ok": False, "error": "SQL review backend unavailable"}))
        return 2
    with state._sql_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH latest_state AS (
                    SELECT DISTINCT ON (candidate_key) candidate_key, action, id
                    FROM exam.question_review_events
                    WHERE action NOT IN (
                        'confirm_not_group', 'confirm_group', 'reset_group_review',
                        'human_review_pdf_visual', 'mobile_defer', 'mobile_resume'
                    )
                    ORDER BY candidate_key, id DESC
                ),
                latest_correction AS (
                    SELECT DISTINCT ON (candidate_key) candidate_key, id
                    FROM exam.question_review_events
                    WHERE corrected_candidate_json IS NOT NULL
                    ORDER BY candidate_key, id DESC
                )
                SELECT c.raw_candidate_json
                FROM latest_state s
                JOIN latest_correction x USING (candidate_key)
                JOIN exam.question_candidates c USING (candidate_key)
                WHERE s.action IN ('unreviewed', 'reset_review')
                  AND x.id < s.id
                ORDER BY c.candidate_key
                """
            )
            rows = [row[0] for row in cur.fetchall()]
    keys = [str(row.get("candidate_key") or "") for row in rows]
    category_counts = Counter(
        str((row.get("metadata") or {}).get("normalized_category_name") or (row.get("metadata") or {}).get("group_name") or "")
        for row in rows
    )
    latest, counts, resets = state._sql_question_review_maps(keys)
    failures: list[dict[str, Any]] = []
    manual_candidate_count = 0
    manual_asset_count = 0
    missing_manual_assets: list[dict[str, str]] = []
    for raw in rows:
        key = str(raw.get("candidate_key") or "")
        payload = state.candidate_payload(
            raw,
            latest_reviews=latest,
            review_counts=counts,
            latest_reset_reviews=resets,
            latest_answer_reviews={},
            answer_review_counts={},
            latest_ai_reviews={},
            ai_review_counts={},
            formal_question_map={},
            latest_group_reviews={},
            latest_ai_feedbacks={},
        )
        correction = (payload.get("review") or {}).get("correction") or {}
        mismatches: list[str] = []
        if not correction:
            mismatches.append("missing_effective_correction")
        if "stem" in correction:
            expected_stem, _suppressed = strip_structured_tables(str(correction.get("stem") or ""))
            if payload.get("stem") != expected_stem:
                mismatches.append("stem")
        for field in (
            "options",
            "answer",
            "group_ref",
            "group_sequence_no",
            "image_refs",
            "stem_image",
            "answer_image_refs",
            "visual_review",
        ):
            if field in correction and payload.get(field) != correction.get(field):
                mismatches.append(field)
        assets = manual_assets(correction)
        if assets:
            manual_candidate_count += 1
            manual_asset_count += len(assets)
            for asset in assets:
                if asset.get("exists") is not True:
                    missing_manual_assets.append(
                        {"candidate_key": key, "path": str(asset.get("path") or "")}
                    )
        if mismatches:
            failures.append({"candidate_key": key, "mismatches": sorted(set(mismatches))})

    result = {
        "ok": not failures and not missing_manual_assets,
        "protected_candidate_count": len(keys),
        "protected_key_sha256": hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest(),
        "category_counts": dict(sorted(category_counts.items())),
        "reset_state_count": len(resets),
        "unexpected_terminal_state_count": len(latest),
        "manual_candidate_count": manual_candidate_count,
        "manual_asset_count": manual_asset_count,
        "missing_manual_asset_count": len(missing_manual_assets),
        "projection_failure_count": len(failures),
        "projection_failures": failures[:20],
        "missing_manual_assets": missing_manual_assets[:20],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
