"""Review-UI the review engine.

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

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - optional runtime dependency in Docker
    psycopg = None
    Jsonb = None
try:
    import promote_ready_candidates_to_formal_postgres as formal_promote
except ImportError:  # pragma: no cover - UI can still run without formal sync helpers
    formal_promote = None
try:
    from question_group_detection import (
        GROUP_CONTINUATION_RE,
        GROUP_COUNT_RE,
        GROUP_COUNT_SQL_RE,
        GROUP_PREFIX_RANGE_RE,
        group_count_from_text,
    )
except ModuleNotFoundError:  # pragma: no cover - importlib-based test loading
    from scripts.question_group_detection import (
        GROUP_CONTINUATION_RE,
        GROUP_COUNT_RE,
        GROUP_COUNT_SQL_RE,
        GROUP_PREFIX_RANGE_RE,
        group_count_from_text,
    )
try:
    from review_feedback import (
        FeedbackContractError,
        NoVisibleChange,
        apply_correction as apply_feedback_correction,
        build_feedback_event,
        build_guardrail_candidate,
        question_snapshot,
    )
except ModuleNotFoundError:  # pragma: no cover - importlib-based test loading
    from scripts.review_feedback import (
        FeedbackContractError,
        NoVisibleChange,
        apply_correction as apply_feedback_correction,
        build_feedback_event,
        build_guardrail_candidate,
        question_snapshot,
    )
from typing import Any
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from qbr import discuss
import gc
import hashlib
import json
import os
import re
from qbr import review_queue
import threading
import time
import urllib.request
from .ai_audit import ai_patch_safety_reason, ai_review_reference, ai_suggested_correction, ai_visual_status, answer_review_hint, candidate_visual_profile, compact_ai_lane_results, compact_candidate_for_ai, effective_ai_audit_status, human_review_supersedes_ai, int_or_zero, issue_quality_status, normalized_correction, openai_question_ai_audit, repair_event_info, split_ai_audit_scopes
from .constants import AI_FEEDBACK_RATINGS, AI_FEEDBACK_SCOPES, AI_RESET_REVIEW_ACTIONS, AI_REVIEW_PROMPT_VERSION, ANSWER_ISSUE_CODES, ANSWER_READY_ACTIONS, CATEGORY_GROUP_FILTERS, CATEGORY_GROUP_NORMALIZED_FILTERS, GROUP_REVIEW_ACTIONS, MANUAL_ASSET_ROOT, MOBILE_REVIEW_ACTIONS, NON_QUESTION_REVIEW_ACTIONS, NOTE_ACTIONS, QBR_AI_FINDINGS_STREAM, QUESTION_READY_ACTIONS, REPAIR_REVIEWER_PREFIXES, RESET_REVIEW_ACTIONS, SQL_ANSWER_CATEGORY_EXPR, SQL_CANDIDATE_CATEGORY_EXPR, SQL_REVIEW_ACCEPTED_REAUDIT_EXPR, SQL_REVIEW_REPAIR_PENDING_EXPR, SqlWriteError, TABLE_DEPENDENCY_SQL_RE, VISUAL_DEPENDENCY_SQL_RE, WORKFLOW_QUEUE_DEFINITIONS
from .events import _merge_note_into_reset, _note_annotates_pending_reset, file_signature, load_ai_feedback_events, load_ai_learning_events, load_append_only_events, load_correction_feedback_events, load_correction_feedback_rows, load_group_review_events, load_keyed_events, load_latest_events, load_review_events
from .legacy_assets import _reaffirm_standing_action, load_issues, load_jsonl, workflow_primary_queue
from .paths import data_url_to_bytes, display_path, safe_path_segment, sibling_pdf, strip_structured_tables
from .queue_view import DISCUSS_BUCKETS, PRINCIPLES_STREAM, QbrAiFindingsStore, REPAIR_QUESTIONS_STREAM, SQL_DISCUSS_PREDICATE, category_filter_values, category_matches_filter, is_discuss_bucket, normalize_category_name, paper_entries_for, principles_projection, repair_questions_projection, review_projection

class ReviewState:
    def __init__(
        self,
        candidate_path: Path,
        issue_path: Path | None,
        review_log: Path,
        *,
        auto_reload_candidates: bool = False,
        review_backend: str = "sql",
        run_summary_path: Path | None = None,
        three_source_analysis_path: Path | None = None,
        three_source_packets_path: Path | None = None,
        repair_log_path: Path | None = None,
    ) -> None:
        self.candidate_path = candidate_path
        self.issue_path = issue_path
        self.review_log = review_log
        self.auto_reload_candidates = auto_reload_candidates
        self.review_backend = review_backend
        self.run_summary_path = run_summary_path
        self.three_source_analysis_path = three_source_analysis_path
        self.three_source_packets_path = three_source_packets_path
        self.repair_log_path = repair_log_path
        self.workflow_summary = self._load_json_object(run_summary_path)
        self.three_source_analysis = self._load_json_object(three_source_analysis_path)
        self.three_source_packets = self._load_packet_map(three_source_packets_path)
        self.three_source_cases = {
            str(case.get("candidate_key")): case
            for case in (self.three_source_analysis.get("cases") or [])
            if isinstance(case, dict) and case.get("candidate_key")
        }
        self.evidence_root = (
            three_source_packets_path.parent.resolve()
            if three_source_packets_path is not None
            else None
        )
        self._candidate_reload_lock = threading.Lock()
        self._candidate_reload_status: dict[str, Any] = {
            "ok": True,
            "auto_reload_candidates": auto_reload_candidates,
            "reloaded": False,
            "busy": False,
            "message": "candidate data loaded at startup",
        }
        self.database_url = os.environ.get("DATABASE_URL")
        self.sql_review_enabled = self.review_backend == "sql" and psycopg is not None and bool(self.database_url)
        self.legacy_jsonl_backup_enabled = os.environ.get("REVIEW_UI_WRITE_LEGACY_JSONL", "0").lower() not in {"0", "false", "no"}
        self.defer_formal_sync = self.sql_review_enabled and os.environ.get("REVIEW_UI_DEFER_FORMAL_SYNC", "1").lower() not in {"0", "false", "no"}
        self._sql_local = threading.local()
        self._sql_facets_cache: dict[str, dict[str, list[str]]] = {}
        self._pipeline_cache_lock = threading.Lock()
        self._pipeline_cache: dict[str, Any] | None = None
        self._pipeline_refreshing = False
        try:
            self._pipeline_cache_ttl_seconds = max(
                5.0,
                float(os.environ.get("REVIEW_UI_PIPELINE_CACHE_SECONDS", "30")),
            )
        except ValueError:
            self._pipeline_cache_ttl_seconds = 30.0
        self._formal_sync_schema_ready = False
        self._formal_sync_wake = threading.Event()
        self._formal_sync_status_lock = threading.Lock()
        self._formal_sync_status: dict[str, Any] = {
            "enabled": self.defer_formal_sync,
            "running": False,
            "queued": 0,
            "last_completed_at": None,
            "last_error": None,
        }
        if self.sql_review_enabled:
            self.candidates = []
            self.candidate_by_key = {}
            self.issues = {}
        else:
            self.candidates = load_jsonl(candidate_path)
            self.candidate_by_key = {str(item.get("candidate_key")): item for item in self.candidates if item.get("candidate_key")}
            self.issues = load_issues(issue_path)
        self._build_candidate_index()
        self.review_log.parent.mkdir(parents=True, exist_ok=True)
        self.answer_review_log = self.review_log.parent / "answer_review_events.jsonl"
        self.ai_review_log = self.review_log.parent / "question_ai_review_events.jsonl"
        self.ai_feedback_log = self.review_log.parent / "question_ai_feedback_events.jsonl"
        self.ai_learning_log = self.review_log.parent / "question_ai_learning_events.jsonl"
        self.correction_feedback_log = self.review_log.parent / "question_correction_feedback_events.jsonl"
        # The qbr pipeline's AI notes live beside the human decisions, and are loaded separately so a
        # note can never be mistaken for a decision: the record has no `action` and no `reviewer`.
        self.qbr_ai_findings_log = self.review_log.parent / QBR_AI_FINDINGS_STREAM
        # The 錯題討論區's two human/agent streams. Both are append-only and both are protected
        # from a `--delete` deploy for the same reason the review log is: nothing outside them
        # can rebuild them. See the constants at the top of this file.
        self.principles_log = self.review_log.parent / PRINCIPLES_STREAM
        self.repair_questions_log = self.review_log.parent / REPAIR_QUESTIONS_STREAM
        self.preference_path = self.review_log.parent / "review_ui_preferences.json"
        if self.sql_review_enabled:
            self.latest_reviews, self.review_counts, self.latest_reset_reviews = {}, {}, {}
            self.latest_group_reviews = {}
            self.latest_answer_reviews, self.answer_review_counts, self.latest_answer_reset_reviews = {}, {}, {}
            self.latest_ai_reviews, self.ai_review_counts = {}, {}
            self.latest_ai_feedbacks = {}
            self.latest_ai_learnings = {}
            self.latest_correction_feedbacks = {}
            self._ensure_ai_feedback_schema()
        else:
            self.latest_reviews, self.review_counts, self.latest_reset_reviews = load_review_events(review_log)
            self.latest_group_reviews = load_group_review_events(review_log)
            self.latest_answer_reviews, self.answer_review_counts, self.latest_answer_reset_reviews = load_latest_events(self.answer_review_log)
            self.latest_ai_reviews, self.ai_review_counts = load_keyed_events(
                self.ai_review_log,
                reset_actions=AI_RESET_REVIEW_ACTIONS,
            )
            self.latest_ai_feedbacks = load_ai_feedback_events(self.ai_feedback_log)
            self.latest_ai_learnings = load_ai_learning_events(self.ai_learning_log)
            self.latest_correction_feedbacks = load_correction_feedback_events(self.correction_feedback_log)
        #: The findings store keeps its own file position and re-reads only the appended tail (see
        #: `QbrAiFindingsStore`). It owns the whole-file load too, so boot reads the file once, not
        #: twice. `latest_qbr_ai_findings` stays the attribute every reader uses; the store only
        #: decides *how* it is kept fresh, because re-reading 480 MB for each appended line was
        #: 1.57 s of every request while the corpus sweep was running.
        self.qbr_ai_findings_store = QbrAiFindingsStore(self.qbr_ai_findings_log)
        self.latest_qbr_ai_findings = self.qbr_ai_findings_store.latest
        # The 錯題討論區's streams are small (a person writes handfuls, not tens of thousands), so
        # they are re-read whole when their signature changes rather than tailed like the findings.
        self.principles_events = load_append_only_events(self.principles_log)
        self.repair_questions_events = load_append_only_events(self.repair_questions_log)
        self._candidate_signature = file_signature(self.candidate_path)
        self._issue_signature = file_signature(self.issue_path) if self.issue_path else None
        self._review_log_signature = file_signature(self.review_log)
        self._answer_review_log_signature = file_signature(self.answer_review_log)
        self._ai_review_log_signature = file_signature(self.ai_review_log)
        self._ai_feedback_log_signature = file_signature(self.ai_feedback_log)
        self._ai_learning_log_signature = file_signature(self.ai_learning_log)
        self._correction_feedback_log_signature = file_signature(self.correction_feedback_log)
        self._qbr_ai_findings_signature = file_signature(self.qbr_ai_findings_log)
        self._principles_log_signature = file_signature(self.principles_log)
        self._repair_questions_log_signature = file_signature(self.repair_questions_log)
        if self.defer_formal_sync:
            try:
                self._ensure_formal_sync_queue_schema()
            except Exception as exc:
                self.defer_formal_sync = False
                self._formal_sync_status.update({"enabled": False, "last_error": str(exc)})
            else:
                threading.Thread(target=self._formal_sync_worker_loop, name="formal-sync", daemon=True).start()
                self._formal_sync_wake.set()
        if self.sql_review_enabled:
            self._start_pipeline_cache_refresh()

    @staticmethod
    def _load_json_object(path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _load_packet_map(path: Path | None) -> dict[str, dict[str, Any]]:
        if path is None or not path.exists():
            return {}
        packets: dict[str, dict[str, Any]] = {}
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        packet = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(packet, dict) and packet.get("candidate_key"):
                        packets[str(packet["candidate_key"])] = packet
        except OSError:
            return {}
        return packets

    def candidate_data_status(self) -> dict[str, Any]:
        current_candidate_signature = file_signature(self.candidate_path)
        current_issue_signature = file_signature(self.issue_path) if self.issue_path else None
        return {
            **self._candidate_reload_status,
            "auto_reload_candidates": self.auto_reload_candidates,
            "candidate_stale": current_candidate_signature != self._candidate_signature,
            "issue_stale": current_issue_signature != self._issue_signature,
            "candidate_signature": self._candidate_signature,
            "current_candidate_signature": current_candidate_signature,
            "issue_signature": self._issue_signature,
            "current_issue_signature": current_issue_signature,
            "candidate_count": len(self.candidates),
            "review_backend": "sql" if self.sql_review_enabled else "jsonl",
            "sql_primary": bool(self.sql_review_enabled),
            "legacy_jsonl_backup": bool(self.legacy_jsonl_backup_enabled),
            "jsonl_status": (
                "legacy_backup"
                if self.sql_review_enabled and self.legacy_jsonl_backup_enabled
                else "disabled"
                if self.sql_review_enabled
                else "primary"
            ),
            "formal_sync": self.formal_sync_status(),
        }

    @staticmethod
    def _short_text(value: Any, limit: int = 1800) -> str:
        text = str(value or "")
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    def workflow_evidence_payload(self, candidate_key: str) -> dict[str, Any] | None:
        case = self.three_source_cases.get(candidate_key)
        packet = self.three_source_packets.get(candidate_key)
        if not case and not packet:
            return None
        packet = packet if isinstance(packet, dict) else {}
        official = packet.get("official_pdf_second_source") if isinstance(packet.get("official_pdf_second_source"), dict) else {}
        visual = packet.get("visual_evidence") if isinstance(packet.get("visual_evidence"), dict) else {}
        question_consensus = official.get("question_consensus") if isinstance(official.get("question_consensus"), dict) else {}
        text_by_engine = question_consensus.get("question_text_by_engine") or official.get("question_text_by_engine") or {}
        engines: dict[str, dict[str, str]] = {}
        for engine, value in text_by_engine.items():
            if isinstance(value, dict):
                engines[str(engine)] = {
                    "source_family": str(value.get("source_family") or engine),
                    "text": self._short_text(value.get("text"), 1600),
                }
            else:
                engines[str(engine)] = {"source_family": str(engine), "text": self._short_text(value, 1600)}
        case_source = case.get("source") if isinstance(case, dict) and isinstance(case.get("source"), dict) else {}
        evidence: dict[str, Any] = {
            "case_id": str(case.get("case_id") or packet.get("case_id") or ""),
            "scope_tags": [str(value) for value in (case.get("scope_tags") or [])] if isinstance(case, dict) else [],
            "model_finding_codes": [str(value) for value in (case.get("model_finding_codes") or [])] if isinstance(case, dict) else [],
            "pdf_flags": [str(value) for value in (case.get("pdf_flags") or official.get("flags") or [])] if isinstance(case, dict) else [str(value) for value in (official.get("flags") or [])],
            "source": {
                "status": str(case_source.get("status") or ""),
                "question_consensus": str(case_source.get("question_consensus") or question_consensus.get("status") or ""),
                "family_count": case_source.get("family_count", question_consensus.get("family_count")),
                "raw_support": case_source.get("raw_support"),
                "nfkc_support": case_source.get("nfkc_support"),
                "auto_rule_candidate": bool(case_source.get("auto_rule_candidate")),
            },
            "pdf": {
                "consensus_status": str(official.get("consensus_status") or ""),
                "families": [str(value) for value in (official.get("families") or [])],
                "page": official.get("page"),
                "pages": official.get("pages") or [],
                "pdf_sha256": str(official.get("pdf_sha256") or ""),
                "text_by_engine": engines,
            },
            "visual": {
                "current_asset_count": visual.get("current_asset_count", case.get("current_asset_count") if isinstance(case, dict) else 0),
                "old_mineru_asset_count": visual.get("old_mineru_asset_count"),
                "official_question_crop_url": f"/evidence-file?key={urllib.parse.quote(candidate_key, safe='')}&asset=official_question_crop" if visual.get("official_question_crop") else "",
                "contact_sheet_url": f"/evidence-file?key={urllib.parse.quote(candidate_key, safe='')}&asset=contact_sheet" if visual.get("contact_sheet") else "",
            },
        }
        return evidence

    def evidence_file_path(self, candidate_key: str, asset_name: str) -> Path | None:
        if self.evidence_root is None:
            return None
        packet = self.three_source_packets.get(candidate_key)
        if not isinstance(packet, dict):
            return None
        visual = packet.get("visual_evidence") if isinstance(packet.get("visual_evidence"), dict) else {}
        allowed = {
            "official_question_crop": visual.get("official_question_crop"),
            "contact_sheet": visual.get("contact_sheet"),
        }
        raw_path = allowed.get(asset_name)
        if not raw_path:
            return None
        try:
            path = Path(str(raw_path)).resolve()
            path.relative_to(self.evidence_root)
        except (OSError, ValueError):
            return None
        return path if path.is_file() else None

    def workflow_payload(self, params: dict[str, str] | None = None) -> dict[str, Any]:
        params = params or {}
        self.refresh_event_logs()
        if self.sql_review_enabled:
            source_payload = self.filtered_candidate_payloads({**params, "reviewStatus": "", "limit": "1000"})
            payloads = list(source_payload.get("candidates") or [])
        else:
            payloads = [self.candidate_payload(item) for item in self.candidates]
        selected_key = str(params.get("candidate_key") or params.get("candidateKey") or "")
        if selected_key and all(str(item.get("candidate_key") or "") != selected_key for item in payloads):
            item = self.candidate_by_key.get(selected_key)
            if item is not None:
                payloads.append(self.candidate_payload(item))

        queue_counts = {key: 0 for key, _label, _description in WORKFLOW_QUEUE_DEFINITIONS}
        queue_items: list[dict[str, Any]] = []
        enriched_by_key: dict[str, dict[str, Any]] = {}
        severity_rank = {"error": 0, "warning": 1, "info": 2}
        for item in payloads:
            key = str(item.get("candidate_key") or "")
            if not key:
                continue
            evidence = self.workflow_evidence_payload(key)
            queue = workflow_primary_queue(item, evidence)
            queue_counts[queue["id"]] += 1
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            review = item.get("review") if isinstance(item.get("review"), dict) else {}
            ai_review = item.get("ai_review") if isinstance(item.get("ai_review"), dict) else {}
            summary = {
                "candidate_key": key,
                "question_number": str(item.get("question_number") or ""),
                "stem_preview": self._short_text(item.get("stem"), 220),
                "category": str(metadata.get("normalized_category_name") or metadata.get("group_name") or ""),
                "subject": str(metadata.get("normalized_subject_name") or ""),
                "year": str(metadata.get("year") or ""),
                "ordinal": str(metadata.get("exam_ordinal") or ""),
                "primary_queue": queue["id"],
                "queue_label": queue["label"],
                "severity": queue["severity"],
                "reason_codes": queue["reason_codes"],
                "lane_statuses": queue["lane_statuses"],
                "lane_findings": queue["lane_findings"],
                "ai_status": str(ai_review.get("audit_status") or "unreviewed"),
                "human_action": str(review.get("action") or "unreviewed"),
                "human_queue": str(review.get("queue_label") or "未看過"),
                "revision_id": str(metadata.get("review_revision_id") or ""),
                "has_visual_asset": bool((item.get("visual_profile") or {}).get("has_visual_asset")),
                "evidence_status": str((evidence or {}).get("source", {}).get("status") or ""),
            }
            queue_items.append(summary)
            enriched_by_key[key] = {"candidate": item, "queue": queue, "evidence": evidence}

        queue_items.sort(
            key=lambda row: (
                severity_rank.get(str(row.get("severity") or "info"), 9),
                0 if str(row.get("human_action") or "") in {"unreviewed", "reset_review", "needs_review"} else 1,
                str(row.get("primary_queue") or ""),
                str(row.get("category") or ""),
                int_or_zero(row.get("year")),
                int_or_zero(row.get("question_number")),
                str(row.get("candidate_key") or ""),
            )
        )
        summary = self.workflow_summary
        analysis = self.three_source_analysis
        analysis_cases = analysis.get("cases") if isinstance(analysis.get("cases"), list) else []
        source_status_counts: dict[str, int] = {}
        consensus_counts: dict[str, int] = {}
        pdf_flag_counts: dict[str, int] = {}
        for case in analysis_cases:
            source = case.get("source") if isinstance(case, dict) and isinstance(case.get("source"), dict) else {}
            status = str(source.get("status") or "unknown")
            source_status_counts[status] = source_status_counts.get(status, 0) + 1
            consensus = str(source.get("question_consensus") or "unknown")
            consensus_counts[consensus] = consensus_counts.get(consensus, 0) + 1
            for flag in case.get("pdf_flags") or []:
                key = str(flag)
                pdf_flag_counts[key] = pdf_flag_counts.get(key, 0) + 1
        lane_status_counts = summary.get("lane_status_counts") if isinstance(summary.get("lane_status_counts"), dict) else {}
        if not lane_status_counts:
            lane_status_counts = {}
            for item in payloads:
                checks = (item.get("ai_review") or {}).get("checks") or {}
                for lane, status in checks.items():
                    lane_status_counts.setdefault(str(lane), {})[str(status)] = lane_status_counts.setdefault(str(lane), {}).get(str(status), 0) + 1
        queue_defs = [
            {"id": key, "label": label, "description": description, "count": queue_counts.get(key, 0)}
            for key, label, description in WORKFLOW_QUEUE_DEFINITIONS
        ]
        selected = enriched_by_key.get(selected_key) if selected_key else None
        return {
            "ok": True,
            "experience": "workflow_console_v1",
            "read_only": os.environ.get("REVIEW_UI_READ_ONLY", "0").lower() not in {"0", "false", "no"},
            "advisory_only": bool(summary.get("production_write_count", 0) == 0) if summary else True,
            "run": {
                key: summary.get(key)
                for key in (
                    "run_id", "model_mode", "model_name", "model_provider", "model_transport",
                    "model_profile_id", "model_prompt_version", "model_endpoint_class", "source_mode",
                    "mineru_mode", "scope_count", "llm_calls", "llm_attempts", "llm_errors",
                    "llm_context_rejections", "llm_context_extensions", "llm_slow_calls", "llm_tool_turns", "revisions_created",
                    "feedback_events", "feedback_skipped", "feedback_agent_attempts", "feedback_agent_calls",
                    "feedback_agent_errors", "guardrail_candidates", "guardrail_active", "guardrail_owner_approval_required",
                    "production_write_count", "human_review_events_written", "config_sha256",
                )
                if key in summary
            },
            "context_policy": summary.get("model_context_policy") or {},
            "queues": queue_defs,
            "queue_total": len(queue_items),
            "queue_counts": queue_counts,
            "queue_items": queue_items[:2000],
            "selected": selected,
            "facets": self.facets(params),
            "three_source": {
                "case_count": len(analysis_cases),
                "automation": analysis.get("automation_assessment") or {},
                "status_counts": source_status_counts,
                "consensus_counts": consensus_counts,
                "pdf_flag_counts": pdf_flag_counts,
                "analysis_path": str(self.three_source_analysis_path) if self.three_source_analysis_path else None,
            },
            "repair_log": str(self.repair_log_path) if self.repair_log_path else None,
            "candidate_data": self.candidate_data_status(),
        }

    def reload_candidate_data(self, force: bool = False, block: bool = True) -> dict[str, Any]:
        if self.sql_review_enabled:
            self._candidate_reload_status = {
                "ok": True,
                "auto_reload_candidates": self.auto_reload_candidates,
                "busy": False,
                "reloaded": False,
                "message": "SQL backend active; JSONL candidate reload skipped",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            return self.candidate_data_status()
        acquired = self._candidate_reload_lock.acquire(blocking=block)
        if not acquired:
            self._candidate_reload_status = {
                **self._candidate_reload_status,
                "ok": False,
                "busy": True,
                "reloaded": False,
                "message": "candidate reload already running",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            return self.candidate_data_status()
        try:
            candidate_signature = file_signature(self.candidate_path)
            issue_signature = file_signature(self.issue_path) if self.issue_path else None
            candidate_stale = candidate_signature != self._candidate_signature
            issue_stale = issue_signature != self._issue_signature
            if not force and not candidate_stale and not issue_stale:
                self._candidate_reload_status = {
                    "ok": True,
                    "auto_reload_candidates": self.auto_reload_candidates,
                    "busy": False,
                    "reloaded": False,
                    "message": "candidate data already current",
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                }
                return self.candidate_data_status()
            if candidate_stale or force:
                new_candidates = load_jsonl(self.candidate_path)
                new_candidate_by_key = {
                    str(item.get("candidate_key")): item
                    for item in new_candidates
                    if item.get("candidate_key")
                }
                self.candidates = new_candidates
                self.candidate_by_key = new_candidate_by_key
                self._candidate_signature = candidate_signature
                self._build_candidate_index()
            if issue_stale or force:
                self.issues = load_issues(self.issue_path)
                self._issue_signature = issue_signature
            gc.collect()
            self._candidate_reload_status = {
                "ok": True,
                "auto_reload_candidates": self.auto_reload_candidates,
                "busy": False,
                "reloaded": bool(candidate_stale or issue_stale or force),
                "message": "candidate data reloaded",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            return self.candidate_data_status()
        except Exception as exc:
            self._candidate_reload_status = {
                "ok": False,
                "auto_reload_candidates": self.auto_reload_candidates,
                "busy": False,
                "reloaded": False,
                "message": f"candidate reload failed: {exc}",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            return self.candidate_data_status()
        finally:
            self._candidate_reload_lock.release()

    def refresh_event_logs(self) -> None:
        if self.sql_review_enabled:
            return
        if self.auto_reload_candidates:
            self.reload_candidate_data(force=False, block=False)

        review_signature = file_signature(self.review_log)
        if review_signature != self._review_log_signature:
            self.latest_reviews, self.review_counts, self.latest_reset_reviews = load_review_events(self.review_log)
            self.latest_group_reviews = load_group_review_events(self.review_log)
            self._review_log_signature = review_signature

        answer_signature = file_signature(self.answer_review_log)
        if answer_signature != self._answer_review_log_signature:
            self.latest_answer_reviews, self.answer_review_counts, self.latest_answer_reset_reviews = load_latest_events(self.answer_review_log)
            self._answer_review_log_signature = answer_signature

        ai_signature = file_signature(self.ai_review_log)
        if ai_signature != self._ai_review_log_signature:
            self.latest_ai_reviews, self.ai_review_counts = load_keyed_events(
                self.ai_review_log,
                reset_actions=AI_RESET_REVIEW_ACTIONS,
            )
            self._ai_review_log_signature = ai_signature

        ai_feedback_signature = file_signature(self.ai_feedback_log)
        if ai_feedback_signature != self._ai_feedback_log_signature:
            self.latest_ai_feedbacks = load_ai_feedback_events(self.ai_feedback_log)
            self._ai_feedback_log_signature = ai_feedback_signature

        ai_learning_signature = file_signature(self.ai_learning_log)
        if ai_learning_signature != self._ai_learning_log_signature:
            self.latest_ai_learnings = load_ai_learning_events(self.ai_learning_log)
            self._ai_learning_log_signature = ai_learning_signature

        correction_feedback_signature = file_signature(self.correction_feedback_log)
        if correction_feedback_signature != self._correction_feedback_log_signature:
            self.latest_correction_feedbacks = load_correction_feedback_events(self.correction_feedback_log)
            self._correction_feedback_log_signature = correction_feedback_signature

        if self.qbr_ai_findings_store.refresh():
            self.latest_qbr_ai_findings = self.qbr_ai_findings_store.latest
        self._qbr_ai_findings_signature = file_signature(self.qbr_ai_findings_log)

        principles_signature = file_signature(self.principles_log)
        if principles_signature != self._principles_log_signature:
            self.principles_events = load_append_only_events(self.principles_log)
            self._principles_log_signature = principles_signature

        repair_questions_signature = file_signature(self.repair_questions_log)
        if repair_questions_signature != self._repair_questions_log_signature:
            self.repair_questions_events = load_append_only_events(self.repair_questions_log)
            self._repair_questions_log_signature = repair_questions_signature

    def _sql_connection(self):
        if not self.sql_review_enabled or psycopg is None or not self.database_url:
            raise RuntimeError("SQL review backend is not available.")
        conn = getattr(self._sql_local, "conn", None)
        if conn is None or getattr(conn, "closed", True):
            conn = psycopg.connect(self.database_url, connect_timeout=5)
            self._sql_local.conn = conn
        return conn

    def _discard_sql_connection(self) -> None:
        conn = getattr(self._sql_local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._sql_local.conn = None

    @contextmanager
    def _sql_connect(self):
        conn = self._sql_connection()
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                self._discard_sql_connection()
            raise
        finally:
            if not getattr(conn, "closed", True):
                try:
                    conn.rollback()
                except Exception:
                    self._discard_sql_connection()

    def _ensure_ai_feedback_schema(self) -> None:
        """Create the feedback stream on an existing review database."""
        if not self.sql_review_enabled:
            return
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS exam.question_ai_feedback_events (
                        id BIGSERIAL PRIMARY KEY,
                        candidate_id BIGINT REFERENCES exam.question_candidates(id) ON DELETE SET NULL,
                        candidate_key TEXT NOT NULL,
                        ai_review_event_id BIGINT REFERENCES exam.question_ai_review_events(id) ON DELETE SET NULL,
                        ai_review_ref TEXT NOT NULL,
                        audit_scope TEXT NOT NULL CHECK (audit_scope IN ('question', 'group', 'visual', 'answer')),
                        rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
                        reviewer TEXT,
                        reason TEXT,
                        feedback_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE INDEX IF NOT EXISTS idx_question_ai_feedback_candidate
                        ON exam.question_ai_feedback_events (candidate_key, audit_scope, reviewer, created_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_question_ai_feedback_rating
                        ON exam.question_ai_feedback_events (rating, created_at DESC);

                    CREATE TABLE IF NOT EXISTS exam.question_ai_learning_events (
                        id BIGSERIAL PRIMARY KEY,
                        candidate_id BIGINT REFERENCES exam.question_candidates(id) ON DELETE SET NULL,
                        candidate_key TEXT NOT NULL,
                        ai_review_event_id BIGINT REFERENCES exam.question_ai_review_events(id) ON DELETE SET NULL,
                        ai_review_ref TEXT NOT NULL,
                        audit_scope TEXT NOT NULL CHECK (audit_scope IN ('question', 'group', 'visual', 'answer')),
                        reviewer TEXT,
                        reason TEXT,
                        learning_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE INDEX IF NOT EXISTS idx_question_ai_learning_candidate
                        ON exam.question_ai_learning_events (candidate_key, audit_scope, reviewer, created_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_question_ai_learning_created
                        ON exam.question_ai_learning_events (created_at DESC);

                    CREATE TABLE IF NOT EXISTS exam.question_correction_feedback_events (
                        id BIGSERIAL PRIMARY KEY,
                        candidate_id BIGINT REFERENCES exam.question_candidates(id) ON DELETE SET NULL,
                        candidate_key TEXT NOT NULL,
                        human_review_event_id BIGINT REFERENCES exam.question_review_events(id) ON DELETE SET NULL,
                        human_answer_review_event_id BIGINT REFERENCES exam.answer_review_events(id) ON DELETE SET NULL,
                        feedback_id TEXT NOT NULL UNIQUE,
                        source_kind TEXT NOT NULL CHECK (source_kind IN ('human_correction', 'three_evidence_correction')),
                        audit_scope TEXT NOT NULL CHECK (audit_scope IN ('question', 'group', 'visual', 'answer')),
                        lane_key TEXT,
                        actor_kind TEXT NOT NULL CHECK (actor_kind IN ('human', 'deterministic_system')),
                        reviewer TEXT,
                        event_ref TEXT,
                        changed_fields JSONB NOT NULL,
                        before_json JSONB NOT NULL,
                        after_json JSONB NOT NULL,
                        diff_json JSONB NOT NULL,
                        evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        ai_task_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        event_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    ALTER TABLE exam.question_correction_feedback_events
                        ADD COLUMN IF NOT EXISTS human_answer_review_event_id BIGINT
                        REFERENCES exam.answer_review_events(id) ON DELETE SET NULL;
                    CREATE INDEX IF NOT EXISTS idx_question_correction_feedback_candidate
                        ON exam.question_correction_feedback_events (candidate_key, created_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_question_correction_feedback_source
                        ON exam.question_correction_feedback_events (source_kind, created_at DESC);

                    CREATE TABLE IF NOT EXISTS exam.question_guardrail_candidates (
                        id BIGSERIAL PRIMARY KEY,
                        feedback_event_id BIGINT NOT NULL REFERENCES exam.question_correction_feedback_events(id) ON DELETE RESTRICT,
                        feedback_id TEXT NOT NULL,
                        candidate_key TEXT NOT NULL,
                        audit_scope TEXT NOT NULL CHECK (audit_scope IN ('question', 'group', 'visual', 'answer')),
                        lane_key TEXT,
                        change_class TEXT NOT NULL,
                        guardrail_type TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('observed', 'proposed', 'no_generalization', 'ai_failed', 'rejected', 'needs_owner_approval', 'approved', 'active')),
                        candidate_json JSONB NOT NULL,
                        validation_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE INDEX IF NOT EXISTS idx_question_guardrail_candidates_status
                        ON exam.question_guardrail_candidates (status, created_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_question_guardrail_candidates_feedback
                        ON exam.question_guardrail_candidates (feedback_id, created_at DESC, id DESC);

                    """
                )
            conn.commit()

    def _ensure_formal_sync_queue_schema(self) -> None:
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS exam.formal_sync_queue (
                        candidate_key TEXT PRIMARY KEY REFERENCES exam.question_candidates(candidate_key) ON DELETE CASCADE,
                        requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        last_attempt_at TIMESTAMPTZ,
                        last_error TEXT,
                        processed_at TIMESTAMPTZ
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_formal_sync_queue_pending
                    ON exam.formal_sync_queue (requested_at, candidate_key)
                    WHERE processed_at IS NULL
                    """
                )
            conn.commit()

    def _enqueue_formal_sync(self, cur: Any, candidate_key: str) -> None:
        if not self.defer_formal_sync or not candidate_key:
            return
        cur.execute(
            """
            INSERT INTO exam.formal_sync_queue (candidate_key, requested_at, processed_at, last_error)
            VALUES (%s, now(), NULL, NULL)
            ON CONFLICT (candidate_key) DO UPDATE
            SET requested_at = now(),
                processed_at = NULL,
                last_error = NULL
            """,
            (candidate_key,),
        )

    def formal_sync_status(self) -> dict[str, Any]:
        with self._formal_sync_status_lock:
            return dict(self._formal_sync_status)

    def _set_formal_sync_status(self, **updates: Any) -> None:
        with self._formal_sync_status_lock:
            self._formal_sync_status.update(updates)

    def _wake_formal_sync_worker(self) -> None:
        if self.defer_formal_sync:
            self._formal_sync_wake.set()

    def _formal_sync_worker_loop(self) -> None:
        while True:
            self._formal_sync_wake.wait()
            self._formal_sync_wake.clear()
            self._set_formal_sync_status(running=True, last_error=None)
            while True:
                try:
                    with self._sql_connect() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT candidate_key
                                FROM exam.formal_sync_queue
                                WHERE processed_at IS NULL
                                ORDER BY requested_at, candidate_key
                                LIMIT 250
                                """
                            )
                            keys = [str(row[0]) for row in cur.fetchall()]
                    if not keys:
                        self._set_formal_sync_status(running=False, queued=0)
                        break
                    self._set_formal_sync_status(queued=len(keys))
                    result = self.sync_formal_candidates(keys)
                    errors = [str(error) for error in (result.get("errors") or []) if str(error)]
                    with self._sql_connect() as conn:
                        with conn.cursor() as cur:
                            if errors:
                                cur.execute(
                                    """
                                    UPDATE exam.formal_sync_queue
                                    SET attempt_count = attempt_count + 1,
                                        last_attempt_at = now(),
                                        last_error = %s
                                    WHERE candidate_key = ANY(%s)
                                    """,
                                    ("; ".join(errors)[:4000], keys),
                                )
                            else:
                                cur.execute(
                                    """
                                    UPDATE exam.formal_sync_queue
                                    SET attempt_count = attempt_count + 1,
                                        last_attempt_at = now(),
                                        last_error = NULL,
                                        processed_at = now()
                                    WHERE candidate_key = ANY(%s)
                                    """,
                                    (keys,),
                                )
                        conn.commit()
                    if errors:
                        self._set_formal_sync_status(running=False, queued=len(keys), last_error="; ".join(errors))
                        break
                    self._set_formal_sync_status(
                        queued=0,
                        last_completed_at=datetime.now().isoformat(timespec="seconds"),
                    )
                except Exception as exc:
                    self._set_formal_sync_status(running=False, last_error=str(exc))
                    break

    def _candidate_by_key_sql(self, candidate_key: str) -> dict[str, Any] | None:
        if not candidate_key:
            return None
        return self._candidates_by_key_sql([candidate_key]).get(candidate_key)

    def _candidates_by_key_sql(self, candidate_keys: list[str]) -> dict[str, dict[str, Any]]:
        keys = [str(key or "") for key in candidate_keys if str(key or "")]
        if not keys:
            return {}
        results: dict[str, dict[str, Any]] = {}
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT candidate_key, raw_candidate_json
                    FROM exam.question_candidates
                    WHERE candidate_key = ANY(%s)
                    """,
                    (keys,),
                )
                for key, raw_candidate in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        results[str(key)] = raw_candidate
                    elif isinstance(raw_candidate, str):
                        results[str(key)] = json.loads(raw_candidate)
        return results

    def _sql_candidate_where(self, params: dict[str, str], ignore: str = "") -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        review_status = params.get("reviewStatus") or ""
        if review_status == "exclude":
            clauses.append("COALESCE(review_status, '') = 'excluded'")
        else:
            clauses.append("COALESCE(review_status, '') <> 'excluded'")
        filters = {
            "category": (SQL_CANDIDATE_CATEGORY_EXPR, params.get("category") or ""),
            "subject": ("raw_candidate_json->'metadata'->>'normalized_subject_name'", params.get("subject") or ""),
            "year": ("raw_candidate_json->'metadata'->>'year'", params.get("year") or ""),
            "ordinal": ("raw_candidate_json->'metadata'->>'exam_ordinal'", params.get("ordinal") or ""),
        }
        for key, (expr, value) in filters.items():
            if key == ignore or not value:
                continue
            if key == "category":
                clauses.append(f"{expr} = ANY(%s)")
                values.append(list(category_filter_values(value)))
                continue
            clauses.append(f"COALESCE({expr}, '') = %s")
            values.append(value)
        return clauses, values

    def sql_facets(self, params: dict[str, str] | None = None) -> dict[str, list[str]]:
        params = params or {}
        cache_key = json.dumps(
            {
                key: params.get(key) or ""
                for key in ("category", "subject", "year", "ordinal", "reviewStatus")
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if cache_key in self._sql_facets_cache:
            return self._sql_facets_cache[cache_key]
        facet_defs = {
            "categories": ("category", SQL_CANDIDATE_CATEGORY_EXPR),
            "subjects": ("subject", "raw_candidate_json->'metadata'->>'normalized_subject_name'"),
            "years": ("year", "raw_candidate_json->'metadata'->>'year'"),
            "ordinals": ("ordinal", "raw_candidate_json->'metadata'->>'exam_ordinal'"),
        }
        result: dict[str, list[str]] = {}
        try:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    for output_key, (filter_key, expr) in facet_defs.items():
                        clauses, values = self._sql_candidate_where(params, ignore=filter_key)
                        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                        cur.execute(
                            f"""
                            SELECT DISTINCT COALESCE({expr}, '')
                            FROM exam.question_candidates
                            {where}
                            """,
                            values,
                        )
                        rows = [str(row[0] or "") for row in cur.fetchall() if str(row[0] or "")]
                        if output_key in {"years", "ordinals"}:
                            rows.sort(key=lambda item: (int(item) if item.isdigit() else 9999, item))
                        else:
                            rows.sort()
                        result[output_key] = rows
        except Exception:
            return self.facets(params)
        if len(self._sql_facets_cache) >= 64:
            self._sql_facets_cache.pop(next(iter(self._sql_facets_cache)))
        self._sql_facets_cache[cache_key] = result
        return result

    def _sql_candidate_rows(self, params: dict[str, str], *, limit: int | None = None) -> list[dict[str, Any]]:
        clauses, values = self._sql_candidate_where(params)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = ""
        query_values = list(values)
        if limit is not None:
            limit_sql = "LIMIT %s"
            query_values.append(max(1, int(limit)))
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT raw_candidate_json
                    FROM exam.question_candidates
                    {where}
                    ORDER BY
                        COALESCE(raw_candidate_json->'metadata'->>'normalized_category_name', ''),
                        COALESCE(raw_candidate_json->'metadata'->>'normalized_subject_name', ''),
                        CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
                            THEN (raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END DESC,
                        CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
                            THEN (raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END DESC,
                        CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END,
                        candidate_key
                    {limit_sql}
                    """,
                    query_values,
                )
                rows = []
                for (raw_candidate,) in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                return rows

    def _sql_group_candidate_rows(self, params: dict[str, str]) -> list[dict[str, Any]]:
        clauses, values = self._sql_candidate_where(params)
        group_review_status = params.get("groupReviewStatus") or ""
        try:
            requested_limit = max(1, min(int(params.get("limit") or "200"), 500))
        except ValueError:
            requested_limit = 200
        row_limit = requested_limit * 20
        group_status_clause = ""
        if group_review_status == "unreviewed":
            group_status_clause = "WHERE (lg.action IS NULL OR lg.action = 'reset_group_review')"
        elif group_review_status == "reviewed":
            group_status_clause = "WHERE lg.action IN ('confirm_group', 'confirm_not_group')"
        elif group_review_status == "confirmed_group":
            group_status_clause = "WHERE lg.action = 'confirm_group'"
        elif group_review_status == "confirmed_not_group":
            group_status_clause = "WHERE lg.action = 'confirm_not_group'"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH scoped_candidates AS MATERIALIZED (
                        SELECT *
                        FROM exam.question_candidates
                        {where}
                    ),
                    latest_group AS (
                        SELECT DISTINCT ON (e.candidate_key)
                            e.candidate_key,
                            e.action
                        FROM exam.question_review_events e
                        JOIN scoped_candidates USING (candidate_key)
                        WHERE e.action IN ('confirm_not_group', 'confirm_group', 'reset_group_review')
                        ORDER BY e.candidate_key, e.id DESC
                    ),
                    filtered AS (
                        SELECT
                            candidate_key,
                            source_registry_key,
                            question_number,
                            raw_candidate_json,
                            COALESCE(raw_candidate_json->'metadata'->>'normalized_category_name', raw_candidate_json->'metadata'->>'group_name', '') AS category,
                            COALESCE(raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
                            COALESCE(raw_candidate_json->'metadata'->>'year', '') AS year,
                            COALESCE(raw_candidate_json->'metadata'->>'exam_ordinal', '') AS ordinal,
                            COALESCE(raw_candidate_json->>'stem', '') AS stem,
                            COALESCE(raw_candidate_json->>'group_ref', '') AS group_ref,
                            COALESCE(lg.action, '') AS group_action,
                            CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END AS qn
                        FROM scoped_candidates
                        LEFT JOIN latest_group lg USING (candidate_key)
                        {group_status_clause}
                    ),
                    keyed AS (
                        SELECT
                            *,
                            concat_ws('|', category, subject, year, ordinal, source_registry_key) AS session_key
                        FROM filtered
                    ),
                    suspects AS MATERIALIZED (
                        SELECT *
                        FROM keyed
                        WHERE group_ref <> ''
                           OR group_action IN ('confirm_group', 'confirm_not_group', 'reset_group_review')
                           OR stem ~ '{GROUP_COUNT_SQL_RE}'
                           OR stem ~ '^[[:space:]]*[（(]?[[:space:]]*(承上題|呈上題|上題|前述)'
                           OR stem ~ '^[[:space:]]*[0-9]{{1,3}}[[:space:]]*(-|－|~|～|至|到)[[:space:]]*[0-9]{{1,3}}'
                    ),
                    wanted_keys AS (
                        SELECT DISTINCT f.candidate_key
                        FROM keyed f
                        JOIN suspects s ON s.session_key = f.session_key
                        WHERE
                            (s.group_action IN ('confirm_group', 'confirm_not_group', 'reset_group_review') AND f.candidate_key = s.candidate_key)
                            OR (s.group_ref <> '' AND f.group_ref = s.group_ref)
                            OR (
                                s.stem ~ '{GROUP_COUNT_SQL_RE}'
                                AND f.qn BETWEEN s.qn AND s.qn + 20
                            )
                            OR (
                                s.stem ~ '^[[:space:]]*[（(]?[[:space:]]*(承上題|呈上題|上題|前述)'
                                AND f.qn BETWEEN s.qn - 1 AND s.qn + 5
                            )
                            OR (
                                s.stem ~ '^[[:space:]]*[0-9]{{1,3}}[[:space:]]*(-|－|~|～|至|到)[[:space:]]*[0-9]{{1,3}}'
                                AND f.qn BETWEEN s.qn AND s.qn + 10
                            )
                    )
                    SELECT f.raw_candidate_json
                    FROM keyed f
                    JOIN wanted_keys w USING (candidate_key)
                    ORDER BY
                        CASE WHEN f.group_action IN ('', 'reset_group_review') THEN 0 ELSE 1 END,
                        f.category,
                        f.subject,
                        CASE WHEN f.year ~ '^[0-9]+$' THEN f.year::integer ELSE 0 END DESC,
                        CASE WHEN f.ordinal ~ '^[0-9]+$' THEN f.ordinal::integer ELSE 0 END DESC,
                        f.qn,
                        f.candidate_key
                    LIMIT %s
                    """,
                    [*values, row_limit],
                )
                rows = []
                for (raw_candidate,) in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                return rows

    def _sql_candidate_filter_parts(self, params: dict[str, str]) -> tuple[str, list[Any]]:
        scope_clauses, scope_values = self._sql_candidate_where(params)
        scope_where = f"WHERE {' AND '.join(scope_clauses)}" if scope_clauses else ""
        clauses: list[str] = []
        values: list[Any] = []
        q = (params.get("q") or "").strip().lower()
        status = params.get("status") or ""
        review_status = params.get("reviewStatus") or ""
        ai_review_status = params.get("aiReviewStatus") or ""
        visual_status = params.get("visualStatus") or ""
        if review_status != "exclude":
            clauses.append("COALESCE(review_action, '') <> 'exclude'")
        if status:
            clauses.append("question_quality_status = %s")
            values.append(status)
        if review_status == "not_accept":
            clauses.append(
                """
                (
                    (is_reviewed AND review_action NOT IN ('accept', 'correct', 'unblock', 'exclude'))
                    OR is_never_reviewed
                    OR is_reset_unreviewed
                )
                """
            )
        elif review_status == "formal_drift":
            clauses.append(
                """
                formal_usable <> ready_for_formal
                """
            )
        elif review_status == "formal":
            clauses.append("formal_usable")
        elif review_status == "answer_stage":
            clauses.append("review_action IN ('accept', 'unblock')")
        elif review_status == "repair_pending":
            clauses.append("is_repair_pending")
        elif review_status == "discuss":
            # The 錯題討論區's own filter: a question a person rejected, or one the pipeline
            # returned for a fresh look.
            #
            # This branch was **missing** here and present only in the light CTE, which is the one
            # discuss never uses: `_sql_can_use_light_candidate_query` returns False for `discuss`
            # (the light query cannot see `repair_kind`), so the request fell through to
            # `review_action = %s` with the literal `'discuss'` and matched **nothing**. Measured on
            # the truth table: the branch existed, was tested, and was unreachable. `discuss` is
            # reachable on the SQL backend (`sql_primary`), where the whole area would have been
            # empty - a filter that returns no rows reads as "nothing is stuck", not as a bug.
            # The shared `SQL_DISCUSS_PREDICATE` is used by both CTEs so the two cannot drift again.
            clauses.append(SQL_DISCUSS_PREDICATE)
        elif review_status == "accepted_reaudit":
            clauses.append("is_accepted_reaudit_pending")
        elif review_status == "correct":
            clauses.append(
                """
                is_reviewed
                AND (
                    corrected_candidate_json IS NOT NULL
                    OR COALESCE(review_event_json, '{}'::jsonb) ? 'correction'
                )
                """
            )
        elif review_status == "reset_review":
            clauses.append(
                "is_reset_unreviewed AND NOT is_repair_pending AND NOT is_accepted_reaudit_pending"
            )
        elif review_status == "unreviewed":
            clauses.append("is_never_reviewed")
        elif review_status == "reviewed":
            clauses.append("is_reviewed")
        elif review_status:
            clauses.append("review_action = %s")
            values.append(review_status)
        if ai_review_status == "unreviewed":
            clauses.append("NOT ai_reviewed")
        elif ai_review_status == "reviewed":
            clauses.append("ai_reviewed")
        elif ai_review_status == "suggested_correction":
            clauses.append("ai_has_suggestion")
        elif ai_review_status == "needs_review":
            clauses.append("ai_effective_status IN ('needs_review', 'block', 'blocked')")
        elif ai_review_status:
            clauses.append("ai_effective_status = %s")
            values.append(ai_review_status)
        if visual_status not in {"", "visual", "visual_all", "visual_asset_pending", "visual_suspect", "visual_ok", "no_visual", "visual_problem"}:
            visual_status = "visual_asset_pending"
        if visual_status == "visual_all":
            clauses.append(
                """
                COALESCE(review_action, '') <> 'exclude'
                AND (
                    has_visual_asset
                    OR has_structured_table
                    OR has_manual_asset
                    OR has_visual_dependency
                    OR visual_review_status IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem')
                    OR visual_ai_status IN ('visual_required_likely', 'visual_uncertain')
                )
                """
            )
        elif visual_status == "visual":
            clauses.append(
                """
                COALESCE(review_action, '') <> 'exclude'
                AND
                visual_review_status NOT IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem')
                AND NOT has_manual_asset
                AND (
                    has_visual_asset
                    OR has_structured_table
                    OR visual_ai_status IN ('visual_required_likely', 'visual_uncertain')
                    OR (has_visual_dependency AND visual_ai_status = '')
                )
                """
            )
        elif visual_status == "visual_asset_pending":
            clauses.append(
                """
                COALESCE(review_action, '') <> 'exclude'
                AND
                visual_review_status NOT IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem')
                AND NOT has_manual_asset
                AND (
                    has_visual_asset
                    OR has_structured_table
                )
                """
            )
        elif visual_status == "visual_suspect":
            clauses.append(
                """
                COALESCE(review_action, '') <> 'exclude'
                AND
                visual_review_status NOT IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem')
                AND NOT has_manual_asset
                AND NOT has_visual_asset
                AND NOT has_structured_table
                AND (
                    visual_ai_status IN ('visual_required_likely', 'visual_uncertain')
                    OR (has_visual_dependency AND visual_ai_status = '')
                )
                """
            )
        elif visual_status == "visual_ok":
            clauses.append("(visual_review_status = 'visual_asset_ok' OR has_manual_asset)")
        elif visual_status == "no_visual":
            clauses.append("visual_review_status = 'no_visual_required'")
        elif visual_status == "visual_problem":
            clauses.append("visual_review_status = 'visual_asset_problem'")
        if q:
            clauses.append(
                """
                lower(concat_ws(
                    ' ',
                    candidate_key,
                    question_number,
                    stem_text,
                    raw_candidate_json::text,
                    category,
                    subject,
                    review_action,
                    review_notes,
                    review_event_json::text,
                    ai_provider,
                    ai_model_name,
                    ai_effective_status,
                    ai_audit_json::text
                )) LIKE %s
                """
            )
            values.append(f"%{q}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cte = f"""
WITH scoped_candidates AS MATERIALIZED (
    SELECT *
    FROM exam.question_candidates
    {scope_where}
),
latest_question AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.action,
        e.corrected_candidate_json,
        e.event_json,
        e.notes,
        e.reviewer,
        e.created_at,
        e.id
    FROM exam.question_review_events e
    JOIN scoped_candidates USING (candidate_key)
    WHERE e.action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual', 'mobile_defer', 'mobile_resume')
    ORDER BY e.candidate_key, e.id DESC
),
latest_question_correction AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.corrected_candidate_json,
        e.created_at,
        e.id
    FROM exam.question_review_events e
    JOIN scoped_candidates USING (candidate_key)
    WHERE e.corrected_candidate_json IS NOT NULL
      AND e.action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'mobile_defer', 'mobile_resume')
    ORDER BY e.candidate_key, e.id DESC
),
latest_visual AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.corrected_candidate_json,
        e.event_json,
        e.notes,
        e.created_at,
        e.id
    FROM exam.question_review_events e
    JOIN scoped_candidates USING (candidate_key)
    WHERE e.corrected_candidate_json ? 'visual_review'
    ORDER BY e.candidate_key, e.id DESC
),
latest_answer AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.action,
        e.created_at,
        e.id
    FROM exam.answer_review_events e
    JOIN scoped_candidates USING (candidate_key)
    ORDER BY e.candidate_key, e.id DESC
),
latest_question_ai AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.action,
        e.provider,
        e.model_name,
        e.audit_status,
        e.recommended_action,
        e.audit_json,
        e.event_json,
        e.created_at,
        e.id
    FROM exam.question_ai_review_events e
    JOIN scoped_candidates USING (candidate_key)
    WHERE NOT (
        COALESCE(e.prompt_version, '') LIKE 'visual_%%'
        OR COALESCE(e.model_name, '') LIKE '%%visual%%'
        OR COALESCE(e.audit_json, '{{}}'::jsonb) ? 'visual_status'
        OR COALESCE(e.audit_json->>'stage', '') = 'image'
    )
      AND NOT (
        COALESCE(e.recommended_action, '') = 'review_group'
        AND NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(COALESCE(e.audit_json->'findings', '[]'::jsonb)) = 'array'
                        THEN COALESCE(e.audit_json->'findings', '[]'::jsonb)
                    ELSE '[]'::jsonb
                END
            ) AS group_finding(value)
            WHERE NOT (
                COALESCE(group_finding.value->>'issue_family', group_finding.value->>'code', '') = 'group_dependency'
                OR COALESCE(group_finding.value->>'route', '') = 'group'
            )
        )
      )
    ORDER BY e.candidate_key, e.id DESC
),
latest_visual_ai AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.audit_json,
        e.created_at,
        e.id
    FROM exam.question_ai_review_events e
    JOIN scoped_candidates USING (candidate_key)
    WHERE
        COALESCE(e.prompt_version, '') LIKE 'visual_%%'
        OR COALESCE(e.model_name, '') LIKE '%%visual%%'
        OR COALESCE(e.audit_json, '{{}}'::jsonb) ? 'visual_status'
        OR COALESCE(e.audit_json->>'stage', '') = 'image'
    ORDER BY e.candidate_key, e.id DESC
),
issue_flags AS (
    SELECT
        e.candidate_key,
        bool_or(e.severity IN ('blocked', 'error')) AS has_blocking_issue,
        bool_or(e.severity = 'warning') AS has_warning_issue
    FROM exam.question_parse_issues e
    JOIN scoped_candidates USING (candidate_key)
    WHERE e.resolved_at IS NULL
      AND e.issue_code NOT IN ('missing_answer', 'missing_answer_markdown', 'unexpected_answer_value')
    GROUP BY e.candidate_key
),
base AS (
    SELECT
        c.candidate_key,
        c.question_number,
        c.stem_text,
        c.raw_candidate_json
        || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb)
        || jsonb_strip_nulls(jsonb_build_object(
            'visual_review',
            NULLIF(COALESCE(lv.corrected_candidate_json->>'visual_review', lqc.corrected_candidate_json->>'visual_review', ''), ''),
            'visual_ai_status',
            NULLIF(COALESCE(
                lvai.audit_json->>'visual_status',
                (
                    SELECT label.value
                    FROM jsonb_array_elements_text(
                        CASE
                            WHEN jsonb_typeof(COALESCE(lvai.audit_json->'labels', '[]'::jsonb)) = 'array'
                                THEN COALESCE(lvai.audit_json->'labels', '[]'::jsonb)
                            ELSE '[]'::jsonb
                        END
                    ) AS label(value)
                    WHERE label.value IN ('visual_required_likely', 'visual_not_required_likely', 'visual_uncertain')
                    LIMIT 1
                ),
                ''
            ), '')
        )) AS raw_candidate_json,
        c.review_status,
        (fq.question_key IS NOT NULL) AS physical_in_formal,
        COALESCE(
            lq.action IN ('accept', 'unblock') AND la.action IN ('accept', 'unblock'),
            false
        ) AS ready_for_formal,
        COALESCE((
            fq.review_status = 'accepted'
            AND EXISTS (SELECT 1 FROM exam.answers fa WHERE fa.question_id = fq.id)
        ), false) AS formal_usable,
        COALESCE((
            fq.review_status = 'accepted'
            AND EXISTS (SELECT 1 FROM exam.answers fa WHERE fa.question_id = fq.id)
        ), false) AS in_formal,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') AS category,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
        COALESCE(c.raw_candidate_json->'metadata'->>'year', '') AS year,
        COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') AS ordinal,
        CASE
            WHEN COALESCE(i.has_blocking_issue, false) THEN 'blocked'
            WHEN COALESCE(i.has_warning_issue, false) THEN 'needs_review'
            ELSE 'pass'
        END AS question_quality_status,
        lq.action AS review_action,
        lqc.corrected_candidate_json,
        lq.event_json AS review_event_json,
        lq.notes AS review_notes,
        lq.reviewer AS review_reviewer,
        la.action AS answer_review_action,
        (lq.action IS NOT NULL AND lq.action NOT IN ('unreviewed', 'reset_review')) AS is_reviewed,
        (lq.candidate_key IS NULL) AS is_never_reviewed,
        (lq.action IN ('unreviewed', 'reset_review')) AS is_reset_unreviewed,
        {SQL_REVIEW_ACCEPTED_REAUDIT_EXPR} AS is_accepted_reaudit_pending,
        {SQL_REVIEW_REPAIR_PENDING_EXPR} AS is_repair_pending,
        lai.action AS ai_action,
        lai.provider AS ai_provider,
        lai.model_name AS ai_model_name,
        lai.audit_json AS ai_audit_json,
        (
            COALESCE(lv.corrected_candidate_json->>'visual_review', lqc.corrected_candidate_json->>'visual_review', '') = 'no_visual_required'
        ) AS no_visual_required,
        COALESCE(lv.corrected_candidate_json->>'visual_review', lqc.corrected_candidate_json->>'visual_review', '') AS visual_review_status,
        (
            CASE
                WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'image_refs') = 'array'
                    THEN jsonb_array_length((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'image_refs')
                ELSE 0
            END > 0
            OR CASE
                WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs') = 'array'
                    THEN jsonb_array_length((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs')
                ELSE 0
            END > 0
            OR jsonb_typeof((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'stem_image') = 'object'
            OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'options') = 'array'
                            THEN (c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'options'
                        ELSE '[]'::jsonb
                    END
                ) AS option_row(value)
                WHERE jsonb_typeof(option_row.value->'image') = 'object'
            )
        ) AS has_visual_asset,
        (
            concat_ws(
                ' ',
                c.stem_text,
                c.raw_candidate_json->>'stem',
                c.raw_candidate_json->'metadata'->>'raw_block',
                lqc.corrected_candidate_json->>'stem'
            ) ~* '{VISUAL_DEPENDENCY_SQL_RE}'
        ) AS has_visual_dependency,
        (
            position('<table' in lower(concat_ws(' ', c.raw_candidate_json->>'stem', lqc.corrected_candidate_json->>'stem'))) > 0
            OR concat_ws(
                ' ',
                c.stem_text,
                c.raw_candidate_json->>'stem',
                c.raw_candidate_json->'metadata'->>'raw_block',
                lqc.corrected_candidate_json->>'stem'
            ) ~* '{TABLE_DEPENDENCY_SQL_RE}'
        ) AS has_structured_table,
        EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'image_refs') = 'array'
                        THEN (c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'image_refs'
                    ELSE '[]'::jsonb
                END
            ) AS ref_row(value)
            WHERE ref_row.value->>'manual_asset' IN ('true', '1')
               OR COALESCE(ref_row.value->>'asset_role', ref_row.value->>'role', '') LIKE '%%manual%%'
               OR COALESCE(ref_row.value->>'asset_role', ref_row.value->>'role', '') = 'table_manual_screenshot'
        )
        OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs') = 'array'
                        THEN (c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs'
                    ELSE '[]'::jsonb
                END
            ) AS answer_ref_row(value)
            WHERE answer_ref_row.value->>'manual_asset' IN ('true', '1')
               OR COALESCE(answer_ref_row.value->>'asset_role', answer_ref_row.value->>'role', '') LIKE '%%manual%%'
        )
        OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'options') = 'array'
                        THEN (c.raw_candidate_json || COALESCE(lqc.corrected_candidate_json, '{{}}'::jsonb))->'options'
                    ELSE '[]'::jsonb
                END
            ) AS option_manual_row(value)
            WHERE option_manual_row.value->'image'->>'manual_asset' IN ('true', '1')
        ) AS has_manual_asset,
        (lai.action IS NOT NULL AND lai.action NOT IN ('unreviewed', 'reset_review', 'reset_ai_review')) AS ai_reviewed,
        (
            NOT COALESCE((
                lq.action IN ('accept', 'unblock', 'block', 'needs_review', 'exclude', 'reviewed', 'correct')
                AND lq.created_at >= lai.created_at
            ), false)
            AND (
                COALESCE(lai.audit_json, '{{}}'::jsonb) ? 'suggested_correction'
                OR COALESCE(lai.event_json->'audit', '{{}}'::jsonb) ? 'suggested_correction'
                OR COALESCE(lai.event_json, '{{}}'::jsonb) ? 'suggested_correction'
            )
        ) AS ai_has_suggestion,
        CASE
            WHEN lai.action IN ('unreviewed', 'reset_review', 'reset_ai_review') OR lai.action IS NULL THEN NULL
            WHEN lq.action IN ('accept', 'unblock', 'block', 'needs_review', 'exclude', 'reviewed', 'correct')
                AND lq.created_at >= lai.created_at THEN 'pass'
            WHEN lai.audit_status IN ('block', 'blocked') THEN 'block'
            WHEN lai.audit_status = 'needs_review' THEN 'needs_review'
            WHEN jsonb_typeof(COALESCE(lai.audit_json->'findings', '[]'::jsonb)) = 'array'
                AND jsonb_array_length(COALESCE(lai.audit_json->'findings', '[]'::jsonb)) > 0 THEN 'needs_review'
            WHEN COALESCE(lai.recommended_action, '') NOT IN ('', 'no_action') THEN 'needs_review'
            WHEN COALESCE(lai.audit_json, '{{}}'::jsonb) ? 'suggested_correction' THEN 'needs_review'
            ELSE COALESCE(lai.audit_status, 'pass')
        END AS ai_effective_status,
        CASE
            WHEN lv.created_at >= lvai.created_at THEN ''
            ELSE COALESCE(
                NULLIF(lvai.audit_json->>'visual_status', ''),
                (
                    SELECT label.value
                    FROM jsonb_array_elements_text(
                        CASE
                            WHEN jsonb_typeof(COALESCE(lvai.audit_json->'labels', '[]'::jsonb)) = 'array'
                                THEN COALESCE(lvai.audit_json->'labels', '[]'::jsonb)
                            ELSE '[]'::jsonb
                        END
                    ) AS label(value)
                    WHERE label.value IN ('visual_required_likely', 'visual_not_required_likely', 'visual_uncertain')
                    LIMIT 1
                ),
                ''
            )
        END AS visual_ai_status,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END AS year_sort,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END AS ordinal_sort,
        CASE WHEN c.question_number ~ '^[0-9]+$' THEN c.question_number::integer ELSE 0 END AS question_sort
    FROM scoped_candidates c
    LEFT JOIN exam.questions fq ON fq.question_key = c.candidate_key
    LEFT JOIN issue_flags i ON i.candidate_key = c.candidate_key
    LEFT JOIN latest_question lq ON lq.candidate_key = c.candidate_key
    LEFT JOIN latest_question_correction lqc ON lqc.candidate_key = c.candidate_key
    LEFT JOIN latest_visual lv ON lv.candidate_key = c.candidate_key
    LEFT JOIN latest_answer la ON la.candidate_key = c.candidate_key
    LEFT JOIN latest_question_ai lai ON lai.candidate_key = c.candidate_key
    LEFT JOIN latest_visual_ai lvai ON lvai.candidate_key = c.candidate_key
),
filtered AS (
    SELECT *
    FROM base
    {where}
)
"""
        return cte, [*scope_values, *values]

    def _sql_can_use_light_candidate_query(self, params: dict[str, str]) -> bool:
        return (
            not (params.get("q") or "").strip()
            and not (params.get("status") or "")
            and not (params.get("aiReviewStatus") or "")
            and not (params.get("visualStatus") or "")
            # `repair_pending` depends on repair_kind inside the latest event;
            # the lightweight query intentionally does not load that payload.
            and (params.get("reviewStatus") or "") not in {"repair_pending", "discuss"}
        )

    def _sql_light_candidate_filter_parts(self, params: dict[str, str]) -> tuple[str, list[Any]]:
        scope_clauses, scope_values = self._sql_candidate_where(params)
        scope_where = f"WHERE {' AND '.join(scope_clauses)}" if scope_clauses else ""
        clauses: list[str] = []
        values: list[Any] = []
        review_status = params.get("reviewStatus") or ""
        if review_status != "exclude":
            clauses.append("COALESCE(review_action, '') <> 'exclude'")
        if review_status == "not_accept":
            clauses.append(
                """
                (
                    (is_reviewed AND review_action NOT IN ('accept', 'correct', 'unblock', 'exclude'))
                    OR is_never_reviewed
                    OR is_reset_unreviewed
                )
                """
            )
        elif review_status == "formal_drift":
            clauses.append(
                """
                formal_usable <> ready_for_formal
                """
            )
        elif review_status == "formal":
            clauses.append("formal_usable")
        elif review_status == "answer_stage":
            clauses.append("review_action IN ('accept', 'unblock')")
        elif review_status == "repair_pending":
            clauses.append("is_repair_pending")
        elif review_status == "discuss":
            # The 錯題討論區's own filter: a question a person rejected, or one the pipeline returned
            # for a fresh look. The predicate is the shared `SQL_DISCUSS_PREDICATE`, so this and the
            # light CTE below cannot drift from each other or from `is_discuss_bucket`.
            clauses.append(SQL_DISCUSS_PREDICATE)
        elif review_status == "accepted_reaudit":
            clauses.append("is_accepted_reaudit_pending")
        elif review_status == "correct":
            clauses.append(
                """
                is_reviewed
                AND (
                    corrected_candidate_json IS NOT NULL
                    OR COALESCE(review_event_json, '{}'::jsonb) ? 'correction'
                )
                """
            )
        elif review_status == "reset_review":
            clauses.append(
                "is_reset_unreviewed AND NOT is_repair_pending AND NOT is_accepted_reaudit_pending"
            )
        elif review_status == "unreviewed":
            clauses.append("is_never_reviewed")
        elif review_status == "reviewed":
            clauses.append("is_reviewed")
        elif review_status:
            clauses.append("review_action = %s")
            values.append(review_status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cte = f"""
WITH scoped_candidates AS MATERIALIZED (
    SELECT *
    FROM exam.question_candidates
    {scope_where}
),
latest_question AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.action,
        e.corrected_candidate_json,
        e.event_json,
        e.notes,
        e.reviewer,
        e.created_at,
        e.id
    FROM exam.question_review_events e
    JOIN scoped_candidates USING (candidate_key)
    WHERE e.action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual', 'mobile_defer', 'mobile_resume')
    ORDER BY e.candidate_key, e.id DESC
),
latest_answer AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.action,
        e.created_at,
        e.id
    FROM exam.answer_review_events e
    JOIN scoped_candidates USING (candidate_key)
    ORDER BY e.candidate_key, e.id DESC
),
latest_question_ai AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.audit_status,
        e.recommended_action,
        e.audit_json,
        e.created_at,
        e.id
    FROM exam.question_ai_review_events e
    JOIN scoped_candidates USING (candidate_key)
    WHERE NOT (
        COALESCE(e.prompt_version, '') LIKE 'visual_%%'
        OR COALESCE(e.model_name, '') LIKE '%%visual%%'
        OR COALESCE(e.audit_json, '{{}}'::jsonb) ? 'visual_status'
        OR COALESCE(e.audit_json->>'stage', '') = 'image'
    )
      AND COALESCE(e.recommended_action, '') <> 'review_group'
    ORDER BY e.candidate_key, e.id DESC
),
issue_flags AS (
    SELECT
        e.candidate_key,
        bool_or(e.severity IN ('blocked', 'error', 'warning')) AS has_question_issue
    FROM exam.question_parse_issues e
    JOIN scoped_candidates USING (candidate_key)
    WHERE e.resolved_at IS NULL
      AND e.issue_code NOT IN ('missing_answer', 'missing_answer_markdown', 'unexpected_answer_value')
    GROUP BY e.candidate_key
),
base AS (
    SELECT
        c.candidate_key,
        c.question_number,
        c.raw_candidate_json,
        c.review_status,
        (fq.question_key IS NOT NULL) AS physical_in_formal,
        COALESCE(
            lq.action IN ('accept', 'unblock') AND la.action IN ('accept', 'unblock'),
            false
        ) AS ready_for_formal,
        COALESCE((
            fq.review_status = 'accepted'
            AND EXISTS (SELECT 1 FROM exam.answers fa WHERE fa.question_id = fq.id)
        ), false) AS formal_usable,
        COALESCE((
            fq.review_status = 'accepted'
            AND EXISTS (SELECT 1 FROM exam.answers fa WHERE fa.question_id = fq.id)
        ), false) AS in_formal,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') AS category,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
        COALESCE(c.raw_candidate_json->'metadata'->>'year', '') AS year,
        COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') AS ordinal,
        lq.action AS review_action,
        lq.corrected_candidate_json,
        lq.event_json AS review_event_json,
        lq.reviewer AS review_reviewer,
        la.action AS answer_review_action,
        (lq.action IS NOT NULL AND lq.action NOT IN ('unreviewed', 'reset_review')) AS is_reviewed,
        (lq.candidate_key IS NULL) AS is_never_reviewed,
        (lq.action IN ('unreviewed', 'reset_review')) AS is_reset_unreviewed,
        {SQL_REVIEW_ACCEPTED_REAUDIT_EXPR} AS is_accepted_reaudit_pending,
        {SQL_REVIEW_REPAIR_PENDING_EXPR} AS is_repair_pending,
        (
            COALESCE(i.has_question_issue, false)
            OR (
                NOT COALESCE(lq.created_at >= lai.created_at, false)
                AND (
                    lai.audit_status IN ('needs_review', 'block', 'blocked')
                    OR COALESCE(lai.recommended_action, '') NOT IN ('', 'no_action')
                    OR (
                        jsonb_typeof(COALESCE(lai.audit_json->'findings', '[]'::jsonb)) = 'array'
                        AND jsonb_array_length(COALESCE(lai.audit_json->'findings', '[]'::jsonb)) > 0
                    )
                )
            )
        ) AS has_active_attention,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END AS year_sort,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END AS ordinal_sort,
        CASE WHEN c.question_number ~ '^[0-9]+$' THEN c.question_number::integer ELSE 0 END AS question_sort
    FROM scoped_candidates c
    LEFT JOIN exam.questions fq ON fq.question_key = c.candidate_key
    LEFT JOIN latest_question lq ON lq.candidate_key = c.candidate_key
    LEFT JOIN latest_answer la ON la.candidate_key = c.candidate_key
    LEFT JOIN latest_question_ai lai ON lai.candidate_key = c.candidate_key
    LEFT JOIN issue_flags i ON i.candidate_key = c.candidate_key
),
filtered AS (
    SELECT *
    FROM base
    {where}
)
"""
        return cte, [*scope_values, *values]

    def _sql_plain_candidate_rows_and_counts(
        self,
        params: dict[str, str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        clauses, values = self._sql_candidate_where(params)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        scoped = bool(clauses)
        order_clause = """
            COALESCE(raw_candidate_json->'metadata'->>'normalized_category_name', raw_candidate_json->'metadata'->>'group_name', ''),
            COALESCE(raw_candidate_json->'metadata'->>'normalized_subject_name', ''),
            CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
                THEN (raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END DESC,
            CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
                THEN (raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END DESC,
            CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END,
            candidate_key
        """ if scoped else "id"
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT raw_candidate_json
                    FROM exam.question_candidates
                    {where}
                    ORDER BY {order_clause}
                    LIMIT %s
                    """,
                    [*values, limit],
                )
                rows: list[dict[str, Any]] = []
                for (raw_candidate,) in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                cur.execute(f"SELECT count(*) FROM exam.question_candidates {where}", values)
                filtered_count = int(cur.fetchone()[0])
                if (params.get("reviewStatus") or "") == "exclude":
                    cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') = 'excluded'")
                else:
                    cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') <> 'excluded'")
                total_count = int(cur.fetchone()[0])
                reviewed_count = 0
                if scoped:
                    cur.execute(
                        f"""
                        WITH scoped_candidates AS MATERIALIZED (
                            SELECT candidate_key
                            FROM exam.question_candidates
                            {where}
                        ),
                        latest_question AS (
                            SELECT DISTINCT ON (e.candidate_key) e.candidate_key, e.action, e.created_at, e.id
                            FROM exam.question_review_events e
                            JOIN scoped_candidates sc ON sc.candidate_key = e.candidate_key
                            WHERE e.action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual', 'mobile_defer', 'mobile_resume')
                            ORDER BY e.candidate_key, e.id DESC
                        )
                        SELECT count(*)
                        FROM scoped_candidates c
                        JOIN latest_question lq ON lq.candidate_key = c.candidate_key
                        WHERE lq.action NOT IN ('unreviewed', 'reset_review')
                        """,
                        values,
                    )
                    reviewed_count = int(cur.fetchone()[0])
                return rows, filtered_count, reviewed_count, total_count

    def _sql_filtered_candidate_rows_and_counts(
        self,
        params: dict[str, str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        use_light_query = self._sql_can_use_light_candidate_query(params)
        if use_light_query:
            cte, values = self._sql_light_candidate_filter_parts(params)
        else:
            cte, values = self._sql_candidate_filter_parts(params)
        stage_priority = (
            "CASE WHEN visual_review_status IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem') "
            "OR has_manual_asset THEN 1 ELSE 0 END"
            if (params.get("visualStatus") or "")
            else "CASE WHEN is_reviewed THEN 1 ELSE 0 END"
        )
        attention_priority = (
            "CASE WHEN has_active_attention OR review_action IN ('block', 'needs_review', 'reset_review', 'unreviewed') "
            "THEN 0 ELSE 1 END"
            if use_light_query
            else "CASE WHEN question_quality_status IN ('blocked', 'needs_review') "
            "OR ai_effective_status IN ('needs_review', 'block', 'blocked') "
            "OR review_action IN ('block', 'needs_review', 'reset_review', 'unreviewed') THEN 0 ELSE 1 END"
        )
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    {cte}
                    SELECT
                        raw_candidate_json,
                        count(*) OVER () AS filtered_count,
                        count(*) FILTER (WHERE is_reviewed) OVER () AS reviewed_count
                    FROM filtered
                    ORDER BY
                        {stage_priority},
                        {attention_priority},
                        category, subject, year_sort DESC, ordinal_sort DESC, question_sort, candidate_key
                    LIMIT %s
                    """,
                    [*values, limit],
                )
                rows: list[dict[str, Any]] = []
                filtered_count = 0
                reviewed_count = 0
                for raw_candidate, row_filtered_count, row_reviewed_count in cur.fetchall():
                    filtered_count = int(row_filtered_count or 0)
                    reviewed_count = int(row_reviewed_count or 0)
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                if not rows:
                    cur.execute(
                        f"""
                        {cte}
                        SELECT
                            count(*) AS filtered_count,
                            count(*) FILTER (WHERE is_reviewed) AS reviewed_count
                        FROM filtered
                        """,
                        values,
                    )
                    count_row = cur.fetchone()
                    filtered_count = int(count_row[0] or 0) if count_row else 0
                    reviewed_count = int(count_row[1] or 0) if count_row else 0
                if (params.get("reviewStatus") or "") == "exclude":
                    cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') = 'excluded'")
                else:
                    cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') <> 'excluded'")
                total_count = int(cur.fetchone()[0])
                return rows, filtered_count, reviewed_count, total_count

    def _sql_issue_map(self, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not keys:
            return {}
        issues: dict[str, list[dict[str, Any]]] = {}
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT candidate_key, issue_code, severity, message, issue_json
                    FROM exam.question_parse_issues
                    WHERE candidate_key = ANY(%s)
                      AND resolved_at IS NULL
                    ORDER BY id
                    """,
                    (keys,),
                )
                for key, issue_code, severity, message, issue_json in cur.fetchall():
                    issues.setdefault(key, []).append(
                        {
                            "candidate_key": key,
                            "issue_code": issue_code,
                            "severity": severity,
                            "message": message,
                            "issue_json": issue_json or {},
                        }
                    )
        return issues

    def _sql_formal_question_map(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        if not keys:
            return {}
        formal: dict[str, dict[str, Any]] = {}
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        q.question_key,
                        q.id,
                        q.review_status,
                        q.created_at,
                        EXISTS (
                            SELECT 1
                            FROM exam.answers a
                            WHERE a.question_id = q.id
                        ) AS has_answer
                    FROM exam.questions q
                    WHERE q.question_key = ANY(%s)
                    """,
                    (keys,),
                )
                for question_key, question_id, review_status, created_at, has_answer in cur.fetchall():
                    formal[str(question_key)] = {
                        "in_formal": True,
                        "question_id": int(question_id),
                        "review_status": review_status,
                        "has_answer": bool(has_answer),
                        "usable": bool(review_status == "accepted" and has_answer),
                        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                    }
        return formal

    def _db_event_value(self, event_json: Any, fallback: dict[str, Any]) -> dict[str, Any]:
        if isinstance(event_json, dict):
            event = dict(event_json)
        elif isinstance(event_json, str) and event_json.strip():
            try:
                event = json.loads(event_json)
            except json.JSONDecodeError:
                event = {}
        else:
            event = {}
        merged = dict(fallback)
        merged.update(event)
        return merged

    def _legacy_jsonl_storage(self, path: Path, event: dict[str, Any]) -> dict[str, Any]:
        if not self.legacy_jsonl_backup_enabled and self.sql_review_enabled:
            return {"ok": True, "enabled": False, "path": str(path)}
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            return {"ok": True, "enabled": True, "path": str(path)}
        except Exception as exc:
            if self.sql_review_enabled:
                return {"ok": False, "enabled": True, "path": str(path), "error": str(exc)}
            raise

    def _legacy_jsonl_storage_many(self, path: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.legacy_jsonl_backup_enabled and self.sql_review_enabled:
            return {"ok": True, "enabled": False, "path": str(path), "count": len(events)}
        try:
            with path.open("a", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            return {"ok": True, "enabled": True, "path": str(path), "count": len(events)}
        except Exception as exc:
            if self.sql_review_enabled:
                return {"ok": False, "enabled": True, "path": str(path), "error": str(exc), "count": len(events)}
            raise

    def _sql_latest_question_review_event(self, candidate_key: str) -> dict[str, Any] | None:
        if not self.sql_review_enabled or not candidate_key:
            return None
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT action, corrected_candidate_json, event_json, notes, reviewer, created_at
                    FROM exam.question_review_events
                    WHERE candidate_key = %s
                      AND action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual', 'mobile_defer', 'mobile_resume')
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (candidate_key,),
                )
                row = cur.fetchone()
        if not row:
            return None
        action, correction, event_json, notes, reviewer, created_at = row
        return self._db_event_value(
            event_json,
            {
                "candidate_key": candidate_key,
                "action": action,
                "correction": correction,
                "notes": notes,
                "reviewer": reviewer,
                "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
            },
        )

    def current_question_review(self, candidate_key: str) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self._sql_latest_question_review_event(candidate_key) or {}
        return self.latest_reviews.get(candidate_key) or {}

    def ai_suggestion_apply_allowed(self, candidate_key: str) -> bool:
        """An AI patch may only be applied after an item is genuinely unreviewed.

        This is intentionally checked server-side as well as in the browser. A
        closed human review must first receive an explicit reset_review event
        from an approved catch report; an AI suggestion is never that approval.
        """
        latest = self.current_question_review(candidate_key)
        return not latest or latest.get("action") in RESET_REVIEW_ACTIONS

    def _sql_question_review_maps(
        self,
        keys: list[str],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
        latest: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = {}
        latest_reset: dict[str, dict[str, Any]] = {}
        if not keys:
            return latest, counts, latest_reset
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT candidate_key, action, corrected_candidate_json, event_json, notes, reviewer, created_at
                    FROM exam.question_review_events
                    WHERE candidate_key = ANY(%s)
                    ORDER BY candidate_key, id
                    """,
                    (keys,),
                )
                for key, action, correction, event_json, notes, reviewer, created_at in cur.fetchall():
                    if action in MOBILE_REVIEW_ACTIONS:
                        continue
                    if action in NON_QUESTION_REVIEW_ACTIONS:
                        counts[key] = counts.get(key, 0) + 1
                        continue
                    event = self._db_event_value(
                        event_json,
                        {
                            "candidate_key": key,
                            "action": action,
                            "correction": correction,
                            "notes": notes,
                            "reviewer": reviewer,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                        },
                    )
                    counts[key] = counts.get(key, 0) + 1
                    # Keep reset events separate from the latest human review.
                    # A parser repair may carry a correction so the UI can show
                    # the repaired text while still requiring a new human pass.
                    if event.get("action") in RESET_REVIEW_ACTIONS:
                        previous = latest.get(key) or latest_reset.get(key)
                        if not event.get("correction") and previous and previous.get("correction"):
                            event["correction"] = previous["correction"]
                        latest.pop(key, None)
                        latest_reset[key] = event
                        continue
                    previous = latest.get(key) or latest_reset.get(key)
                    if not event.get("correction") and previous and previous.get("correction"):
                        event["correction"] = previous["correction"]
                    # A note on a question whose latest state is a pending reset must merge into
                    # that reset, not replace it - the SQL half of the 錯題討論區 rule.
                    if _note_annotates_pending_reset(event, key, latest, latest_reset):
                        latest_reset[key] = _merge_note_into_reset(latest_reset[key], event)
                        continue
                    latest[key] = event
                    latest_reset.pop(key, None)
        return latest, counts, latest_reset

    def _sql_group_review_maps(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        if not keys:
            return latest
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (candidate_key)
                        candidate_key,
                        action,
                        event_json,
                        notes,
                        reviewer,
                        created_at
                    FROM exam.question_review_events
                    WHERE candidate_key = ANY(%s)
                      AND action IN ('confirm_not_group', 'confirm_group', 'reset_group_review')
                    ORDER BY candidate_key, id DESC
                    """,
                    (keys,),
                )
                for key, action, event_json, notes, reviewer, created_at in cur.fetchall():
                    event = self._db_event_value(
                        event_json,
                        {
                            "candidate_key": key,
                            "action": action,
                            "notes": notes,
                            "reviewer": reviewer,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                        },
                    )
                    latest[key] = event
        return latest

    def _mobile_review_maps(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        key_set = {str(key or "") for key in keys if str(key or "")}
        if not key_set:
            return {}
        latest: dict[str, dict[str, Any]] = {}
        if self.sql_review_enabled:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (candidate_key)
                            candidate_key,
                            action,
                            event_json,
                            notes,
                            reviewer,
                            created_at
                        FROM exam.question_review_events
                        WHERE candidate_key = ANY(%s)
                          AND action IN ('mobile_defer', 'mobile_resume')
                        ORDER BY candidate_key, id DESC
                        """,
                        (list(key_set),),
                    )
                    for key, action, event_json, notes, reviewer, created_at in cur.fetchall():
                        event = self._db_event_value(
                            event_json,
                            {
                                "candidate_key": key,
                                "action": action,
                                "notes": notes,
                                "reviewer": reviewer,
                                "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                            },
                        )
                        latest[str(key)] = event
        elif self.review_log.exists():
            with self.review_log.open(encoding="utf-8") as review_file:
                for line in review_file:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = str(event.get("candidate_key") or "")
                    if key in key_set and event.get("action") in MOBILE_REVIEW_ACTIONS:
                        latest[key] = event
        return {
            key: {
                "deferred": event.get("action") == "mobile_defer",
                "action": event.get("action"),
                "updated_at": event.get("created_at"),
                "reviewer": event.get("reviewer"),
            }
            for key, event in latest.items()
        }

    def _sql_latest_event_maps(
        self,
        table: str,
        keys: list[str],
        *,
        reset_actions: set[str],
        ai: bool = False,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
        latest: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = {}
        latest_reset: dict[str, dict[str, Any]] = {}
        if not keys:
            return latest, counts, latest_reset
        if table not in {"exam.answer_review_events", "exam.question_ai_review_events"}:
            raise ValueError(table)
        if ai:
            sql = """
                SELECT candidate_key, action, audit_json, event_json, notes, reviewer, provider, model_name, prompt_version, input_hash, created_at, id
                FROM exam.question_ai_review_events
                WHERE candidate_key = ANY(%s)
                  AND NOT (
                      COALESCE(prompt_version, '') LIKE 'visual_%%'
                      OR COALESCE(model_name, '') LIKE '%%visual%%'
                      OR COALESCE(audit_json, '{}'::jsonb) ? 'visual_status'
                      OR COALESCE(audit_json->>'stage', '') = 'image'
                  )
                ORDER BY candidate_key, id
            """
        else:
            sql = """
                SELECT candidate_key, action, reviewed_answer_json, corrected_answer_json, event_json, notes, reviewer, answer_source_registry_key, created_at
                FROM exam.answer_review_events
                WHERE candidate_key = ANY(%s)
                ORDER BY candidate_key, id
            """
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (keys,))
                for row in cur.fetchall():
                    if ai:
                        key, action, audit_json, event_json, notes, reviewer, provider, model_name, prompt_version, input_hash, created_at, event_id = row
                        fallback = {
                            "candidate_key": key,
                            "action": action,
                            "audit": audit_json or {},
                            "notes": notes,
                            "reviewer": reviewer,
                            "provider": provider,
                            "model": model_name,
                            "prompt_version": prompt_version,
                            "input_hash": input_hash,
                            "event_id": int(event_id),
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                        }
                    else:
                        key, action, reviewed_answer, corrected_answer, event_json, notes, reviewer, answer_source_registry_key, created_at = row
                        fallback = {
                            "candidate_key": key,
                            "answer_source_registry_key": answer_source_registry_key,
                            "action": action,
                            "reviewed_answer": reviewed_answer,
                            "corrected_answer": corrected_answer,
                            "notes": notes,
                            "reviewer": reviewer,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                        }
                    event = self._db_event_value(event_json, fallback)
                    counts[key] = counts.get(key, 0) + 1
                    if event.get("action") in reset_actions:
                        latest.pop(key, None)
                        latest_reset[key] = event
                        continue
                    if _note_annotates_pending_reset(event, key, latest, latest_reset):
                        latest_reset[key] = _merge_note_into_reset(latest_reset[key], event)
                        continue
                    latest[key] = event
                    latest_reset.pop(key, None)
        return latest, counts, latest_reset

    def _sql_ai_feedback_maps(
        self,
        keys: list[str],
        *,
        reviewer: str = "local",
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Return the latest append-only rating for each audit scope."""
        latest: dict[str, dict[str, dict[str, Any]]] = {}
        if not keys:
            return latest
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (candidate_key, audit_scope)
                        candidate_key,
                        audit_scope,
                        rating,
                        reviewer,
                        reason,
                        ai_review_ref,
                        ai_review_event_id,
                        feedback_json,
                        created_at,
                        id
                    FROM exam.question_ai_feedback_events
                    WHERE candidate_key = ANY(%s)
                      AND COALESCE(reviewer, 'local') = %s
                    ORDER BY candidate_key, audit_scope, id DESC
                    """,
                    (keys, reviewer or "local"),
                )
                for row in cur.fetchall():
                    (
                        key,
                        scope,
                        rating,
                        event_reviewer,
                        reason,
                        ai_review_ref,
                        ai_review_event_id,
                        feedback_json,
                        created_at,
                        feedback_id,
                    ) = row
                    event = self._db_event_value(
                        feedback_json,
                        {
                            "action": "ai_feedback",
                            "candidate_key": key,
                            "audit_scope": scope,
                            "rating": rating,
                            "reviewer": event_reviewer or "local",
                            "reason": reason or "",
                            "ai_review_ref": ai_review_ref,
                            "ai_review_event_id": ai_review_event_id,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                            "feedback_event_id": int(feedback_id),
                        },
                    )
                    latest.setdefault(str(key), {})[str(scope)] = event
        return latest

    def _sql_ai_learning_maps(
        self,
        keys: list[str],
        *,
        reviewer: str = "local",
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Return the latest human-selected training example per audit scope."""
        latest: dict[str, dict[str, dict[str, Any]]] = {}
        if not keys:
            return latest
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (candidate_key, audit_scope)
                        candidate_key,
                        audit_scope,
                        reviewer,
                        reason,
                        ai_review_ref,
                        ai_review_event_id,
                        learning_json,
                        created_at,
                        id
                    FROM exam.question_ai_learning_events
                    WHERE candidate_key = ANY(%s)
                      AND COALESCE(reviewer, 'local') = %s
                    ORDER BY candidate_key, audit_scope, id DESC
                    """,
                    (keys, reviewer or "local"),
                )
                for row in cur.fetchall():
                    (
                        key,
                        scope,
                        event_reviewer,
                        reason,
                        ai_review_ref,
                        ai_review_event_id,
                        learning_json,
                        created_at,
                        learning_id,
                    ) = row
                    event = self._db_event_value(
                        learning_json,
                        {
                            "action": "ai_learning",
                            "candidate_key": key,
                            "audit_scope": scope,
                            "reviewer": event_reviewer or "local",
                            "reason": reason or "",
                            "ai_review_ref": ai_review_ref,
                            "ai_review_event_id": ai_review_event_id,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                            "learning_event_id": int(learning_id),
                        },
                    )
                    latest.setdefault(str(key), {})[str(scope)] = event
        return latest

    def _sql_correction_feedback_maps(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """Return the latest immutable before/after feedback per candidate."""
        latest: dict[str, dict[str, Any]] = {}
        if not keys:
            return latest
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (candidate_key)
                        candidate_key,
                        event_json,
                        feedback_id,
                        source_kind,
                        audit_scope,
                        created_at,
                        id,
                        (
                            SELECT jsonb_build_object(
                                'status', guardrail.status,
                                'candidate', guardrail.candidate_json,
                                'validation', guardrail.validation_json,
                                'created_at', guardrail.created_at,
                                'guardrail_candidate_id', guardrail.id
                            )
                            FROM exam.question_guardrail_candidates AS guardrail
                            WHERE guardrail.feedback_id = feedback.feedback_id
                            ORDER BY guardrail.id DESC
                            LIMIT 1
                        ) AS guardrail_candidate
                    FROM exam.question_correction_feedback_events
                    AS feedback
                    WHERE candidate_key = ANY(%s)
                    ORDER BY candidate_key, id DESC
                    """,
                    (keys,),
                )
                for key, event_json, feedback_id, source_kind, scope, created_at, event_id, guardrail_candidate in cur.fetchall():
                    event = self._db_event_value(
                        event_json,
                        {
                            "feedback_id": feedback_id,
                            "candidate_key": key,
                            "source_kind": source_kind,
                            "scope": scope,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                            "feedback_event_id": int(event_id),
                        },
                    )
                    event.setdefault("feedback_event_id", int(event_id))
                    if isinstance(guardrail_candidate, dict):
                        event["guardrail_candidate"] = guardrail_candidate
                    latest[str(key)] = event
        return latest

    def correction_feedback_payload(self, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Expose the immutable correction outbox without granting activation rights."""
        params = params or {}
        candidate_key = str(params.get("candidate_key") or params.get("candidateKey") or "").strip()
        try:
            limit = max(1, min(int(params.get("limit") or "100"), 500))
        except ValueError:
            limit = 100
        requested_status = str(params.get("status") or "").strip().lower()
        if requested_status not in {"", "pending", "all"}:
            requested_status = ""

        events: list[dict[str, Any]] = []
        if not self.sql_review_enabled:
            events = load_correction_feedback_rows(self.correction_feedback_log, limit=500)
        else:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    if candidate_key:
                        cur.execute(
                            """
                            SELECT id, candidate_key, feedback_id, source_kind, audit_scope,
                                   lane_key, reviewer, event_ref, changed_fields, before_json,
                                   after_json, diff_json, evidence_json, ai_task_json, event_json,
                                   created_at
                            FROM exam.question_correction_feedback_events
                            WHERE candidate_key = %s
                            ORDER BY id ASC
                            LIMIT %s
                            """,
                            (candidate_key, limit),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT id, candidate_key, feedback_id, source_kind, audit_scope,
                                   lane_key, reviewer, event_ref, changed_fields, before_json,
                                   after_json, diff_json, evidence_json, ai_task_json, event_json,
                                   created_at
                            FROM exam.question_correction_feedback_events
                            ORDER BY id ASC
                            LIMIT %s
                            """,
                            (limit,),
                        )
                    rows = cur.fetchall()
                    for (
                        event_id,
                        key,
                        feedback_id,
                        source_kind,
                        scope,
                        lane,
                        reviewer,
                        event_ref,
                        changed_fields,
                        before_json,
                        after_json,
                        diff_json,
                        evidence_json,
                        ai_task_json,
                        event_json,
                        created_at,
                    ) in rows:
                        fallback = {
                            "feedback_id": feedback_id,
                            "candidate_key": key,
                            "source_kind": source_kind,
                            "scope": scope,
                            "lane": lane or "",
                            "reviewer": reviewer or "",
                            "event_ref": event_ref or "",
                            "changed_fields": changed_fields or [],
                            "before": before_json or {},
                            "after": after_json or {},
                            "diff": diff_json or [],
                            "evidence": evidence_json or [],
                            "ai_task": ai_task_json or {},
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                            "feedback_event_id": int(event_id),
                        }
                        events.append(self._db_event_value(event_json, fallback))

        if self.sql_review_enabled and events:
            feedback_ids = [str(event.get("feedback_id") or "") for event in events if event.get("feedback_id")]
            if feedback_ids:
                guardrails: dict[str, dict[str, Any]] = {}
                with self._sql_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT DISTINCT ON (feedback_id)
                                   feedback_id, status, candidate_json, validation_json, created_at, id
                            FROM exam.question_guardrail_candidates
                            WHERE feedback_id = ANY(%s)
                            ORDER BY feedback_id, id DESC
                            """,
                            (feedback_ids,),
                        )
                        for feedback_id, status, candidate_json, validation_json, created_at, guardrail_id in cur.fetchall():
                            guardrails[str(feedback_id)] = {
                                "status": status,
                                "candidate": candidate_json or {},
                                "validation": validation_json or {},
                                "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                                "guardrail_candidate_id": int(guardrail_id),
                            }
                for event in events:
                    guardrail = guardrails.get(str(event.get("feedback_id") or ""))
                    if guardrail:
                        event["guardrail_candidate"] = guardrail

        if requested_status == "pending":
            events = [
                event
                for event in events
                if not isinstance(event.get("guardrail_candidate"), dict)
                or not event["guardrail_candidate"].get("status")
            ]
        events = events[-limit:]
        return {
            "ok": True,
            "experience": "correction_feedback_outbox_v1",
            "advisory_only": True,
            "owner_approval_required": True,
            "direct_rule_or_skill_write": False,
            "candidate_key": candidate_key or None,
            "status_filter": requested_status or "all",
            "count": len(events),
            "events": events,
        }

    def batch_accept_questions(self, candidate_keys: list[str], reviewer: str = "local", notes: str = "") -> dict[str, Any]:
        saved: list[dict[str, Any]] = []
        pending_events: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()
        ordered_keys = []
        for raw_key in candidate_keys:
            key = str(raw_key or "")
            if key and key not in seen:
                seen.add(key)
                ordered_keys.append(key)
        seen.clear()
        sql_items = self._candidates_by_key_sql(ordered_keys) if self.sql_review_enabled else {}
        sql_issues = self._sql_issue_map(ordered_keys) if self.sql_review_enabled else {}
        sql_latest_ai, _sql_ai_counts, _sql_ai_reset = self._sql_latest_event_maps(
            "exam.question_ai_review_events",
            ordered_keys,
            reset_actions=AI_RESET_REVIEW_ACTIONS,
            ai=True,
        ) if self.sql_review_enabled else ({}, {}, {})
        sql_latest_reviews, _sql_review_counts, _sql_review_resets = self._sql_question_review_maps(ordered_keys) if self.sql_review_enabled else ({}, {}, {})
        for raw_key in candidate_keys:
            key = str(raw_key or "")
            if not key or key in seen:
                continue
            seen.add(key)
            item = sql_items.get(key) if self.sql_review_enabled else self.candidate_by_key.get(key)
            if not item:
                skipped.append({"candidate_key": key, "reason": "not_found"})
                continue
            latest = sql_latest_reviews.get(key) if self.sql_review_enabled else self.current_question_review(key)
            latest_action = latest.get("action") if latest else None
            if latest_action in {"block", "exclude", "needs_review"}:
                skipped.append({"candidate_key": key, "reason": f"manual_{latest_action}"})
                continue
            if latest_action in {"accept", "unblock"}:
                skipped.append({"candidate_key": key, "reason": "already_accepted"})
                continue
            latest_ai = sql_latest_ai.get(key) if self.sql_review_enabled else self.latest_ai_reviews.get(key)
            latest_ai_audit = latest_ai.get("audit") if latest_ai else None
            ai_suggestion, _ai_suggestion_changes = ai_suggested_correction(item, latest_ai_audit)
            ai_status = effective_ai_audit_status(latest_ai_audit, ai_suggestion)
            if ai_status in {"needs_review", "block"}:
                skipped.append({"candidate_key": key, "reason": f"ai_{ai_status}"})
                continue
            issue_map = sql_issues if self.sql_review_enabled else self.issues
            question_issues = [issue for issue in issue_map.get(key, []) if issue.get("issue_code") not in ANSWER_ISSUE_CODES]
            quality = issue_quality_status(question_issues)
            if quality != "pass":
                skipped.append({"candidate_key": key, "reason": f"quality_{quality}"})
                continue
            event = {
                "candidate_key": key,
                "action": "accept",
                "notes": notes,
                "reviewer": reviewer,
                "batch_action": "accept_visible_pass",
            }
            correction = normalized_correction(latest.get("correction") if latest else None)
            if correction:
                event["correction"] = correction
            pending_events.append(event)
        if pending_events:
            try:
                saved = self.append_reviews_batch(pending_events)
            except SqlWriteError as exc:
                skipped.extend(
                    {"candidate_key": str(event.get("candidate_key") or ""), "reason": f"sql_write_failed: {exc}"}
                    for event in pending_events
                )
        return {"saved": saved, "skipped": skipped}

    def confirm_not_group(self, candidate_keys: list[str], reviewer: str = "local", notes: str = "", group_sheet_key: str = "") -> dict[str, Any]:
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_key in candidate_keys:
            key = str(raw_key or "")
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in self.candidate_by_key and not self.sql_candidate_exists(key):
                skipped.append({"candidate_key": key, "reason": "not_found"})
                continue
            event = {
                "candidate_key": key,
                "action": "confirm_not_group",
                "reviewer": reviewer,
                "notes": notes or "題組審核：人工確認此候選不是題組，不應再出現在題組待審清單。",
                "group_sheet_key": group_sheet_key,
                "review_layer": "group",
            }
            try:
                saved.append(self.append_review(event))
            except SqlWriteError as exc:
                skipped.append({"candidate_key": key, "reason": f"sql_write_failed: {exc}"})
        if self.sql_review_enabled and seen:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE exam.questions
                        SET question_group_id = NULL,
                            group_sequence_no = NULL
                        WHERE question_key = ANY(%s)
                        """,
                        (list(seen),),
                    )
                conn.commit()
        return {"saved": saved, "skipped": skipped}

    def reset_group_review(self, candidate_keys: list[str], reviewer: str = "local", notes: str = "", group_sheet_key: str = "") -> dict[str, Any]:
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_key in candidate_keys:
            key = str(raw_key or "")
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in self.candidate_by_key and not self.sql_candidate_exists(key):
                skipped.append({"candidate_key": key, "reason": "not_found"})
                continue
            event = {
                "candidate_key": key,
                "action": "reset_group_review",
                "reviewer": reviewer,
                "notes": notes or "題組審核：人工退回題組層未審，不改變審題與答案狀態。",
                "group_sheet_key": group_sheet_key,
                "review_layer": "group",
            }
            try:
                saved.append(self.append_review(event))
            except SqlWriteError as exc:
                skipped.append({"candidate_key": key, "reason": f"sql_write_failed: {exc}"})
        if self.sql_review_enabled and seen:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE exam.questions
                        SET question_group_id = NULL,
                            group_sequence_no = NULL
                        WHERE question_key = ANY(%s)
                        """,
                        (list(seen),),
                    )
                conn.commit()
        return {"saved": saved, "skipped": skipped}

    def _candidate_rows_for_group(self, candidate_keys: list[str]) -> list[dict[str, Any]]:
        candidate_keys = list(dict.fromkeys(str(key) for key in candidate_keys if str(key or "").strip()))
        if not candidate_keys:
            return []
        if self.sql_review_enabled:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT candidate_key, source_registry_key, source_document_id, question_number, raw_candidate_json
                        FROM exam.question_candidates
                        WHERE candidate_key = ANY(%s)
                        """,
                        (candidate_keys,),
                    )
                    rows = []
                    for key, source_registry_key, source_document_id, question_number, raw_candidate in cur.fetchall():
                        payload = raw_candidate if isinstance(raw_candidate, dict) else json.loads(raw_candidate)
                        rows.append(
                            {
                                "candidate_key": key,
                                "source_registry_key": source_registry_key,
                                "source_document_id": source_document_id,
                                "question_number": question_number,
                                "raw_candidate_json": payload,
                            }
                        )
                    return rows
        rows = []
        for key in candidate_keys:
            item = self.candidate_by_key.get(key)
            if item:
                rows.append(
                    {
                        "candidate_key": key,
                        "source_registry_key": item.get("source_registry_key"),
                        "source_document_id": None,
                        "question_number": item.get("question_number"),
                        "raw_candidate_json": item,
                    }
                )
        return rows

    def _upsert_sql_question_group(
        self,
        candidate_keys: list[str],
        *,
        group_ref: str,
        group_type: str,
        shared_stem: str,
        metadata: dict[str, Any],
        candidate_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False}
        rows = candidate_rows if candidate_rows is not None else self._candidate_rows_for_group(candidate_keys)
        if not rows:
            return {"ok": False, "error": "no candidate rows for group"}
        rows.sort(key=lambda row: int_or_zero(row.get("question_number")))
        first = rows[0]
        source_registry_key = str(first.get("source_registry_key") or "")
        group_key = f"{source_registry_key}:{group_ref}"
        official_document_id = first.get("source_document_id")
        question_numbers = [str(row.get("question_number") or "") for row in rows]
        range_label = f"q{int_or_zero(question_numbers[0]):03d}-q{int_or_zero(question_numbers[-1]):03d}" if question_numbers else group_ref
        metadata_payload = {
            **metadata,
            "candidate_keys": candidate_keys,
            "question_numbers": question_numbers,
            "source_registry_key": source_registry_key,
            "group_ref": group_ref,
            "shared_stem_updated_by_review": True,
        }
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_groups (
                        official_document_id,
                        group_key,
                        group_type,
                        shared_stem_text,
                        stem,
                        shared_stem_json,
                        metadata,
                        group_question_range,
                        review_status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'accepted')
                    ON CONFLICT (group_key) DO UPDATE
                    SET group_type = EXCLUDED.group_type,
                        shared_stem_text = EXCLUDED.shared_stem_text,
                        stem = EXCLUDED.stem,
                        shared_stem_json = EXCLUDED.shared_stem_json,
                        metadata = EXCLUDED.metadata,
                        group_question_range = EXCLUDED.group_question_range,
                        review_status = 'accepted'
                    RETURNING id
                    """,
                    (
                        official_document_id,
                        group_key,
                        group_type,
                        shared_stem,
                        shared_stem,
                        Jsonb({"text": shared_stem}) if Jsonb is not None else json.dumps({"text": shared_stem}, ensure_ascii=False),
                        Jsonb(metadata_payload) if Jsonb is not None else json.dumps(metadata_payload, ensure_ascii=False),
                        range_label,
                    ),
                )
                group_id = int(cur.fetchone()[0])
                cur.execute(
                    """
                    UPDATE exam.questions q
                    SET question_group_id = %s,
                        group_sequence_no = data.sequence_no
                    FROM (
                        SELECT unnest(%s::text[]) AS question_key,
                               unnest(%s::int[]) AS sequence_no
                    ) AS data
                    WHERE q.question_key = data.question_key
                    """,
                    (group_id, candidate_keys, list(range(1, len(candidate_keys) + 1))),
                )
                cur.execute(
                    """
                    UPDATE exam.question_groups g
                    SET anchor_question_id = q.id
                    FROM exam.questions q
                    WHERE g.id = %s
                      AND q.question_key = %s
                    """,
                    (group_id, candidate_keys[0]),
                )
            conn.commit()
        return {"ok": True, "sql_primary": True, "group_id": group_id, "group_key": group_key}

    def append_group_reviews(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        created_at = datetime.now().isoformat(timespec="seconds")
        for event in events:
            prepared_event = dict(event)
            prepared_event.setdefault("created_at", created_at)
            prepared.append(prepared_event)
        sql_storage_by_key = self._insert_sql_group_review_events(prepared)
        jsonl_storage = self._legacy_jsonl_storage_many(self.review_log, prepared)
        self._review_log_signature = file_signature(self.review_log)
        saved: list[dict[str, Any]] = []
        for event in prepared:
            key = str(event.get("candidate_key") or "")
            self.review_counts[key] = self.review_counts.get(key, 0) + 1
            self.latest_group_reviews[key] = event
            storage = {**sql_storage_by_key.get(key, {}), "legacy_jsonl_backup": jsonl_storage}
            saved.append({**event, "storage": storage})
        return saved

    def confirm_group(
        self,
        candidate_keys: list[str],
        *,
        reviewer: str = "local",
        notes: str = "",
        group_ref: str = "",
        group_type: str = "shared_stem",
        shared_stem: str = "",
        group_sheet_key: str = "",
    ) -> dict[str, Any]:
        unique_keys = list(dict.fromkeys(str(key) for key in candidate_keys if str(key or "").strip()))
        rows = self._candidate_rows_for_group(unique_keys)
        rows.sort(key=lambda row: int_or_zero(row.get("question_number")))
        ordered_keys = [str(row["candidate_key"]) for row in rows]
        if not ordered_keys:
            return {"saved": [], "skipped": [{"candidate_key": "", "reason": "no_valid_candidates"}], "group": {}}
        if group_type not in {"shared_stem", "chained_context", "manual_range", "unknown"}:
            group_type = "shared_stem"
        if not group_ref:
            first_no = int_or_zero(rows[0].get("question_number"))
            last_no = int_or_zero(rows[-1].get("question_number"))
            group_ref = f"q{first_no:03d}-q{last_no:03d}" if first_no and last_no else group_sheet_key or "manual_group"
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        member_summary = [
            {"candidate_key": key, "group_sequence_no": index + 1}
            for index, key in enumerate(ordered_keys)
        ]
        for index, key in enumerate(ordered_keys, start=1):
            event = {
                "candidate_key": key,
                "action": "confirm_group",
                "reviewer": reviewer,
                "notes": notes or f"題組審核：人工確認屬於 {group_ref}。",
                "group_sheet_key": group_sheet_key,
                "review_layer": "group",
                "group_ref": group_ref,
                "group_type": group_type,
                "group_sequence_no": index,
                "group_members": member_summary,
                "shared_stem": shared_stem,
            }
            try:
                saved.append(event)
            except SqlWriteError as exc:
                skipped.append({"candidate_key": key, "reason": f"sql_write_failed: {exc}"})
        if saved:
            try:
                saved = self.append_group_reviews(saved)
            except SqlWriteError as exc:
                skipped.extend({"candidate_key": key, "reason": f"sql_write_failed: {exc}"} for key in ordered_keys)
                saved = []
        group_result = (
            self._upsert_sql_question_group(
                ordered_keys,
                group_ref=group_ref,
                group_type=group_type,
                shared_stem=shared_stem,
                metadata={"group_sheet_key": group_sheet_key, "reviewer": reviewer},
                candidate_rows=rows,
            )
            if saved else {"ok": False, "error": "group review events were not saved"}
        )
        return {"saved": saved, "skipped": skipped, "group": group_result}

    def candidate_keys_for_manual_group_range(self, seed_candidate_key: str, range_text: str) -> list[str]:
        match = re.search(r"(\d{1,3})\s*(?:-|－|~|～|至|到)\s*(\d{1,3})", str(range_text or ""))
        if not match:
            return []
        start, end = int(match.group(1)), int(match.group(2))
        if start <= 0 or end < start or end > start + 30:
            return []
        seed_source = ""
        seed_item = self.candidate_by_key.get(seed_candidate_key)
        if seed_item:
            seed_source = str(seed_item.get("source_registry_key") or "")
        if self.sql_review_enabled:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    if not seed_source:
                        cur.execute(
                            "SELECT source_registry_key FROM exam.question_candidates WHERE candidate_key = %s",
                            (seed_candidate_key,),
                        )
                        row = cur.fetchone()
                        seed_source = str(row[0] or "") if row else ""
                    if not seed_source:
                        return []
                    cur.execute(
                        """
                        SELECT candidate_key
                        FROM exam.question_candidates
                        WHERE source_registry_key = %s
                          AND question_number ~ '^[0-9]+$'
                          AND question_number::integer BETWEEN %s AND %s
                        ORDER BY question_number::integer, candidate_key
                        """,
                        (seed_source, start, end),
                    )
                    return [str(row[0]) for row in cur.fetchall()]
        if not seed_source:
            return []
        rows = [
            item for item in self.candidates
            if str(item.get("source_registry_key") or "") == seed_source
            and start <= int_or_zero(item.get("question_number")) <= end
        ]
        rows.sort(key=lambda item: int_or_zero(item.get("question_number")))
        return [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]

    def candidate_keys_for_manual_group_filters(
        self,
        *,
        category: str = "",
        subject: str = "",
        year: str = "",
        ordinal: str = "",
        range_text: str = "",
    ) -> list[str]:
        match = re.search(r"(\d{1,3})\s*(?:-|－|~|～|至|到)\s*(\d{1,3})", str(range_text or ""))
        if not match:
            return []
        start, end = int(match.group(1)), int(match.group(2))
        if start <= 0 or end < start or end > start + 30:
            return []
        category = str(category or "").strip()
        subject = str(subject or "").strip()
        year = str(year or "").strip()
        ordinal = str(ordinal or "").strip()
        if not (category and subject and year and ordinal):
            return []
        if self.sql_review_enabled:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH matched AS (
                            SELECT
                                candidate_key,
                                source_registry_key,
                                CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END AS qn
                            FROM exam.question_candidates
                            WHERE COALESCE(raw_candidate_json->'metadata'->>'normalized_category_name', raw_candidate_json->'metadata'->>'group_name', '') = %s
                              AND COALESCE(raw_candidate_json->'metadata'->>'normalized_subject_name', '') = %s
                              AND COALESCE(raw_candidate_json->'metadata'->>'year', '') = %s
                              AND COALESCE(raw_candidate_json->'metadata'->>'exam_ordinal', '') = %s
                              AND question_number ~ '^[0-9]+$'
                              AND question_number::integer BETWEEN %s AND %s
                        ),
                        source_counts AS (
                            SELECT source_registry_key, count(*) AS row_count
                            FROM matched
                            GROUP BY source_registry_key
                            ORDER BY row_count DESC, source_registry_key
                            LIMIT 1
                        )
                        SELECT m.candidate_key
                        FROM matched m
                        JOIN source_counts s USING (source_registry_key)
                        ORDER BY m.qn, m.candidate_key
                        """,
                        (category, subject, year, ordinal, start, end),
                    )
                    return [str(row[0]) for row in cur.fetchall()]
        rows = [
            item for item in self.candidates
            if str((item.get("metadata") or {}).get("normalized_category_name") or (item.get("metadata") or {}).get("group_name") or "") == category
            and str((item.get("metadata") or {}).get("normalized_subject_name") or "") == subject
            and str((item.get("metadata") or {}).get("year") or "") == year
            and str((item.get("metadata") or {}).get("exam_ordinal") or "") == ordinal
            and start <= int_or_zero(item.get("question_number")) <= end
        ]
        rows.sort(key=lambda item: int_or_zero(item.get("question_number")))
        return [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]

    def sql_candidate_exists(self, candidate_key: str) -> bool:
        if not self.sql_review_enabled:
            return False
        try:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM exam.question_candidates WHERE candidate_key = %s LIMIT 1",
                        (candidate_key,),
                    )
                    return cur.fetchone() is not None
        except Exception:
            return False

    def save_manual_image_asset(
        self,
        candidate_key: str,
        data_url: str,
        *,
        reviewer: str = "local",
        notes: str = "",
        caption: str = "",
        asset_role: str = "manual_question_image",
        placement: str = "stem",
        target_option: str = "",
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        candidate = self.candidate_by_key.get(candidate_key)
        if not candidate and self.sql_review_enabled:
            candidate = self._candidate_by_key_sql(candidate_key)
        if not candidate:
            raise KeyError(candidate_key)
        image_bytes, mime_type, extension = data_url_to_bytes(data_url)
        full_hash = hashlib.sha256(image_bytes).hexdigest()
        digest = full_hash[:16]
        metadata = candidate.get("metadata") or {}
        category = safe_path_segment(metadata.get("normalized_category_name") or metadata.get("group_name"))
        subject = safe_path_segment(metadata.get("normalized_subject_name"))
        year = safe_path_segment(metadata.get("year"))
        ordinal = safe_path_segment(metadata.get("exam_ordinal"))
        question_number = safe_path_segment(candidate.get("question_number"), "q")
        target_dir = MANUAL_ASSET_ROOT / "question_images" / category / subject / f"{year}_{ordinal}"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{year}{ordinal}_q{question_number}_{digest}{extension}"
        target = target_dir / filename
        if not target.exists():
            target.write_bytes(image_bytes)

        asset_ref = {
            "asset_key": f"{candidate_key}:manual_image:{digest}",
            "raw_ref": filename,
            "path": display_path(target),
            "path_relative": display_path(target),
            "exists": True,
            "asset_role": asset_role or "manual_question_image",
            "placement": placement or "stem",
            "target_option": target_option or "",
            "source": "human_paste",
            "caption": caption or "人工貼上修正圖片",
            "description": notes or "",
            "manual_asset": True,
            "mime_type": mime_type,
            "sha256": full_hash,
            "bytes": len(image_bytes),
        }
        previous = self.current_question_review(candidate_key)
        existing_correction = normalized_correction(previous.get("correction"))
        correction = dict(existing_correction)
        placement = str(placement or "stem").strip()
        target_option = str(target_option or "").strip().upper()[:1]
        table_markup_removed = False
        if placement == "option":
            if target_option not in {"A", "B", "C", "D", "E"}:
                raise ValueError("option placement requires target_option A-E.")
            asset_ref["asset_role"] = "option_image"
            asset_ref["placement"] = "option"
            asset_ref["target_option"] = target_option
            option_rows = list(existing_correction.get("options") or candidate.get("options") or [])
            next_options = []
            touched = False
            for option in option_rows:
                if not isinstance(option, dict):
                    continue
                row = dict(option)
                if str(row.get("key") or "").strip().upper()[:1] == target_option:
                    row["image"] = asset_ref
                    touched = True
                next_options.append(row)
            if not touched:
                next_options.append({"key": target_option, "text": "", "image": asset_ref})
            correction["options"] = next_options
        elif placement == "answer":
            asset_ref["asset_role"] = "manual_answer_image"
            asset_ref["placement"] = "answer"
            if replace_existing:
                existing_refs = []
            elif "answer_image_refs" in existing_correction:
                existing_refs = list(existing_correction.get("answer_image_refs") or [])
            else:
                existing_refs = list(candidate.get("answer_image_refs") or [])
            existing_paths = {
                str(ref.get("path") or ref.get("path_relative") or "")
                for ref in existing_refs
                if isinstance(ref, dict)
            }
            if str(asset_ref["path"]) not in existing_paths:
                existing_refs.append(asset_ref)
            correction["answer_image_refs"] = existing_refs
        else:
            if placement == "table":
                asset_ref["asset_role"] = "table_manual_screenshot"
                # A human table screenshot is the display authority. Remove only
                # rendered table markup from the human correction, never from the
                # raw candidate, so provenance remains intact.
                source_stem = str(
                    existing_correction.get("stem")
                    or candidate.get("stem_with_tables")
                    or candidate.get("stem")
                    or ""
                )
                clean_stem, table_markup_removed = strip_structured_tables(source_stem)
                if table_markup_removed:
                    correction["stem"] = clean_stem
            elif placement == "group":
                asset_ref["asset_role"] = "group_shared_asset"
            else:
                asset_ref["asset_role"] = asset_role or "manual_question_image"
                placement = "stem"
            asset_ref["placement"] = placement
            if replace_existing:
                existing_refs = []
            elif "image_refs" in existing_correction:
                existing_refs = list(existing_correction.get("image_refs") or [])
            else:
                existing_refs = list(candidate.get("image_refs") or [])
            existing_paths = {
                str(ref.get("path") or ref.get("path_relative") or "")
                for ref in existing_refs
                if isinstance(ref, dict)
            }
            if str(asset_ref["path"]) not in existing_paths:
                existing_refs.append(asset_ref)
            correction["image_refs"] = existing_refs
        correction["visual_review"] = "visual_asset_ok"
        table_replacement_note = (
            "表格人工截圖已取代題幹的結構化表格文字；原始 parser candidate 保留供追溯。"
            if table_markup_removed
            else ""
        )
        default_note = (
            "人工貼上修正圖片並取代既有圖片；圖片審核視為已確認，題目是否通過仍依審題狀態。"
            if replace_existing
            else f"人工貼上修正圖片至{placement}；圖片審核視為已確認，題目是否通過仍依審題狀態。"
        )
        event = {
            "candidate_key": candidate_key,
            "action": "correct",
            "reviewer": reviewer or "local",
            "notes": "\n".join(part for part in (notes or default_note, table_replacement_note) if part),
            "correction": correction,
            "manual_asset": asset_ref,
            "correction_action": "replace_manual_asset" if replace_existing else "add_manual_asset",
        }
        saved_event = self.append_review(event)
        return {"asset": asset_ref, "event": saved_event}

    def candidate_payload(
        self,
        item: dict[str, Any],
        *,
        issues_by_key: dict[str, list[dict[str, Any]]] | None = None,
        latest_reviews: dict[str, dict[str, Any]] | None = None,
        review_counts: dict[str, int] | None = None,
        latest_reset_reviews: dict[str, dict[str, Any]] | None = None,
        latest_answer_reviews: dict[str, dict[str, Any]] | None = None,
        answer_review_counts: dict[str, int] | None = None,
        latest_ai_reviews: dict[str, dict[str, Any]] | None = None,
        ai_review_counts: dict[str, int] | None = None,
        formal_question_map: dict[str, dict[str, Any]] | None = None,
        latest_group_reviews: dict[str, dict[str, Any]] | None = None,
        latest_ai_feedbacks: dict[str, dict[str, dict[str, Any]]] | None = None,
        latest_ai_learnings: dict[str, dict[str, dict[str, Any]]] | None = None,
        latest_correction_feedbacks: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        key = item["candidate_key"]
        issues_by_key = self.issues if issues_by_key is None else issues_by_key
        latest_reviews = self.latest_reviews if latest_reviews is None else latest_reviews
        review_counts = self.review_counts if review_counts is None else review_counts
        latest_reset_reviews = self.latest_reset_reviews if latest_reset_reviews is None else latest_reset_reviews
        latest_answer_reviews = self.latest_answer_reviews if latest_answer_reviews is None else latest_answer_reviews
        answer_review_counts = self.answer_review_counts if answer_review_counts is None else answer_review_counts
        latest_ai_reviews = self.latest_ai_reviews if latest_ai_reviews is None else latest_ai_reviews
        ai_review_counts = self.ai_review_counts if ai_review_counts is None else ai_review_counts
        formal_question_map = {} if formal_question_map is None else formal_question_map
        latest_group_reviews = self.latest_group_reviews if latest_group_reviews is None else latest_group_reviews
        latest_ai_feedbacks = self.latest_ai_feedbacks if latest_ai_feedbacks is None else latest_ai_feedbacks
        latest_ai_learnings = getattr(self, "latest_ai_learnings", {}) if latest_ai_learnings is None else latest_ai_learnings
        latest_correction_feedbacks = getattr(self, "latest_correction_feedbacks", {}) if latest_correction_feedbacks is None else latest_correction_feedbacks
        copy = dict(item)
        metadata = copy.get("metadata") or {}
        issues = issues_by_key.get(key, [])
        question_issues = [issue for issue in issues if issue.get("issue_code") not in ANSWER_ISSUE_CODES]
        answer_issues = [issue for issue in issues if issue.get("issue_code") in ANSWER_ISSUE_CODES]
        copy["raw_quality_status"] = copy.get("quality_status")
        copy["question_quality_status"] = issue_quality_status(question_issues)
        copy["answer_gate_status"] = issue_quality_status(answer_issues)
        copy["issues"] = question_issues
        copy["question_issues"] = question_issues
        copy["answer_issues"] = answer_issues
        copy["question_issue_count"] = len(question_issues)
        copy["answer_issue_count"] = len(answer_issues)
        latest_review = latest_reviews.get(key)
        latest_reset_review = latest_reset_reviews.get(key)
        review_projection_data = review_projection(latest_review, latest_reset_review, metadata)
        copy["repair_status"] = repair_event_info(latest_review, latest_reset_review, metadata)
        review_event = latest_review or latest_reset_review
        correction = normalized_correction(review_event.get("correction") if review_event else None)
        if correction:
            copy["parser_original"] = {
                "stem": item.get("stem"),
                "options": item.get("options"),
                "answer": item.get("answer"),
                "group_ref": item.get("group_ref"),
                "group_sequence_no": item.get("group_sequence_no"),
                "image_refs": item.get("image_refs"),
                "stem_image": item.get("stem_image"),
                "answer_image_refs": item.get("answer_image_refs"),
                "visual_review": item.get("visual_review"),
            }
            for field in ("stem", "answer", "group_ref", "group_sequence_no", "image_refs", "stem_image", "answer_image_refs", "visual_review"):
                if field in correction:
                    if field == "group_ref" and not str(correction.get(field) or "").strip():
                        continue
                    copy[field] = correction[field]
            if "options" in correction:
                copy["options"] = correction["options"]
            # The correction **is text**, and every dispute was measured from the text that was just
            # replaced. Leaving them meant a reviewer was shown a dispute about a character that is
            # no longer in the field: measured 2026-09-23, all 204 repaired questions still displayed
            # the `substituted-script` dispute the repair had already fixed, so the 錯題討論區's own
            # "① 機器偵測" panel contradicted the text directly above it. Re-measured here with the
            # same function the build path uses, so there is still one rule for what a dispute is -
            # this changes only *when* the rule runs. `disputes_recomputed` records that it ran, so
            # the UI (and any reader) can tell a dispute that survived the repair from one that was
            # never re-checked.
            review_queue.disputes_for_paper([copy])
            copy["disputes_recomputed"] = True
        display_stem, table_suppressed = strip_structured_tables(str(copy.get("stem") or ""))
        if table_suppressed:
            copy["stem_with_tables"] = copy.get("stem")
            copy["stem"] = display_stem
            copy["table_markup_suppressed"] = True
        option_image_paths = {
            str((option.get("image") or {}).get("path") or "")
            for option in (copy.get("options") or [])
            if isinstance(option, dict) and isinstance(option.get("image"), dict)
        }
        copy["non_option_image_refs"] = [
            ref
            for ref in (copy.get("image_refs") or [])
            if isinstance(ref, dict) and str(ref.get("path") or "") not in option_image_paths
        ]
        copy["visual_profile"] = candidate_visual_profile(copy)
        copy["is_visual_question"] = bool(
            copy["visual_profile"]["has_visual_asset"]
            or copy["visual_profile"]["has_visual_dependency"]
            or copy["visual_profile"]["has_structured_table"]
        )
        group_review = latest_group_reviews.get(key)
        if group_review and group_review.get("action") == "confirm_group":
            if group_review.get("group_ref"):
                copy["group_ref"] = group_review.get("group_ref")
            if group_review.get("group_sequence_no") is not None:
                copy["group_sequence_no"] = group_review.get("group_sequence_no")
            if group_review.get("group_type"):
                copy["group_type"] = group_review.get("group_type")
            if "shared_stem" in group_review:
                copy["shared_stem"] = group_review.get("shared_stem") or ""
        copy["group_review"] = group_review
        copy["review"] = {
            "status": "reviewed" if latest_review else "unreviewed",
            "action": review_event.get("action") if review_event else None,
            "notes": (
                (review_event.get("notes") or review_event.get("previous_notes"))
                if review_event else None
            ),
            "updated_at": review_event.get("created_at") if review_event else None,
            "event_count": review_counts.get(key, 0),
            "has_correction": bool(correction),
            "correction": correction or None,
            "reset": latest_reset_review,
            "is_reset_unreviewed": review_projection_data["is_reset_unreviewed"],
            "has_human_event": review_projection_data["has_human_event"],
            "is_never_reviewed": review_projection_data["is_never_reviewed"],
            "is_repair_pending": review_projection_data["is_repair_pending"],
            "is_accepted_reaudit_pending": review_projection_data["is_accepted_reaudit_pending"],
            "was_previously_accepted": review_projection_data["was_previously_accepted"],
            "previous_action": review_projection_data["previous_action"],
            "queue_bucket": review_projection_data["queue_bucket"],
            "queue_label": review_projection_data["display_label"],
        }
        correction_feedback = latest_correction_feedbacks.get(key)
        copy["correction_feedback"] = correction_feedback
        latest_action = latest_review.get("action") if latest_review else None
        formal = dict(formal_question_map.get(key) or {"in_formal": False})
        physical_in_formal = bool(formal.get("in_formal"))
        formal_usable = bool(formal.get("usable"))
        latest_answer_review = latest_answer_reviews.get(key)
        latest_answer_action = latest_answer_review.get("action") if latest_answer_review else None
        copy["answer_review"] = {
            "status": "reviewed" if latest_answer_review else "unreviewed",
            "action": latest_answer_action,
            "notes": latest_answer_review.get("notes") if latest_answer_review else None,
            "updated_at": latest_answer_review.get("created_at") if latest_answer_review else None,
            "event_count": answer_review_counts.get(key, 0),
            "correction": latest_answer_review.get("corrected_answer") if latest_answer_review else None,
        }
        question_ready = latest_action in QUESTION_READY_ACTIONS
        answer_ready = latest_answer_action in ANSWER_READY_ACTIONS
        ready_for_formal = bool(question_ready and answer_ready)
        formal.update(
            {
                "physical_in_formal": physical_in_formal,
                "formal_usable": formal_usable,
                "question_ready": question_ready,
                "answer_ready": answer_ready,
                "ready_for_formal": ready_for_formal,
                "pending_promotion": bool(ready_for_formal and not formal_usable),
                "review_drift": bool(formal_usable and not ready_for_formal),
                "soft_withdrawn": bool(physical_in_formal and not formal_usable and not ready_for_formal),
                "in_formal": formal_usable,
            }
        )
        copy["formal"] = formal
        latest_ai_review = latest_ai_reviews.get(key)
        historical_ai_audit = latest_ai_review.get("audit") if latest_ai_review else None
        ai_superseded = human_review_supersedes_ai(latest_review, latest_ai_review)
        ai_audit, group_ai_audit = (
            (None, None)
            if ai_superseded
            else split_ai_audit_scopes(copy, historical_ai_audit)
        )
        ai_patch_reason = ai_patch_safety_reason(copy, ai_audit)
        ai_suggestion, ai_suggestion_changes = ai_suggested_correction(copy, ai_audit)
        ai_event_ref = ai_review_reference(latest_ai_review)
        feedback_by_scope = latest_ai_feedbacks.get(key) or {}
        question_feedback = feedback_by_scope.get("question")
        if question_feedback and question_feedback.get("ai_review_ref") != ai_event_ref:
            question_feedback = None
        group_feedback = feedback_by_scope.get("group")
        if group_feedback and group_feedback.get("ai_review_ref") != ai_event_ref:
            group_feedback = None
        learning_by_scope = latest_ai_learnings.get(key) or {}
        question_learning = learning_by_scope.get("question")
        if question_learning and question_learning.get("ai_review_ref") != ai_event_ref:
            question_learning = None
        group_learning = learning_by_scope.get("group")
        if group_learning and group_learning.get("ai_review_ref") != ai_event_ref:
            group_learning = None
        if isinstance(group_ai_audit, dict):
            group_ai_audit = {
                **group_ai_audit,
                "event_ref": ai_event_ref,
                "feedback": group_feedback,
                "learning": group_learning,
            }
        copy["group_ai_review"] = group_ai_audit
        copy["ai_patch_suppressed_reason"] = ai_patch_reason
        ai_suggestion_allowed = bool(
            ai_suggestion
            and (
                latest_review is None
                or latest_review.get("action") in RESET_REVIEW_ACTIONS
            )
        )
        copy["ai_review"] = {
            "status": "reviewed" if latest_ai_review else "unreviewed",
            "active": bool(latest_ai_review and not ai_superseded),
            "superseded_by_human": ai_superseded,
            "audit_status": effective_ai_audit_status(ai_audit, ai_suggestion),
            "raw_audit_status": historical_ai_audit.get("status") if isinstance(historical_ai_audit, dict) else None,
            "visual_status": (
                None
                if copy.get("visual_review")
                else copy.get("visual_ai_status") or ai_visual_status(historical_ai_audit)
            ),
            "recommended_action": ai_audit.get("recommended_action") if isinstance(ai_audit, dict) else None,
            "summary": ai_audit.get("summary") if isinstance(ai_audit, dict) else None,
            "findings": ai_audit.get("findings") if isinstance(ai_audit, dict) else [],
            "labels": ai_audit.get("labels") if isinstance(ai_audit, dict) else [],
            "checks": ai_audit.get("checks") if isinstance(ai_audit, dict) else {},
            "lane_results": compact_ai_lane_results(historical_ai_audit),
            "suggested_correction": ai_suggestion,
            "suggested_changes": ai_suggestion_changes,
            "correction_coverage": ai_audit.get("correction_coverage") if isinstance(ai_audit, dict) else None,
            "uncorrected_findings": ai_audit.get("uncorrected_findings") if isinstance(ai_audit, dict) else [],
            "suggestion_apply_allowed": ai_suggestion_allowed,
            "patch_suppressed_reason": ai_patch_reason,
            "suggestion_apply_reason": (
                "題目目前未審，可套用後進行人工複核。"
                if ai_suggestion_allowed
                else "此題已有人工審核。請先核准 AI 抓漏清單並建立退回未審事件，才可套用 AI 建議。"
            ) if ai_suggestion else None,
            "provider": latest_ai_review.get("provider") if latest_ai_review else None,
            "model": latest_ai_review.get("model") if latest_ai_review else None,
            "updated_at": latest_ai_review.get("created_at") if latest_ai_review else None,
            "superseded_at": latest_review.get("created_at") if ai_superseded and latest_review else None,
            "event_count": ai_review_counts.get(key, 0),
            "event_ref": ai_event_ref,
            "feedback": question_feedback,
            "learning": question_learning,
        }
        # What the qbr pipeline's model said about this question, if it has been asked at all.
        #
        # Deliberately a separate field from `ai_review` above: that one is the SQL-era audit record
        # (`question_ai_review_events.jsonl`), which is a human-supervised review with a status and a
        # recommended action. This is the qbr loop's note - no status, no action, no reviewer - and
        # folding them into one field would let a note be read as a review. The screen draws it as a
        # note under its own heading, with the model named, so a reader can weigh it.
        copy["qbr_ai_finding"] = (getattr(self, "latest_qbr_ai_findings", {}) or {}).get(key)
        copy["source_files"] = {
            "official_pdf": metadata.get("question_pdf_relative") or metadata.get("question_pdf"),
            "mineru_layout_pdf": sibling_pdf(metadata.get("question_markdown_relative") or metadata.get("question_markdown") or "", "_layout"),
            "mineru_origin_pdf": sibling_pdf(metadata.get("question_markdown_relative") or metadata.get("question_markdown") or "", "_origin"),
            "question_markdown": metadata.get("question_markdown_relative") or metadata.get("question_markdown"),
        }
        copy["answer_source_files"] = {
            "official_pdf": metadata.get("answer_pdf_primary_relative") or metadata.get("answer_pdf_primary"),
            "mineru_layout_pdf": sibling_pdf(metadata.get("answer_markdown_relative") or metadata.get("answer_markdown") or "", "_layout"),
            "mineru_origin_pdf": sibling_pdf(metadata.get("answer_markdown_relative") or metadata.get("answer_markdown") or "", "_origin"),
            "answer_markdown": metadata.get("answer_markdown_relative") or metadata.get("answer_markdown"),
        }
        return copy

    def answer_sheet_key(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") or {}
        return "|".join(
            str(value or "")
            for value in [
                item.get("answer_source_registry_key"),
                metadata.get("answer_pdf_primary_relative") or metadata.get("answer_pdf_primary"),
                metadata.get("exam_code"),
                metadata.get("category_code"),
                metadata.get("subject_code"),
                metadata.get("year"),
                metadata.get("exam_ordinal"),
            ]
        )

    def answer_sheet_payload(
        self,
        items: list[dict[str, Any]],
        *,
        issues_by_key: dict[str, list[dict[str, Any]]] | None = None,
        latest_reviews: dict[str, dict[str, Any]] | None = None,
        review_counts: dict[str, int] | None = None,
        latest_reset_reviews: dict[str, dict[str, Any]] | None = None,
        latest_answer_reviews: dict[str, dict[str, Any]] | None = None,
        answer_review_counts: dict[str, int] | None = None,
        latest_ai_reviews: dict[str, dict[str, Any]] | None = None,
        ai_review_counts: dict[str, int] | None = None,
        latest_group_reviews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload_items = [
            self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            for item in sorted(items, key=lambda row: int_or_zero(row.get("question_number")))
        ]
        first = payload_items[0]
        metadata = first.get("metadata") or {}
        role = metadata.get("answer_role_primary") or ""
        rows = []
        reviewed_count = 0
        accepted_count = 0
        blocked_count = 0
        needs_review_count = 0
        corrected_count = 0
        answer_issue_count = 0
        answer_attention_count = 0
        concrete_rows: list[dict[str, Any]] = []
        for item in payload_items:
            review = item.get("answer_review") or {}
            hint = answer_review_hint(role, item.get("answer"), item.get("answer_payload"))
            action = review.get("action")
            if review.get("status") == "reviewed":
                reviewed_count += 1
            if action in {"accept", "unblock"}:
                accepted_count += 1
            elif action == "block":
                blocked_count += 1
            elif action == "needs_review":
                needs_review_count += 1
            if review.get("correction"):
                corrected_count += 1
            answer_issue_count += int(item.get("answer_issue_count") or 0)
            if hint.get("severity") == "warning":
                answer_attention_count += 1
            concrete_rows.append(
                {
                    "candidate_key": item.get("candidate_key"),
                    "question_number": item.get("question_number"),
                    "question_number_occurrence": item.get("question_number_occurrence"),
                    "stem": item.get("stem"),
                    "options": item.get("options") or [],
                    "answer": item.get("answer"),
                    "answer_image_refs": item.get("answer_image_refs") or [],
                    "answer_payload": item.get("answer_payload"),
                    "answer_review": review,
                    "answer_hint": hint,
                    "answer_issues": item.get("answer_issues") or [],
                    "question_review": item.get("review") or {},
                }
            )
        rows_by_number = {int_or_zero(row.get("question_number")): row for row in concrete_rows if int_or_zero(row.get("question_number")) > 0}
        max_question_number = max(rows_by_number) if rows_by_number else len(concrete_rows)
        for number in range(1, max_question_number + 1):
            if number in rows_by_number:
                rows.append(rows_by_number[number])
                continue
            rows.append(
                {
                    "candidate_key": "",
                    "question_number": str(number),
                    "question_number_occurrence": None,
                    "stem": "",
                    "answer": None,
                    "answer_payload": None,
                    "answer_review": {"status": "unreviewed", "action": None},
                    "answer_hint": {"flags": [], "severity": "", "message": "", "needs_manual_choice": False},
                    "answer_issues": [],
                    "question_review": {"status": "not_accepted", "action": "not_accepted"},
                    "is_placeholder": True,
                    "placeholder_reason": "題目尚未通過審核",
                }
            )
        if blocked_count:
            sheet_action = "block"
        elif needs_review_count:
            sheet_action = "needs_review"
        elif accepted_count == len(concrete_rows) and concrete_rows:
            sheet_action = "accept"
        elif reviewed_count:
            sheet_action = "reviewed"
        else:
            sheet_action = None
        return {
            "candidate_key": self.answer_sheet_key(items[0]),
            "sheet_key": self.answer_sheet_key(items[0]),
            "sheet_type": "answer_sheet",
            "question_count": len(rows),
            "reviewable_question_count": len(concrete_rows),
            "placeholder_count": len(rows) - len(concrete_rows),
            "reviewed_count": reviewed_count,
            "accepted_count": accepted_count,
            "blocked_count": blocked_count,
            "needs_review_count": needs_review_count,
            "corrected_count": corrected_count,
            "answer_issue_count": answer_issue_count,
            "answer_attention_count": answer_attention_count,
            "answer_gate_status": "blocked" if answer_issue_count else "needs_review" if answer_attention_count else "pass",
            "answer_review": {
                "status": "reviewed" if reviewed_count else "unreviewed",
                "action": sheet_action,
            },
            "answer_role_primary": role,
            "answer_role_label": "MOD" if role == "correction" else "ANS" if role == "answer" else role or "unknown",
            "metadata": metadata,
            "source_files": {
                "official_pdf": metadata.get("answer_pdf_primary_relative") or metadata.get("answer_pdf_primary"),
                "mineru_layout_pdf": sibling_pdf(metadata.get("answer_markdown_relative") or metadata.get("answer_markdown") or "", "_layout"),
                "mineru_origin_pdf": sibling_pdf(metadata.get("answer_markdown_relative") or metadata.get("answer_markdown") or "", "_origin"),
                "answer_markdown": metadata.get("answer_markdown_relative") or metadata.get("answer_markdown"),
            },
            "question_source_files": first.get("source_files") or {},
            "rows": rows,
        }

    def group_sheet_key(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") or {}
        group_ref = str(item.get("group_ref") or "").strip()
        inferred_group_ref = str(item.get("inferred_group_ref") or "").strip()
        group_review = item.get("group_review") or {}
        reviewed_sheet_key = str(group_review.get("group_sheet_key") or "").strip()
        if group_review.get("action") in {"confirm_group", "confirm_not_group"} and reviewed_sheet_key:
            return reviewed_sheet_key
        session_key = "|".join(
            str(value or "")
            for value in [
                metadata.get("normalized_category_name") or metadata.get("group_name"),
                metadata.get("normalized_subject_name"),
                metadata.get("year"),
                metadata.get("exam_ordinal"),
                item.get("source_registry_key"),
            ]
        )
        if inferred_group_ref:
            return f"inferred_continuation|{session_key}|{inferred_group_ref}"
        return f"group_ref|{session_key}|{group_ref}" if group_ref else f"unbound_suspect|{session_key}"

    def group_suspect_reasons(self, item: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if (item.get("group_review") or {}).get("action") == "confirm_not_group":
            return reasons
        group_ref = str(item.get("group_ref") or "").strip()
        if group_ref:
            reasons.append("已綁題組")
        inferred_group_ref = str(item.get("inferred_group_ref") or "").strip()
        inferred_group_kind = str(item.get("inferred_group_kind") or "").strip()
        if inferred_group_ref and inferred_group_kind != "explicit_count":
            reasons.append("承上題連續關聯")
        if inferred_group_kind == "explicit_count":
            reasons.append("明示範圍題組")
        text_parts = [
            str(item.get("stem") or ""),
            str(((item.get("metadata") or {}).get("raw_block")) or ""),
        ]
        combined = "\n".join(text_parts)
        if GROUP_CONTINUATION_RE.search(combined):
            reasons.append("題幹開頭承上題")
        if GROUP_PREFIX_RANGE_RE.search(combined) or GROUP_COUNT_RE.search(combined):
            reasons.append("題幹明示回答多題")
        return sorted(set(reasons))

    def group_session_key(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") or {}
        return "|".join(
            str(value or "")
            for value in [
                metadata.get("normalized_category_name") or metadata.get("group_name"),
                metadata.get("normalized_subject_name"),
                metadata.get("year"),
                metadata.get("exam_ordinal"),
                item.get("source_registry_key"),
            ]
        )

    def inferred_continuation_groups(self, payloads: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        by_session: dict[str, dict[int, dict[str, Any]]] = {}
        for payload in payloads:
            number = int_or_zero(payload.get("question_number"))
            if number <= 0:
                continue
            by_session.setdefault(self.group_session_key(payload), {})[number] = payload

        groups_by_key: dict[str, list[dict[str, Any]]] = {}
        for session_key, by_number in by_session.items():
            for number, payload in sorted(by_number.items()):
                if (payload.get("group_review") or {}).get("action") == "confirm_not_group":
                    continue
                stem = str(payload.get("stem") or "")
                group_count = group_count_from_text(stem)
                prefix_match = GROUP_PREFIX_RANGE_RE.search(stem)
                start = number
                end = 0
                if prefix_match:
                    start = int(prefix_match.group(1))
                    end = int(prefix_match.group(2))
                elif group_count:
                    end = start + group_count - 1
                if not (start <= number <= end <= start + 20):
                    continue
                group_ref = f"q{start:03d}-q{end:03d}"
                group_key = f"{session_key}|{group_ref}"
                group_items = []
                for group_number in range(start, end + 1):
                    item = by_number.get(group_number)
                    if item and (item.get("group_review") or {}).get("action") == "confirm_not_group":
                        item = None
                    if item:
                        item = dict(item)
                        item["inferred_group_ref"] = group_ref
                        item["inferred_group_kind"] = "explicit_count"
                        group_items.append(item)
                if len(group_items) >= 2:
                    groups_by_key[group_key] = group_items
            continuation_numbers = {
                number
                for number, payload in by_number.items()
                if GROUP_CONTINUATION_RE.search(str(payload.get("stem") or ""))
                and (payload.get("group_review") or {}).get("action") != "confirm_not_group"
            }
            for number in sorted(continuation_numbers):
                start = number - 1
                while start in continuation_numbers:
                    start -= 1
                if start not in by_number:
                    continue
                end = number
                while end + 1 in continuation_numbers:
                    end += 1
                group_ref = f"q{start:03d}-q{end:03d}"
                group_key = f"{session_key}|{group_ref}"
                group_items = []
                for group_number in range(start, end + 1):
                    item = by_number.get(group_number)
                    if item and (item.get("group_review") or {}).get("action") == "confirm_not_group":
                        item = None
                    if item:
                        item = dict(item)
                        item["inferred_group_ref"] = group_ref
                        group_items.append(item)
                if len(group_items) >= 2:
                    groups_by_key[group_key] = group_items
        return list(groups_by_key.values())

    def group_sheet_payload(
        self,
        items: list[dict[str, Any]],
        *,
        issues_by_key: dict[str, list[dict[str, Any]]] | None = None,
        latest_reviews: dict[str, dict[str, Any]] | None = None,
        review_counts: dict[str, int] | None = None,
        latest_reset_reviews: dict[str, dict[str, Any]] | None = None,
        latest_answer_reviews: dict[str, dict[str, Any]] | None = None,
        answer_review_counts: dict[str, int] | None = None,
        latest_ai_reviews: dict[str, dict[str, Any]] | None = None,
        ai_review_counts: dict[str, int] | None = None,
        latest_group_reviews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        unique_items = list({str(item.get("candidate_key") or ""): item for item in items if item.get("candidate_key")}.values())
        payload_items = [
            self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            for item in sorted(unique_items, key=lambda row: (int_or_zero(row.get("question_number")), str(row.get("candidate_key") or "")))
        ]
        if not payload_items:
            raise ValueError("group sheet requires at least one candidate")
        first = payload_items[0]
        metadata = first.get("metadata") or {}
        first_group_review_action = (first.get("group_review") or {}).get("action") or ""
        group_ref = str(first.get("group_ref") or "").strip()
        inferred_group_ref = str(first.get("inferred_group_ref") or "").strip()
        inferred_group_kind = str(first.get("inferred_group_kind") or "").strip()
        rows = []
        reason_counts: dict[str, int] = {}
        accepted_count = 0
        blocked_count = 0
        needs_review_count = 0
        group_review_actions: list[str] = []
        shared_stem = ""
        group_type = ""
        for item in payload_items:
            reasons = self.group_suspect_reasons(item)
            for reason in reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            group_action = (item.get("group_review") or {}).get("action") or ""
            if group_action in {"confirm_group", "confirm_not_group"}:
                group_review_actions.append(str(group_action))
            if not shared_stem:
                shared_stem = str((item.get("group_review") or {}).get("shared_stem") or item.get("shared_stem") or "").strip()
            if not group_type:
                group_type = str((item.get("group_review") or {}).get("group_type") or item.get("group_type") or "").strip()
            action = (item.get("review") or {}).get("action")
            if action in {"accept", "unblock"}:
                accepted_count += 1
            elif action in {"block", "exclude"}:
                blocked_count += 1
            elif action == "needs_review":
                needs_review_count += 1
            rows.append(
                {
                    "candidate_key": item.get("candidate_key"),
                    "question_number": item.get("question_number"),
                    "question_number_occurrence": item.get("question_number_occurrence"),
                    "stem": item.get("stem"),
                    "options": item.get("options") or [],
                    "group_ref": item.get("group_ref") or "",
                    "inferred_group_ref": item.get("inferred_group_ref") or "",
                    "inferred_group_kind": item.get("inferred_group_kind") or "",
                    "review": item.get("review") or {},
                    "group_review": item.get("group_review") or {},
                    "ai_review": item.get("ai_review") or {},
                    "group_ai_review": item.get("group_ai_review") or {},
                    "visual_profile": item.get("visual_profile") or {},
                    "is_visual_question": bool(item.get("is_visual_question")),
                    "reasons": reasons,
                }
            )
        group_review_status = "unreviewed"
        if group_review_actions and all(action == "confirm_group" for action in group_review_actions):
            group_review_status = "confirmed_group"
        elif group_review_actions and all(action == "confirm_not_group" for action in group_review_actions):
            group_review_status = "confirmed_not_group"
        elif group_review_actions:
            group_review_status = "reviewed"
        is_confirmed_not_group = group_review_status == "confirmed_not_group" or first_group_review_action == "confirm_not_group"
        if is_confirmed_not_group:
            reason_counts = {}
        return {
            "sheet_type": "group_sheet",
            "candidate_key": self.group_sheet_key(first),
            "group_sheet_key": self.group_sheet_key(first),
            "group_review_status": group_review_status,
            "group_ref": group_ref,
            "group_type": group_type,
            "shared_stem": shared_stem,
            "inferred_group_ref": inferred_group_ref,
            "inferred_group_kind": inferred_group_kind,
            "group_label": "已確認非題組" if is_confirmed_not_group else group_ref or (
                f"明示範圍 {inferred_group_ref}"
                if inferred_group_kind == "explicit_count"
                else f"承上題 {inferred_group_ref}"
                if inferred_group_ref
                else "未綁疑似題組"
            ),
            "metadata": metadata,
            "source_files": first.get("source_files") or {},
            "rows": rows,
            "question_count": len(rows),
            "accepted_count": accepted_count,
            "blocked_count": blocked_count,
            "needs_review_count": needs_review_count,
            "reason_counts": reason_counts,
            "gate_status": "not_group" if is_confirmed_not_group else "linked" if group_ref else "inferred_continuation" if inferred_group_ref else "unbound_suspect",
        }

    def facets(self, params: dict[str, str] | None = None) -> dict[str, list[str]]:
        params = params or {}
        # The facet values depend only on the candidate rows, not on review events, and every
        # queue load resets this. Cached because it walks the whole queue and it is asked for on
        # every `/api/candidates` response - measured at 0.27 s of every request, for an answer
        # that changes only when the queue is rebuilt.
        cache_key = "\u0000".join(sorted(f"{key}={value}" for key, value in params.items()))
        cached = getattr(self, "_facets_cache", {}).get(cache_key)
        if cached is not None:
            return cached
        values: dict[str, set[str]] = {"categories": set(), "subjects": set(), "years": set(), "ordinals": set()}
        # The category filter is folded once per request, not once per row; the row's own folded
        # form was computed at load. `category_matches_filter` also accepts the group aliases, so
        # the alias lookup is resolved here too and the per-row test is a set membership.
        category_filter = params.get("category") or ""
        wanted_categories = (
            CATEGORY_GROUP_NORMALIZED_FILTERS.get(category_filter) if category_filter in CATEGORY_GROUP_FILTERS
            else (frozenset({normalize_category_name(category_filter)}) if category_filter else None)
        )
        subject_filter = params.get("subject") or ""
        year_filter_v = params.get("year") or ""
        ordinal_filter_v = params.get("ordinal") or ""
        for category, category_norm, subject, year, ordinal in self._facet_row_values():
            category_ok = True if wanted_categories is None else category_norm in wanted_categories
            # The category selector is the top-level navigation rail. Keep it
            # global so selecting one category never hides the other categories
            # and traps the reviewer inside the current choice.
            if category:
                values["categories"].add(category)
            if category_ok and (not year_filter_v or year == year_filter_v) \
                    and (not ordinal_filter_v or ordinal == ordinal_filter_v) and subject:
                values["subjects"].add(subject)
            if category_ok and (not subject_filter or subject == subject_filter) \
                    and (not ordinal_filter_v or ordinal == ordinal_filter_v) and year:
                values["years"].add(year)
            if category_ok and (not subject_filter or subject == subject_filter) \
                    and (not year_filter_v or year == year_filter_v) and ordinal:
                values["ordinals"].add(ordinal)
        result = {
            key: sorted(value, key=lambda item: (int(item) if item.isdigit() else 9999, item))
            if key in {"years", "ordinals"}
            else sorted(value)
            for key, value in values.items()
        }
        if hasattr(self, "_facets_cache"):
            self._facets_cache[cache_key] = result
        return result

    def _facet_match(
        self,
        category: str,
        subject: str,
        year: str,
        ordinal: str,
        params: dict[str, str],
        ignore: str,
    ) -> bool:
        checks = {
            "category": (category, params.get("category") or ""),
            "subject": (subject, params.get("subject") or ""),
            "year": (year, params.get("year") or ""),
            "ordinal": (ordinal, params.get("ordinal") or ""),
        }
        for key, (value, expected) in checks.items():
            if key == ignore or not expected:
                continue
            if key == "category":
                if not category_matches_filter(value, expected):
                    return False
            elif value != expected:
                return False
        return True

    def _facet_row_values(self) -> list[tuple[str, str, str, str, str]]:
        """(category, folded category, subject, year, ordinal) for every candidate.

        The category is folded **once per queue load**, not once per row per request. Measured:
        the regex in `category_matches_filter` run over all 79,090 rows cost 0.755 s of the 0.79 s
        that a single sitting's `/api/candidates` response took, and a 32-sitting category paid it
        32 times. The raw category is kept beside the folded one because the facets *report* the
        paper's own spelling; only the comparison uses the folded form.

        Built lazily so a `ReviewState` assembled without `__init__` (as the tests do) still
        answers, and so there is exactly **one** implementation of the row projection.
        """
        rows = getattr(self, "_facet_rows", None)
        if rows is None:
            rows = []
            for item in self.candidates:
                metadata = item.get("metadata") or {}
                category = str(
                    metadata.get("normalized_category_name") or metadata.get("group_name") or ""
                )
                rows.append((
                    category,
                    normalize_category_name(category),
                    str(metadata.get("normalized_subject_name") or ""),
                    str(metadata.get("year") or ""),
                    str(metadata.get("exam_ordinal") or ""),
                ))
            self._facet_rows = rows
        return rows

    def _build_candidate_index(self) -> None:
        """Index candidates by (year, sitting) so a narrow request does not scan the whole queue.

        Measured: `filtered_candidate_payloads` walked all 79,090 candidates for **every** request,
        even one that names a single sitting of 480 questions, because the counts it returns
        (`filtered_count`, `reviewed_count`) are computed by counting matches over the full list. So
        one sitting cost 1.4 ms/question of scanning plus the payload build - 0.43 s for 480 rows -
        and the whole 物理治療師 category cost 13.7 s because `v2.html` fetches it one sitting at a
        time.

        (year, sitting) is the right key because it is what the UI asks by and it is already narrow:
        220 buckets over 79,090 rows, the largest 480. A request that names both year and sitting
        can therefore iterate only that bucket and get the *same* counts, since a row outside it
        cannot satisfy either filter. A request that names neither still falls back to the full
        list, so no filter is silently dropped.
        """
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in self.candidates:
            metadata = item.get("metadata") or {}
            key = (str(metadata.get("year") or ""), str(metadata.get("exam_ordinal") or ""))
            buckets.setdefault(key, []).append(item)
        self._candidate_sitting_index = buckets
        self._facet_rows = None
        self._facets_cache = {}

    def _candidate_scan(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """The candidates a request has to look at - all of them, or just one sitting's worth."""
        year_filter = params.get("year") or ""
        ordinal_filter = params.get("ordinal") or ""
        if year_filter and ordinal_filter:
            index = getattr(self, "_candidate_sitting_index", None)
            if index is not None:
                return index.get((year_filter, ordinal_filter), [])
        return self.candidates

    def filtered_candidate_payloads(self, params: dict[str, str]) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self.filtered_candidate_payloads_sql(params)
        q = (params.get("q") or "").strip().lower()
        status = params.get("status") or ""
        review_status = params.get("reviewStatus") or ""
        ai_review_status = params.get("aiReviewStatus") or ""
        category_filter = params.get("category") or ""
        subject_filter = params.get("subject") or ""
        year_filter = params.get("year") or ""
        ordinal_filter = params.get("ordinal") or ""
        focus_key = params.get("focusKey") or ""
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500
        # `_count=1` asks for the two numbers and nothing else. The home page's cards need
        # `total_count` and `reviewed_count` and draw no rows, but it used to send `limit=1000` to
        # get them - and `_count` was **never read**, so the server built, serialised and shipped a
        # thousand full candidate payloads (measured: 6.0 MB, 0.39 s, ~4.7 KB each) for two
        # integers that cost 0.11 s to count. The count-only path runs the **same** filter loop and
        # simply does not build a payload (`if len(payloads) < limit` is never true), so the numbers
        # cannot come from a different rule than the rows do.
        if params.get("_count") in {"1", "true", "True", "yes"}:
            limit = 0

        payloads: list[dict[str, Any]] = []
        filtered_count = 0
        reviewed_count = 0
        for item in self._candidate_scan(params):
            key = item["candidate_key"]
            latest_review = self.latest_reviews.get(key)
            latest_reset_review = self.latest_reset_reviews.get(key)
            metadata = item.get("metadata") or {}
            review_projection_data = review_projection(latest_review, latest_reset_review, metadata)
            review = {
                "status": "reviewed" if latest_review else "unreviewed",
                "action": latest_review.get("action") if latest_review else None,
                "notes": latest_review.get("notes") if latest_review else None,
                "reset": latest_reset_review,
                "is_reset_unreviewed": review_projection_data["is_reset_unreviewed"],
                "has_human_event": review_projection_data["has_human_event"],
                "is_never_reviewed": review_projection_data["is_never_reviewed"],
                "is_repair_pending": review_projection_data["is_repair_pending"],
                "is_accepted_reaudit_pending": review_projection_data["is_accepted_reaudit_pending"],
                "was_previously_accepted": review_projection_data["was_previously_accepted"],
                "previous_action": review_projection_data["previous_action"],
                "queue_bucket": review_projection_data["queue_bucket"],
                "queue_label": review_projection_data["display_label"],
            }
            category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
            subject = metadata.get("normalized_subject_name") or ""
            latest_ai_review = self.latest_ai_reviews.get(key)
            latest_ai_audit = latest_ai_review.get("audit") if latest_ai_review else None
            latest_ai_audit, _group_ai_audit = split_ai_audit_scopes(item, latest_ai_audit)
            group_only_ai = bool(
                _group_ai_audit
                and isinstance(latest_ai_audit, dict)
                and not latest_ai_audit.get("findings")
            )
            ai_suggestion, _ai_suggestion_changes = ai_suggested_correction(item, latest_ai_audit)
            ai_audit_status = effective_ai_audit_status(latest_ai_audit, ai_suggestion) or ""
            if review_status == "not_accept":
                review_match = (
                    (review["status"] == "reviewed" and review["action"] not in {"accept", "correct", "unblock", "exclude"})
                    or review["is_never_reviewed"]
                    or review["is_reset_unreviewed"]
                )
            elif review_status == "correct":
                review_match = bool(latest_review and normalized_correction(latest_review.get("correction")))
            elif review_status == "repair_pending":
                review_match = review["is_repair_pending"]
            elif review_status == "discuss":
                # The 錯題討論區's own filter: a question a person rejected, or one the pipeline
                # returned for a fresh look. The rule lives in `is_discuss_bucket` so it cannot
                # differ from the SQL paths or from what the row says its own bucket is.
                review_match = is_discuss_bucket(review)
            elif review_status == "accepted_reaudit":
                review_match = review["is_accepted_reaudit_pending"]
            elif review_status == "reset_review":
                review_match = (
                    review["is_reset_unreviewed"]
                    and not review["is_repair_pending"]
                    and not review["is_accepted_reaudit_pending"]
                )
            elif review_status == "unreviewed":
                review_match = review["is_never_reviewed"]
            elif review_status == "exclude":
                review_match = review["action"] == "exclude"
            else:
                review_match = (
                    review["action"] != "exclude"
                    and (not review_status or review["status"] == review_status or review["action"] == review_status)
                )
            item_issues = [issue for issue in self.issues.get(key, []) if issue.get("issue_code") not in ANSWER_ISSUE_CODES]
            question_quality_status = issue_quality_status(item_issues)
            if status and question_quality_status != status:
                continue
            if ai_review_status:
                if ai_review_status == "unreviewed":
                    ai_match = not latest_ai_review or group_only_ai
                elif ai_review_status == "reviewed":
                    ai_match = bool(latest_ai_review) and not group_only_ai
                elif ai_review_status == "suggested_correction":
                    ai_match = bool(ai_suggestion)
                elif ai_review_status == "needs_review":
                    ai_match = ai_audit_status in {"needs_review", "block", "blocked"}
                else:
                    ai_match = ai_audit_status == ai_review_status and not group_only_ai
                if not ai_match:
                    continue
            if not review_match:
                continue
            if category_filter and not category_matches_filter(category, category_filter):
                continue
            if subject_filter and subject != subject_filter:
                continue
            if year_filter and str(metadata.get("year") or "") != year_filter:
                continue
            if ordinal_filter and str(metadata.get("exam_ordinal") or "") != ordinal_filter:
                continue
            if q:
                reset_review = latest_reset_review or {}
                ai_audit = latest_ai_audit if isinstance(latest_ai_audit, dict) else {}
                haystack = " ".join(
                    str(value or "")
                    for value in [
                        item.get("candidate_key"),
                        item.get("question_number"),
                        item.get("stem"),
                        json.dumps(item.get("options") or [], ensure_ascii=False),
                        json.dumps((latest_review or {}).get("correction") or {}, ensure_ascii=False),
                        category,
                        subject,
                        review.get("action"),
                        review.get("notes"),
                        reset_review.get("action"),
                        reset_review.get("notes"),
                        reset_review.get("previous_action"),
                        reset_review.get("previous_notes"),
                        reset_review.get("reset_notes"),
                        latest_ai_review.get("provider") if latest_ai_review else "",
                        latest_ai_review.get("model") if latest_ai_review else "",
                        ai_audit.get("status"),
                        ai_audit.get("summary"),
                        ai_audit.get("reason"),
                        ai_audit.get("recommended_action"),
                        json.dumps(ai_audit.get("labels") or [], ensure_ascii=False),
                        json.dumps(ai_audit.get("suggested_changes") or [], ensure_ascii=False),
                    ]
                ).lower()
                if q not in haystack:
                    continue
            filtered_count += 1
            if review["status"] == "reviewed":
                reviewed_count += 1
            if len(payloads) < limit:
                payloads.append(self.candidate_payload(item))
        if focus_key and all(payload.get("candidate_key") != focus_key for payload in payloads):
            focus_item = self.candidate_by_key.get(focus_key)
            if focus_item:
                focus_payload = self.candidate_payload(focus_item)
                focus_payload["focus_injected"] = True
                payloads.insert(0, focus_payload)
        mobile_reviews = self._mobile_review_maps(
            [str(payload.get("candidate_key") or "") for payload in payloads]
        )
        for payload in payloads:
            payload["mobile_review"] = mobile_reviews.get(
                str(payload.get("candidate_key") or ""),
                {"deferred": False, "action": None, "updated_at": None, "reviewer": None},
            )
        return {
            "candidates": payloads,
            "total_count": len(self.candidates),
            "filtered_count": filtered_count,
            "returned_count": len(payloads),
            "reviewed_count": reviewed_count,
            "facets": self.facets(params),
            "candidate_data": self.candidate_data_status(),
        }

    def filtered_candidate_payloads_sql(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500
        # The same `_count=1` contract as the JSONL path: two numbers, no rows. `LIMIT 0` already
        # makes the SQL query itself count-only (the counts ride on window functions over the CTE),
        # so there is nothing else to skip - and the counts still come from the one query.
        if params.get("_count") in {"1", "true", "True", "yes"}:
            limit = 0

        rows, filtered_count, reviewed_count, total_count = self._sql_filtered_candidate_rows_and_counts(params, limit)
        focus_key = params.get("focusKey") or ""
        if focus_key and all(str(item.get("candidate_key") or "") != focus_key for item in rows):
            focus_row = self._candidate_by_key_sql(focus_key)
            if focus_row:
                focus_row["focus_injected"] = True
                rows.insert(0, focus_row)
        keys = [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]
        issues_by_key = self._sql_issue_map(keys)
        formal_question_map = self._sql_formal_question_map(keys)
        latest_reviews, review_counts, latest_reset_reviews = self._sql_question_review_maps(keys)
        latest_group_reviews = self._sql_group_review_maps(keys)
        mobile_reviews = self._mobile_review_maps(keys)
        latest_answer_reviews, answer_review_counts, _answer_reset_reviews = self._sql_latest_event_maps(
            "exam.answer_review_events",
            keys,
            reset_actions=RESET_REVIEW_ACTIONS,
        )
        latest_ai_reviews, ai_review_counts, _ai_reset_reviews = self._sql_latest_event_maps(
            "exam.question_ai_review_events",
            keys,
            reset_actions=AI_RESET_REVIEW_ACTIONS,
            ai=True,
        )
        latest_ai_feedbacks = self._sql_ai_feedback_maps(
            keys,
            reviewer=params.get("reviewer") or "local",
        )
        latest_ai_learnings = self._sql_ai_learning_maps(
            keys,
            reviewer=params.get("reviewer") or "local",
        )
        latest_correction_feedbacks = self._sql_correction_feedback_maps(keys)

        payloads: list[dict[str, Any]] = []
        for item in rows:
            payload = self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                formal_question_map=formal_question_map,
                latest_group_reviews=latest_group_reviews,
                latest_ai_feedbacks=latest_ai_feedbacks,
                latest_ai_learnings=latest_ai_learnings,
                latest_correction_feedbacks=latest_correction_feedbacks,
            )
            payload["mobile_review"] = mobile_reviews.get(
                str(item.get("candidate_key") or ""),
                {"deferred": False, "action": None, "updated_at": None, "reviewer": None},
            )
            payloads.append(payload)
        return {
            "candidates": payloads,
            "total_count": total_count,
            "filtered_count": filtered_count,
            "returned_count": len(payloads),
            "reviewed_count": reviewed_count,
            "facets": self.sql_facets(params),
            "candidate_data": self.candidate_data_status(),
        }

    def filtered_answer_payloads(self, params: dict[str, str]) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self.filtered_answer_payloads_sql(params)
        q = (params.get("q") or "").strip().lower()
        review_status = params.get("answerReviewStatus") or ""
        category_filter = params.get("category") or ""
        subject_filter = params.get("subject") or ""
        year_filter = params.get("year") or ""
        ordinal_filter = params.get("ordinal") or ""
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500

        sheet_items: dict[str, list[dict[str, Any]]] = {}
        reviewed_count = 0
        eligible_count = 0
        for item in self.candidates:
            key = item["candidate_key"]
            question_review = self.latest_reviews.get(key)
            if not question_review or question_review.get("action") not in {"accept", "unblock"}:
                continue
            latest_answer_review = self.latest_answer_reviews.get(key)
            answer_review = {
                "status": "reviewed" if latest_answer_review else "unreviewed",
                "action": latest_answer_review.get("action") if latest_answer_review else None,
                "notes": latest_answer_review.get("notes") if latest_answer_review else None,
            }
            metadata = item.get("metadata") or {}
            category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
            subject = metadata.get("normalized_subject_name") or ""
            if category_filter and not category_matches_filter(category, category_filter):
                continue
            if subject_filter and subject != subject_filter:
                continue
            if year_filter and str(metadata.get("year") or "") != year_filter:
                continue
            if ordinal_filter and str(metadata.get("exam_ordinal") or "") != ordinal_filter:
                continue
            eligible_count += 1
            if answer_review["status"] == "reviewed":
                reviewed_count += 1
            sheet_items.setdefault(self.answer_sheet_key(item), []).append(item)
        candidate_sheets = [self.answer_sheet_payload(items) for items in sheet_items.values()]

        def sheet_matches_query(sheet: dict[str, Any]) -> bool:
            if not q:
                return True
            metadata = sheet.get("metadata") or {}
            row_values = []
            for row in sheet.get("rows") or []:
                answer_review = row.get("answer_review") or {}
                row_values.extend(
                    [
                        row.get("candidate_key"),
                        row.get("question_number"),
                        row.get("stem"),
                        json.dumps(row.get("options") or [], ensure_ascii=False),
                        row.get("answer"),
                        answer_review.get("action"),
                        answer_review.get("notes"),
                    ]
                )
            haystack = " ".join(
                str(value or "")
                for value in [
                    sheet.get("sheet_key"),
                    metadata.get("normalized_category_name") or metadata.get("group_name"),
                    metadata.get("normalized_subject_name"),
                    metadata.get("year"),
                    metadata.get("exam_ordinal"),
                    sheet.get("answer_role_label"),
                    *row_values,
                ]
            ).lower()
            return q in haystack

        def sheet_matches_review(sheet: dict[str, Any]) -> bool:
            if not review_status:
                return True
            question_count = int(sheet.get("question_count") or 0)
            reviewable_count = int(sheet.get("reviewable_question_count") or question_count)
            reviewed = int(sheet.get("reviewed_count") or 0)
            accepted = int(sheet.get("accepted_count") or 0)
            blocked = int(sheet.get("blocked_count") or 0)
            needs_review = int(sheet.get("needs_review_count") or 0)
            corrected = int(sheet.get("corrected_count") or 0)
            if review_status == "unreviewed":
                return reviewed < reviewable_count
            if review_status == "reviewed":
                return reviewed > 0
            if review_status == "not_accept":
                return reviewed > 0 and accepted < reviewable_count
            if review_status == "accept":
                return reviewable_count > 0 and accepted == reviewable_count
            if review_status == "block":
                return blocked > 0
            if review_status == "needs_review":
                return needs_review > 0
            if review_status == "correct":
                return corrected > 0
            if review_status == "comment":
                return any((row.get("answer_review") or {}).get("action") == "comment" for row in sheet.get("rows") or [])
            return True

        sheets = [sheet for sheet in candidate_sheets if sheet_matches_query(sheet) and sheet_matches_review(sheet)]
        filtered_count = sum(int(sheet.get("question_count") or 0) for sheet in sheets)
        sheets.sort(
            key=lambda sheet: (
                0
                if int(sheet.get("reviewed_count") or 0) < int(sheet.get("reviewable_question_count") or sheet.get("question_count") or 0)
                else 1,
                0 if (sheet.get("answer_gate_status") or "pass") != "pass" else 1,
                str((sheet.get("metadata") or {}).get("normalized_category_name") or ""),
                str((sheet.get("metadata") or {}).get("normalized_subject_name") or ""),
                int_or_zero((sheet.get("metadata") or {}).get("year")),
                int_or_zero((sheet.get("metadata") or {}).get("exam_ordinal")),
                sheet.get("answer_role_label") or "",
            )
        )
        return {
            "candidates": sheets[:limit],
            "eligible_count": eligible_count,
            "filtered_count": filtered_count,
            "sheet_count": len(candidate_sheets),
            "returned_count": len(sheets[:limit]),
            "reviewed_count": reviewed_count,
            "facets": self.facets(params),
            "candidate_data": self.candidate_data_status(),
        }

    def _sql_answer_filter_values(self, params: dict[str, str]) -> tuple[list[str], list[Any]]:
        clauses = ["COALESCE(c.review_status, '') <> 'excluded'"]
        values: list[Any] = []
        filters = {
            "category": (SQL_ANSWER_CATEGORY_EXPR, params.get("category") or ""),
            "subject": ("c.raw_candidate_json->'metadata'->>'normalized_subject_name'", params.get("subject") or ""),
            "year": ("c.raw_candidate_json->'metadata'->>'year'", params.get("year") or ""),
            "ordinal": ("c.raw_candidate_json->'metadata'->>'exam_ordinal'", params.get("ordinal") or ""),
        }
        for key, (expr, value) in filters.items():
            if not value:
                continue
            if key == "category":
                clauses.append(f"{expr} = ANY(%s)")
                values.append(list(category_filter_values(value)))
            else:
                clauses.append(f"COALESCE({expr}, '') = %s")
                values.append(value)
        return clauses, values

    def _sql_answer_sheet_cte(self, params: dict[str, str]) -> tuple[str, list[Any]]:
        clauses, values = self._sql_answer_filter_values(params)
        scope_where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        q = (params.get("q") or "").strip().lower()
        answer_review_status = params.get("answerReviewStatus") or ""
        answer_filter_sql = ""
        if answer_review_status == "unreviewed":
            answer_filter_sql = "WHERE reviewed_count < question_count"
        elif answer_review_status == "reviewed":
            answer_filter_sql = "WHERE reviewed_count > 0"
        elif answer_review_status == "not_accept":
            answer_filter_sql = "WHERE reviewed_count > 0 AND accepted_count < question_count"
        elif answer_review_status == "accept":
            answer_filter_sql = "WHERE question_count > 0 AND accepted_count = question_count"
        elif answer_review_status == "block":
            answer_filter_sql = "WHERE blocked_count > 0"
        elif answer_review_status == "needs_review":
            answer_filter_sql = "WHERE needs_review_count > 0"
        elif answer_review_status == "correct":
            answer_filter_sql = "WHERE corrected_count > 0"
        elif answer_review_status == "comment":
            answer_filter_sql = "WHERE comment_count > 0"
        cte = f"""
WITH scoped_candidates AS MATERIALIZED (
    SELECT c.*
    FROM exam.question_candidates c
    {scope_where}
),
latest_question AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.action,
        e.corrected_candidate_json,
        e.event_json,
        e.notes,
        e.created_at,
        e.id
    FROM exam.question_review_events e
    JOIN scoped_candidates USING (candidate_key)
    WHERE e.action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual', 'mobile_defer', 'mobile_resume')
    ORDER BY e.candidate_key, e.id DESC
),
latest_answer AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.candidate_key,
        e.action,
        e.corrected_answer_json,
        e.event_json,
        e.notes,
        e.created_at,
        e.id
    FROM exam.answer_review_events e
    JOIN scoped_candidates USING (candidate_key)
    ORDER BY e.candidate_key, e.id DESC
),
eligible AS (
    SELECT
        c.candidate_key,
        c.question_number,
        c.raw_candidate_json,
        array_to_string(ARRAY[
            COALESCE(c.raw_candidate_json->>'answer_source_registry_key', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'answer_pdf_primary_relative', c.raw_candidate_json->'metadata'->>'answer_pdf_primary', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'exam_code', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'category_code', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'subject_code', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'year', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '')
        ], '|') AS sheet_key,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') AS category,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
        COALESCE(c.raw_candidate_json->'metadata'->>'answer_role_primary', '') AS answer_role,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END AS year_sort,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END AS ordinal_sort,
        CASE WHEN c.question_number ~ '^[0-9]+$' THEN c.question_number::integer ELSE 0 END AS question_sort,
        la.action AS answer_action,
        la.corrected_answer_json,
        la.event_json AS answer_event_json,
        (
            %s = ''
            OR lower(concat_ws(
                ' ',
                c.candidate_key,
                c.question_number,
                c.stem_text,
                c.raw_candidate_json->>'stem',
                c.raw_candidate_json->>'answer',
                c.raw_candidate_json::text,
                lq.corrected_candidate_json::text,
                la.corrected_answer_json::text,
                COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', ''),
                COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', ''),
                c.raw_candidate_json->'metadata'->>'year',
                c.raw_candidate_json->'metadata'->>'exam_ordinal',
                la.action,
                la.notes,
                la.event_json::text
            )) LIKE %s
        ) AS query_match
    FROM scoped_candidates c
    JOIN latest_question lq ON lq.candidate_key = c.candidate_key
    LEFT JOIN latest_answer la ON la.candidate_key = c.candidate_key
    WHERE lq.action IN ('accept', 'unblock')
),
sheet_stats AS (
    SELECT
        sheet_key,
        min(category) AS category,
        min(subject) AS subject,
        min(year_sort) AS year_sort,
        min(ordinal_sort) AS ordinal_sort,
        min(answer_role) AS answer_role,
        count(*) AS question_count,
        count(*) FILTER (WHERE answer_action IS NOT NULL AND answer_action NOT IN ('unreviewed', 'reset_review')) AS reviewed_count,
        count(*) FILTER (WHERE answer_action IN ('accept', 'unblock')) AS accepted_count,
        count(*) FILTER (WHERE answer_action = 'block') AS blocked_count,
        count(*) FILTER (WHERE answer_action = 'needs_review') AS needs_review_count,
        count(*) FILTER (
            WHERE corrected_answer_json IS NOT NULL
               OR COALESCE(answer_event_json, '{{}}'::jsonb) ? 'correction'
        ) AS corrected_count,
        count(*) FILTER (WHERE answer_action = 'comment') AS comment_count,
        bool_or(query_match) AS query_match
    FROM eligible
    GROUP BY sheet_key
),
query_sheets AS (
    SELECT *
    FROM sheet_stats
    WHERE query_match
),
filtered_sheets AS (
    SELECT *
    FROM query_sheets
    {answer_filter_sql}
)
"""
        return cte, [*values, q, f"%{q}%"]

    def _sql_answer_sheet_rows_and_counts(
        self,
        params: dict[str, str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, int, int, int]:
        cte, values = self._sql_answer_sheet_cte(params)
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    {cte}
                    , selected_sheets AS (
                        SELECT *
                        FROM filtered_sheets
                        ORDER BY
                            CASE WHEN reviewed_count < question_count THEN 0 ELSE 1 END,
                            CASE WHEN blocked_count > 0 OR needs_review_count > 0 THEN 0 ELSE 1 END,
                            category, subject, year_sort DESC, ordinal_sort DESC, answer_role, sheet_key
                        LIMIT %s
                    ),
                    counts AS (
                        SELECT
                            COALESCE(sum(question_count), 0)::integer AS eligible_count,
                            COALESCE(sum(reviewed_count), 0)::integer AS reviewed_count,
                            count(*)::integer AS sheet_count,
                            (SELECT COALESCE(sum(question_count), 0)::integer FROM filtered_sheets) AS filtered_count
                        FROM query_sheets
                    )
                    SELECT
                        e.raw_candidate_json,
                        counts.eligible_count,
                        counts.reviewed_count,
                        counts.sheet_count,
                        counts.filtered_count
                    FROM counts
                    LEFT JOIN selected_sheets s ON true
                    LEFT JOIN eligible e ON s.sheet_key = e.sheet_key
                    ORDER BY e.category, e.subject, e.year_sort DESC, e.ordinal_sort DESC, e.answer_role, e.sheet_key, e.question_sort, e.candidate_key
                    """,
                    [*values, limit],
                )
                rows: list[dict[str, Any]] = []
                eligible_count = 0
                reviewed_count = 0
                sheet_count = 0
                filtered_count = 0
                for raw_candidate, row_eligible_count, row_reviewed_count, row_sheet_count, row_filtered_count in cur.fetchall():
                    eligible_count = int(row_eligible_count or 0)
                    reviewed_count = int(row_reviewed_count or 0)
                    sheet_count = int(row_sheet_count or 0)
                    filtered_count = int(row_filtered_count or 0)
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
        return rows, eligible_count, reviewed_count, sheet_count, filtered_count

    def filtered_answer_payloads_sql(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500

        rows, eligible_count, reviewed_count, sheet_count, filtered_count = self._sql_answer_sheet_rows_and_counts(params, limit)
        keys = [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]
        issues_by_key = self._sql_issue_map(keys)
        latest_reviews, review_counts, latest_reset_reviews = self._sql_question_review_maps(keys)
        latest_group_reviews = self._sql_group_review_maps(keys)
        latest_answer_reviews, answer_review_counts, _answer_reset_reviews = self._sql_latest_event_maps(
            "exam.answer_review_events",
            keys,
            reset_actions=RESET_REVIEW_ACTIONS,
        )
        latest_ai_reviews, ai_review_counts, _ai_reset_reviews = self._sql_latest_event_maps(
            "exam.question_ai_review_events",
            keys,
            reset_actions=AI_RESET_REVIEW_ACTIONS,
            ai=True,
        )

        sheet_items: dict[str, list[dict[str, Any]]] = {}
        for item in rows:
            key = item["candidate_key"]
            question_review = latest_reviews.get(key)
            if not question_review or question_review.get("action") not in {"accept", "unblock"}:
                continue
            sheet_items.setdefault(self.answer_sheet_key(item), []).append(item)
        sheets = [
            self.answer_sheet_payload(
                items,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            for items in sheet_items.values()
        ]
        sheets.sort(
            key=lambda sheet: (
                0
                if int(sheet.get("reviewed_count") or 0) < int(sheet.get("reviewable_question_count") or sheet.get("question_count") or 0)
                else 1,
                0 if (sheet.get("answer_gate_status") or "pass") != "pass" else 1,
                str((sheet.get("metadata") or {}).get("normalized_category_name") or ""),
                str((sheet.get("metadata") or {}).get("normalized_subject_name") or ""),
                int_or_zero((sheet.get("metadata") or {}).get("year")),
                int_or_zero((sheet.get("metadata") or {}).get("exam_ordinal")),
                sheet.get("answer_role_label") or "",
            )
        )
        return {
            "candidates": sheets[:limit],
            "eligible_count": eligible_count,
            "filtered_count": filtered_count,
            "sheet_count": sheet_count,
            "returned_count": len(sheets[:limit]),
            "reviewed_count": reviewed_count,
            "facets": self.sql_facets(params),
            "candidate_data": self.candidate_data_status(),
        }

    def filtered_group_payloads(self, params: dict[str, str]) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self.filtered_group_payloads_sql(params)
        return self.filtered_group_payloads_from_rows(params, self.candidates, self.facets(params))

    def filtered_group_payloads_sql(self, params: dict[str, str]) -> dict[str, Any]:
        rows = self._sql_group_candidate_rows(params)
        keys = [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]
        issues_by_key = self._sql_issue_map(keys)
        latest_reviews, review_counts, latest_reset_reviews = self._sql_question_review_maps(keys)
        latest_group_reviews = self._sql_group_review_maps(keys)
        latest_answer_reviews, answer_review_counts, _answer_reset_reviews = self._sql_latest_event_maps(
            "exam.answer_review_events",
            keys,
            reset_actions=RESET_REVIEW_ACTIONS,
        )
        latest_ai_reviews, ai_review_counts, _ai_reset_reviews = self._sql_latest_event_maps(
            "exam.question_ai_review_events",
            keys,
            reset_actions=AI_RESET_REVIEW_ACTIONS,
            ai=True,
        )
        return self.filtered_group_payloads_from_rows(
            params,
            rows,
            self.sql_facets(params),
            issues_by_key=issues_by_key,
            latest_reviews=latest_reviews,
            review_counts=review_counts,
            latest_reset_reviews=latest_reset_reviews,
            latest_answer_reviews=latest_answer_reviews,
            answer_review_counts=answer_review_counts,
            latest_ai_reviews=latest_ai_reviews,
            ai_review_counts=ai_review_counts,
            latest_group_reviews=latest_group_reviews,
        )

    def filtered_group_payloads_from_rows(
        self,
        params: dict[str, str],
        rows: list[dict[str, Any]],
        facets: dict[str, list[str]],
        *,
        issues_by_key: dict[str, list[dict[str, Any]]] | None = None,
        latest_reviews: dict[str, dict[str, Any]] | None = None,
        review_counts: dict[str, int] | None = None,
        latest_reset_reviews: dict[str, dict[str, Any]] | None = None,
        latest_answer_reviews: dict[str, dict[str, Any]] | None = None,
        answer_review_counts: dict[str, int] | None = None,
        latest_ai_reviews: dict[str, dict[str, Any]] | None = None,
        ai_review_counts: dict[str, int] | None = None,
        latest_group_reviews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        q = (params.get("q") or "").strip().lower()
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500
        payloads_by_key: dict[str, list[dict[str, Any]]] = {}
        inferred_candidate_keys: set[str] = set()
        suspect_count = 0
        payloads: list[dict[str, Any]] = []
        category_filter = params.get("category") or ""
        subject_filter = params.get("subject") or ""
        year_filter = params.get("year") or ""
        ordinal_filter = params.get("ordinal") or ""
        for item in rows:
            metadata = item.get("metadata") or {}
            category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
            subject = str(metadata.get("normalized_subject_name") or "")
            year = str(metadata.get("year") or "")
            ordinal = str(metadata.get("exam_ordinal") or "")
            if category_filter and not category_matches_filter(category, category_filter):
                continue
            if subject_filter and subject != subject_filter:
                continue
            if year_filter and year != year_filter:
                continue
            if ordinal_filter and ordinal != ordinal_filter:
                continue
            payload = self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            payloads.append(payload)
        for group_items in self.inferred_continuation_groups(payloads):
            suspect_count += len(group_items)
            inferred_candidate_keys.update(
                str(item.get("candidate_key") or "")
                for item in group_items
                if item.get("candidate_key")
            )
            payloads_by_key[self.group_sheet_key(group_items[0])] = group_items
        for payload in payloads:
            if str(payload.get("candidate_key") or "") in inferred_candidate_keys and not str(payload.get("group_ref") or "").strip():
                continue
            reasons = self.group_suspect_reasons(payload)
            group_review_action = (payload.get("group_review") or {}).get("action")
            if group_review_action in GROUP_REVIEW_ACTIONS:
                payloads_by_key.setdefault(self.group_sheet_key(payload), []).append(payload)
                continue
            if not reasons:
                continue
            if GROUP_CONTINUATION_RE.search(str(payload.get("stem") or "")) and not str(payload.get("group_ref") or "").strip():
                continue
            if str(payload.get("inferred_group_ref") or "").strip() and not str(payload.get("group_ref") or "").strip():
                continue
            payloads_by_key.setdefault(self.group_sheet_key(payload), []).append(payload)
        sheets = [
            self.group_sheet_payload(
                items,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            for items in payloads_by_key.values()
        ]
        sheets_by_semantic_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        status_rank = {"confirmed_group": 4, "confirmed_not_group": 3, "reviewed": 2, "unreviewed": 1}
        for sheet in sheets:
            metadata = sheet.get("metadata") or {}
            group_range = str(sheet.get("group_ref") or sheet.get("inferred_group_ref") or sheet.get("group_label") or "")
            semantic_key = (
                str(metadata.get("normalized_category_name") or metadata.get("group_name") or ""),
                str(metadata.get("normalized_subject_name") or ""),
                str(metadata.get("year") or ""),
                str(metadata.get("exam_ordinal") or ""),
                group_range,
            )
            current_sheet = sheets_by_semantic_key.get(semantic_key)
            if not current_sheet:
                sheets_by_semantic_key[semantic_key] = sheet
                continue
            current_score = (
                status_rank.get(str(current_sheet.get("group_review_status") or "unreviewed"), 0),
                1 if current_sheet.get("group_ref") else 0,
                1 if str(current_sheet.get("group_sheet_key") or current_sheet.get("candidate_key") or "").startswith("group_ref|") else 0,
                len(current_sheet.get("rows") or []),
            )
            next_score = (
                status_rank.get(str(sheet.get("group_review_status") or "unreviewed"), 0),
                1 if sheet.get("group_ref") else 0,
                1 if str(sheet.get("group_sheet_key") or sheet.get("candidate_key") or "").startswith("group_ref|") else 0,
                len(sheet.get("rows") or []),
            )
            if next_score > current_score:
                sheets_by_semantic_key[semantic_key] = sheet
        sheets = list(sheets_by_semantic_key.values())
        group_review_status = params.get("groupReviewStatus") or ""
        if group_review_status:
            if group_review_status == "reviewed":
                sheets = [sheet for sheet in sheets if sheet.get("group_review_status") != "unreviewed"]
            else:
                sheets = [sheet for sheet in sheets if sheet.get("group_review_status") == group_review_status]
        if q:
            sheets = [
                sheet for sheet in sheets
                if q in " ".join(
                    str(value or "")
                    for value in [
                        sheet.get("group_sheet_key"),
                        sheet.get("group_ref"),
                        sheet.get("group_label"),
                        sheet.get("shared_stem"),
                        (sheet.get("metadata") or {}).get("normalized_category_name"),
                        (sheet.get("metadata") or {}).get("normalized_subject_name"),
                        (sheet.get("metadata") or {}).get("year"),
                        (sheet.get("metadata") or {}).get("exam_ordinal"),
                        json.dumps(sheet.get("reason_counts") or {}, ensure_ascii=False),
                            *[
                                " ".join(
                                    str(row.get(field) or "")
                                    for field in ("candidate_key", "question_number", "stem", "group_ref")
                                )
                                + " "
                                + json.dumps(row.get("options") or [], ensure_ascii=False)
                                for row in sheet.get("rows") or []
                            ],
                    ]
                ).lower()
            ]
        sheets.sort(
            key=lambda sheet: (
                0 if str(sheet.get("group_review_status") or "unreviewed") == "unreviewed" else 1,
                0 if str(sheet.get("gate_status") or "") in {"unbound_suspect", "inferred_continuation"} else 1,
                str((sheet.get("metadata") or {}).get("normalized_category_name") or ""),
                str((sheet.get("metadata") or {}).get("normalized_subject_name") or ""),
                -int_or_zero((sheet.get("metadata") or {}).get("year")),
                -int_or_zero((sheet.get("metadata") or {}).get("exam_ordinal")),
                0 if sheet.get("group_ref") else 1,
                str(sheet.get("group_label") or ""),
            )
        )
        return {
            "candidates": sheets[:limit],
            "total_count": len(rows),
            "filtered_count": len(sheets),
            "returned_count": len(sheets[:limit]),
            "group_count": len(sheets),
            "suspect_question_count": suspect_count,
            "facets": facets,
            "candidate_data": self.candidate_data_status(),
        }

    def load_preferences(self, reviewer: str) -> dict[str, Any]:
        preferences = self._load_file_preferences().get(reviewer, {})
        db_preferences = self._load_db_preferences(reviewer)
        if db_preferences:
            preferences.update(db_preferences)
        return preferences

    def save_preferences(self, reviewer: str, preferences: dict[str, Any]) -> None:
        preferences = dict(preferences)
        file_preferences = self._load_file_preferences()
        file_preferences[reviewer] = preferences
        with self.preference_path.open("w", encoding="utf-8") as f:
            json.dump(file_preferences, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        self._save_db_preferences(reviewer, preferences)

    def _load_file_preferences(self) -> dict[str, dict[str, Any]]:
        if not self.preference_path.exists():
            return {}
        try:
            with self.preference_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        return {}

    def _load_db_preferences(self, reviewer: str) -> dict[str, Any]:
        if psycopg is None or not self.database_url:
            return {}
        try:
            connection_factory = self._sql_connect if self.sql_review_enabled else lambda: psycopg.connect(self.database_url, connect_timeout=2)
            with connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT preferences_json FROM exam.review_ui_preferences WHERE reviewer = %s",
                        (reviewer,),
                    )
                    row = cur.fetchone()
                    if row and isinstance(row[0], dict):
                        return row[0]
        except Exception:
            return {}
        return {}

    def _save_db_preferences(self, reviewer: str, preferences: dict[str, Any]) -> None:
        if psycopg is None or not self.database_url:
            return
        try:
            connection_factory = self._sql_connect if self.sql_review_enabled else lambda: psycopg.connect(self.database_url, connect_timeout=2)
            with connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO exam.review_ui_preferences (reviewer, preferences_json, updated_at)
                        VALUES (%s, %s::jsonb, now())
                        ON CONFLICT (reviewer) DO UPDATE
                        SET preferences_json = EXCLUDED.preferences_json,
                            updated_at = now()
                        """,
                        (reviewer, json.dumps(preferences, ensure_ascii=False)),
                    )
                conn.commit()
        except Exception:
            return

    def _insert_sql_question_review_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.question_review_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        action = str(event.get("action") or "")
        is_non_question_review_action = action in NON_QUESTION_REVIEW_ACTIONS
        status = {
            "accept": "accepted",
            "correct": "corrected",
            "needs_review": "needs_review",
            "block": "blocked",
            "reviewed": "accepted",
            "unblock": "accepted",
            "comment": "needs_review",
            "exclude": "excluded",
            "unreviewed": "unreviewed",
            "reset_review": "unreviewed",
        }.get(action, "unreviewed")
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_review_events (
                        candidate_id,
                        candidate_key,
                        reviewer,
                        action,
                        corrected_candidate_json,
                        event_json,
                        notes,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("reviewer"),
                        event.get("action"),
                        Jsonb(event.get("correction")) if event.get("correction") is not None else None,
                        Jsonb(event),
                        event.get("notes") or "",
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                event_id = int(row[0])
                if not is_non_question_review_action:
                    cur.execute(
                        """
                        UPDATE exam.question_candidates
                        SET review_status = %s,
                            updated_at = now()
                        WHERE candidate_key = %s
                        """,
                        (status, event.get("candidate_key")),
                    )
                    self._enqueue_formal_sync(cur, str(event.get("candidate_key") or ""))
            conn.commit()
        self._sql_facets_cache.clear()
        return {"ok": True, "sql_primary": True, "table": "exam.question_review_events", "event_id": event_id}

    def _insert_sql_group_review_events(self, events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if not self.sql_review_enabled:
            return {
                str(event.get("candidate_key") or ""): {"ok": True, "sql_primary": False, "table": "exam.question_review_events"}
                for event in events
            }
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        storage_by_key: dict[str, dict[str, Any]] = {}
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                for event in events:
                    key = str(event.get("candidate_key") or "")
                    cur.execute(
                        """
                        INSERT INTO exam.question_review_events (
                            candidate_id,
                            candidate_key,
                            reviewer,
                            action,
                            corrected_candidate_json,
                            event_json,
                            notes,
                            created_at
                        )
                        SELECT id, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                        FROM exam.question_candidates
                        WHERE candidate_key = %s
                        RETURNING id
                        """,
                        (
                            key,
                            event.get("reviewer"),
                            event.get("action"),
                            Jsonb(event.get("correction")) if event.get("correction") is not None else None,
                            Jsonb(event),
                            event.get("notes") or "",
                            event.get("created_at"),
                            key,
                        ),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise SqlWriteError(f"candidate_key not found in SQL: {key}")
                    storage_by_key[key] = {
                        "ok": True,
                        "sql_primary": True,
                        "table": "exam.question_review_events",
                        "event_id": int(row[0]),
                    }
            conn.commit()
        self._sql_facets_cache.clear()
        return storage_by_key

    def _insert_sql_question_review_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []
        if not self.sql_review_enabled:
            return [{"ok": True, "sql_primary": False, "table": "exam.question_review_events"} for _event in events]
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        status_by_action = {
            "accept": "accepted",
            "correct": "corrected",
            "needs_review": "needs_review",
            "block": "blocked",
            "reviewed": "accepted",
            "unblock": "accepted",
            "comment": "needs_review",
            "exclude": "excluded",
            "unreviewed": "unreviewed",
            "reset_review": "unreviewed",
        }
        storage_rows: list[dict[str, Any]] = []
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                for event in events:
                    key = str(event.get("candidate_key") or "")
                    action = str(event.get("action") or "")
                    cur.execute(
                        """
                        INSERT INTO exam.question_review_events (
                            candidate_id,
                            candidate_key,
                            reviewer,
                            action,
                            corrected_candidate_json,
                            event_json,
                            notes,
                            created_at
                        )
                        SELECT id, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                        FROM exam.question_candidates
                        WHERE candidate_key = %s
                        RETURNING id
                        """,
                        (
                            key,
                            event.get("reviewer"),
                            action,
                            Jsonb(event.get("correction")) if event.get("correction") is not None else None,
                            Jsonb(event),
                            event.get("notes") or "",
                            event.get("created_at"),
                            key,
                        ),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise SqlWriteError(f"candidate_key not found in SQL: {key}")
                    cur.execute(
                        """
                        UPDATE exam.question_candidates
                        SET review_status = %s,
                            updated_at = now()
                        WHERE candidate_key = %s
                        """,
                        (status_by_action.get(action, "unreviewed"), key),
                    )
                    self._enqueue_formal_sync(cur, key)
                    storage_rows.append(
                        {"ok": True, "sql_primary": True, "table": "exam.question_review_events", "event_id": int(row[0])}
                    )
            conn.commit()
        self._sql_facets_cache.clear()
        return storage_rows

    def _insert_sql_answer_review_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.answer_review_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.answer_review_events (
                        candidate_id,
                        candidate_key,
                        answer_source_registry_key,
                        reviewer,
                        action,
                        reviewed_answer_json,
                        corrected_answer_json,
                        event_json,
                        notes,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("answer_source_registry_key") or None,
                        event.get("reviewer"),
                        event.get("action"),
                        Jsonb(event.get("reviewed_answer")) if event.get("reviewed_answer") is not None else None,
                        Jsonb(event.get("corrected_answer")) if event.get("corrected_answer") is not None else None,
                        Jsonb(event),
                        event.get("notes") or "",
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                event_id = int(row[0])
                self._enqueue_formal_sync(cur, str(event.get("candidate_key") or ""))
            conn.commit()
        self._sql_facets_cache.clear()
        return {"ok": True, "sql_primary": True, "table": "exam.answer_review_events", "event_id": event_id}

    def _insert_sql_answer_review_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []
        if not self.sql_review_enabled:
            return [{"ok": True, "sql_primary": False, "table": "exam.answer_review_events"} for _event in events]
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        storage_rows: list[dict[str, Any]] = []
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                for event in events:
                    cur.execute(
                        """
                        INSERT INTO exam.answer_review_events (
                            candidate_id,
                            candidate_key,
                            answer_source_registry_key,
                            reviewer,
                            action,
                            reviewed_answer_json,
                            corrected_answer_json,
                            event_json,
                            notes,
                            created_at
                        )
                        SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                        FROM exam.question_candidates
                        WHERE candidate_key = %s
                        RETURNING id
                        """,
                        (
                            event.get("candidate_key"),
                            event.get("answer_source_registry_key") or None,
                            event.get("reviewer"),
                            event.get("action"),
                            Jsonb(event.get("reviewed_answer")) if event.get("reviewed_answer") is not None else None,
                            Jsonb(event.get("corrected_answer")) if event.get("corrected_answer") is not None else None,
                            Jsonb(event),
                            event.get("notes") or "",
                            event.get("created_at"),
                            event.get("candidate_key"),
                        ),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                    storage_rows.append({"ok": True, "sql_primary": True, "table": "exam.answer_review_events", "event_id": int(row[0])})
                    self._enqueue_formal_sync(cur, str(event.get("candidate_key") or ""))
            conn.commit()
        self._sql_facets_cache.clear()
        return storage_rows

    def _insert_sql_ai_review_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.question_ai_review_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        audit = event.get("audit") if isinstance(event.get("audit"), dict) else {"status": "pass"}
        audit_status = str(audit.get("status") or "pass")
        if audit_status in {"blocked", "block"}:
            audit_status = "block"
        elif audit_status != "needs_review":
            audit_status = "pass"
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_ai_review_events (
                        candidate_id,
                        candidate_key,
                        action,
                        reviewer,
                        provider,
                        model_name,
                        prompt_version,
                        input_hash,
                        audit_status,
                        recommended_action,
                        audit_json,
                        event_json,
                        notes,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("action") or "ai_audit",
                        event.get("reviewer"),
                        event.get("provider") or audit.get("provider") or "local",
                        event.get("model") or audit.get("model"),
                        event.get("prompt_version"),
                        event.get("input_hash"),
                        audit_status,
                        audit.get("recommended_action"),
                        Jsonb(audit),
                        Jsonb(event),
                        event.get("notes") or "",
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                event_id = int(row[0])
            conn.commit()
        self._sql_facets_cache.clear()
        return {"ok": True, "sql_primary": True, "table": "exam.question_ai_review_events", "event_id": event_id}

    def _insert_sql_ai_feedback_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.question_ai_feedback_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_ai_feedback_events (
                        candidate_id,
                        candidate_key,
                        ai_review_event_id,
                        ai_review_ref,
                        audit_scope,
                        rating,
                        reviewer,
                        reason,
                        feedback_json,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("ai_review_event_id"),
                        event.get("ai_review_ref"),
                        event.get("audit_scope"),
                        event.get("rating"),
                        event.get("reviewer") or "local",
                        event.get("reason") or "",
                        Jsonb(event),
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                event_id = int(row[0])
            conn.commit()
        return {
            "ok": True,
            "sql_primary": True,
            "table": "exam.question_ai_feedback_events",
            "event_id": event_id,
        }

    def _insert_sql_ai_learning_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.question_ai_learning_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_ai_learning_events (
                        candidate_id,
                        candidate_key,
                        ai_review_event_id,
                        ai_review_ref,
                        audit_scope,
                        reviewer,
                        reason,
                        learning_json,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("ai_review_event_id"),
                        event.get("ai_review_ref"),
                        event.get("audit_scope"),
                        event.get("reviewer") or "local",
                        event.get("reason") or "",
                        Jsonb(event),
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                event_id = int(row[0])
            conn.commit()
        return {
            "ok": True,
            "sql_primary": True,
            "table": "exam.question_ai_learning_events",
            "event_id": event_id,
        }

    def _insert_sql_correction_feedback_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.question_correction_feedback_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_correction_feedback_events (
                        candidate_id,
                        candidate_key,
                        human_review_event_id,
                        human_answer_review_event_id,
                        feedback_id,
                        source_kind,
                        audit_scope,
                        lane_key,
                        actor_kind,
                        reviewer,
                        event_ref,
                        changed_fields,
                        before_json,
                        after_json,
                        diff_json,
                        evidence_json,
                        ai_task_json,
                        event_json,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    ON CONFLICT (feedback_id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("human_review_event_id"),
                        event.get("human_answer_review_event_id"),
                        event.get("feedback_id"),
                        event.get("source_kind"),
                        event.get("scope"),
                        event.get("lane") or None,
                        event.get("actor_kind"),
                        event.get("reviewer") or None,
                        event.get("event_ref") or None,
                        Jsonb(event.get("changed_fields") or []),
                        Jsonb(event.get("before") or {}),
                        Jsonb(event.get("after") or {}),
                        Jsonb(event.get("diff") or []),
                        Jsonb(event.get("evidence") or []),
                        Jsonb(event.get("ai_task") or {}),
                        Jsonb(event),
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if row:
                    event_id = int(row[0])
                else:
                    cur.execute(
                        "SELECT id FROM exam.question_correction_feedback_events WHERE feedback_id = %s",
                        (event.get("feedback_id"),),
                    )
                    existing = cur.fetchone()
                    if not existing:
                        raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                    event_id = int(existing[0])
            conn.commit()
        return {
            "ok": True,
            "sql_primary": True,
            "table": "exam.question_correction_feedback_events",
            "event_id": event_id,
        }

    def _ensure_formal_sync_schema(self, conn: Any) -> None:
        if self._formal_sync_schema_ready:
            return
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE exam.questions
                    ADD COLUMN IF NOT EXISTS normalized_text TEXT,
                    ADD COLUMN IF NOT EXISTS display_text TEXT,
                    ADD COLUMN IF NOT EXISTS question_markup_json JSONB,
                    ADD COLUMN IF NOT EXISTS question_raw_json JSONB,
                    ADD COLUMN IF NOT EXISTS human_corrected_json JSONB,
                    ADD COLUMN IF NOT EXISTS source_page_start INTEGER,
                    ADD COLUMN IF NOT EXISTS source_page_end INTEGER,
                    ADD COLUMN IF NOT EXISTS source_bbox JSONB,
                    ADD COLUMN IF NOT EXISTS parse_confidence NUMERIC(5,4);

                ALTER TABLE exam.question_options
                    ADD COLUMN IF NOT EXISTS normalized_text TEXT,
                    ADD COLUMN IF NOT EXISTS display_text TEXT,
                    ADD COLUMN IF NOT EXISTS option_markup_json JSONB,
                    ADD COLUMN IF NOT EXISTS option_raw_json JSONB,
                    ADD COLUMN IF NOT EXISTS human_corrected_json JSONB;

                ALTER TABLE exam.question_groups
                    ADD COLUMN IF NOT EXISTS display_markup_json JSONB,
                    ADD COLUMN IF NOT EXISTS asset_policy_json JSONB,
                    ADD COLUMN IF NOT EXISTS source_page_start INTEGER,
                    ADD COLUMN IF NOT EXISTS source_page_end INTEGER,
                    ADD COLUMN IF NOT EXISTS source_bbox JSONB,
                    ADD COLUMN IF NOT EXISTS group_question_range TEXT;

                ALTER TABLE exam.question_assets
                    ADD COLUMN IF NOT EXISTS source_mineru_block_id TEXT,
                    ADD COLUMN IF NOT EXISTS display_order INTEGER,
                    ADD COLUMN IF NOT EXISTS asset_quality_status TEXT NOT NULL DEFAULT 'unreviewed';

                ALTER TABLE exam.question_assets
                    DROP CONSTRAINT IF EXISTS question_assets_role_check;

                ALTER TABLE exam.question_assets
                    ADD CONSTRAINT question_assets_role_check
                    CHECK (role IN (
                        'page_image',
                        'figure',
                        'stem_figure',
                        'table',
                        'table_structured',
                        'table_manual_screenshot',
                        'option_image',
                        'source_pdf_region',
                        'answer_explanation_image',
                        'group_shared_asset',
                        'other'
                    ));
                """
            )
        self._formal_sync_schema_ready = True

    def _jsonb_or_none(self, value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        return Jsonb(value) if Jsonb is not None else value

    def _formal_unready_status(self, question_event: dict[str, Any] | None, answer_event: dict[str, Any] | None) -> str:
        action = str((question_event or {}).get("action") or "")
        answer_action = str((answer_event or {}).get("action") or "")
        if action == "exclude":
            return "excluded"
        if action == "block" or answer_action == "block":
            return "blocked"
        if action in RESET_REVIEW_ACTIONS or answer_action in RESET_REVIEW_ACTIONS:
            return "unreviewed"
        if action == "needs_review" or answer_action == "needs_review":
            return "needs_review"
        return "review_drift"

    def _withdraw_formal_candidate(
        self,
        cur: Any,
        candidate_key: str,
        question_event: dict[str, Any] | None,
        answer_event: dict[str, Any] | None,
    ) -> bool:
        status = self._formal_unready_status(question_event, answer_event)
        cur.execute(
            """
            UPDATE exam.questions
            SET review_status = %s
            WHERE question_key = %s
            RETURNING id
            """,
            (status, candidate_key),
        )
        row = cur.fetchone()
        if not row:
            return False
        question_id = int(row[0])
        cur.execute("DELETE FROM exam.answers WHERE question_id = %s", (question_id,))
        return True

    def _upsert_formal_candidate_rows(
        self,
        cur: Any,
        question_rows: list[dict[str, object]],
        option_rows: list[dict[str, object]],
        answer_rows: list[dict[str, object]],
        asset_rows: list[dict[str, object]],
    ) -> list[str]:
        promoted: list[str] = []
        options_by_key: dict[str, list[dict[str, object]]] = {}
        answers_by_key: dict[str, list[dict[str, object]]] = {}
        assets_by_key: dict[str, list[dict[str, object]]] = {}
        for row in option_rows:
            options_by_key.setdefault(str(row.get("question_key") or ""), []).append(row)
        for row in answer_rows:
            answers_by_key.setdefault(str(row.get("question_key") or ""), []).append(row)
        for row in asset_rows:
            assets_by_key.setdefault(str(row.get("question_key") or ""), []).append(row)

        for row in question_rows:
            question_key = str(row.get("question_key") or "")
            source_registry_key = str(row.get("source_registry_key") or "")
            group_key = str(row.get("group_key") or "")
            group_id: int | None = None
            if group_key:
                cur.execute(
                    """
                    INSERT INTO exam.question_groups (
                        official_document_id,
                        group_key,
                        shared_stem_json,
                        review_status
                    )
                    SELECT id, %s, %s, 'accepted'
                    FROM exam.official_documents
                    WHERE registry_key = %s
                    ON CONFLICT (group_key) DO UPDATE
                    SET shared_stem_json = COALESCE(exam.question_groups.shared_stem_json, EXCLUDED.shared_stem_json),
                        review_status = EXCLUDED.review_status
                    RETURNING id
                    """,
                    (group_key, Jsonb({"group_ref": group_key}), source_registry_key),
                )
                group_row = cur.fetchone()
                group_id = int(group_row[0]) if group_row else None
            cur.execute(
                """
                INSERT INTO exam.questions (
                    official_document_id,
                    question_group_id,
                    question_key,
                    question_number,
                    question_text,
                    normalized_text,
                    display_text,
                    question_markup_json,
                    question_raw_json,
                    human_corrected_json,
                    question_json,
                    parser_version,
                    review_status
                )
                SELECT
                    od.id,
                    %s,
                    %s,
                    %s,
                    NULLIF(%s, ''),
                    NULLIF(%s, ''),
                    NULLIF(%s, ''),
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'accepted'
                FROM exam.official_documents od
                WHERE od.registry_key = %s
                ON CONFLICT (question_key) DO UPDATE
                SET official_document_id = EXCLUDED.official_document_id,
                    question_group_id = EXCLUDED.question_group_id,
                    question_number = EXCLUDED.question_number,
                    question_text = EXCLUDED.question_text,
                    normalized_text = EXCLUDED.normalized_text,
                    display_text = EXCLUDED.display_text,
                    question_markup_json = EXCLUDED.question_markup_json,
                    question_raw_json = EXCLUDED.question_raw_json,
                    human_corrected_json = EXCLUDED.human_corrected_json,
                    question_json = EXCLUDED.question_json,
                    parser_version = EXCLUDED.parser_version,
                    review_status = EXCLUDED.review_status
                RETURNING id
                """,
                (
                    group_id,
                    question_key,
                    str(row.get("question_number") or ""),
                    str(row.get("question_text") or ""),
                    str(row.get("normalized_text") or ""),
                    str(row.get("display_text") or ""),
                    self._jsonb_or_none(row.get("question_markup_json")),
                    self._jsonb_or_none(row.get("question_raw_json")),
                    self._jsonb_or_none(row.get("human_corrected_json")),
                    self._jsonb_or_none(row.get("question_json")),
                    str(row.get("parser_version") or "unknown"),
                    source_registry_key,
                ),
            )
            question_row = cur.fetchone()
            if not question_row:
                continue
            question_id = int(question_row[0])

            option_labels: list[str] = []
            for option in options_by_key.get(question_key, []):
                label = str(option.get("option_label") or "").strip().upper()
                if not label:
                    continue
                option_labels.append(label)
                cur.execute(
                    """
                    INSERT INTO exam.question_options (
                        question_id,
                        option_label,
                        option_text,
                        normalized_text,
                        display_text,
                        option_markup_json,
                        option_raw_json,
                        human_corrected_json,
                        option_json
                    )
                    VALUES (%s, %s, NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), %s, %s, %s, %s)
                    ON CONFLICT (question_id, option_label) DO UPDATE
                    SET option_text = EXCLUDED.option_text,
                        normalized_text = EXCLUDED.normalized_text,
                        display_text = EXCLUDED.display_text,
                        option_markup_json = EXCLUDED.option_markup_json,
                        option_raw_json = EXCLUDED.option_raw_json,
                        human_corrected_json = EXCLUDED.human_corrected_json,
                        option_json = EXCLUDED.option_json
                    """,
                    (
                        question_id,
                        label,
                        str(option.get("option_text") or ""),
                        str(option.get("normalized_text") or ""),
                        str(option.get("display_text") or ""),
                        self._jsonb_or_none(option.get("option_markup_json")),
                        self._jsonb_or_none(option.get("option_raw_json")),
                        self._jsonb_or_none(option.get("human_corrected_json")),
                        self._jsonb_or_none(option.get("option_json")),
                    ),
                )
            if option_labels:
                cur.execute(
                    "DELETE FROM exam.question_options WHERE question_id = %s AND option_label <> ALL(%s)",
                    (question_id, option_labels),
                )
            else:
                cur.execute("DELETE FROM exam.question_options WHERE question_id = %s", (question_id,))

            cur.execute("DELETE FROM exam.answers WHERE question_id = %s", (question_id,))
            for answer in answers_by_key.get(question_key, []):
                cur.execute(
                    """
                    INSERT INTO exam.answers (
                        question_id,
                        answer_source_document_id,
                        answer_value,
                        answer_json,
                        is_correction
                    )
                    SELECT
                        %s,
                        answer_doc.id,
                        NULLIF(%s, ''),
                        %s,
                        %s
                    FROM (SELECT 1) seed
                    LEFT JOIN exam.official_documents answer_doc
                      ON answer_doc.registry_key = NULLIF(%s, '')
                    """,
                    (
                        question_id,
                        str(answer.get("answer_value") or ""),
                        self._jsonb_or_none(answer.get("answer_json")) or Jsonb({}),
                        str(answer.get("is_correction") or "").lower() == "true",
                        str(answer.get("answer_source_registry_key") or ""),
                    ),
                )

            cur.execute("DELETE FROM exam.question_assets WHERE question_id = %s", (question_id,))
            asset_keys: list[str] = []
            for index, asset in enumerate(assets_by_key.get(question_key, []), start=1):
                asset_key = str(asset.get("asset_key") or "").strip()
                if not asset_key:
                    continue
                asset_keys.append(asset_key)
                cur.execute(
                    """
                    INSERT INTO exam.assets (
                        asset_key,
                        asset_type,
                        asset_path,
                        relative_asset_path,
                        mime_type
                    )
                    VALUES (%s, %s, %s, NULLIF(%s, ''), NULLIF(%s, ''))
                    ON CONFLICT (asset_key) DO UPDATE
                    SET asset_type = EXCLUDED.asset_type,
                        asset_path = EXCLUDED.asset_path,
                        relative_asset_path = EXCLUDED.relative_asset_path,
                        mime_type = EXCLUDED.mime_type
                    RETURNING id
                    """,
                    (
                        asset_key,
                        str(asset.get("asset_type") or "other"),
                        str(asset.get("asset_path") or ""),
                        str(asset.get("relative_asset_path") or ""),
                        str(asset.get("mime_type") or ""),
                    ),
                )
                asset_id = int(cur.fetchone()[0])
                cur.execute(
                    """
                    INSERT INTO exam.question_assets (
                        question_id,
                        asset_id,
                        role,
                        display_order,
                        asset_quality_status
                    )
                    VALUES (%s, %s, %s, %s, 'accepted')
                    ON CONFLICT (question_id, asset_id, role) DO UPDATE
                    SET display_order = EXCLUDED.display_order,
                        asset_quality_status = EXCLUDED.asset_quality_status
                    """,
                    (
                        question_id,
                        asset_id,
                        str(asset.get("role") or "figure"),
                        int(asset.get("display_order") or index),
                    ),
                )
            if asset_keys:
                cur.execute(
                    """
                    DELETE FROM exam.question_assets qa
                    USING exam.assets a
                    WHERE qa.asset_id = a.id
                      AND qa.question_id = %s
                      AND a.asset_key <> ALL(%s)
                    """,
                    (question_id, asset_keys),
                )
            else:
                cur.execute("DELETE FROM exam.question_assets WHERE question_id = %s", (question_id,))
            promoted.append(question_key)
        return promoted

    def sync_formal_candidates(self, candidate_keys: list[str]) -> dict[str, Any]:
        keys = list(dict.fromkeys(str(key or "") for key in candidate_keys if str(key or "").strip()))
        if not keys or not self.sql_review_enabled:
            return {"ok": True, "enabled": False, "promoted": [], "withdrawn": [], "skipped": [], "errors": []}
        if formal_promote is None or Jsonb is None:
            return {
                "ok": False,
                "enabled": True,
                "promoted": [],
                "withdrawn": [],
                "skipped": [],
                "errors": ["formal promotion helpers are unavailable"],
            }
        try:
            candidates = self._candidates_by_key_sql(keys)
            issues = self._sql_issue_map(keys)
            question_reviews, _question_counts, question_resets = self._sql_question_review_maps(keys)
            answer_reviews, _answer_counts, answer_resets = self._sql_latest_event_maps(
                "exam.answer_review_events",
                keys,
                reset_actions=RESET_REVIEW_ACTIONS,
            )
            ordered_candidates = [candidates[key] for key in keys if key in candidates]
            question_rows, option_rows, answer_rows, asset_rows, skipped = formal_promote.build_rows(
                ordered_candidates,
                issues,
                question_reviews,
                question_resets,
                answer_reviews,
                answer_resets,
            )
            promoted: list[str] = []
            withdrawn: list[str] = []
            with self._sql_connect() as conn:
                self._ensure_formal_sync_schema(conn)
                with conn.cursor() as cur:
                    promoted = self._upsert_formal_candidate_rows(cur, question_rows, option_rows, answer_rows, asset_rows)
                    promoted_set = set(promoted)
                    for key in keys:
                        if key in promoted_set:
                            continue
                        if self._withdraw_formal_candidate(cur, key, question_reviews.get(key), answer_reviews.get(key)):
                            withdrawn.append(key)
                conn.commit()
            self._sql_facets_cache.clear()
            return {
                "ok": True,
                "enabled": True,
                "promoted": promoted,
                "withdrawn": withdrawn,
                "skipped": skipped,
                "errors": [],
            }
        except Exception as exc:
            return {
                "ok": False,
                "enabled": True,
                "promoted": [],
                "withdrawn": [],
                "skipped": [],
                "errors": [str(exc)],
            }

    def _raw_candidate_for_feedback(self, candidate_key: str) -> dict[str, Any] | None:
        if self.sql_review_enabled:
            return self._candidate_by_key_sql(candidate_key)
        return self.candidate_by_key.get(candidate_key)

    def _feedback_before_snapshot(self, candidate_key: str) -> dict[str, Any]:
        candidate = self._raw_candidate_for_feedback(candidate_key) or {}
        before = question_snapshot(candidate)
        previous = self.current_question_review(candidate_key)
        previous_correction = normalized_correction(previous.get("correction")) if previous else {}
        if previous_correction:
            before = apply_feedback_correction(before, previous_correction)
        return question_snapshot(before)

    @staticmethod
    def _event_is_human_correction(event: dict[str, Any]) -> bool:
        source = str(event.get("source") or "").strip().lower()
        reviewer = str(event.get("reviewer") or "").strip().lower()
        if source in {"ai_suggestion", "ai_auto", "deterministic_rule", "parser_repair"}:
            return False
        if source.startswith(("ai_", "repair_", "backfill_", "parser_", "deterministic_")):
            return False
        if reviewer.startswith(REPAIR_REVIEWER_PREFIXES):
            return False
        return True

    def _append_correction_feedback(
        self,
        *,
        candidate_key: str,
        before: dict[str, Any],
        after: dict[str, Any],
        source_kind: str,
        scope: str,
        lane: str,
        reviewer: str,
        human_review_event_id: int | None,
        event_ref: str,
        evidence: list[dict[str, Any]],
        created_at: str,
    ) -> dict[str, Any] | None:
        try:
            feedback = build_feedback_event(
                candidate_key=candidate_key,
                before=before,
                after=after,
                source_kind=source_kind,
                scope=scope,
                lane=lane,
                reviewer=reviewer,
                event_ref=event_ref,
                evidence=evidence,
                question_number=after.get("question_number") or before.get("question_number"),
                created_at=created_at,
            )
        except NoVisibleChange:
            # Accepting or re-saving an unchanged candidate is not a formatting
            # lesson.  Keep the human review event, but do not manufacture AI
            # feedback from a no-op.
            return None
        except FeedbackContractError:
            raise
        if source_kind == "human_correction" and scope == "answer":
            if human_review_event_id is not None:
                feedback["human_answer_review_event_id"] = human_review_event_id
        elif human_review_event_id is not None:
            feedback["human_review_event_id"] = human_review_event_id
        sql_storage = self._insert_sql_correction_feedback_event(feedback)
        jsonl_storage = self._legacy_jsonl_storage(self.correction_feedback_log, feedback)
        self._correction_feedback_log_signature = file_signature(self.correction_feedback_log)
        self.latest_correction_feedbacks[candidate_key] = feedback
        return {
            "event": feedback,
            "storage": {**sql_storage, "legacy_jsonl_backup": jsonl_storage},
        }

    def _record_question_correction_feedback(
        self,
        event: dict[str, Any],
        *,
        before: dict[str, Any],
        review_storage: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidate_key = str(event.get("candidate_key") or "")
        correction = normalized_correction(event.get("correction"))
        if not candidate_key or not correction or not self._event_is_human_correction(event):
            return None
        after = apply_feedback_correction(before, correction)
        changed_scope = "visual" if any(
            key in correction for key in ("image_refs", "stem_image", "answer_image_refs", "visual_review")
        ) else "group" if any(key in correction for key in ("group_ref", "group_sequence_no")) else "question"
        evidence = [{
            "kind": "human_review",
            "source": str(event.get("review_surface") or event.get("source") or "review_ui"),
            "reference": str(review_storage.get("event_id") or event.get("created_at") or "human_review"),
            "status": "human_confirmed",
        }]
        if isinstance(event.get("manual_asset"), dict):
            asset = event["manual_asset"]
            evidence.append({
                "kind": "human_review",
                "family": "manual_asset",
                "reference": str(asset.get("asset_key") or "manual_asset"),
                "sha256": str(asset.get("sha256") or ""),
                "status": "human_confirmed",
            })
        for row in event.get("evidence") or []:
            if isinstance(row, dict):
                evidence.append(row)
        event_id = review_storage.get("event_id")
        return self._append_correction_feedback(
            candidate_key=candidate_key,
            before=before,
            after=after,
            source_kind="human_correction",
            scope=changed_scope,
            lane=str(event.get("lane") or changed_scope),
            reviewer=str(event.get("reviewer") or "local"),
            human_review_event_id=int(event_id) if event_id not in (None, "") else None,
            event_ref=f"human_review:{event_id or event.get('created_at') or candidate_key}",
            evidence=evidence,
            created_at=str(event.get("created_at") or datetime.now().isoformat(timespec="seconds")),
        )

    def _record_answer_correction_feedback(
        self,
        event: dict[str, Any],
        *,
        before: dict[str, Any],
        review_storage: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidate_key = str(event.get("candidate_key") or "")
        if not candidate_key or not self._event_is_human_correction(event):
            return None
        if "corrected_answer" not in event or event.get("corrected_answer") in (None, ""):
            return None
        after = apply_feedback_correction(before, {"answer": event.get("corrected_answer")})
        event_id = review_storage.get("event_id")
        return self._append_correction_feedback(
            candidate_key=candidate_key,
            before=before,
            after=after,
            source_kind="human_correction",
            scope="answer",
            lane="answer",
            reviewer=str(event.get("reviewer") or "local"),
            human_review_event_id=int(event_id) if event_id not in (None, "") else None,
            event_ref=f"human_answer_review:{event_id or event.get('created_at') or candidate_key}",
            evidence=[{
                "kind": "human_review",
                "source": "answer_review_ui",
                "reference": str(event_id or event.get("created_at") or "human_answer_review"),
                "status": "human_confirmed",
            }],
            created_at=str(event.get("created_at") or datetime.now().isoformat(timespec="seconds")),
        )

    def append_review(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        key = event.get("candidate_key")
        feedback_before = self._feedback_before_snapshot(str(key or "")) if event.get("correction") else None
        if event.get("action") in (NOTE_ACTIONS | {"correct"}):
            _reaffirm_standing_action(event, self.current_question_review(str(key or "")))
        event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        sql_storage = self._insert_sql_question_review_event(event)
        jsonl_storage = self._legacy_jsonl_storage(self.review_log, event)
        self._review_log_signature = file_signature(self.review_log)
        storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
        if feedback_before is not None:
            try:
                feedback_storage = self._record_question_correction_feedback(
                    event,
                    before=feedback_before,
                    review_storage=sql_storage,
                )
            except FeedbackContractError as exc:
                feedback_storage = {"ok": False, "error": str(exc), "status": "not_recorded"}
            if feedback_storage is not None:
                storage["correction_feedback"] = feedback_storage
        if key:
            if event.get("action") not in MOBILE_REVIEW_ACTIONS:
                self.review_counts[key] = self.review_counts.get(key, 0) + 1
            if event.get("action") in MOBILE_REVIEW_ACTIONS:
                pass
            elif event.get("action") in GROUP_REVIEW_ACTIONS:
                self.latest_group_reviews[key] = event
            elif event.get("action") in RESET_REVIEW_ACTIONS:
                self.latest_reviews.pop(key, None)
                self.latest_reset_reviews[key] = event
            elif _note_annotates_pending_reset(event, key, self.latest_reviews, self.latest_reset_reviews):
                # In-memory half of the same rule: a note on a stuck question keeps it stuck.
                self.latest_reset_reviews[key] = _merge_note_into_reset(self.latest_reset_reviews[key], event)
            else:
                self.latest_reviews[key] = event
                self.latest_reset_reviews.pop(key, None)
        formal_sync = None
        if key and event.get("action") not in NON_QUESTION_REVIEW_ACTIONS:
            if self.defer_formal_sync:
                self._wake_formal_sync_worker()
                formal_sync = {"ok": True, "enabled": True, "queued": True}
            else:
                formal_sync = self.sync_formal_candidates([str(key)])
        if formal_sync is not None:
            storage["formal_sync"] = formal_sync
        return {**event, "storage": storage}

    def append_reviews_batch(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_events: list[dict[str, Any]] = []
        created_at = datetime.now().isoformat(timespec="seconds")
        for raw_event in events:
            event = dict(raw_event)
            key = str(event.get("candidate_key") or "")
            if not key:
                continue
            if event.get("action") in (NOTE_ACTIONS | {"correct"}):
                _reaffirm_standing_action(event, self.current_question_review(key))
            event.setdefault("created_at", created_at)
            normalized_events.append(event)
        if not normalized_events:
            return []
        feedback_befores = [
            self._feedback_before_snapshot(str(event.get("candidate_key") or ""))
            if event.get("correction") and self._event_is_human_correction(event)
            else None
            for event in normalized_events
        ]
        sql_storages = self._insert_sql_question_review_events(normalized_events)
        jsonl_storage = self._legacy_jsonl_storage_many(self.review_log, normalized_events)
        self._review_log_signature = file_signature(self.review_log)
        saved: list[dict[str, Any]] = []
        for event, sql_storage, feedback_before in zip(normalized_events, sql_storages, feedback_befores):
            key = str(event.get("candidate_key") or "")
            self.review_counts[key] = self.review_counts.get(key, 0) + 1
            if event.get("action") in RESET_REVIEW_ACTIONS:
                self.latest_reviews.pop(key, None)
                self.latest_reset_reviews[key] = event
            elif _note_annotates_pending_reset(event, key, self.latest_reviews, self.latest_reset_reviews):
                self.latest_reset_reviews[key] = _merge_note_into_reset(self.latest_reset_reviews[key], event)
            else:
                self.latest_reviews[key] = event
                self.latest_reset_reviews.pop(key, None)
            storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
            if feedback_before is not None:
                try:
                    feedback_storage = self._record_question_correction_feedback(
                        event,
                        before=feedback_before,
                        review_storage=sql_storage,
                    )
                except FeedbackContractError as exc:
                    feedback_storage = {"ok": False, "error": str(exc), "status": "not_recorded"}
                if feedback_storage is not None:
                    storage["correction_feedback"] = feedback_storage
            saved.append({**event, "storage": storage})
        if self.defer_formal_sync:
            self._wake_formal_sync_worker()
            formal_sync = {"ok": True, "enabled": True, "queued": True}
        else:
            formal_sync = self.sync_formal_candidates([str(event.get("candidate_key") or "") for event in normalized_events])
        for saved_event in saved:
            saved_event["storage"]["formal_sync"] = formal_sync
        return saved

    def append_answer_review(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        key = event.get("candidate_key")
        feedback_before = self._feedback_before_snapshot(str(key or "")) if "corrected_answer" in event else None
        if event.get("action") in (NOTE_ACTIONS | {"correct"}):
            _reaffirm_standing_action(event, self.latest_answer_reviews.get(key or ""))
        event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        sql_storage = self._insert_sql_answer_review_event(event)
        jsonl_storage = self._legacy_jsonl_storage(self.answer_review_log, event)
        self._answer_review_log_signature = file_signature(self.answer_review_log)
        storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
        if feedback_before is not None:
            try:
                feedback_storage = self._record_answer_correction_feedback(
                    event,
                    before=feedback_before,
                    review_storage=sql_storage,
                )
            except FeedbackContractError as exc:
                feedback_storage = {"ok": False, "error": str(exc), "status": "not_recorded"}
            if feedback_storage is not None:
                storage["correction_feedback"] = feedback_storage
        if key:
            self.answer_review_counts[key] = self.answer_review_counts.get(key, 0) + 1
            if event.get("action") in RESET_REVIEW_ACTIONS:
                self.latest_answer_reviews.pop(key, None)
                self.latest_answer_reset_reviews[key] = event
            else:
                self.latest_answer_reviews[key] = event
                self.latest_answer_reset_reviews.pop(key, None)
        if key and self.defer_formal_sync:
            self._wake_formal_sync_worker()
            formal_sync = {"ok": True, "enabled": True, "queued": True}
        else:
            formal_sync = self.sync_formal_candidates([str(key)]) if key else None
        if formal_sync is not None:
            storage["formal_sync"] = formal_sync
        return {**event, "storage": storage}

    def append_answer_reviews_batch(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_events: list[dict[str, Any]] = []
        latest_for_correct: dict[str, dict[str, Any]] = {}
        if any(event.get("action") in (NOTE_ACTIONS | {"correct"}) for event in events):
            keys = [str(event.get("candidate_key") or "") for event in events if event.get("candidate_key")]
            if self.sql_review_enabled:
                latest_for_correct, _counts, _reset = self._sql_latest_event_maps(
                    "exam.answer_review_events",
                    keys,
                    reset_actions=RESET_REVIEW_ACTIONS,
                )
            else:
                latest_for_correct = self.latest_answer_reviews
        created_at = datetime.now().isoformat(timespec="seconds")
        for raw_event in events:
            event = dict(raw_event)
            key = str(event.get("candidate_key") or "")
            if not key:
                continue
            if event.get("action") in (NOTE_ACTIONS | {"correct"}):
                _reaffirm_standing_action(event, latest_for_correct.get(key))
            event.setdefault("created_at", created_at)
            normalized_events.append(event)
        if not normalized_events:
            return []
        feedback_befores = [
            self._feedback_before_snapshot(str(event.get("candidate_key") or ""))
            if "corrected_answer" in event and self._event_is_human_correction(event)
            else None
            for event in normalized_events
        ]
        sql_storages = self._insert_sql_answer_review_events(normalized_events)
        if self.legacy_jsonl_backup_enabled or not self.sql_review_enabled:
            try:
                with self.answer_review_log.open("a", encoding="utf-8") as f:
                    for event in normalized_events:
                        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                jsonl_storage = {"ok": True, "enabled": True, "path": str(self.answer_review_log)}
            except Exception as exc:
                if self.sql_review_enabled:
                    jsonl_storage = {"ok": False, "enabled": True, "path": str(self.answer_review_log), "error": str(exc)}
                else:
                    raise
        else:
            jsonl_storage = {"ok": True, "enabled": False, "path": str(self.answer_review_log)}
        self._answer_review_log_signature = file_signature(self.answer_review_log)
        saved: list[dict[str, Any]] = []
        for event, sql_storage, feedback_before in zip(normalized_events, sql_storages, feedback_befores):
            key = str(event.get("candidate_key") or "")
            storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
            if feedback_before is not None:
                try:
                    feedback_storage = self._record_answer_correction_feedback(
                        event,
                        before=feedback_before,
                        review_storage=sql_storage,
                    )
                except FeedbackContractError as exc:
                    feedback_storage = {"ok": False, "error": str(exc), "status": "not_recorded"}
                if feedback_storage is not None:
                    storage["correction_feedback"] = feedback_storage
            if key:
                self.answer_review_counts[key] = self.answer_review_counts.get(key, 0) + 1
                if event.get("action") in RESET_REVIEW_ACTIONS:
                    self.latest_answer_reviews.pop(key, None)
                    self.latest_answer_reset_reviews[key] = event
                else:
                    self.latest_answer_reviews[key] = event
                    self.latest_answer_reset_reviews.pop(key, None)
            saved.append({**event, "storage": storage})
        if self.defer_formal_sync:
            self._wake_formal_sync_worker()
            formal_sync = {"ok": True, "enabled": True, "queued": True}
        else:
            formal_sync = self.sync_formal_candidates([str(event.get("candidate_key") or "") for event in normalized_events])
        for saved_event in saved:
            saved_event.setdefault("storage", {})["formal_sync"] = formal_sync
        return saved

    def append_ai_review(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        key = event.get("candidate_key")
        event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        sql_storage = self._insert_sql_ai_review_event(event)
        jsonl_storage = self._legacy_jsonl_storage(self.ai_review_log, event)
        self._ai_review_log_signature = file_signature(self.ai_review_log)
        storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
        if key:
            self.ai_review_counts[key] = self.ai_review_counts.get(key, 0) + 1
            if event.get("action") in AI_RESET_REVIEW_ACTIONS:
                self.latest_ai_reviews.pop(key, None)
            else:
                self.latest_ai_reviews[key] = event
        return {**event, "storage": storage}

    def _ai_learning_candidate_snapshot(
        self,
        candidate_key: str,
        candidate: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Capture the effective text shown to the reviewer for future training export."""
        if not isinstance(candidate, dict):
            return {}
        metadata = candidate.get("metadata") or {}
        snapshot: dict[str, Any] = {
            "candidate_key": candidate_key,
            "question_number": candidate.get("question_number"),
            "metadata": {
                key: metadata.get(key)
                for key in (
                    "normalized_category_name",
                    "normalized_subject_name",
                    "year",
                    "exam_ordinal",
                    "question_pdf_relative",
                    "question_markdown_relative",
                )
                if metadata.get(key) not in (None, "")
            },
            "stem": candidate.get("stem"),
            "options": candidate.get("options") or [],
            "answer": candidate.get("answer"),
            "group_ref": candidate.get("group_ref"),
            "group_sequence_no": candidate.get("group_sequence_no"),
        }
        correction = normalized_correction(self.current_question_review(candidate_key).get("correction"))
        if correction:
            snapshot["parser_original"] = {
                "stem": candidate.get("stem"),
                "options": candidate.get("options") or [],
                "answer": candidate.get("answer"),
                "group_ref": candidate.get("group_ref"),
                "group_sequence_no": candidate.get("group_sequence_no"),
            }
            for field in ("stem", "answer", "group_ref", "group_sequence_no"):
                if field in correction:
                    snapshot[field] = correction[field]
            if "options" in correction:
                snapshot["options"] = correction["options"]
        return snapshot

    # ------------------------------------------------------------------ 錯題討論區 streams
    #
    # These two writers are deliberately plain `open(path, "a")` appends with no SQL mirror.
    # The other five event streams have SQL tables because they are read back per-question by
    # queries; these two are read whole by the discuss area (a person writes handfuls), so a table
    # would be a second representation of the same fact with nothing reading it back. The failure
    # mode that matters here is *loss*, and an append-only JSONL in a directory the deploy protects
    # is the same protection the review log relies on.
    def append_principle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add or remove one 基本原則. Append-only: a removal is an event, not a deletion."""
        action = str(payload.get("action") or "add").strip().lower()
        if action not in {"add", "remove"}:
            raise ValueError("action must be add or remove")
        text = str(payload.get("text") or "").strip()
        principle_id = str(payload.get("principle_id") or "").strip()
        if action == "add" and not text:
            raise ValueError("text is required")
        if action == "remove" and not principle_id:
            raise ValueError("principle_id is required")
        reviewer = str(payload.get("reviewer") or "local").strip() or "local"
        if action == "add":
            # An id derived from the text would collide for two identical principles, which is
            # correct (they are one principle) but would make removing one remove both. A counted
            # id in the writer keeps each add a distinct, retractable statement. `discuss.next_id`
            # counts the events rather than keeping a counter file, which is a second state that can
            # drift out of step and hand two adds the same id.
            principle_id = principle_id or discuss.next_id(self.principles_events, "p")
        event = {
            "schema": "qbr_review_principle_v0.1",
            "action": action,
            "principle_id": principle_id,
            "text": text,
            "scope": str(payload.get("scope") or "question").strip() or "question",
            "reviewer": reviewer,
        }
        if action == "remove":
            event["reason"] = str(payload.get("reason") or "").strip()[:2000]
        # Written through the shared writer, so the line format (sorted keys, one `open("a")`) cannot
        # differ between the server and the agent that reads the same file.
        event = discuss.append_event(self.principles_log, event)
        self._principles_log_signature = file_signature(self.principles_log)
        self.principles_events.append(event)
        return event

    def append_repair_question(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The repair agent asks the person back, or the person answers. Append-only."""
        action = str(payload.get("action") or "ask").strip().lower()
        if action not in {"ask", "answer"}:
            raise ValueError("action must be ask or answer")
        question_id = str(payload.get("question_id") or "").strip()
        candidate_key = str(payload.get("candidate_key") or "").strip()
        question = str(payload.get("question") or "").strip()
        answer = str(payload.get("answer") or "").strip()
        if action == "ask":
            if not question:
                raise ValueError("question is required")
            if not candidate_key:
                raise ValueError("candidate_key is required")
        else:
            if not question_id:
                raise ValueError("question_id is required")
            if not answer:
                raise ValueError("answer is required")
        reviewer = str(payload.get("reviewer") or "repair_agent").strip() or "repair_agent"
        if action == "ask":
            question_id = question_id or discuss.next_id(self.repair_questions_events, "rq")
        event = {
            "schema": "qbr_repair_question_v0.1",
            "action": action,
            "question_id": question_id,
            "candidate_key": candidate_key,
            "question": question,
            "answer": answer if action == "answer" else "",
            "reason": str(payload.get("reason") or "").strip()[:2000],
            "model": str(payload.get("model") or "").strip(),
            "endpoint": str(payload.get("endpoint") or "").strip(),
            "reviewer": reviewer,
        }
        event = discuss.append_event(self.repair_questions_log, event)
        self._repair_questions_log_signature = file_signature(self.repair_questions_log)
        self.repair_questions_events.append(event)
        return event

    def discuss_taxonomy(self) -> tuple[dict[str, Any], int]:
        """The taxonomy of the **stuck** papers only, and how many stuck questions there are.

        The 錯題討論區's pickers must offer the branches that exist *in this area*. A whole-queue
        taxonomy would offer 類科/科目 with no stuck questions in them, and a control that leads
        nowhere is worse than no control: it looks like the filter is broken rather than empty.

        Built over the **unfiltered** stuck set, always. If it were built over the current filter,
        selecting 科目 = A would drop every other subject from the picker and the reviewer could not
        get back to them - the collapsing picker the question area already fixed once.

        The tree itself comes from `review_queue.taxonomy_of` (the single implementation) via
        `paper_entries_for`; this method only decides *which* papers are in it. Membership uses
        `is_discuss_bucket`, the same predicate the list filter and both SQL CTEs use, so the tree
        cannot offer a branch the list will not fill.
        """
        signature = (
            file_signature(self.candidate_path),
            file_signature(self.review_log),
            bool(self.sql_review_enabled),
        )
        cached = getattr(self, "_discuss_taxonomy_cache", None)
        if cached is not None and cached[0] == signature:
            return cached[1], cached[2]
        if self.sql_review_enabled:
            rows = self._sql_discuss_candidate_rows()
        else:
            rows = [
                item for item in self.candidates
                if is_discuss_bucket(review_projection(
                    self.latest_reviews.get(item.get("candidate_key")),
                    self.latest_reset_reviews.get(item.get("candidate_key")),
                    item.get("metadata") or {},
                ))
            ]
        tree = review_queue.taxonomy_of(paper_entries_for(rows))
        self._discuss_taxonomy_cache = (signature, tree, len(rows))
        return tree, len(rows)

    def _sql_discuss_candidate_rows(self) -> list[dict[str, Any]]:
        """Every stuck candidate row on the SQL backend, with no limit.

        Runs the **shared** full CTE with `reviewStatus=discuss`, so the rows are selected by
        `SQL_DISCUSS_PREDICATE` - the same string the list filter uses. Selecting the taxonomy's
        rows by a second hand-written clause is exactly how a picker and a list come to disagree.
        """
        cte, values = self._sql_candidate_filter_parts({"reviewStatus": "discuss"})
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"{cte} SELECT raw_candidate_json FROM filtered", values)
                rows: list[dict[str, Any]] = []
                for (raw_candidate,) in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                return rows

    def discuss_payload(self, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Everything the 錯題討論區 draws, in one response.

        The candidate rows come from the *same* `filtered_candidate_payloads` the question area
        uses, so the two cannot disagree about what a question is or what its disputes are. Only
        the bucket filter differs: see `DISCUSS_BUCKETS`.
        """
        params = dict(params or {})
        # `candidate_payload` already attaches `qbr_ai_finding` (the question area draws it too), so
        # the rows are taken as they are - a second attachment here would be a second place the
        # finding could differ from the one the question area shows.
        filtered = self.filtered_candidate_payloads(params)
        rows = filtered.get("candidates") or []
        # The pickers' tree is the stuck population's own, computed over everything stuck and never
        # over the current filter (see `discuss_taxonomy`). `stuck_total` is the tree's own count, so
        # the "卡住的題" number and the branches the pickers offer are the same measurement.
        taxonomy, stuck_total = self.discuss_taxonomy()
        return {
            "candidates": rows,
            # The number the scope really holds, and the number that fit. A capped list that does not
            # say it was capped is the truncation the question area already had to fix; the discuss
            # area shows "N / M" rather than silently dropping the tail.
            "filtered_count": filtered.get("filtered_count"),
            "returned_count": len(rows),
            "taxonomy": taxonomy,
            "stuck_total": stuck_total,
            "principles": principles_projection(self.principles_events),
            "repair_questions": repair_questions_projection(self.repair_questions_events),
            "buckets": list(DISCUSS_BUCKETS),
            "storage": self.candidate_data_status(),
        }

    def append_ai_learning(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Append a human-selected training example bound to one exact AI audit."""
        candidate_key = str(payload.get("candidate_key") or "").strip()
        audit_scope = str(payload.get("audit_scope") or "question").strip()
        reviewer = str(payload.get("reviewer") or "local").strip() or "local"
        reason = str(payload.get("reason") or "").strip()[:2000]
        if not candidate_key:
            raise ValueError("candidate_key is required")
        if audit_scope not in AI_FEEDBACK_SCOPES:
            raise ValueError("audit_scope must be question, group, visual, or answer")

        if self.sql_review_enabled:
            latest_map, _counts, _resets = self._sql_latest_event_maps(
                "exam.question_ai_review_events",
                [candidate_key],
                reset_actions=AI_RESET_REVIEW_ACTIONS,
                ai=True,
            )
            latest_ai_review = latest_map.get(candidate_key)
            candidate = self._candidate_by_key_sql(candidate_key)
        else:
            latest_ai_review = self.latest_ai_reviews.get(candidate_key)
            candidate = self.candidate_by_key.get(candidate_key)
        if not latest_ai_review:
            raise ValueError("No active AI audit is available to add to AI learning")

        current_ref = ai_review_reference(latest_ai_review)
        requested_ref = str(payload.get("ai_review_ref") or "").strip()
        if not current_ref or requested_ref != current_ref:
            raise ValueError("AI audit has changed; reload the question before adding it to AI learning")
        audit = latest_ai_review.get("audit") if isinstance(latest_ai_review.get("audit"), dict) else {}
        event = {
            "action": "ai_learning",
            "candidate_key": candidate_key,
            "audit_scope": audit_scope,
            "reviewer": reviewer,
            "reason": reason,
            "selected": True,
            "learning_kind": "human_training_material",
            "ai_review_ref": current_ref,
            "ai_review_event_id": latest_ai_review.get("event_id"),
            "ai_context": {
                "provider": latest_ai_review.get("provider") or audit.get("provider"),
                "model": latest_ai_review.get("model") or audit.get("model"),
                "prompt_version": latest_ai_review.get("prompt_version"),
                "input_hash": latest_ai_review.get("input_hash"),
                "audit_status": audit.get("status"),
                "summary": audit.get("summary"),
                "labels": audit.get("labels") or [],
                "findings": audit.get("findings") or [],
                "suggested_changes": audit.get("suggested_changes") or [],
                "suggested_correction": audit.get("suggested_correction"),
                "patch_suppressed_reason": audit.get("patch_suppressed_reason"),
            },
            "candidate_snapshot": self._ai_learning_candidate_snapshot(candidate_key, candidate),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        sql_storage = self._insert_sql_ai_learning_event(event)
        jsonl_storage = self._legacy_jsonl_storage(self.ai_learning_log, event)
        self._ai_learning_log_signature = file_signature(self.ai_learning_log)
        self.latest_ai_learnings.setdefault(candidate_key, {})[audit_scope] = event
        return {
            **event,
            "storage": {**sql_storage, "legacy_jsonl_backup": jsonl_storage},
        }

    def append_ai_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Append a human rating tied to the exact currently displayed audit."""
        candidate_key = str(payload.get("candidate_key") or "").strip()
        rating = str(payload.get("rating") or "").strip()
        audit_scope = str(payload.get("audit_scope") or "question").strip()
        reviewer = str(payload.get("reviewer") or "local").strip() or "local"
        reason = str(payload.get("reason") or "").strip()[:2000]
        if not candidate_key:
            raise ValueError("candidate_key is required")
        if rating not in AI_FEEDBACK_RATINGS:
            raise ValueError("rating must be up or down")
        if audit_scope not in AI_FEEDBACK_SCOPES:
            raise ValueError("audit_scope must be question, group, visual, or answer")

        if self.sql_review_enabled:
            latest_map, _counts, _resets = self._sql_latest_event_maps(
                "exam.question_ai_review_events",
                [candidate_key],
                reset_actions=AI_RESET_REVIEW_ACTIONS,
                ai=True,
            )
            latest_ai_review = latest_map.get(candidate_key)
        else:
            latest_ai_review = self.latest_ai_reviews.get(candidate_key)
        if not latest_ai_review:
            raise ValueError("No active AI audit is available to rate")

        current_ref = ai_review_reference(latest_ai_review)
        requested_ref = str(payload.get("ai_review_ref") or "").strip()
        if not current_ref or requested_ref != current_ref:
            raise ValueError("AI audit has changed; reload the question before rating")
        audit = latest_ai_review.get("audit") if isinstance(latest_ai_review.get("audit"), dict) else {}
        ai_context = {
            "provider": latest_ai_review.get("provider") or audit.get("provider"),
            "model": latest_ai_review.get("model") or audit.get("model"),
            "prompt_version": latest_ai_review.get("prompt_version"),
            "input_hash": latest_ai_review.get("input_hash"),
            "audit_status": audit.get("status"),
            "summary": audit.get("summary"),
            "labels": audit.get("labels") or [],
            "findings": audit.get("findings") or [],
            "suggested_changes": audit.get("suggested_changes") or [],
            "suggested_correction": audit.get("suggested_correction"),
            "patch_suppressed_reason": audit.get("patch_suppressed_reason"),
        }
        event = {
            "action": "ai_feedback",
            "candidate_key": candidate_key,
            "audit_scope": audit_scope,
            "rating": rating,
            "reviewer": reviewer,
            "reason": reason,
            "ai_review_ref": current_ref,
            "ai_review_event_id": latest_ai_review.get("event_id"),
            "ai_context": ai_context,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        sql_storage = self._insert_sql_ai_feedback_event(event)
        jsonl_storage = self._legacy_jsonl_storage(self.ai_feedback_log, event)
        self._ai_feedback_log_signature = file_signature(self.ai_feedback_log)
        self.latest_ai_feedbacks.setdefault(candidate_key, {})[audit_scope] = event
        return {
            **event,
            "storage": {**sql_storage, "legacy_jsonl_backup": jsonl_storage},
        }

    def reset_ai_review(self, candidate_key: str, reviewer: str = "local", notes: str = "") -> dict[str, Any]:
        if self.sql_review_enabled:
            exists = self.sql_candidate_exists(candidate_key)
        else:
            exists = candidate_key in self.candidate_by_key
        if not exists:
            raise KeyError(candidate_key)
        event = {
            "candidate_key": candidate_key,
            "reviewer": reviewer,
            "action": "reset_ai_review",
            "notes": notes or "撤回 AI 格式稽核；保留歷史事件但目前視為未稽核。",
        }
        return self.append_ai_review(event)

    def run_question_ai_audit(self, candidate_key: str, reviewer: str = "local", notes: str = "") -> dict[str, Any]:
        item = self._candidate_by_key_sql(candidate_key) if self.sql_review_enabled else self.candidate_by_key.get(candidate_key)
        if not item:
            raise KeyError(candidate_key)
        if self.sql_review_enabled:
            issues_by_key = self._sql_issue_map([candidate_key])
            latest_reviews, review_counts, latest_reset_reviews = self._sql_question_review_maps([candidate_key])
            latest_answer_reviews, answer_review_counts, _answer_reset_reviews = self._sql_latest_event_maps(
                "exam.answer_review_events",
                [candidate_key],
                reset_actions=RESET_REVIEW_ACTIONS,
            )
            latest_ai_reviews, ai_review_counts, _ai_reset_reviews = self._sql_latest_event_maps(
                "exam.question_ai_review_events",
                [candidate_key],
                reset_actions=AI_RESET_REVIEW_ACTIONS,
                ai=True,
            )
            candidate = self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
            )
        else:
            candidate = self.candidate_payload(item)
        audit = openai_question_ai_audit(candidate)
        audit_input = compact_candidate_for_ai(candidate)
        event = {
            "candidate_key": candidate_key,
            "reviewer": reviewer,
            "action": "ai_audit",
            "prompt_version": AI_REVIEW_PROMPT_VERSION,
            "provider": audit.get("provider"),
            "model": audit.get("model"),
            "input_hash": hashlib.sha256(json.dumps(audit_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "notes": notes,
            "audit": audit,
        }
        return self.append_ai_review(event)

    def question_review_action(self, candidate_key: str) -> str | None:
        latest = self.current_question_review(candidate_key)
        if not latest:
            return None
        return latest.get("action")

    def question_is_answer_eligible(self, candidate_key: str) -> bool:
        return self.question_review_action(candidate_key) in {"accept", "unblock"}

    def question_answer_eligibility_map(self, candidate_keys: list[str]) -> dict[str, str]:
        keys = [str(key or "") for key in candidate_keys if str(key or "")]
        if not keys:
            return {}
        if self.sql_review_enabled:
            latest_reviews, _counts, _reset = self._sql_question_review_maps(keys)
            return {key: str((latest_reviews.get(key) or {}).get("action") or "") for key in keys}
        return {key: str((self.latest_reviews.get(key) or {}).get("action") or "") for key in keys}

    def _compute_pipeline_cache(self) -> dict[str, Any]:
        payload = self._sql_pipeline_payload()
        updated_at = datetime.now().isoformat(timespec="seconds")
        with self._pipeline_cache_lock:
            self._pipeline_cache = {
                "payload": payload,
                "updated_at": updated_at,
                "updated_monotonic": time.monotonic(),
                "error": None,
            }
            self._pipeline_refreshing = False
        return self._pipeline_cached_response(payload, updated_at, 0.0, False, None)

    def _refresh_pipeline_cache_background(self) -> None:
        try:
            self._compute_pipeline_cache()
        except Exception as exc:
            with self._pipeline_cache_lock:
                self._pipeline_refreshing = False
                if self._pipeline_cache is not None:
                    self._pipeline_cache["error"] = str(exc)

    def _start_pipeline_cache_refresh(self) -> None:
        with self._pipeline_cache_lock:
            if self._pipeline_refreshing:
                return
            self._pipeline_refreshing = True
        threading.Thread(
            target=self._refresh_pipeline_cache_background,
            name="pipeline-stats-refresh",
            daemon=True,
        ).start()

    def _pipeline_cached_response(
        self,
        payload: dict[str, Any],
        updated_at: str,
        age_seconds: float,
        stale: bool,
        error: str | None,
    ) -> dict[str, Any]:
        response = dict(payload)
        response.update(
            {
                "statistics_updated_at": updated_at,
                "statistics_age_seconds": round(max(age_seconds, 0.0), 1),
                "statistics_stale": stale,
            }
        )
        if error:
            response["statistics_refresh_error"] = error
        return response

    def _sql_pipeline_payload(self) -> dict[str, Any]:
        counts = {
            "official_documents": 0,
            "question_candidates": 0,
            "question_parse_issues": 0,
            "question_reviewed": 0,
            "question_accepted": 0,
            "question_needs_review": 0,
            "question_blocked": 0,
            "answer_reviewed": 0,
            "answer_accepted": 0,
            "answer_blocked": 0,
            "answer_ready": 0,
            "question_accepted_answer_pending": 0,
            "ready_for_formal": 0,
            "formal_pending_promotion": 0,
            "ai_reviewed": 0,
            "ai_needs_review": 0,
            "ai_blocked": 0,
            "formal_questions": 0,
            "formal_review_drift": 0,
        }
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH active_candidates AS MATERIALIZED (
                        SELECT candidate_key, raw_candidate_json
                        FROM exam.question_candidates
                        WHERE COALESCE(review_status, '') <> 'excluded'
                    ),
                    latest_question AS MATERIALIZED (
                        SELECT DISTINCT ON (e.candidate_key)
                            e.candidate_key,
                            e.action
                        FROM exam.question_review_events e
                        WHERE e.action NOT IN (
                            'confirm_not_group', 'confirm_group', 'reset_group_review',
                            'human_review_pdf_visual', 'mobile_defer', 'mobile_resume'
                        )
                        ORDER BY e.candidate_key, e.id DESC
                    ),
                    latest_answer AS MATERIALIZED (
                        SELECT DISTINCT ON (e.candidate_key)
                            e.candidate_key,
                            e.action
                        FROM exam.answer_review_events e
                        ORDER BY e.candidate_key, e.id DESC
                    ),
                    latest_ai AS MATERIALIZED (
                        SELECT DISTINCT ON (e.candidate_key)
                            e.candidate_key,
                            e.audit_status
                        FROM exam.question_ai_review_events e
                        ORDER BY e.candidate_key, e.id DESC
                    ),
                    question_states AS MATERIALIZED (
                        SELECT c.candidate_key, c.raw_candidate_json, l.action
                        FROM active_candidates c
                        JOIN latest_question l USING (candidate_key)
                    ),
                    answer_states AS MATERIALIZED (
                        SELECT c.candidate_key, l.action
                        FROM active_candidates c
                        JOIN latest_answer l USING (candidate_key)
                    ),
                    ai_states AS MATERIALIZED (
                        SELECT c.candidate_key, l.audit_status
                        FROM active_candidates c
                        JOIN latest_ai l USING (candidate_key)
                    ),
                    ready AS MATERIALIZED (
                        SELECT q.candidate_key
                        FROM question_states q
                        JOIN answer_states a USING (candidate_key)
                        WHERE q.action IN ('accept', 'unblock')
                          AND a.action IN ('accept', 'unblock')
                    ),
                    ready_formal AS MATERIALIZED (
                        SELECT
                            r.candidate_key,
                            fq.id,
                            fq.review_status,
                            EXISTS (
                                SELECT 1
                                FROM exam.answers fa
                                WHERE fa.question_id = fq.id
                            ) AS has_answers
                        FROM ready r
                        LEFT JOIN exam.questions fq ON fq.question_key = r.candidate_key
                    ),
                    formal_states AS MATERIALIZED (
                        SELECT
                            q.question_key,
                            EXISTS (
                                SELECT 1
                                FROM exam.answers fa
                                WHERE fa.question_id = q.id
                            ) AS has_answers,
                            lq.action AS question_action,
                            la.action AS answer_action
                        FROM exam.questions q
                        LEFT JOIN latest_question lq ON lq.candidate_key = q.question_key
                        LEFT JOIN latest_answer la ON la.candidate_key = q.question_key
                        WHERE q.review_status = 'accepted'
                    ),
                    source_stats AS (
                        SELECT
                            (SELECT count(*) FROM exam.official_documents) AS official_documents,
                            (SELECT count(*) FROM exam.question_parse_issues) AS question_parse_issues
                    ),
                    candidate_stats AS (
                        SELECT
                            count(*) AS question_candidates,
                            count(*) FILTER (
                                WHERE NULLIF(raw_candidate_json->>'answer', '') IS NOT NULL
                            ) AS answer_ready
                        FROM active_candidates
                    ),
                    question_stats AS (
                        SELECT
                            count(*) AS question_reviewed,
                            count(*) FILTER (WHERE action IN ('accept', 'unblock')) AS question_accepted,
                            count(*) FILTER (WHERE action = 'needs_review') AS question_needs_review,
                            count(*) FILTER (WHERE action = 'block') AS question_blocked,
                            count(*) FILTER (
                                WHERE action IN ('accept', 'unblock')
                                  AND NULLIF(raw_candidate_json->>'answer', '') IS NOT NULL
                            ) AS question_accepted_answer_pending
                        FROM question_states
                    ),
                    answer_stats AS (
                        SELECT
                            count(*) AS answer_reviewed,
                            count(*) FILTER (WHERE action IN ('accept', 'unblock')) AS answer_accepted,
                            count(*) FILTER (WHERE action = 'block') AS answer_blocked
                        FROM answer_states
                    ),
                    ready_stats AS (
                        SELECT
                            count(*) AS ready_for_formal,
                            count(*) FILTER (
                                WHERE id IS NULL
                                   OR review_status <> 'accepted'
                                   OR NOT has_answers
                            ) AS formal_pending_promotion
                        FROM ready_formal
                    ),
                    ai_stats AS (
                        SELECT
                            count(*) AS ai_reviewed,
                            count(*) FILTER (WHERE audit_status = 'needs_review') AS ai_needs_review,
                            count(*) FILTER (WHERE audit_status IN ('block', 'blocked')) AS ai_blocked
                        FROM ai_states
                    ),
                    formal_stats AS (
                        SELECT
                            count(*) FILTER (WHERE has_answers) AS formal_questions,
                            count(*) FILTER (
                                WHERE has_answers
                                  AND NOT (
                                      COALESCE(question_action, '') IN ('accept', 'unblock')
                                      AND COALESCE(answer_action, '') IN ('accept', 'unblock')
                                  )
                            ) AS formal_review_drift
                        FROM formal_states
                    )
                    SELECT
                        source_stats.official_documents,
                        candidate_stats.question_candidates,
                        source_stats.question_parse_issues,
                        question_stats.question_reviewed,
                        question_stats.question_accepted,
                        question_stats.question_needs_review,
                        question_stats.question_blocked,
                        answer_stats.answer_reviewed,
                        answer_stats.answer_accepted,
                        answer_stats.answer_blocked,
                        candidate_stats.answer_ready,
                        question_stats.question_accepted_answer_pending,
                        ready_stats.ready_for_formal,
                        ready_stats.formal_pending_promotion,
                        ai_stats.ai_reviewed,
                        ai_stats.ai_needs_review,
                        ai_stats.ai_blocked,
                        formal_stats.formal_questions,
                        formal_stats.formal_review_drift
                    FROM source_stats
                    CROSS JOIN candidate_stats
                    CROSS JOIN question_stats
                    CROSS JOIN answer_stats
                    CROSS JOIN ready_stats
                    CROSS JOIN ai_stats
                    CROSS JOIN formal_stats
                    """
                )
                row = cur.fetchone()
                if row:
                    stat_keys = (
                        "official_documents",
                        "question_candidates",
                        "question_parse_issues",
                        "question_reviewed",
                        "question_accepted",
                        "question_needs_review",
                        "question_blocked",
                        "answer_reviewed",
                        "answer_accepted",
                        "answer_blocked",
                        "answer_ready",
                        "question_accepted_answer_pending",
                        "ready_for_formal",
                        "formal_pending_promotion",
                        "ai_reviewed",
                        "ai_needs_review",
                        "ai_blocked",
                        "formal_questions",
                        "formal_review_drift",
                    )
                    counts.update({key: int(value or 0) for key, value in zip(stat_keys, row)})
        return self._pipeline_payload_from_counts(counts)

    def pipeline_payload(self) -> dict[str, Any]:
        if self.sql_review_enabled:
            now = time.monotonic()
            refresh_needed = False
            with self._pipeline_cache_lock:
                cached = dict(self._pipeline_cache) if self._pipeline_cache else None
                if cached:
                    age_seconds = now - float(cached.get("updated_monotonic") or now)
                    stale = age_seconds >= self._pipeline_cache_ttl_seconds
                    refresh_needed = stale
                    response = self._pipeline_cached_response(
                        cached["payload"],
                        str(cached.get("updated_at") or ""),
                        age_seconds,
                        stale,
                        cached.get("error"),
                    )
                else:
                    response = None
            if response is not None:
                if refresh_needed:
                    self._start_pipeline_cache_refresh()
                return response
            return self._compute_pipeline_cache()
        reviewed = []
        accepted = []
        blocked = []
        needs_review = []
        answer_ready_count = 0
        question_accepted_answer_pending_count = 0
        answer_reviewed = []
        answer_accepted = []
        answer_blocked = []
        ai_reviewed = []
        ai_needs_review = []
        ai_blocked = []
        for item in self.candidates:
            latest = self.latest_reviews.get(item["candidate_key"])
            if latest:
                reviewed.append(item)
                if latest.get("action") == "accept":
                    accepted.append(item)
                    if item.get("answer") not in (None, ""):
                        question_accepted_answer_pending_count += 1
                elif latest.get("action") in {"block", "exclude"}:
                    blocked.append(item)
                elif latest.get("action") == "needs_review":
                    needs_review.append(item)
            if item.get("answer") not in (None, ""):
                answer_ready_count += 1
            latest_answer = self.latest_answer_reviews.get(item["candidate_key"])
            if latest_answer:
                answer_reviewed.append(item)
                if latest_answer.get("action") == "accept":
                    answer_accepted.append(item)
                elif latest_answer.get("action") == "block":
                    answer_blocked.append(item)
            latest_ai = self.latest_ai_reviews.get(item["candidate_key"])
            if latest_ai:
                ai_reviewed.append(item)
                audit = latest_ai.get("audit") if isinstance(latest_ai.get("audit"), dict) else {}
                ai_suggestion, _ai_suggestion_changes = ai_suggested_correction(item, audit)
                effective_status = effective_ai_audit_status(audit, ai_suggestion)
                if effective_status == "block":
                    ai_blocked.append(item)
                elif effective_status == "needs_review":
                    ai_needs_review.append(item)
        issue_count = sum(len(value) for value in self.issues.values())
        counts = {
            "official_documents": len({item.get("source_registry_key") for item in self.candidates}),
            "question_candidates": len(self.candidates),
            "question_parse_issues": issue_count,
            "question_reviewed": len(reviewed),
            "question_accepted": len(accepted),
            "question_needs_review": len(needs_review),
            "question_blocked": len(blocked),
            "answer_reviewed": len(answer_reviewed),
            "answer_accepted": len(answer_accepted),
            "answer_blocked": len(answer_blocked),
            "question_accepted_answer_pending": question_accepted_answer_pending_count,
            "answer_ready": answer_ready_count,
            "ai_reviewed": len(ai_reviewed),
            "ai_needs_review": len(ai_needs_review),
            "ai_blocked": len(ai_blocked),
        }
        payload = self._pipeline_payload_from_counts(counts)
        payload.update(
            {
                "statistics_updated_at": datetime.now().isoformat(timespec="seconds"),
                "statistics_age_seconds": 0.0,
                "statistics_stale": False,
            }
        )
        return payload

    def _pipeline_payload_from_counts(self, counts: dict[str, int]) -> dict[str, Any]:
        return {
            "storage": {
                "review_backend": "sql" if self.sql_review_enabled else "jsonl",
                "sql_primary": bool(self.sql_review_enabled),
                "legacy_jsonl_backup": bool(self.legacy_jsonl_backup_enabled),
                "jsonl_status": (
                    "legacy_backup"
                    if self.sql_review_enabled and self.legacy_jsonl_backup_enabled
                    else "disabled"
                    if self.sql_review_enabled
                    else "primary"
                ),
                "formal_sync": self.formal_sync_status(),
            },
            "candidate_source_jsonl": str(self.candidate_path),
            "issue_source_csv": str(self.issue_path) if self.issue_path else None,
            "legacy_review_log": str(self.review_log),
            "layers": [
                {
                    "name": "官方 PDF / MinerU raw",
                    "tables": ["exam.official_documents", "exam.assets", "exam.document_assets", "exam.mineru_runs"],
                    "status": "source",
                    "count": counts.get("official_documents", 0),
                    "description": "官方 PDF、MinerU markdown、圖片與 layout PDF。這一層只追溯來源，不代表題目已可入庫。",
                },
                {
                    "name": "題目 candidate",
                    "tables": ["exam.question_candidates"],
                    "status": "pre_ingestion",
                    "count": counts.get("question_candidates", 0),
                    "description": "parser 從 MinerU markdown 切出的候選題目，目前仍需人工審核。",
                },
                {
                    "name": "QA flags",
                    "tables": ["exam.question_parse_issues"],
                    "status": "pre_ingestion",
                    "count": counts.get("question_parse_issues", 0),
                    "description": "機械檢查疑點，例如題號重複、選項不足、圖片提示但未偵測圖片。",
                },
                {
                    "name": "題目人工審核",
                    "tables": ["exam.question_review_events"],
                    "status": "human_review",
                    "count": counts.get("question_reviewed", 0),
                    "description": "你在 Review UI 按下通過、保留疑問、阻擋入庫、註記後產生的事件。",
                    "breakdown": {
                        "accepted": counts.get("question_accepted", 0),
                        "needs_review": counts.get("question_needs_review", 0),
                        "blocked": counts.get("question_blocked", 0),
                    },
                },
                {
                    "name": "AI 格式稽核",
                    "tables": ["exam.question_ai_review_events", "exam.model_runs"],
                    "status": "ai_review",
                    "count": counts.get("ai_reviewed", 0),
                    "description": "模型或本機規則對候選題做字形、格式、圖片/表格線索與 parser 結構稽核；只提供疑點，不自動改變人工審核狀態。",
                    "breakdown": {
                        "needs_review": counts.get("ai_needs_review", 0),
                        "blocked": counts.get("ai_blocked", 0),
                    },
                },
                {
                    "name": "答案核對",
                    "tables": ["exam.answer_review_events"],
                    "status": "planned",
                    "count": counts.get("question_accepted_answer_pending", 0),
                    "description": "獨立於題目結構審核。題目通過後，再集中核對答案、MOD/ANS 優先序與答案表解析。",
                    "breakdown": {
                        "candidates_with_answer": counts.get("answer_ready", 0),
                        "question_accepted_answer_pending": counts.get("question_accepted_answer_pending", 0),
                        "answer_reviewed": counts.get("answer_reviewed", 0),
                        "answer_accepted": counts.get("answer_accepted", 0),
                        "answer_blocked": counts.get("answer_blocked", 0),
                    },
                },
                {
                    "name": "正式題庫",
                    "tables": ["exam.question_groups", "exam.questions", "exam.question_options", "exam.answers", "exam.question_assets"],
                    "status": "usable_bank",
                    "count": counts.get("ready_for_formal", 0),
                    "description": "最新題目審核與答案核對都通過的目前可用題目。SQL 審核事件提交後由 formal sync queue 背景同步正式表；題組審核是額外結構標籤，不阻擋可用狀態。",
                    "breakdown": {
                        "ready_for_formal": counts.get("ready_for_formal", 0),
                        "formal_questions_usable": counts.get("formal_questions", 0),
                        "pending_promotion": counts.get("formal_pending_promotion", 0),
                        "physical_review_drift": counts.get("formal_review_drift", 0),
                    },
                },
            ],
        }
