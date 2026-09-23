"""Review-UI append-only event loading.

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
from datetime import datetime
from qbr import discuss
import json
from .constants import AI_FEEDBACK_SCOPES, GROUP_REVIEW_ACTIONS, MOBILE_REVIEW_ACTIONS, NOTE_ACTIONS, RESET_REVIEW_ACTIONS

def load_ai_feedback_events(
    path: Path,
    *,
    reviewer: str = "local",
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load the latest feedback per candidate/scope/reviewer from JSONL."""
    latest: dict[str, dict[str, dict[str, Any]]] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("action") != "ai_feedback":
                continue
            if reviewer and str(event.get("reviewer") or "local") != reviewer:
                continue
            key = str(event.get("candidate_key") or "")
            scope = str(event.get("audit_scope") or "")
            if not key or scope not in AI_FEEDBACK_SCOPES:
                continue
            latest.setdefault(key, {})[scope] = event
    return latest


def load_ai_learning_events(
    path: Path,
    *,
    reviewer: str = "local",
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load the latest human-selected training example per candidate/scope."""
    latest: dict[str, dict[str, dict[str, Any]]] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("action") != "ai_learning":
                continue
            if reviewer and str(event.get("reviewer") or "local") != reviewer:
                continue
            key = str(event.get("candidate_key") or "")
            scope = str(event.get("audit_scope") or "")
            if not key or scope not in AI_FEEDBACK_SCOPES:
                continue
            latest.setdefault(key, {})[scope] = event
    return latest


def load_append_only_events(path: Path) -> list[dict[str, Any]]:
    """Every record of an append-only stream, in file order, skipping damaged lines.

    Delegates to `qbr.discuss.load_events`. The folding rule for these streams (add/remove, ask/answer)
    has three readers - this server, the repair agent's prompt, and the push/deploy/carry name lists -
    and the one that must not be duplicated is the *interpretation*. `refresh_queue_text.py` carried a
    second `_taxonomy` and a queue index in a different shape broke the whole question area; the same
    mistake here would make the principles the agent is given differ from the ones the person sees.
    """
    return discuss.load_events(path)


def load_correction_feedback_events(path: Path) -> dict[str, dict[str, Any]]:
    """Load the latest immutable before/after feedback per candidate."""
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or not event.get("feedback_id"):
                    continue
                key = str(event.get("candidate_key") or "")
                if key:
                    latest[key] = event
    except OSError:
        return {}
    return latest


def load_correction_feedback_rows(path: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    """Load an append-only feedback outbox for the current local operator export."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("feedback_id"):
                    rows.append(event)
    except OSError:
        return []
    return rows[-max(1, min(int(limit), 500)):]


def _is_note_event(event: dict[str, Any]) -> bool:
    """A note *about* a question, as opposed to a verdict on it.

    `comment` is the action a note is written with; `note_action` is the marker
    `_reaffirm_standing_action` leaves behind when it re-states the decision the note is attached to
    (in which case `action` is that decision, and the event is still a note).
    """
    return event.get("action") in NOTE_ACTIONS or event.get("note_action") == "note"


def _note_annotates_pending_reset(
    event: dict[str, Any],
    key: str,
    latest: dict[str, Any],
    latest_reset: dict[str, Any],
) -> bool:
    """True when this event is a note for a question whose latest state is a pending reset.

    This is the *one* place the condition lives, because it has to hold in every fold: the JSONL
    load and the SQL load, for questions and for answers. Six hand-written copies of one condition
    is six chances for the 錯題討論區 to keep a different set of stuck questions than `review_projection`
    describes.
    """
    return _is_note_event(event) and key not in latest and key in latest_reset


def _merge_note_into_reset(reset_event: dict[str, Any], note_event: dict[str, Any]) -> dict[str, Any]:
    """Attach a note to a pending reset **without** letting the note clear the reset.

    The defect this exists for: a repair/accepted-reaudit reset is what puts a question into the
    錯題討論區, and a note is what the 註解 box writes. Because the event log keeps the latest event as
    the question's state, an unreaffirmed note popped the pending reset - so writing the very 註解
    that explains a stuck question **removed that question from the stuck list**. Measured 2026-09-23
    through the real `append_review` + fold path.

    The reset's own `notes` is preserved as `reset_notes`, because that is where a repair marker like
    「修復後待複核」 lives and `review_projection` reads `reset_notes` before `notes`. The person's note
    then becomes the visible `notes` (what the UI shows), and neither has to overwrite the other.
    """
    merged = dict(reset_event)
    original_notes = merged.get("notes")
    if original_notes and not merged.get("reset_notes"):
        merged["reset_notes"] = original_notes
    note = note_event.get("notes")
    if note:
        merged["notes"] = note
    return merged


def load_review_events(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    latest_reset: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest, counts, latest_reset
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            if event.get("action") in MOBILE_REVIEW_ACTIONS:
                continue
            if event.get("action") in GROUP_REVIEW_ACTIONS:
                counts[key] = counts.get(key, 0) + 1
                continue
            if event.get("action") in RESET_REVIEW_ACTIONS:
                counts[key] = counts.get(key, 0) + 1
                # Reset only reopens the human decision.  A previously saved
                # text/image/manual-asset correction remains the effective
                # candidate layer and must not fall back to MinerU raw.
                previous = latest.get(key) or latest_reset.get(key)
                if not event.get("correction") and previous and previous.get("correction"):
                    event["correction"] = previous["correction"]
                latest.pop(key, None)
                latest_reset[key] = event
                continue
            if _note_annotates_pending_reset(event, key, latest, latest_reset):
                latest_reset[key] = _merge_note_into_reset(latest_reset[key], event)
                counts[key] = counts.get(key, 0) + 1
                continue
            # A human decision must not drop the repaired text a previous human decision was made
            # against. The correction can be sitting in `latest_reset`: a repair reopens the
            # question (`latest` popped) and carries the new text; the person then reads it and
            # decides. Measured 2026-09-23: four questions (`108030:305:33 q034/q040/q072/q073`) had
            # a `qbr_dispute_apply` reset at 08:20:05 and a human `accept` after it (the station
            # clock is UTC), and this branch - which looked only at `latest` - lost the correction,
            # so the accepted question fell back to the raw `⻑` the repair had just fixed. The SQL
            # path already read `latest or latest_reset`; this is the same rule, spelled the same.
            previous = latest.get(key) or latest_reset.get(key)
            if "correction" not in event and previous and previous.get("correction"):
                event["correction"] = previous["correction"]
            counts[key] = counts.get(key, 0) + 1
            latest[key] = event
            latest_reset.pop(key, None)
    return latest, counts, latest_reset


def load_group_review_events(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key or event.get("action") not in GROUP_REVIEW_ACTIONS:
                continue
            latest[str(key)] = event
    return latest


def load_latest_events(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    latest_reset: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest, counts, latest_reset
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            if event.get("action") in RESET_REVIEW_ACTIONS:
                counts[key] = counts.get(key, 0) + 1
                latest.pop(key, None)
                latest_reset[key] = event
                continue
            if _note_annotates_pending_reset(event, key, latest, latest_reset):
                latest_reset[key] = _merge_note_into_reset(latest_reset[key], event)
                counts[key] = counts.get(key, 0) + 1
                continue
            counts[key] = counts.get(key, 0) + 1
            latest[key] = event
            latest_reset.pop(key, None)
    return latest, counts, latest_reset


def load_keyed_events(
    path: Path,
    key_field: str = "candidate_key",
    reset_actions: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    if not path.exists():
        return latest, counts
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get(key_field)
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            if reset_actions and event.get("action") in reset_actions:
                latest.pop(key, None)
                continue
            latest[key] = event
    return latest, counts


def file_signature(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _first_event_value(event: dict[str, Any] | None, *keys: str) -> str:
    if not isinstance(event, dict):
        return ""
    for key in keys:
        value = event.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def event_timestamp(event: dict[str, Any] | None) -> float | None:
    if not isinstance(event, dict):
        return None
    value = event.get("created_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OSError):
        return None
