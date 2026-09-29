"""Review-UI v1 reference page + asset responses.

Extracted verbatim from `scripts/serve_question_review_ui.py` so one file is no longer 10,700 lines.
`serve_question_review_ui` re-exports every name here; that indirection is deliberate and is why the
test files that load the server by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations



import sys as _sys
from pathlib import Path as _Path

# The server's guarded imports fall back to `from scripts.x import ...`. `scripts/` has no
# `__init__.py`, so that is a namespace-package import and needs the *repository root* on `sys.path`
# — not the `scripts/` directory. Both are added: the root for `scripts.x`, the directory for the
# plain `import x` form that a bare `sys.path` entry would otherwise be needed for.
_REPO_ROOT = str(_Path(__file__).resolve().parents[4])
for _p in (_REPO_ROOT, _REPO_ROOT + "/scripts"):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from typing import Any
from pathlib import Path
import base64
import csv
import json
from .ai_audit import normalized_correction
from .constants import DEFAULT_CANDIDATE_ROOT, MOBILE_UI_ROOT, NOTE_ACTIONS, PAGE_HTML, STANDING_ACTIONS, WORKFLOW_QUEUE_DESCRIPTIONS, WORKFLOW_QUEUE_LABELS, WORKFLOW_UI_PATH

def latest_path(pattern: str) -> Path:
    paths = sorted(DEFAULT_CANDIDATE_ROOT.glob(pattern))
    if not paths:
        raise SystemExit(f"No candidate output found: {DEFAULT_CANDIDATE_ROOT}/{pattern}")
    return paths[-1]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_issues(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}
    issues: dict[str, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("candidate_key") or ""
            if row.get("issue_json"):
                try:
                    row["issue_json"] = json.loads(row["issue_json"])
                except json.JSONDecodeError:
                    pass
            issues.setdefault(key, []).append(row)
    return issues


def html_page() -> bytes:
    return PAGE_HTML.encode("utf-8")


def workflow_page() -> bytes:
    """Return the revision/evidence workbench used by the new workflow.

    v1, reference-only. It is kept served so a bookmarked console and an event consumer do not break
    when a better one arrives; see `mobile_asset_response` for why that matters more than tidiness.
    """
    try:
        return WORKFLOW_UI_PATH.read_bytes()
    except OSError:
        # Keep the legacy page available if a source checkout is incomplete.
        return html_page()


def mobile_asset_response(path: str) -> tuple[bytes, str, str] | None:
    """Return a mobile Review UI asset without exposing arbitrary project files.

    v2 is the baseline; v1 is reference. That is a statement about maintenance, not about routing:
    the v1 pages still answer at their old paths, because an existing bookmark, a PWA install and an
    event consumer are all contracts that a rewrite does not get to break by being better. What
    changes is where the files live (`review_ui/v1-reference/`) and that nothing here is edited any
    more unless it is to keep v1 *working* - a fix, never a feature.

    Keeping v1 served rather than deleting it is also how the two can be compared. A UI claim like
    "the list you walk is the list you see" is worth nothing without the older console still
    answering, on the same data, to walk it differently.
    """
    route = path.rstrip("/") or "/"
    route_map = {
        # v1 (reference-only, no longer maintained). Served for compatibility; the source lives in
        # `review_ui/v1-reference/`. The mobile fast-triage contract at `/mobile/` and its event
        # vocabulary (`mobile_defer` / `mobile_resume`) are unchanged, because review events recorded
        # by an installed PWA have to keep landing in the same ledger.
        "/mobile": ("v1-reference/mobile.html", "text/html; charset=utf-8", "no-store"),
        "/mobile/workflow": ("v1-reference/workflow.html", "text/html; charset=utf-8", "no-store"),
        "/workflow": ("v1-reference/workflow.html", "text/html; charset=utf-8", "no-store"),
        # The linear pass: one list, one question, four decisions. **This is the baseline.**
        # One array is both drawn and walked (charter: 導覽與內容必須來自同一個來源); a filter
        # narrows what is drawn *and* what is walked, because a reviewer who filters to the 12
        # questions with figures and then presses `S` must arrive at the next one with a figure.
        "/v2": ("v2.html", "text/html; charset=utf-8", "no-store"),
        "/v2/": ("v2.html", "text/html; charset=utf-8", "no-store"),
        # 一區一檔（載入序＝檔名前綴；`v2.html` 以 `<script src>` 串起）。
        # `no-cache`：304 由檔案 mtime 復核（本機實測 3.14 的 `os.stat().st_mtime_ns` 為整數）。
        "/v2/01-core.js": ("v2/01-core.js", "text/javascript; charset=utf-8", "no-cache"),
        "/v2/02-area-question.js": (
            "v2/02-area-question.js", "text/javascript; charset=utf-8", "no-cache"),
        "/v2/03-areas.js": ("v2/03-areas.js", "text/javascript; charset=utf-8", "no-cache"),
        "/v2/04-area-discuss.js": (
            "v2/04-area-discuss.js", "text/javascript; charset=utf-8", "no-cache"),
        "/v2/05-boot.js": ("v2/05-boot.js", "text/javascript; charset=utf-8", "no-cache"),
        "/mobile/manifest.webmanifest": (
            "v1-reference/mobile.webmanifest",
            "application/manifest+json; charset=utf-8",
            "public, max-age=3600",
        ),
        "/mobile/sw.js": (
            "v1-reference/mobile-sw.js",
            "text/javascript; charset=utf-8",
            "no-cache",
        ),
    }
    asset = route_map.get(route)
    if asset:
        filename, content_type, cache_control = asset
        try:
            return (MOBILE_UI_ROOT / filename).read_bytes(), content_type, cache_control
        except OSError:
            return None
    if route == "/mobile/icon.png":
        try:
            encoded = (MOBILE_UI_ROOT / "v1-reference" / "mobile-icon.png.b64").read_text(
                encoding="ascii")
            icon = base64.b64decode("".join(encoded.split()), validate=True)
            return icon, "image/png", "public, max-age=86400"
        except (OSError, ValueError):
            return None
    return None


def mobile_review_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Map the phone's intentionally small decision vocabulary to review events."""
    disposition = str(payload.get("disposition") or "").strip()
    action_by_disposition = {
        "accept": "accept",
        "reject": "block",
        "note": "needs_review",
        "defer": "mobile_defer",
        "resume": "mobile_resume",
    }
    action = action_by_disposition.get(disposition)
    if action is None:
        raise ValueError("disposition must be accept, reject, note, defer, or resume")
    candidate_key = str(payload.get("candidate_key") or "").strip()
    if not candidate_key:
        raise ValueError("candidate_key is required")
    notes = str(payload.get("notes") or "").strip()
    if disposition == "note" and not notes:
        raise ValueError("notes are required for note disposition")
    correction = normalized_correction(payload.get("correction"))
    ai_followup_requested = disposition in {"reject", "note"}
    event = {
        "candidate_key": candidate_key,
        "action": action,
        "notes": notes,
        "reviewer": str(payload.get("reviewer") or "local"),
        "source": "mobile_triage",
        "review_surface": "mobile",
        "mobile_disposition": disposition,
        "mobile_only_tag": disposition in {"defer", "resume"},
        "ai_followup": {
            "requested": ai_followup_requested,
            "stage": "pdf_remediation_proposal" if ai_followup_requested else "none",
            "inspect_source_pdf": ai_followup_requested,
            "apply_only_approved_rules": True,
            "new_rule_requires_human_approval": True,
            "auto_accept_allowed": False,
        },
    }
    if correction:
        event["correction"] = correction
    return event


def workflow_lane_map(item: dict[str, Any]) -> tuple[dict[str, str], dict[str, list[str]], list[dict[str, Any]]]:
    ai_review = item.get("ai_review") if isinstance(item.get("ai_review"), dict) else {}
    checks = {
        str(key): str(value)
        for key, value in (ai_review.get("checks") or {}).items()
        if str(key).strip()
    }
    lane_results = ai_review.get("lane_results") if isinstance(ai_review.get("lane_results"), list) else []
    findings_by_lane: dict[str, list[str]] = {}
    for finding in ai_review.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        lane = str(finding.get("lane") or "").strip()
        code = str(finding.get("code") or finding.get("finding_code") or "").strip()
        if lane:
            findings_by_lane.setdefault(lane, [])
        if lane and code:
            findings_by_lane[lane].append(code)
    for result in lane_results:
        if not isinstance(result, dict):
            continue
        lane = str(result.get("lane") or "").strip()
        if not lane:
            continue
        checks.setdefault(lane, str(result.get("status") or "unknown"))
        findings_by_lane.setdefault(lane, [])
        for code in result.get("finding_codes") or []:
            if str(code) not in findings_by_lane[lane]:
                findings_by_lane[lane].append(str(code))
    return checks, findings_by_lane, lane_results


def workflow_primary_queue(item: dict[str, Any], evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assign one candidate to one human queue using deterministic priority."""
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    issues = [issue for issue in (item.get("issues") or []) if isinstance(issue, dict)]
    answer_issues = [issue for issue in (item.get("answer_issues") or []) if isinstance(issue, dict)]
    tags = {str(value).strip() for value in (metadata.get("review_scope_tags") or []) if str(value).strip()}
    checks, findings_by_lane, lane_results = workflow_lane_map(item)
    all_finding_codes = {
        code
        for values in findings_by_lane.values()
        for code in values
        if code
    }
    issue_codes = {
        str(issue.get("issue_code") or "").strip()
        for issue in [*issues, *answer_issues]
        if str(issue.get("issue_code") or "").strip()
    }
    owner_stages = {
        str(issue.get("owner_stage") or "").strip().lower()
        for issue in [*issues, *answer_issues]
        if str(issue.get("owner_stage") or "").strip()
    }
    review = item.get("review") if isinstance(item.get("review"), dict) else {}
    repair_status = item.get("repair_status") if isinstance(item.get("repair_status"), dict) else {}
    source = evidence.get("source") if isinstance(evidence, dict) else {}
    source_status = str(source.get("status") or "")
    source_conflict = source_status in {
        "source_text_difference",
        "insufficient_independent_families",
    } or str(source.get("question_consensus") or "") == "question_text_disagreement"
    has_parser_issue = bool(
        metadata.get("parser_status") in {"blocked", "needs_review"}
        or any(
            code.startswith("parser_")
            or code in {"question_count_mismatch", "question_number_gap", "option_anomaly", "parser_warning", "parser_blocked"}
            for code in issue_codes | tags
        )
        or "parser" in owner_stages
    )
    has_answer_issue = bool(
        answer_issues
        or "answer" in owner_stages
        or "answer" in checks and checks.get("answer") in {"finding", "failed"}
        or findings_by_lane.get("answer")
        or any(code.startswith("answer_") or code in {"mod_answer", "multiple_answer", "answer_special"} for code in all_finding_codes | issue_codes)
    )
    has_vision_issue = bool(
        findings_by_lane.get("vision")
        or checks.get("vision") in {"finding", "failed"}
        or "image" in owner_stages
        or "visual_missing" in tags
        or item.get("visual_profile", {}).get("needs_visual_asset_review")
        or item.get("visual_profile", {}).get("visual_review_status") == "visual_asset_problem"
    )
    has_group_issue = bool(
        findings_by_lane.get("group")
        or checks.get("group") in {"finding", "failed"}
        or "group" in tags
        or item.get("inferred_group_ref")
        or item.get("group_ref")
    )
    has_notation_issue = bool(
        findings_by_lane.get("notation")
        or checks.get("notation") in {"finding", "failed"}
        or "notation" in tags
        or any(code in {"notation", "subscript", "superscript", "glyph", "compatibility_glyph"} for code in all_finding_codes | issue_codes)
    )
    has_revision_issue = bool(
        repair_status.get("active")
        or review.get("is_repair_pending")
        or review.get("is_accepted_reaudit_pending")
        or metadata.get("review_revision_status") not in {None, "", "active"}
    )
    if has_revision_issue:
        queue = "revision"
    elif has_parser_issue:
        queue = "source"
    elif has_answer_issue:
        queue = "answer"
    elif has_vision_issue:
        queue = "vision"
    elif has_group_issue:
        queue = "group"
    elif has_notation_issue:
        queue = "notation"
    elif source_conflict or findings_by_lane.get("text_evidence") or checks.get("text_evidence") in {"finding", "failed"} or "text_evidence" in tags:
        queue = "text"
    else:
        queue = "sample"

    severities = {str(issue.get("severity") or "").lower() for issue in [*issues, *answer_issues]}
    severity = "error" if "error" in severities or queue in {"source", "answer", "vision"} and (issues or answer_issues) else "warning" if severities & {"warning", "warn"} else "info"
    reasons = sorted(issue_codes | all_finding_codes | tags)
    if source_status:
        reasons.insert(0, source_status)
    return {
        "id": queue,
        "label": WORKFLOW_QUEUE_LABELS[queue],
        "description": WORKFLOW_QUEUE_DESCRIPTIONS[queue],
        "severity": severity,
        "reason_codes": list(dict.fromkeys(reasons)),
        "lane_statuses": checks,
        "lane_findings": findings_by_lane,
        "lane_results": lane_results,
    }


def _reaffirm_standing_action(event: dict[str, Any], previous: dict[str, Any] | None) -> None:
    """Keep a note or a correction from replacing the decision it is attached to.

    A correction has always done this; a note has to do the same thing for the same reason. Both say
    something *about* a question, and neither is a verdict on it - but the event log keeps the latest
    event as the question's state, so an unreaffirmed `comment` would silently withdraw an `accept`
    from the formal set (`comment` is not in `QUESTION_READY_ACTIONS`) and make an annotated question
    look unreviewed again. So the event keeps the action that stands, and `comment` is left as the
    action only when there is no decision yet - where it promotes nothing, because nothing but
    `accept`/`unblock` ever marks a question ready.
    """
    action = event.get("action")
    if action not in NOTE_ACTIONS and action != "correct":
        return
    if action == "correct":
        event.setdefault("correction_action", "save")
    else:
        event.setdefault("note_action", "note")
    previous_action = (previous or {}).get("action")
    if previous_action in STANDING_ACTIONS:
        event["action"] = previous_action
    elif action == "correct":
        event["action"] = "reviewed"
