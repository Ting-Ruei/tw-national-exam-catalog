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


V2_PAGE_PATH = MOBILE_UI_ROOT / "v2.html"


def v2_page() -> bytes:
    """The baseline linear pass, as bytes, for whichever route serves it today.

    v2.html's asset refs are relative (`v2/01-core.js`), and both `/` and `/v2` resolve them to
    `/v2/<name>.js` - the document directory is the root on both mounts - so the same bytes serve
    unchanged at either route and `_v2_script_response` keeps being the only script source.
    """
    try:
        return V2_PAGE_PATH.read_bytes()
    except OSError:
        return workflow_page()


def _v2_script_response(route: str) -> tuple[bytes, str, str] | None:
    """Serve `/v2/<name>.js` **from the file, not from a list**.

    Why this is not another `route_map` entry: the list and the `<script src>` list in `v2.html` are
    two things that have to agree, and on 2026-09-24 they did not. Two area files were added to the
    page and not to the map; the browser got 404 for them, the area function was therefore undefined,
    and the 原則區 rendered an empty pane **with no error anywhere** - the page load was `200`, the
    console was quiet, and the only symptom was a feature that did not exist. A missing route is
    silent by construction, so the way to stop the class of defect is to have nothing to forget.

    Nothing arbitrary is exposed: the route must be exactly `/v2/<one path segment>.js`. A segment
    cannot contain `/` or `.` by construction (checked below, character by character), so `..` and
    nested escapes are unrepresentable - there is no traversal to filter. A name the page never asked
    for still has to exist on disk to answer.
    """
    if not route.startswith("/v2/") or not route.endswith(".js"):
        return None
    name = route[len("/v2/"):-len(".js")]
    # One path segment, and only the characters a filename uses: no `/`, no `.`, nothing to escape.
    if not name or not all(c.isalnum() or c in "_-" for c in name):
        return None
    try:
        return ((MOBILE_UI_ROOT / "v2" / f"{name}.js").read_bytes(),
                "text/javascript; charset=utf-8",
                # `no-cache`: the 304 is revalidated by the file mtime (measured on 3.14:
                # `os.stat().st_mtime_ns` is an integer), so editing a file is visible on reload.
                "no-cache")
    except OSError:
        return None


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
        # 5.1（設計者 2026-09-30 裁決：root serve v2、v1 遷 /v1/*）：`/workflow` 與 `/legacy`
        # **由 handlers 轉址到 /v1/***；`/v2` 本身也轉址到 `/`（書籤過渡）。資產引用是絕對路徑
        # （`/mobile/*`、`/api/*`、`/file?…`），所以同一頁在 /v1/* 下照樣成立；`/mobile/*` 是
        # PWA 的契約，原樣保留。
        "/v1/workflow": ("v1-reference/workflow.html", "text/html; charset=utf-8", "no-store"),
        "/v1/legacy": ("v1-reference/legacy.html", "text/html; charset=utf-8", "no-store"),
        # 一區一檔（載入序＝檔名前綴；`v2.html` 以 `<script src>` 串起）。
        # 這一張表**只留頁面**（`/v2`）與 v1 的相容資產。`v2/*.js` 不列在這裡：清單與
        # `<script src>` 是兩個會不一致的地方，而 2026-09-24 就真的不一致了——
        # `03-area-answer.js` 與 `03-area-principles.js` 加進 `v2.html` 之後忘了加進這張表，
        # 瀏覽器拿到 404，`renderPrinciples` 是 undefined，**整個原則區畫不出東西卻沒有任何錯誤訊息**。
        # 所以 v2 的每個 `.js` 都由下面的檔名規則供應（見 `_v2_script_response`）：新增一個區
        # 只要檔案真的存在就會被服務，沒有第二份清單可以忘記。
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
    # v2 的每一個區都是一個 `v2/<name>.js`；由檔案供應，不由清單供應（見 `_v2_script_response`）。
    script = _v2_script_response(route)
    if script:
        return script
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
    """Keep a note from replacing the decision it belongs to, and a correction from wearing its name.

    **A note is not a verdict, and must not withdraw one.** A `comment` says something *about* a
    question; the event log keeps the latest event as the question's state, so an unreaffirmed
    `comment` would silently withdraw an `accept` from the formal set (`comment` is not in
    `QUESTION_READY_ACTIONS`) and make an annotated question look unreviewed again. So a note keeps
    the action that stands, and stays a `comment` only when there is no decision yet - where it
    promotes nothing, because nothing but `accept`/`unblock` ever marks a question ready.

    **A correction is a decision, and it must be recorded as its own.** It says "this text was wrong
    and I replaced it", which is neither a note nor a verdict that the text is right - so it is in
    `STANDING_ACTIONS` (it stands as the question's state, and a later note re-states *it*) and it is
    deliberately **not** in `QUESTION_READY_ACTIONS`. Until 2026-09-25 this function rewrote a
    correction's action into whatever it found underneath it (`reviewed`, or the previous
    `accept`/`block`), on the reasoning that "a correction has always reaffirmed the decision
    underneath". The reasoning was about the *decision*, but the effect was on the *log*: the one
    human correction in the live queue (`moex:107100:305:33:1:question:q076`, 2026-09-25T02:24:43)
    was stored as `reviewed`, so the file could not answer "whose text did a person change?", the list
    drew it as an ordinary 已過目 row, and the reviewer's own act was unfindable in every chip. The
    reviewer reported it as "修正完它就通過了，我就找不到了" - and the projection, which has no label
    for `reviewed`, showed them a decision they had not made.

    The one thing the old rewrite got right is kept: `correction_action` is still stamped, so a
    correction written before this rule can still be told apart from a bare decision.

    **The second half is the reviewer's note surviving a later decision.** A decision that carries no
    note must keep the note the question already stands on, because the note is a property of the
    question and not of one event. Measured in the live log: `moex:105100:305:33:1:question:q046`
    is comment「檢查上下標」→ comment → `block` with `notes: ""`, and every latest-event-wins reader
    (the review UI's projection, `repair_loop.read_latest_actions`, the prompt builders) then sees an
    empty note - so the one sentence the reviewer wrote about that question is invisible to the
    model that is asked to find the problem. The review UI's decision box sends `notes` only while
    the box is open, so an empty box is the *normal* case for a decision; the writer is where that
    has to stop blanking what the person already said. A note the reviewer deliberately changes is
    what they typed; an empty box must not undo it.

    Only a *standing* decision can give or receive a note (`STANDING_ACTIONS`). A `reset_review` is
    not a statement about the question's content and a group/visual/mobile event is not about this
    question at all; `events._merge_note_into_reset` already owns the "a note on a pending reset"
    rule, and carrying a note into a reset here would give one question's note two owners.
    """
    action = event.get("action")
    if action == "correct":
        # 修正的名字留著。它是一個人對**文字**做的處置，不是對題目的判決；改寫成別人的決定會讓
        # 日誌說不出「這題的文字被人改過」，而畫面會拿一個他當下沒做過的決定給他看。
        event.setdefault("correction_action", "save")
    elif action in NOTE_ACTIONS:
        event.setdefault("note_action", "note")
        previous_action = (previous or {}).get("action")
        if previous_action in STANDING_ACTIONS:
            event["action"] = previous_action
    # 以下那一半（把題目既有的註解帶進這一筆）對**每一筆站著的決定**都要跑，不只對註解與修正：
    # 一筆不帶註解的 `block` 也必須保留審題者為這一題寫過的那一句。把閘門往上挪成
    # 「只有註解與修正才跑」正好會把它關掉（2026-09-25 由
    # `qbr/tests/test_reviewer_notes_reach_the_prompt.py` 的兩條測資抓到）。
    if event.get("action") not in STANDING_ACTIONS:
        return
    if str(event.get("notes") or "").strip():
        return
    standing = (previous or {}).get("notes")
    if str(standing or "").strip() and (previous or {}).get("action") in STANDING_ACTIONS:
        event["notes"] = standing
