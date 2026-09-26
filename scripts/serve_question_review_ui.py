#!/usr/bin/env python3
"""Serve the local review UI for question candidates.

This file is the **composition root**: it parses arguments, builds the review engine and the HTTP
handler, and re-exports every symbol the split modules define. It used to be 10,713 lines; the
implementation now lives in `qbr/review_ui/`.

The re-export block below is a contract, not tidiness. Test files load *this path* by
`importlib.util.spec_from_file_location(...)` and then call things like
`module.split_ai_audit_scopes(...)`; keeping those names reachable from here is what lets the split
happen without touching them. When you move something out of `qbr/review_ui/`, add it to the block.

PostgreSQL is the primary review surface when REVIEW_UI_BACKEND=sql. The legacy JSONL files are kept
only as a transitional append-only backup while the review workflow is migrating fully into SQL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The pipeline package lives at `qbr/src`; the review container mounts the repository at /workspace,
# and `qbr` imports nothing third-party at module level, so this is safe there.
_QBR_SRC = str(Path(__file__).resolve().parents[1] / "qbr" / "src")
if _QBR_SRC not in sys.path:
    sys.path.insert(0, _QBR_SRC)
_cached_qbr = sys.modules.get("qbr")
if _cached_qbr is not None and not getattr(_cached_qbr, "__file__", None):
    # A namespace package with no `__file__` is the repository directory, not the library.
    del sys.modules["qbr"]

from qbr.review_ui import ai_audit as _ai_audit  # noqa: E402
from qbr.review_ui import constants as _constants  # noqa: E402
from qbr.review_ui import events as _events  # noqa: E402
from qbr.review_ui import handlers as _handlers  # noqa: E402
from qbr.review_ui import legacy_assets as _legacy  # noqa: E402
from qbr.review_ui import paths as _paths  # noqa: E402
from qbr.review_ui import queue_view as _queue_view  # noqa: E402
from qbr.review_ui import review_state as _review_state  # noqa: E402

# The modules themselves, under their plain names. A test that patches a module-level global (e.g.
# `PROJECT_ROOT` before calling `safe_file_path`) must patch the module that *owns* it: patching the
# re-exported copy here rebinds this module's name and leaves the function reading its own module's
# value. Exposing them keeps that possible without importing `qbr.review_ui.x` by hand.
ai_audit = _ai_audit
constants = _constants
events = _events
handlers = _handlers
legacy_assets = _legacy
paths = _paths
queue_view = _queue_view
review_state = _review_state

# `review_queue` and `discuss` were imported directly by the original server and are reached through
# it by tests (`self.ui.review_queue.disputes_for_paper`, the discuss taxonomy test). They belong to
# the pipeline package, not to the review UI, so they stay imported once here rather than in every
# module that needs one function from them.
from qbr import discuss  # noqa: E402,F401
from qbr import review_queue  # noqa: E402,F401
# ── the composition root's own imports ───────────────────────────────────────────────────────
#
# Derived from what `parse_args`/`main` actually read, so a name added to them is not silently
# missing here. The first version listed imports by hand and forgot `argparse`; every unit test
# passed because they import the module, and `main` only runs when the program starts.
from pathlib import Path
from http.server import ThreadingHTTPServer
import argparse
import os
import threading
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a local human review UI for question candidates.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument(
        "--run-summary",
        type=Path,
        default=None,
        help="Optional isolated staging summary.json for the workflow console.",
    )
    parser.add_argument(
        "--three-source-analysis",
        type=Path,
        default=None,
        help="Optional three-source analysis.json for evidence queues.",
    )
    parser.add_argument(
        "--three-source-packets",
        type=Path,
        default=None,
        help="Optional blind-packets.jsonl containing bounded PDF evidence.",
    )
    parser.add_argument(
        "--repair-log",
        type=Path,
        default=None,
        help="Optional repair log path shown as an audit reference only.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--mobile-port",
        type=int,
        default=None,
        help="Optional second listener whose root serves only the mobile triage UI.",
    )
    parser.add_argument(
        "--auto-reload-candidates",
        action="store_true",
        help="Automatically reload large candidate/issue files when they change. Disabled by default to avoid memory spikes during review.",
    )
    parser.add_argument(
        "--review-backend",
        choices=["jsonl", "sql"],
        default=os.environ.get("REVIEW_UI_BACKEND", "sql"),
        help="Use JSONL files or PostgreSQL review staging for candidate list queries.",
    )
    return parser.parse_args()



def main() -> None:
    args = parse_args()
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(
        candidate_path,
        issue_path,
        review_log,
        auto_reload_candidates=args.auto_reload_candidates,
        review_backend=args.review_backend,
        run_summary_path=args.run_summary,
        three_source_analysis_path=args.three_source_analysis,
        three_source_packets_path=args.three_source_packets,
        repair_log_path=args.repair_log,
    )
    if args.mobile_port == args.port:
        raise SystemExit("--mobile-port must differ from --port")
    Handler.state = state
    MobileHandler.state = state
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    mobile_server = None
    mobile_thread = None
    if args.mobile_port is not None:
        mobile_server = ThreadingHTTPServer((args.host, args.mobile_port), MobileHandler)
        mobile_thread = threading.Thread(
            target=mobile_server.serve_forever,
            name="mobile-review-ui",
            daemon=True,
        )
        mobile_thread.start()
    print(f"Review UI: http://{args.host}:{args.port}/")
    if args.mobile_port is not None:
        print(f"Mobile Review UI: http://{args.host}:{args.mobile_port}/")
    else:
        print(f"Mobile Review UI: http://{args.host}:{args.port}/mobile/")
    print(f"Candidate JSONL: {candidate_path}")
    print(f"Issue CSV: {issue_path}")
    print(f"Review log: {review_log}")
    print(f"Review backend: {'sql' if state.sql_review_enabled else 'jsonl'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        if mobile_server is not None:
            mobile_server.shutdown()
            mobile_server.server_close()
        if mobile_thread is not None:
            mobile_thread.join(timeout=5)


# ── re-exports ────────────────────────────────────────────────────────────────────────────────
#
# Every symbol the extracted modules define, made reachable from this module again. `test_*.py`
# loads this file by path and calls these directly, so removing a name here breaks tests that never
# import `qbr.review_ui` at all.

from qbr.review_ui.constants import (  # noqa: F401,E402
    ABBREVIATED_BINOMIAL_RE,
    AI_ANSWER_DEFER_LABELS,
    AI_FEEDBACK_RATINGS,
    AI_FEEDBACK_SCOPES,
    AI_OCR_CHAR_REPLACEMENTS,
    AI_OCR_TEXT_REPLACEMENTS,
    AI_RESET_REVIEW_ACTIONS,
    AI_REVIEW_ACTIONS_WITH_WORK,
    AI_REVIEW_PROMPT_VERSION,
    ANSWER_ISSUE_CODES,
    ANSWER_READY_ACTIONS,
    ANSWER_REVIEW_ACTIONS,
    ASSET_ROOT,
    CAPSULE_COMPOUND_RE,
    CAPSULE_EXACT_REPLACEMENTS,
    CATEGORY_GROUP_FILTERS,
    CATEGORY_GROUP_LABELS,
    CATEGORY_GROUP_NORMALIZED_FILTERS,
    DEFAULT_AI_MODEL,
    DEFAULT_CANDIDATE_ROOT,
    GROUP_REVIEW_ACTIONS,
    HUMAN_SUPERSEDES_AI_ACTIONS,
    LATIN_BINOMIAL_RE,
    MANUAL_ASSET_ROOT,
    MOBILE_REVIEW_ACTIONS,
    MOBILE_UI_ROOT,
    NON_QUESTION_REVIEW_ACTIONS,
    NOTE_ACTIONS,
    OPENAI_API_BASE,
    PAGE_HTML,
    PHARMACIST_TRACK_CATEGORIES,
    PHARMACIST_TRACK_FILTER,
    PROJECT_ROOT,
    QBR_AI_FINDINGS_STREAM,
    QUESTION_READY_ACTIONS,
    QUESTION_REVIEW_ACTIONS,
    REPAIR_REVIEWER_PREFIXES,
    RESET_REVIEW_ACTIONS,
    SQL_ANSWER_CATEGORY_EXPR,
    SQL_CANDIDATE_CATEGORY_EXPR,
    SQL_REVIEW_ACCEPTED_REAUDIT_EXPR,
    SQL_REVIEW_PREVIOUS_ACTION_EXPR,
    SQL_REVIEW_REPAIR_PENDING_EXPR,
    STANDING_ACTIONS,
    STRUCTURED_TABLE_OPEN_RE,
    STRUCTURED_TABLE_RE,
    SqlWriteError,
    TABLE_DEPENDENCY_RE,
    TABLE_DEPENDENCY_SQL_RE,
    VISUAL_DEPENDENCY_RE,
    VISUAL_DEPENDENCY_SQL_RE,
    VISUAL_REVIEW_ACTIONS,
    WORKFLOW_QUEUE_DEFINITIONS,
    WORKFLOW_QUEUE_DESCRIPTIONS,
    WORKFLOW_QUEUE_LABELS,
    WORKFLOW_UI_PATH,
    _TAIL_ANCHOR_BYTES,
    category_filter_values,
    category_matches_filter,
    normalize_category_name,
)

from qbr.review_ui.paths import (  # noqa: F401,E402
    content_type_of,
    data_url_to_bytes,
    display_path,
    project_path,
    safe_file_path,
    safe_path_segment,
    sibling_pdf,
    strip_structured_tables,
)

from qbr.review_ui.events import (  # noqa: F401,E402
    _first_event_value,
    _is_note_event,
    _merge_note_into_reset,
    _note_annotates_pending_reset,
    event_timestamp,
    file_signature,
    load_ai_feedback_events,
    load_ai_learning_events,
    load_append_only_events,
    load_correction_feedback_events,
    load_correction_feedback_rows,
    load_group_review_events,
    load_keyed_events,
    load_latest_events,
    load_review_events,
)

from qbr.review_ui.ai_audit import (  # noqa: F401,E402
    ai_audit_has_work,
    ai_audit_is_answer_deferred_only,
    ai_patch_safety_reason,
    ai_review_reference,
    ai_suggested_correction,
    ai_visual_status,
    answer_choice_letters,
    answer_payload_values,
    answer_review_hint,
    apply_ai_ocr_replacements,
    candidate_visual_profile,
    compact_ai_lane_results,
    compact_candidate_for_ai,
    correction_changes_candidate,
    effective_ai_audit_status,
    extract_response_text,
    has_structured_table_evidence,
    human_review_supersedes_ai,
    int_or_zero,
    issue_quality_status,
    local_question_ai_audit,
    normalized_asset_ref,
    normalized_correction,
    openai_question_ai_audit,
    repair_event_info,
    split_ai_audit_scopes,
)

from qbr.review_ui.queue_view import (  # noqa: F401,E402
    CATEGORY_GROUP_NORMALIZED_FILTERS,
    DISCUSS_BUCKETS,
    PRINCIPLES_STREAM,
    QBR_AI_FINDING_FIELDS,
    QbrAiFindingsStore,
    REPAIR_QUESTIONS_STREAM,
    SQL_DISCUSS_PREDICATE,
    _compact_qbr_finding,
    _paper_of_candidate,
    category_filter_values,
    category_matches_filter,
    is_discuss_bucket,
    load_qbr_ai_findings,
    normalize_category_name,
    paper_entries_for,
    principles_projection,
    repair_questions_projection,
    review_projection,
)

from qbr.review_ui.legacy_assets import (  # noqa: F401,E402
    _reaffirm_standing_action,
    html_page,
    latest_path,
    load_issues,
    load_jsonl,
    mobile_asset_response,
    mobile_review_event,
    workflow_lane_map,
    workflow_page,
    workflow_primary_queue,
)

from qbr.review_ui.review_state import (  # noqa: F401,E402
    ReviewState,
)

from qbr.review_ui.handlers import (  # noqa: F401,E402
    Handler,
    MobileHandler,
)


if __name__ == "__main__":
    main()
