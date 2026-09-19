#!/usr/bin/env python3
"""Export one AI395 staging run into a read-only Review UI JSONL bundle.

The staging SQL remains the audit record. This bundle is a human-facing
projection: it contains the active candidate revision, parser exceptions, one
aggregated advisory event per question, and empty append-only human event
files. The export never writes to the formal database or marks a question as
accepted/rejected.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai395_review_staging import StagingDB, canonical_json


def parse_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    db = StagingDB(args.database_url)
    try:
        run = db.fetchone(f"SELECT * FROM {db.table('pipeline_runs')} WHERE run_id=?", (args.run_id,))
        if not run:
            raise SystemExit(f"staging run not found: {args.run_id}")
        candidate_rows = db.fetchall(
            f"SELECT candidate_key, raw_candidate_json FROM {db.table('pipeline_candidates')} WHERE run_id=? ORDER BY candidate_key",
            (args.run_id,),
        )
        candidates: list[dict[str, Any]] = []
        revision_ids: dict[str, str] = {}
        for row in candidate_rows:
            key = str(row["candidate_key"])
            revision = db.fetchone(
                f"""SELECT revision_id, content_json, status
                    FROM {db.table('question_revisions')}
                    WHERE run_id=? AND candidate_key=?
                    ORDER BY revision_no DESC LIMIT 1""",
                (args.run_id, key),
            )
            if not revision:
                continue
            candidate = parse_json(revision["content_json"], parse_json(row["raw_candidate_json"], {}))
            if not isinstance(candidate, dict):
                continue
            metadata = dict(candidate.get("metadata") or {})
            metadata.update({
                "ai395_staging_run_id": args.run_id,
                "ai395_staging_revision_id": str(revision["revision_id"]),
                "ai395_staging_revision_status": str(revision["status"]),
            })
            candidate["metadata"] = metadata
            candidates.append(candidate)
            revision_ids[key] = str(revision["revision_id"])

        candidate_numbers = {
            str(candidate.get("candidate_key") or ""): str(candidate.get("question_number") or "")
            for candidate in candidates
        }
        exception_rows = db.fetchall(
            f"""SELECT candidate_key, owner_stage, reason_code, severity,
                       status, evidence_json
                FROM {db.table('review_exception_queue')}
                WHERE run_id=? ORDER BY candidate_key, exception_id""",
            (args.run_id,),
        )
        issue_rows: list[dict[str, Any]] = []
        for row in exception_rows:
            evidence = parse_json(row["evidence_json"], {})
            issue_json = evidence.get("issue_json") if isinstance(evidence, dict) else {}
            issue_rows.append({
                "candidate_key": str(row["candidate_key"]),
                "question_number": candidate_numbers.get(str(row["candidate_key"]), ""),
                "issue_code": str(row["reason_code"]),
                "severity": str(row["severity"]),
                "message": str((evidence or {}).get("message") or (evidence or {}).get("issue_code") or row["reason_code"]),
                "owner_stage": str(row["owner_stage"]),
                "status": str(row["status"]),
                "issue_json": issue_json,
                "source": (evidence or {}).get("source", "staging_exception_queue"),
            })

        lane_rows = db.fetchall(
            f"""SELECT candidate_key, revision_id, lane_key, provider, model_name,
                       status, result_json, created_at
                FROM {db.table('review_lane_runs')}
                WHERE run_id=? ORDER BY candidate_key, lane_key""",
            (args.run_id,),
        )
        findings = db.fetchall(
            f"""SELECT candidate_key, revision_id, lane_key, finding_code, severity,
                       disposition, evidence_json, proposal_json
                FROM {db.table('review_findings')}
                WHERE run_id=? ORDER BY candidate_key, lane_key, finding_id""",
            (args.run_id,),
        )
        findings_by_key: dict[str, list[dict[str, Any]]] = {}
        for row in findings:
            findings_by_key.setdefault(str(row["candidate_key"]), []).append({
                "lane": str(row["lane_key"]),
                "code": str(row["finding_code"]),
                "severity": str(row["severity"]),
                "disposition": str(row["disposition"]),
                "evidence": parse_json(row["evidence_json"], {}),
                "proposal": parse_json(row["proposal_json"], {}),
            })

        lanes_by_key: dict[str, list[dict[str, Any]]] = {}
        for row in lane_rows:
            result = parse_json(row["result_json"], {})
            lanes_by_key.setdefault(str(row["candidate_key"]), []).append({
                "lane": str(row["lane_key"]),
                "revision_id": str(row["revision_id"]),
                "provider": str(row["provider"]),
                "model": str(row["model_name"]),
                "status": str(row["status"]),
                "created_at": str(row["created_at"]),
                "result": result,
            })

        ai_events: list[dict[str, Any]] = []
        for key in sorted(revision_ids):
            lane_results = lanes_by_key.get(key, [])
            key_findings = findings_by_key.get(key, [])
            model_names = sorted({str(row.get("model") or "") for row in lane_results if row.get("model")})
            providers = sorted({str(row.get("provider") or "") for row in lane_results if row.get("provider")})
            visual_result = next((row for row in lane_results if row.get("lane") == "vision"), None)
            visual_status = None
            if visual_result:
                visual_model_result = (visual_result.get("result") or {}).get("model_result") or {}
                if visual_model_result.get("error") in {"pixels_unavailable", "provider_error"} or visual_result.get("status") in {"finding", "failed"}:
                    visual_status = "visual_uncertain"
            status = "needs_review" if key_findings else "pass"
            audit = {
                "status": status,
                "confidence": 0.0 if key_findings else 0.5,
                "summary": "AI395 staging 五條 lane advisory 匯總；AI 結果只能供人工查閱。" if not key_findings else "AI395 staging 發現需人工核對的 lane 或 parser 證據。",
                "reason": "; ".join(str(item.get("code") or "") for item in key_findings[:12]) or "目前沒有 staging lane finding。",
                "recommended_action": "needs_review" if key_findings else "no_action",
                "findings": key_findings,
                "labels": sorted({str(item.get("lane")) for item in key_findings}),
                "checks": {str(row.get("lane")): str(row.get("status")) for row in lane_results},
                "lane_results": lane_results,
                "visual_status": visual_status,
                "advisory_only": True,
                "materialize": False,
                "source_fidelity_required": True,
                "staging_run_id": args.run_id,
                "revision_id": revision_ids[key],
            }
            ai_events.append({
                "candidate_key": key,
                "action": "ai_audit",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "reviewer": "ai395-staging-export",
                "provider": providers[0] if len(providers) == 1 else ",".join(providers),
                "model": model_names[0] if len(model_names) == 1 else ",".join(model_names),
                "prompt_version": "ai395_lane_advisory_v1",
                "audit_scope": "question",
                "audit": audit,
            })

        write_jsonl(output_dir / "candidates.jsonl", candidates)
        with (output_dir / "issues.csv").open("w", encoding="utf-8", newline="") as handle:
            import csv
            fields = ["candidate_key", "question_number", "issue_code", "severity", "message", "owner_stage", "status", "issue_json", "source"]
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in issue_rows:
                writer.writerow({field: json.dumps(row[field], ensure_ascii=False, sort_keys=True) if field == "issue_json" else row.get(field, "") for field in fields})
        write_jsonl(output_dir / "question_ai_review_events.jsonl", ai_events)
        for name in ("question_review_events.jsonl", "answer_review_events.jsonl"):
            (output_dir / name).write_text("", encoding="utf-8")
        manifest = {
            "bundle_version": "ai395-review-ui-bundle-v1",
            "run_id": args.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "candidate_count": len(candidates),
            "exception_count": len(issue_rows),
            "ai_event_count": len(ai_events),
            "human_review_events_written": 0,
            "advisory_only": True,
            "source_database": "isolated staging database",
            "files": [
                "candidates.jsonl",
                "issues.csv",
                "question_ai_review_events.jsonl",
                "question_review_events.jsonl",
                "answer_review_events.jsonl",
            ],
        }
        (output_dir / "bundle_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "pass", "output_dir": str(output_dir), **manifest}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
