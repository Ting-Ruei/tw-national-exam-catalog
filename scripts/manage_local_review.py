#!/usr/bin/env python3
"""Local, deterministic-first review with immutable runs and per-node commands."""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from build_local_audit_scope import classify, lane_targets, read_issues, write_issues
from review_source_adapter import sha256_file

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_rows(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def run_script(script, args, env=None):
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                   cwd=ROOT, env=env, check=True)


def selection(rows, issues, groups, per_subject):
    buckets = collections.defaultdict(list)
    for row in rows:
        m = row.get("metadata") or {}
        if m.get("group_name") in groups:
            buckets[(str(m.get("group_name")), str(m.get("year")),
                     str(m.get("exam_ordinal")), str(m.get("subject_code")))].append(row)
    selected, coverage = [], []
    for bucket, items in sorted(buckets.items()):
        tagged = [(r, classify(r, issues.get(r["candidate_key"], []))) for r in items]
        # Spread the sample across known defects before filling with clean controls.
        picked = {}
        for tag in ("parser_blocked", "vision", "visual_missing", "group", "answer_special", "notation", "clean"):
            match = next((r for r, tags in tagged if tag in tags and r["candidate_key"] not in picked), None)
            if match is not None and (per_subject == 0 or len(picked) < per_subject):
                picked[match["candidate_key"]] = match
        for r, tags in sorted(tagged, key=lambda x: x[0]["candidate_key"]):
            if per_subject and len(picked) >= per_subject:
                break
            picked[r["candidate_key"]] = r
        for row in picked.values():
            row = json.loads(json.dumps(row))
            tags = classify(row, issues.get(row["candidate_key"], []))
            row.setdefault("metadata", {}).update(review_scope_tags=tags, llm_lane_targets=lane_targets(tags),
                                                   local_review_source="local_parser_snapshot")
            selected.append(row)
        coverage.append({"group": bucket[0], "year": bucket[1], "ordinal": bucket[2],
                         "subject_code": bucket[3], "available": len(items), "selected": len(picked)})
    keys = [r["candidate_key"] for r in selected]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate candidate keys in input; select one version before preparing")
    return sorted(selected, key=lambda r: r["candidate_key"]), coverage


def prepare(config, run_dir):
    if run_dir.exists():
        raise ValueError("run already exists; use evidence/audit/report to resume, or a new --run-id")
    source = (ROOT / config["candidate_jsonl"]).resolve()
    issue_path = (ROOT / config["issue_csv"]).resolve()
    issues = read_issues(issue_path)
    rows, coverage = selection(read_rows(source), issues, config["groups"], config["per_subject"])
    if not rows:
        raise ValueError("empty scope")
    scope = run_dir / "scope"
    scope.mkdir(parents=True)
    candidate = scope / "candidates.jsonl"
    candidate.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    issue = scope / "issues.csv"
    write_issues(issue, rows, issues)
    artifact_id = run_dir.name + ":parser-snapshot"
    write_json(scope / "source_manifest.json", {
        "fixture_id": "real:" + run_dir.name,
        "source_artifacts": [{"artifact_id": artifact_id, "artifact_type": "parser_candidate_jsonl",
            "path": str(source), "sha256": sha256_file(source), "immutable": True, "read_only": True}],
        "candidate_jsonl": {"path": "candidates.jsonl", "sha256": sha256_file(candidate)},
        "issue_csv": {"path": "issues.csv", "sha256": sha256_file(issue)}})
    mineru = (ROOT / config["asset_root"] / "20_mineru_output").resolve()
    write_json(scope / "mineru_manifest.json", {
        "mineru_run_id": run_dir.name + ":existing-mineru", "source_artifact_id": artifact_id,
        "status": "existing_artifact", "input_sha256": sha256_file(source),
        "output_root": str(mineru), "image_root": str(mineru), "read_only": True})
    write_json(run_dir / "spec.json", {"cases": [
        {"id": f"case-{i:05d}", "candidate_key": r["candidate_key"], "lane": "text_evidence",
         "scope_tags": r["metadata"]["review_scope_tags"]} for i, r in enumerate(rows, 1)]})
    config = dict(config, run_id=run_dir.name)
    write_json(run_dir / "config.json", config)
    write_json(run_dir / "inventory.json", {"count": len(rows), "coverage": coverage,
        "input_sha256": sha256_file(source), "issue_sha256": sha256_file(issue_path),
        "human_history": "local parser snapshot; external review history is not available; reconcile before publish",
        "prepared_at": datetime.now(timezone.utc).isoformat()})
    print(json.dumps({"run": str(run_dir), "count": len(rows), "papers": len(coverage)}, ensure_ascii=False))


def evidence(config, run_dir):
    run_script("build_three_source_audit_pilot.py", ["--spec", run_dir / "spec.json",
        "--candidate-jsonl", run_dir / "scope/candidates.jsonl", "--asset-root", ROOT / config["asset_root"],
        "--output-dir", run_dir / "evidence", "--render-dpi", config["render_dpi"], "--resume"])
    run_script("analyze_three_source_pilot.py", ["--packets", run_dir / "evidence/blind-packets.jsonl",
        "--spec", run_dir / "spec.json", "--output", run_dir / "evidence/analysis.json"])


def audit(config, run_dir):
    packets = run_dir / "evidence/blind-packets.jsonl"
    if not packets.exists():
        raise ValueError("run evidence first; never bypass missing three-source preparation")
    png = run_dir / "png-view"
    if not (png / "source_manifest.json").exists():
        run_script("normalize_vision_assets_to_png.py", ["--source-manifest", run_dir / "scope/source_manifest.json",
            "--mineru-manifest", run_dir / "scope/mineru_manifest.json", "--output-dir", png])
    enriched = run_dir / "audit-input"
    if not (enriched / "source_manifest.json").exists():
        from local_answer_evidence import build_answer_evidence
        enriched.mkdir(exist_ok=True)
        manifest = json.loads((png / "source_manifest.json").read_text())
        candidate_path = png / manifest["candidate_jsonl"]["path"]
        rows = list(read_rows(candidate_path))
        answers = build_answer_evidence(rows, ROOT / config["asset_root"], run_dir / "answer-evidence")
        packet_rows = list(read_rows(packets))
        packet_map = {p["candidate_key"]: p for p in packet_rows}
        for row in rows:
            packet = packet_map.get(row["candidate_key"], {})
            packet["answer_source_evidence"] = answers.get(row["candidate_key"], {})
            refs = []
            for relative in (packet.get("visual_evidence") or {}).get("official_question_crops", []):
                crop = (run_dir / "evidence" / relative).resolve()
                if not crop.is_relative_to((run_dir / "evidence").resolve()) or not crop.is_file():
                    raise ValueError("source PDF crop missing or outside evidence root")
                digest = sha256_file(crop)
                target = png / "assets" / ("pdf-" + digest + ".png")
                shutil.copy2(crop, target)
                refs.append({"path": str(target), "sha256": digest})
            row.setdefault("metadata", {})["local_review_pdf_images"] = refs
        candidate_out = enriched / "candidates.jsonl"
        candidate_out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        (enriched / "packets.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in packet_rows), encoding="utf-8")
        manifest["candidate_jsonl"] = {"path": str(candidate_out), "sha256": sha256_file(candidate_out)}
        manifest["issue_csv"]["path"] = str((png / manifest["issue_csv"]["path"]).resolve())
        write_json(enriched / "source_manifest.json", manifest)
    packets = enriched / "packets.jsonl"
    from local_review_evidence import assess_packet

    manifest = json.loads((enriched / "source_manifest.json").read_text(encoding="utf-8"))
    candidate_path = Path(manifest["candidate_jsonl"]["path"])
    rows = list(read_rows(candidate_path))
    packet_rows = list(read_rows(packets))
    packet_map = {str(packet.get("candidate_key")): packet for packet in packet_rows}
    assessments = []
    for row in rows:
        packet = packet_map.get(str(row.get("candidate_key")), {})
        assessments.append(assess_packet(packet, row))
    audit_dir = run_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "assessments.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in assessments),
        encoding="utf-8",
    )
    passed = sum(1 for item in assessments if item.get("status") == "pass")
    blocked = len(assessments) - passed
    summary = {
        "schema_version": "local-review-summary-v1",
        "run_id": run_dir.name,
        "scope_count": len(rows),
        "deterministic_assessment": {"pass": passed, "needs_review": blocked},
        "llm_calls": 0,
        "llm_errors": 0,
        "blocked_count": blocked,
        "eligible_count": passed,
        "production_write_count": 0,
        "human_review_events_written": 0,
        "external_runtime": False,
    }
    summary_path = run_dir / "artifacts" / run_dir.name / "summary.json"
    write_json(summary_path, summary)
    bundle = run_dir / "review-ui"
    bundle.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate_path, bundle / "candidates.jsonl")
    issue_path = Path(manifest["issue_csv"]["path"])
    shutil.copy2(issue_path, bundle / "issues.csv")
    (bundle / "question_review_events.jsonl").write_text("", encoding="utf-8")
    report(run_dir)


def report(run_dir):
    summary = run_dir / "artifacts" / run_dir.name / "summary.json"
    if summary.exists():
        data = json.loads(summary.read_text())
        fields = ["scope_count", "lane_status_counts", "llm_calls", "llm_errors", "blocked_count", "eligible_count",
                  "production_write_count", "human_review_events_written"]
        print(json.dumps({k: data.get(k) for k in fields}, ensure_ascii=False, indent=2))
    else:
        print("No final summary yet; evidence cases and staging SQLite retain checkpoints.")


def serve(config, run_dir, read_only):
    # Input roots remain read-only in the worker; UI writes only its isolated event bundle.
    env = dict(os.environ, ASSET_ROOT=str(ROOT / config["asset_root"]),
               REVIEW_UI_READ_ONLY="1" if read_only else "0", REVIEW_UI_BACKEND="jsonl",
               REVIEW_UI_ADDITIONAL_ASSET_ROOTS=str(run_dir / "png-view/assets"))
    bundle = run_dir / "review-ui"
    args = ["--candidate-jsonl", bundle / "candidates.jsonl", "--issue-csv", bundle / "issues.csv",
        "--review-log", bundle / "question_review_events.jsonl", "--review-backend", "jsonl",
        "--run-summary", run_dir / "artifacts" / run_dir.name / "summary.json",
        "--three-source-packets", run_dir / "evidence/blind-packets.jsonl",
        "--three-source-analysis", run_dir / "evidence/analysis.json",
        "--host", "127.0.0.1", "--port", config["port"], "--mobile-port", config["mobile_port"]]
    run_script("serve_question_review_ui.py", args, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "prepare", "evidence", "audit", "run", "report", "serve"])
    parser.add_argument("--config", type=Path, default=ROOT / "configs/local_review.json")
    parser.add_argument("--run-id", default="local-medpharm-120")
    parser.add_argument("--per-subject", type=int)
    parser.add_argument("--model-mode", choices=["disabled"])
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", args.run_id):
        parser.error("run-id must be a simple identifier, not a path")
    config = json.loads(args.config.read_text())
    if args.per_subject is not None:
        if args.per_subject < 0:
            parser.error("per-subject must be >= 0 (0 means all)")
        config["per_subject"] = args.per_subject
    if args.model_mode:
        config["model_mode"] = args.model_mode
    if config["model_mode"] != "disabled":
        parser.error("local workflow is deterministic-only; model calls require a new task contract")
    run_dir = (ROOT / config["output_root"] / args.run_id).resolve()
    if not run_dir.is_relative_to((ROOT / "tmp").resolve()):
        parser.error("local run output must be below repository tmp/")
    if args.command == "doctor":
        from build_pdf_reference_source import resolve_binary
        checks = {name: bool(importlib.util.find_spec(name)) for name in ("pypdf", "pdfplumber", "PIL")}
        checks.update({name: bool(resolve_binary(name)) for name in ("pdfinfo", "pdftotext", "pdftoppm")})
        checks.update({key: (ROOT / config[key]).is_file() for key in ("candidate_jsonl", "issue_csv")})
        print(json.dumps({"checks": checks, "ready": all(checks.values()), "python": sys.executable}, indent=2))
        return 0 if all(checks.values()) else 2
    if args.command in {"prepare", "run"} and not run_dir.exists():
        prepare(config, run_dir)
    elif args.command == "prepare":
        raise ValueError("run exists; use a new run-id")
    saved = json.loads((run_dir / "config.json").read_text())
    if args.model_mode and args.model_mode != saved["model_mode"]:
        raise ValueError("model change requires a new run-id; do not overwrite prior results")
    if args.command in {"evidence", "run"}: evidence(saved, run_dir)
    if args.command in {"audit", "run"}: audit(saved, run_dir)
    if args.command == "report": report(run_dir)
    if args.command == "serve": serve(saved, run_dir, args.read_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
