#!/usr/bin/env python3
"""Run the dependency-light AI395 review staging walking skeleton.

The runner deliberately starts at an immutable source/MinerU artifact
boundary. Download and MinerU execution are represented by contracts and
disabled configuration; this command only validates existing artifacts or a
synthetic fixture, builds parser/revision state, fans out five advisory lanes,
and produces a formal dry-run report.

It can use SQLite without extra packages for local tests. The Docker staging
worker uses the same code with PostgreSQL and the staging migration.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ai395_context_guard import DEFAULT_CONTEXT_LIMIT_TOKENS, ContextBudgetExceeded
from ai395_evidence_tools import collect_evidence
from ai395_llm_adapter import (
    LLMAdapterError,
    PixelsUnavailable,
    PROMPT_VERSION,
    call_feedback,
    normalized_openai_base_url,
    call_lane,
)
from ai395_feedback import (
    FeedbackContractError,
    NoVisibleChange,
    build_feedback_event,
    build_guardrail_candidate,
)
from ai395_source_adapter import ContractError, validate_manifests
from probe_qwen_mlx_tailscale import ProbeError, normalized_base_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "configs" / "ai395_review_pipeline"
DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "fixtures" / "ai395_review"
DEFAULT_SQL_DIR = PROJECT_ROOT / "deploy" / "ai395-review-staging" / "sql"
LANES = ("text_evidence", "notation", "group", "vision", "answer")
REQUIRED_CANDIDATE_KEYS = ("candidate_key", "source_registry_key", "question_number", "stem", "options", "answer", "metadata")
MAX_LOCAL_QWEN_CONTEXT_TOKENS = 196_608
MAX_LITELLM_CONTEXT_TOKENS = 1_000_000


class StagingContractError(ValueError):
    """Raised when the staging input or configuration is unsafe."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagingContractError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StagingContractError(f"expected JSON object: {path}")
    return payload


def load_config(config_dir: Path) -> tuple[dict[str, Any], str]:
    names = (
        "pipeline",
        "provider_registry",
        "route_registry",
        "resource_policy",
        "budget_policy",
        "schedule_policy",
        "invalidation_matrix",
    )
    config: dict[str, Any] = {}
    for name in names:
        path = config_dir / f"{name}.yaml"
        payload = load_json(path)
        config[name] = payload
    pipeline = config["pipeline"]
    if pipeline.get("environment") != "staging":
        raise StagingContractError("pipeline config must declare environment=staging")
    source_stage = pipeline.get("source_stage") or {}
    mineru_stage = pipeline.get("mineru_stage") or {}
    if source_stage.get("enabled") is not False or mineru_stage.get("enabled") is not False:
        raise StagingContractError("walking skeleton requires source_stage and mineru_stage disabled")
    if source_stage.get("mode") != "existing_artifact" or mineru_stage.get("mode") != "existing_artifact":
        raise StagingContractError("walking skeleton requires existing_artifact source and MinerU modes")
    if pipeline.get("formal_stage", {}).get("production_write") is not False:
        raise StagingContractError("formal staging must have production_write=false")
    if pipeline.get("model_stage", {}).get("advisory_only") is not True:
        raise StagingContractError("model stage must remain advisory_only")
    feedback_stage = pipeline.get("feedback_stage") or {}
    if feedback_stage.get("enabled") is not True and feedback_stage.get("enabled") is not False:
        raise StagingContractError("feedback_stage.enabled must be true or false")
    if feedback_stage.get("advisory_only") is not True:
        raise StagingContractError("feedback stage must remain advisory_only")
    if feedback_stage.get("allow_direct_rule_write") is not False:
        raise StagingContractError("feedback stage cannot write rules directly")
    if feedback_stage.get("allow_direct_skill_write") is not False:
        raise StagingContractError("feedback stage cannot write Skills directly")
    if feedback_stage.get("require_owner_approval") is not True:
        raise StagingContractError("feedback stage must require owner approval")
    return config, sha256_text(canonical_json(config))


class StagingDB:
    """Small database adapter for SQLite tests and the PostgreSQL container."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.sqlite = database_url.startswith("sqlite://")
        self.conn: Any
        if self.sqlite:
            raw_path = database_url[len("sqlite://") :]
            path = raw_path if raw_path else ":memory:"
            if path.startswith("/") and path != "/:memory:":
                db_path = Path(path)
                db_path.parent.mkdir(parents=True, exist_ok=True)
                path = str(db_path)
            elif path == "/:memory:":
                path = ":memory:"
            self.conn = sqlite3.connect(path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
        else:
            if "192.168.10.90" in database_url or "production" in database_url.lower():
                raise StagingContractError("refusing a production-looking database URL")
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise StagingContractError("PostgreSQL mode requires psycopg; use the staging Docker worker or sqlite:// for local tests") from exc
            self.conn = psycopg.connect(database_url, row_factory=dict_row)

    @property
    def table_prefix(self) -> str:
        return "" if self.sqlite else "review_staging."

    def table(self, name: str) -> str:
        return f"{self.table_prefix}{name}"

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        if not self.sqlite:
            sql = sql.replace("?", "%s")
        return self.conn.execute(sql, tuple(params))

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Any:
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[Any]:
        return list(self.execute(sql, params).fetchall())

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def apply_migration(self) -> None:
        if self.sqlite:
            script = (DEFAULT_SQL_DIR / "001_review_staging.sqlite.sql").read_text(encoding="utf-8")
            self.conn.executescript(script)
        else:
            script = (DEFAULT_SQL_DIR / "001_review_staging.sql").read_text(encoding="utf-8")
            self.conn.execute(script)
        self.commit()


def resolve_fixture(fixture_id: str) -> Path:
    fixture = DEFAULT_FIXTURE_ROOT / fixture_id
    if not fixture.is_dir():
        raise StagingContractError(f"unknown fixture: {fixture_id}")
    return fixture


def load_candidates(path: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StagingContractError(f"invalid candidate JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(item, dict):
                raise StagingContractError(f"candidate must be an object at {path}:{line_number}")
            missing = [key for key in REQUIRED_CANDIDATE_KEYS if key not in item]
            if missing:
                raise StagingContractError(f"candidate {line_number} missing keys: {', '.join(missing)}")
            key = str(item["candidate_key"])
            if not key or key in seen:
                raise StagingContractError(f"duplicate or empty candidate_key: {key!r}")
            if not isinstance(item["options"], list):
                raise StagingContractError(f"candidate {key} has invalid options type")
            if len(item["options"]) < 2:
                # Real parser output is allowed to enter staging with an
                # incomplete option list so the defect can be shown to the
                # human exception queue.  It must already be explicitly
                # marked as blocked/needs_review (or carry parser issues);
                # otherwise this is a malformed upstream contract and should
                # still stop the run rather than silently downgrade it.
                metadata = item.get("metadata") or {}
                parser_status_hint = str(metadata.get("parser_status") or item.get("quality_status") or "")
                if parser_status_hint not in {"blocked", "needs_review"} and int(item.get("issue_count") or 0) <= 0:
                    raise StagingContractError(f"candidate {key} has invalid options")
            if not isinstance(item["metadata"], dict):
                raise StagingContractError(f"candidate {key} metadata must be an object")
            seen.add(key)
            candidates.append(item)
    if not candidates:
        raise StagingContractError(f"candidate JSONL is empty: {path}")
    return candidates


def load_issues(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parser_status(item: dict[str, Any]) -> tuple[str, int | None, int, int]:
    metadata = item.get("metadata") or {}
    expected = metadata.get("expected_question_count")
    # expected_question_count is an exam/document-level invariant, not the
    # number of questions represented by this one candidate row.  A parser
    # emits actual_question_count only when it measured the source document;
    # otherwise the candidate inherits the verified expected total.
    actual_value = metadata.get("actual_question_count")
    actual = int(actual_value) if actual_value is not None else int(expected) if expected is not None else 1
    issue_count = int(item.get("issue_count") or 0)
    status = str(metadata.get("parser_status") or item.get("quality_status") or "needs_review")
    if expected is not None and actual != int(expected):
        status = "blocked"
        issue_count = max(issue_count, 1)
    if status not in {"pass", "needs_review", "blocked"}:
        raise StagingContractError(f"unsupported parser_status for {item['candidate_key']}: {status}")
    return status, int(expected) if expected is not None else None, actual, issue_count


def current_revision_id(db: StagingDB, run_id: str, candidate_key: str) -> str:
    row = db.fetchone(
        f"SELECT revision_id FROM {db.table('question_revisions')} WHERE run_id=? AND candidate_key=? AND status IN ('active','exception') ORDER BY revision_no DESC LIMIT 1",
        (run_id, candidate_key),
    )
    if not row:
        raise StagingContractError(f"missing active revision for {candidate_key}")
    return str(row["revision_id"])


def insert_run(db: StagingDB, run_id: str, config_hash: str, source_mode: str, mineru_mode: str, fixture_id: str | None) -> bool:
    table = db.table("pipeline_runs")
    existing = db.fetchone(f"SELECT status FROM {table} WHERE run_id=?", (run_id,))
    if existing and str(existing["status"]) in {"dry_run", "succeeded"}:
        return False
    timestamp = now_iso()
    db.execute(
        f"""INSERT INTO {table}
            (run_id, pipeline_id, environment, source_mode, mineru_mode, fixture_id,
             status, config_sha256, manifest_json, production_write_count, created_at, started_at)
            VALUES (?, 'ai395_review_staging', 'staging', ?, ?, ?, 'running', ?, ?, 0, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET status='running', config_sha256=excluded.config_sha256,
              manifest_json=excluded.manifest_json, started_at=excluded.started_at,
              finished_at=NULL, production_write_count=0""",
        (run_id, source_mode, mineru_mode, fixture_id, config_hash, "{}", timestamp, timestamp),
    )
    db.commit()
    return True


def register_artifacts(db: StagingDB, run_id: str, verified: dict[str, Any]) -> None:
    table = db.table("source_artifacts")
    rows: list[dict[str, Any]] = []
    for item in verified["source"]["source_artifacts"]:
        rows.append({
            "artifact_id": f"{run_id}:{item['artifact_id']}",
            "artifact_type": item.get("artifact_type", "unknown"),
            "status": item.get("status", "existing_artifact"),
            "path": item["resolved_path"],
            "sha256": item["verified_sha256"],
            "bytes": item.get("bytes"),
            "producer": item.get("producer", "unknown"),
            "producer_version": item.get("producer_version", "unknown"),
            "manifest": item,
        })
    for key, item in (("candidate_jsonl", verified["source"]["candidate_jsonl"]), ("issue_csv", verified["source"]["issue_csv"])):
        rows.append({
            "artifact_id": f"{run_id}:{key}",
            "artifact_type": key,
            "status": "existing_artifact",
            "path": item["resolved_path"],
            "sha256": item["verified_sha256"],
            "bytes": item.get("bytes"),
            "producer": "source-adapter",
            "producer_version": "ai395_source_adapter_v1",
            "manifest": item,
        })
    mineru = verified["mineru"]
    rows.append({
        "artifact_id": f"{run_id}:{mineru['mineru_run_id']}",
        "artifact_type": "mineru_output",
        "status": mineru.get("status", "existing_artifact"),
        "path": mineru["resolved_output_root"],
        "sha256": mineru["input_sha256"],
        "bytes": None,
        "producer": "MinerU",
        "producer_version": str(mineru.get("mineru_version", "unknown")),
        "manifest": mineru,
    })
    for row in rows:
        db.execute(
            f"""INSERT INTO {table}
                (artifact_id, run_id, artifact_type, status, path, sha256, bytes,
                 producer, producer_version, immutable, read_only, manifest_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                ON CONFLICT(artifact_id) DO NOTHING""",
            (row["artifact_id"], run_id, row["artifact_type"], row["status"], row["path"], row["sha256"], row["bytes"], row["producer"], row["producer_version"], canonical_json(row["manifest"]), now_iso()),
        )
    db.commit()


def insert_candidate_and_revisions(db: StagingDB, run_id: str, item: dict[str, Any]) -> str:
    candidate_key = str(item["candidate_key"])
    status, expected, actual, issue_count = parser_status(item)
    raw = canonical_json(item)
    input_hash = sha256_text(raw)
    table = db.table("pipeline_candidates")
    db.execute(
        f"""INSERT INTO {table}
            (run_id, candidate_key, source_registry_key, question_number, raw_candidate_json,
             input_sha256, parser_status, expected_question_count, actual_question_count,
             issue_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, candidate_key) DO UPDATE SET raw_candidate_json=excluded.raw_candidate_json,
              input_sha256=excluded.input_sha256, parser_status=excluded.parser_status,
              expected_question_count=excluded.expected_question_count,
              actual_question_count=excluded.actual_question_count, issue_count=excluded.issue_count""",
        (run_id, candidate_key, str(item["source_registry_key"]), str(item["question_number"]), raw, input_hash, status, expected, actual, issue_count, now_iso()),
    )
    revision_table = db.table("question_revisions")
    revision_id = f"{run_id}:{candidate_key}:r1"
    db.execute(
        f"""INSERT INTO {revision_table}
            (revision_id, candidate_key, run_id, revision_no, parent_revision_id,
             content_hash, content_json, changed_fields, created_by_kind, status, created_at)
            VALUES (?, ?, ?, 1, NULL, ?, ?, ?, 'parser', ?, ?)
            ON CONFLICT(revision_id) DO NOTHING""",
        (revision_id, candidate_key, run_id, input_hash, raw, "[]", "exception" if status == "blocked" else "active", now_iso()),
    )
    metadata = item.get("metadata") or {}
    patch_kind = metadata.get("deterministic_patch")
    if patch_kind == "collapse_whitespace" and status != "blocked":
        patched = copy.deepcopy(item)
        patched["stem"] = " ".join(str(patched.get("stem", "")).split())
        patched_raw = canonical_json(patched)
        patched_hash = sha256_text(patched_raw)
        if patched_raw != raw:
            revision2 = f"{run_id}:{candidate_key}:r2"
            db.execute(
                f"UPDATE {revision_table} SET status='superseded' WHERE run_id=? AND candidate_key=? AND status='active'",
                (run_id, candidate_key),
            )
            db.execute(
                f"""INSERT INTO {revision_table}
                    (revision_id, candidate_key, run_id, revision_no, parent_revision_id,
                     content_hash, content_json, changed_fields, created_by_kind, status, created_at)
                    VALUES (?, ?, ?, 2, ?, ?, ?, ?, 'deterministic_rule', 'active', ?)
                    ON CONFLICT(revision_id) DO NOTHING""",
                (revision2, candidate_key, run_id, revision_id, patched_hash, patched_raw, json.dumps(["stem"], ensure_ascii=False), now_iso()),
            )
            proposal_table = db.table("correction_proposals")
            db.execute(
                f"""INSERT INTO {proposal_table}
                    (run_id, candidate_key, parent_revision_id, proposed_revision_id, source,
                     materializable, status, changed_fields, before_json, after_json, evidence_json, created_at)
                    SELECT ?, ?, ?, ?, 'deterministic_rule', 1, 'materialized', ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                      SELECT 1 FROM {proposal_table} WHERE run_id=? AND candidate_key=? AND parent_revision_id=?
                    )""",
                (run_id, candidate_key, revision_id, revision2, json.dumps(["stem"], ensure_ascii=False), raw, patched_raw, json.dumps({
                    "rule_id": "fixture.collapse_whitespace",
                    "exact_unique_match": True,
                    "three_evidence": True,
                    "evidence": [
                        {"kind": "official_pdf", "family": "fixture-official", "reference": "fixture:official_pdf:mini20"},
                        {"kind": "mineru_alignment", "family": "fixture-layout", "reference": "fixture:mineru_layout:mini20"},
                        {"kind": "gold_regression", "family": "fixture-gold", "reference": "fixture:gold:mini20"},
                    ],
                }, ensure_ascii=False), now_iso(), run_id, candidate_key, revision_id),
            )
            revision_id = revision2
    db.commit()
    return revision_id


def enqueue_jobs(db: StagingDB, run_id: str, candidate_key: str, revision_id: str) -> None:
    table = db.table("pipeline_jobs")
    jobs = [("parser", None), *[(f"lane:{lane}", lane) for lane in LANES]]
    for node_key, lane in jobs:
        idempotency = f"{run_id}:{candidate_key}:{revision_id}:{node_key}"
        payload_hash = sha256_text(canonical_json({"candidate_key": candidate_key, "revision_id": revision_id, "node_key": node_key}))
        db.execute(
            f"""INSERT INTO {table}
                (run_id, candidate_key, revision_id, node_key, lane_key, resource_class,
                 status, attempt, idempotency_key, payload_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING""",
            (run_id, candidate_key, revision_id, node_key, lane, "deterministic_cpu" if lane is None else "external_llm", idempotency, payload_hash, now_iso(), now_iso()),
        )


def build_model_runtime(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Resolve an explicit staging model mode without changing production config."""

    mode = str(getattr(args, "model_mode", "mock"))
    model_stage = config.get("pipeline", {}).get("model_stage") or {}
    if model_stage.get("advisory_only") is not True:
        raise StagingContractError("model stage must remain advisory_only")
    if mode == "mock":
        return {
            "mode": "mock",
            "provider": "mock",
            "model": "mock-fixture-v1",
            "profile_id": "mock-fixture-v1",
            "prompt_version": None,
            "base_url": None,
            "timeout": 0,
            "max_tokens": 0,
        }
    if mode == "litellm_glm":
        if not bool(getattr(args, "allow_live_provider", False)):
            raise StagingContractError("live provider requires the explicit --allow-live-provider flag")
        provider = (config.get("provider_registry", {}).get("providers") or {}).get("litellm_glm") or {}
        enable_env = str(provider.get("enable_env") or "LITELLM_GLM_ENABLED")
        env_enabled = os.environ.get(enable_env, "0").lower() in {"1", "true", "yes"}
        if provider.get("enabled") is not True and not env_enabled:
            raise StagingContractError(
                f"litellm_glm is disabled; set {enable_env}=1 for an explicit staging run"
            )
        endpoint_env = str(provider.get("endpoint_env") or "LITELLM_BASE_URL")
        model_env = str(provider.get("model_env") or "LITELLM_MODEL")
        credential_env = str(provider.get("credential_env") or "LITELLM_API_KEY")
        base_url = os.environ.get(endpoint_env, "").strip()
        model = os.environ.get(model_env, "").strip() or str(provider.get("model_default") or "glm-5.3-flash")
        api_key = os.environ.get(credential_env, "").strip()
        if not base_url or not api_key:
            raise StagingContractError(
                f"litellm_glm requires {endpoint_env} and {credential_env}; keep the secret out of the repository"
            )
        allow_insecure_http = os.environ.get("LITELLM_ALLOW_INSECURE_HTTP", "0").lower() in {"1", "true", "yes"}
        try:
            base_url, endpoint_class = normalized_openai_base_url(
                base_url,
                allow_insecure_http=allow_insecure_http,
            )
        except LLMAdapterError as exc:
            raise StagingContractError(f"invalid litellm_glm endpoint: {exc}") from exc
        if provider.get("network_allowed") is not True:
            raise StagingContractError("litellm_glm provider contract does not allow network transport")
        transport = str(provider.get("transport") or "openai_chat_completions")
        if transport not in {"openai_compatible", "openai_chat_completions", "litellm_openai_compatible"}:
            raise StagingContractError(f"unsupported litellm_glm transport: {transport}")
        context_policy = model_stage.get("context_policy") or {}
        context_limit_tokens = int(
            getattr(args, "context_limit_tokens", None)
            or context_policy.get("hard_limit_tokens", DEFAULT_CONTEXT_LIMIT_TOKENS)
        )
        context_safety_margin_tokens = int(
            getattr(args, "context_safety_margin_tokens", None)
            or context_policy.get("safety_margin_tokens", 8_192)
        )
        if context_limit_tokens <= 0 or context_limit_tokens > MAX_LITELLM_CONTEXT_TOKENS:
            raise StagingContractError(
                f"litellm_glm context limit must be between 1 and {MAX_LITELLM_CONTEXT_TOKENS} tokens"
            )
        if context_safety_margin_tokens < 0 or context_safety_margin_tokens >= context_limit_tokens:
            raise StagingContractError("invalid litellm_glm context safety margin")
        context_extension_limit_tokens = int(
            context_policy.get("one_time_extension_limit_tokens", context_limit_tokens)
        )
        if (
            context_extension_limit_tokens < context_limit_tokens
            or context_extension_limit_tokens > MAX_LITELLM_CONTEXT_TOKENS
        ):
            raise StagingContractError(
                "litellm_glm one_time_extension_limit_tokens must be between the hard limit and "
                f"{MAX_LITELLM_CONTEXT_TOKENS}"
            )
        max_tool_turns = int(
            getattr(args, "max_tool_turns", None)
            if getattr(args, "max_tool_turns", None) is not None
            else context_policy.get("max_tool_turns", 1)
        )
        if max_tool_turns < 0 or max_tool_turns > 2:
            raise StagingContractError("max_tool_turns must be between 0 and 2")
        reasoning_effort = os.environ.get(
            str(provider.get("reasoning_effort_env") or "LITELLM_REASONING_EFFORT"),
            str(provider.get("reasoning_effort_default") or "low"),
        ).strip().lower()
        if reasoning_effort not in {"low", "high", "max"}:
            raise StagingContractError("LITELLM_REASONING_EFFORT must be low, high, or max")
        send_reasoning_effort = os.environ.get(
            str(provider.get("send_reasoning_effort_env") or "LITELLM_SEND_REASONING_EFFORT"),
            "1" if provider.get("send_reasoning_effort_default", False) else "0",
        ).lower() in {"1", "true", "yes"}
        send_thinking = os.environ.get(
            str(provider.get("send_thinking_env") or "LITELLM_SEND_THINKING"),
            "1" if provider.get("send_thinking_default", False) else "0",
        ).lower() in {"1", "true", "yes"}
        clear_thinking = (
            os.environ.get(
                str(provider.get("clear_thinking_env") or "LITELLM_CLEAR_THINKING"),
                "1" if provider.get("clear_thinking_default", False) else "0",
            ).lower() in {"1", "true", "yes"}
            if send_thinking
            else None
        )
        return {
            "mode": "litellm_glm",
            "provider": "litellm_glm",
            "model": model,
            "profile_id": str(provider.get("profile_id") or "glm-5.3-flash-litellm"),
            "prompt_version": PROMPT_VERSION,
            "base_url": base_url,
            "timeout": float(getattr(args, "model_timeout", 120.0)),
            "max_tokens": int(getattr(args, "model_max_tokens", 512)),
            "transport": transport,
            "endpoint_class": endpoint_class,
            "enabled_by_env": env_enabled,
            "api_key_env": credential_env,
            "reasoning_effort": reasoning_effort if send_reasoning_effort else None,
            "configured_reasoning_effort": reasoning_effort,
            "send_reasoning_effort": send_reasoning_effort,
            "clear_thinking": clear_thinking,
            "send_thinking": send_thinking,
            "allow_insecure_http": allow_insecure_http,
            "context_limit_tokens": context_limit_tokens,
            "context_extension_limit_tokens": context_extension_limit_tokens,
            "max_context_extensions_per_lane": 1,
            "context_safety_margin_tokens": context_safety_margin_tokens,
            "slow_threshold_seconds": float(
                getattr(args, "model_slow_threshold", None)
                or context_policy.get("slow_threshold_seconds", 30.0)
            ),
            "max_tool_turns": max_tool_turns,
        }
    if mode != "local_qwen_mlx":
        raise StagingContractError(f"unsupported model_mode: {mode}")
    if not bool(getattr(args, "allow_live_provider", False)):
        raise StagingContractError("live provider requires the explicit --allow-live-provider flag")
    provider = (config.get("provider_registry", {}).get("providers") or {}).get("local_qwen_mlx") or {}
    env_enabled = os.environ.get("QWEN_MLX_ENABLED", "0").lower() in {"1", "true", "yes"}
    if provider.get("enabled") is not True and not env_enabled:
        raise StagingContractError("local_qwen_mlx is disabled; set QWEN_MLX_ENABLED=1 for an explicit staging run")
    base_url = os.environ.get(str(provider.get("endpoint_env") or "QWEN_MLX_BASE_URL"), "").strip()
    model = os.environ.get(str(provider.get("model_env") or "QWEN_MLX_MODEL"), "").strip()
    if not base_url or not model:
        raise StagingContractError("local_qwen_mlx requires QWEN_MLX_BASE_URL and QWEN_MLX_MODEL")
    try:
        base_url, endpoint_class = normalized_base_url(base_url)
    except ProbeError as exc:
        raise StagingContractError(f"invalid local_qwen_mlx endpoint: {exc}") from exc
    if provider.get("network_allowed") is not True or provider.get("tailnet_only") is not True:
        raise StagingContractError("local_qwen_mlx provider contract does not allow the required tailnet transport")
    context_policy = model_stage.get("context_policy") or {}
    context_limit_tokens = int(getattr(args, "context_limit_tokens", None) or context_policy.get("hard_limit_tokens", DEFAULT_CONTEXT_LIMIT_TOKENS))
    context_safety_margin_tokens = int(getattr(args, "context_safety_margin_tokens", None) or context_policy.get("safety_margin_tokens", 8_192))
    if context_limit_tokens <= 0 or context_limit_tokens > MAX_LOCAL_QWEN_CONTEXT_TOKENS:
        raise StagingContractError(
            f"local_qwen_mlx context limit must be between 1 and {MAX_LOCAL_QWEN_CONTEXT_TOKENS} tokens"
        )
    if context_safety_margin_tokens < 0 or context_safety_margin_tokens >= context_limit_tokens:
        raise StagingContractError("invalid local_qwen_mlx context safety margin")
    max_tool_turns = int(getattr(args, "max_tool_turns", None) if getattr(args, "max_tool_turns", None) is not None else context_policy.get("max_tool_turns", 1))
    if max_tool_turns < 0 or max_tool_turns > 2:
        raise StagingContractError("max_tool_turns must be between 0 and 2")
    return {
        "mode": "local_qwen_mlx",
        "provider": "local_qwen_mlx",
        "model": model,
        "profile_id": "qwen3.8-27b-mlx-tailscale",
        "prompt_version": PROMPT_VERSION,
        "base_url": base_url,
        "timeout": float(getattr(args, "model_timeout", 120.0)),
        "max_tokens": int(getattr(args, "model_max_tokens", 512)),
        "transport": str(provider.get("transport") or "ollama_native"),
        "endpoint_class": endpoint_class,
        "enabled_by_env": env_enabled,
        # This is a staging admission limit for the MacBook provider.  It is
        # intentionally independent of AI395's current llama.cpp setting;
        # AI395 is not routed by this mode.
        "context_limit_tokens": context_limit_tokens,
        "context_safety_margin_tokens": context_safety_margin_tokens,
        "slow_threshold_seconds": float(getattr(args, "model_slow_threshold", None) or context_policy.get("slow_threshold_seconds", 30.0)),
        "max_tool_turns": max_tool_turns,
    }


def lane_needs_model(lane: str, revised: dict[str, Any], policy: str) -> bool:
    if policy == "all":
        return True
    metadata = revised.get("metadata") or {}
    configured_targets = metadata.get("llm_lane_targets")
    if isinstance(configured_targets, list):
        return lane in {str(value) for value in configured_targets}
    if lane == "text_evidence":
        return bool(metadata.get("demo_text_issue") or metadata.get("llm_residual"))
    if lane == "notation":
        return bool(metadata.get("demo_notation_issue") or metadata.get("llm_residual_notation"))
    if lane == "group":
        return bool(revised.get("group_ref") and metadata.get("group_boundary") == "ambiguous")
    if lane == "vision":
        return bool(revised.get("image_refs") or metadata.get("visual_expected") or metadata.get("visual_cue") or metadata.get("crop_status") == "bad")
    if lane == "answer":
        return bool(metadata.get("answer_mode") in {"voided", "multiple"} or revised.get("answer") == "#" or isinstance(revised.get("answer"), list))
    return False


def model_finding(model_result: dict[str, Any], lane: str) -> dict[str, Any] | None:
    status = str(model_result.get("status") or "unclear")
    if status == "pass":
        return None
    codes = model_result.get("finding_codes") or []
    if codes:
        code = str(codes[0])
    elif model_result.get("error") == "pixels_unavailable":
        code = "pixels_unavailable"
    elif model_result.get("error") == "provider_error":
        code = "provider_error"
    elif model_result.get("error") == "model_call_budget_exhausted":
        code = "call_budget_exhausted"
    elif model_result.get("error") == "context_budget_exceeded":
        code = "context_budget_exceeded"
    else:
        code = "model_unclear" if status == "unclear" else "model_finding"
    severity = "error" if status in {"unclear", "error"} or lane in {"group", "vision", "answer"} else "warning"
    return {
        "code": f"model_{code}"[:100],
        "severity": severity,
        "message": str(model_result.get("reason") or model_result.get("message") or "模型要求人工核對")[:160],
    }


def evaluate_lane(lane: str, item: dict[str, Any], revised: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    metadata = revised.get("metadata") or {}
    findings: list[dict[str, Any]] = []
    status = "machine_pass"
    owner = {"text_evidence": "question", "notation": "question", "group": "group", "vision": "image", "answer": "answer"}[lane]

    if lane == "text_evidence" and metadata.get("demo_text_issue") and "  " in str(revised.get("stem", "")):
        findings.append({"code": "text_residual", "severity": "warning", "message": "text residual remains after deterministic pass"})
    elif lane == "notation" and metadata.get("demo_notation_issue"):
        findings.append({"code": "notation_residual", "severity": "warning", "message": "notation requires PDF/pixel evidence"})
    elif lane == "group" and revised.get("group_ref") and metadata.get("group_boundary") == "ambiguous":
        findings.append({"code": "group_boundary_ambiguous", "severity": "warning", "message": "group range is advisory and needs human confirmation"})
    elif lane == "vision":
        refs = revised.get("image_refs") or []
        expected = bool(metadata.get("visual_expected") or metadata.get("visual_cue"))
        if refs and not metadata.get("visual_expected", True):
            findings.append({"code": "false_positive_image", "severity": "warning", "message": "asset exists but text evidence says no image is required"})
        elif expected and not refs:
            findings.append({"code": "missing_visual_asset", "severity": "error", "message": "visual cue has no existing asset"})
        elif refs and metadata.get("crop_status") == "bad":
            findings.append({"code": "crop_needed", "severity": "error", "message": "existing image crop requires before/after evidence"})
    elif lane == "answer":
        answer = revised.get("answer")
        if metadata.get("answer_mode") in {"voided", "multiple"} or answer == "#" or isinstance(answer, list):
            findings.append({"code": "answer_special", "severity": "warning", "message": "ANS/MOD requires answer-owner review"})

    if findings:
        status = "finding"
    result = {
        "checked_count": 1,
        "status": status,
        "finding_codes": [finding["code"] for finding in findings],
        "advisory_only": True,
        "model_action": "proposal_only" if findings else "no_model_finding",
    }
    return status, result, findings


def run_model_agent(
    *,
    db: StagingDB,
    run_id: str,
    lane: str,
    item: dict[str, Any],
    revised: dict[str, Any],
    fixture_root: Path,
    model_runtime: dict[str, Any],
    model_budget: dict[str, Any],
    evidence_cache_dir: Path,
) -> dict[str, Any]:
    """Run bounded model turns with allow-listed evidence tool requests."""

    evidence: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    final_result: dict[str, Any] | None = None
    compact_level = int(model_budget.get("compact_level", 0))
    max_tool_turns = int(model_runtime.get("max_tool_turns", 1))
    slow_threshold = float(model_runtime.get("slow_threshold_seconds", 30.0))

    turn = 0
    active_context_limit_tokens = int(
        model_runtime.get("context_limit_tokens", DEFAULT_CONTEXT_LIMIT_TOKENS)
    )
    context_extension_limit_tokens = int(
        model_runtime.get("context_extension_limit_tokens", active_context_limit_tokens)
    )
    context_extension_used = False
    while True:
        if model_budget["calls"] >= model_budget["max_calls"]:
            final_result = {
                "status": "error",
                "error": "model_call_budget_exhausted",
                "advisory_only": True,
                "materialize": False,
                "requested_evidence": [],
            }
            model_budget["errors"] += 1
            break
        model_budget["attempts"] += 1
        model_budget["calls"] += 1
        started = time.monotonic()
        try:
            result = call_lane(
                base_url=str(model_runtime["base_url"]),
                model=str(model_runtime["model"]),
                lane=lane,
                item=item,
                revised=revised,
                fixture_root=fixture_root,
                timeout=float(model_runtime["timeout"]),
                max_tokens=int(model_runtime["max_tokens"]),
                transport=str(model_runtime["transport"]),
                provider=str(model_runtime.get("provider") or "local_qwen_mlx"),
                api_key=(
                    os.environ.get(str(model_runtime.get("api_key_env")), "").strip()
                    if model_runtime.get("api_key_env")
                    else None
                ),
                reasoning_effort=model_runtime.get("reasoning_effort"),
                clear_thinking=model_runtime.get("clear_thinking"),
                allow_insecure_http=bool(model_runtime.get("allow_insecure_http", False)),
                evidence=evidence,
                compact_level=compact_level,
                context_limit_tokens=active_context_limit_tokens,
                context_safety_margin_tokens=int(model_runtime.get("context_safety_margin_tokens", 8_192)),
            )
        except PixelsUnavailable as exc:
            model_budget["calls"] -= 1
            result = {
                "status": "error",
                "error": "pixels_unavailable",
                "message": str(exc),
                "advisory_only": True,
                "materialize": False,
                "requested_evidence": [],
            }
            model_budget["errors"] += 1
        except ContextBudgetExceeded as exc:
            model_budget["calls"] -= 1
            model_budget["context_rejections"] += 1
            can_extend_context = (
                model_runtime.get("mode") == "litellm_glm"
                and not context_extension_used
                and context_extension_limit_tokens > active_context_limit_tokens
                and context_extension_limit_tokens <= MAX_LITELLM_CONTEXT_TOKENS
            )
            if can_extend_context:
                context_extension_used = True
                active_context_limit_tokens = context_extension_limit_tokens
                model_budget["context_extensions"] = model_budget.get("context_extensions", 0) + 1
                result = {
                    "status": "retry",
                    "error": "context_extension_retry",
                    "message": str(exc),
                    "context_guard": exc.details,
                    "advisory_only": True,
                    "materialize": False,
                    "requested_evidence": [],
                    "context_extension_limit_tokens": active_context_limit_tokens,
                }
            else:
                result = {
                    "status": "error",
                    "error": "context_budget_exceeded",
                    "message": str(exc),
                    "context_guard": exc.details,
                    "advisory_only": True,
                    "materialize": False,
                    "requested_evidence": [],
                }
                model_budget["errors"] += 1
        except LLMAdapterError as exc:
            result = {
                "status": "error",
                "error": "provider_error",
                "message": str(exc),
                "advisory_only": True,
                "materialize": False,
                "requested_evidence": [],
            }
            model_budget["errors"] += 1
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        result["turn"] = turn
        result["latency_ms"] = elapsed_ms
        result["latency_status"] = "slow" if elapsed_ms > slow_threshold * 1000 else "normal"
        result["compact_level"] = compact_level
        result["model_called"] = result.get("error") not in {
            "pixels_unavailable",
            "context_budget_exceeded",
            "context_extension_retry",
            "model_call_budget_exhausted",
        }
        trace_row: dict[str, Any] = {
            "turn": turn,
            "latency_ms": elapsed_ms,
            "latency_status": result["latency_status"],
            "compact_level": compact_level,
            "requested_evidence": result.get("requested_evidence") or [],
        }
        if result.get("context_guard"):
            trace_row["context_guard"] = result["context_guard"]
        if result["latency_status"] == "slow":
            model_budget["slow_calls"] += 1
            model_budget["compact_level"] = 1
            compact_level = 1
            trace_row["adaptive_action"] = "compact_context_for_next_turn_and_lane"

        final_result = result
        if result.get("error") == "context_extension_retry":
            trace_row["adaptive_action"] = "one_time_context_extension"
            trace.append(trace_row)
            continue
        requests = result.get("requested_evidence") or []
        if not requests or result.get("error") or turn >= max_tool_turns:
            trace.append(trace_row)
            break
        tool_started = time.monotonic()
        tool_results = collect_evidence(
            requests,
            item,
            cache_dir=evidence_cache_dir,
            compact_level=compact_level,
        )
        tool_elapsed_ms = round((time.monotonic() - tool_started) * 1000, 1)
        trace_row["tool_elapsed_ms"] = tool_elapsed_ms
        trace_row["tool_results"] = tool_results
        trace.append(trace_row)
        model_budget["tool_turns"] += 1
        evidence = tool_results
        turn += 1

    if final_result is None:
        final_result = {
            "status": "error",
            "error": "model_agent_no_result",
            "advisory_only": True,
            "materialize": False,
            "requested_evidence": [],
        }
        model_budget["errors"] += 1
    final_result["agentic_trace"] = trace
    final_result["tool_turn_count"] = sum(1 for row in trace if row.get("tool_results"))
    final_result["context_policy"] = {
        "hard_limit_tokens": int(model_runtime.get("context_limit_tokens", DEFAULT_CONTEXT_LIMIT_TOKENS)),
        "effective_limit_tokens": active_context_limit_tokens,
        "safety_margin_tokens": int(model_runtime.get("context_safety_margin_tokens", 8_192)),
        "slow_threshold_seconds": slow_threshold,
        "context_extension_used": context_extension_used,
    }
    return final_result


def run_lane(
    db: StagingDB,
    run_id: str,
    candidate_key: str,
    revision_id: str,
    item: dict[str, Any],
    revised: dict[str, Any],
    lane: str,
    parser_blocked: bool,
    model_runtime: dict[str, Any],
    model_budget: dict[str, Any],
    fixture_root: Path,
    llm_lane_policy: str,
    evidence_cache_dir: Path,
) -> None:
    table = db.table("review_lane_runs")
    if parser_blocked:
        status = "skipped"
        result = {"checked_count": 0, "status": "skipped", "reason": "parser_blocked", "advisory_only": True}
        findings: list[dict[str, Any]] = []
    else:
        _deterministic_status, result, findings = evaluate_lane(lane, item, revised)
        result["provider"] = model_runtime["provider"]
        result["model"] = model_runtime["model"]
        result["model_called"] = False
        result["model_result"] = None
        if model_runtime["mode"] in {"local_qwen_mlx", "litellm_glm"} and lane_needs_model(lane, revised, llm_lane_policy):
            model_result = run_model_agent(
                db=db,
                run_id=run_id,
                lane=lane,
                item=item,
                revised=revised,
                fixture_root=fixture_root,
                model_runtime=model_runtime,
                model_budget=model_budget,
                evidence_cache_dir=evidence_cache_dir,
            )
            result["model_called"] = bool(model_result.get("model_called"))
            result["model_result"] = model_result
            finding = model_finding(model_result, lane)
            if finding:
                findings.append(finding)
        status = "finding" if findings else "machine_pass"
        result["status"] = status
        result["finding_codes"] = [finding["code"] for finding in findings]
        result["model_action"] = "proposal_only" if findings else "no_model_finding"
    output_hash = sha256_text(canonical_json(result))
    route_key = lane
    provider = str(model_runtime["provider"])
    model_name = str(model_runtime["model"])
    db.execute(
        f"""INSERT INTO {table}
            (run_id, candidate_key, revision_id, lane_key, route_key, provider, model_name,
             status, advisory_only, result_json, output_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(run_id, candidate_key, revision_id, lane_key) DO NOTHING""",
        (run_id, candidate_key, revision_id, lane, route_key, provider, model_name, status, canonical_json(result), output_hash, now_iso()),
    )
    lane_row = db.fetchone(
        f"SELECT lane_run_id FROM {table} WHERE run_id=? AND candidate_key=? AND revision_id=? AND lane_key=?",
        (run_id, candidate_key, revision_id, lane),
    )
    if lane_row and findings:
        finding_table = db.table("review_findings")
        existing = db.fetchone(f"SELECT 1 FROM {finding_table} WHERE lane_run_id=?", (lane_row["lane_run_id"],))
        if not existing:
            for finding in findings:
                disposition = "human_exception" if finding["severity"] in {"error", "blocked"} or lane in {"group", "answer"} or finding["code"].startswith("model_") or finding["code"] in {"notation_residual", "false_positive_image", "text_residual", "missing_visual_asset", "crop_needed"} else "advisory"
                db.execute(
                    f"""INSERT INTO {finding_table}
                        (lane_run_id, run_id, candidate_key, revision_id, lane_key, owner_stage,
                         finding_code, severity, disposition, evidence_json, proposal_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (lane_row["lane_run_id"], run_id, candidate_key, revision_id, lane, {"text_evidence": "question", "notation": "question", "group": "group", "vision": "image", "answer": "answer"}[lane], finding["code"], finding["severity"], disposition, canonical_json({"fixture": True, "source_fidelity_required": True, "provider": provider, "model": model_name}), canonical_json({"materialize": False, "advisory_only": True}), now_iso()),
                )
                if disposition == "human_exception":
                    exception_table = db.table("review_exception_queue")
                    exists = db.fetchone(
                        f"SELECT 1 FROM {exception_table} WHERE run_id=? AND candidate_key=? AND revision_id=? AND owner_stage=? AND reason_code=?",
                        (run_id, candidate_key, revision_id, {"text_evidence": "question", "notation": "question", "group": "group", "vision": "image", "answer": "answer"}[lane], finding["code"]),
                    )
                    if not exists:
                        db.execute(
                            f"""INSERT INTO {exception_table}
                                (run_id, candidate_key, revision_id, owner_stage, reason_code,
                                 severity, status, evidence_json, ai_advisory_only, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, 'open', ?, 1, ?)""",
                            (run_id, candidate_key, revision_id, {"text_evidence": "question", "notation": "question", "group": "group", "vision": "image", "answer": "answer"}[lane], finding["code"], finding["severity"], canonical_json({"lane": lane, "message": finding["message"]}), now_iso()),
                        )
    job_table = db.table("pipeline_jobs")
    db.execute(
        f"UPDATE {job_table} SET status=?, attempt=attempt+1, updated_at=? WHERE run_id=? AND candidate_key=? AND revision_id=? AND lane_key=?",
        ("succeeded" if status in {"machine_pass", "finding", "skipped"} else "failed", now_iso(), run_id, candidate_key, revision_id, lane),
    )
    # Persist each lane checkpoint so a slow or interrupted advisory batch can
    # be observed without waiting for the final dry-run transaction.  This is
    # still isolated staging; no production or human-review event is written.
    db.commit()


def add_parser_exception(db: StagingDB, run_id: str, candidate_key: str, revision_id: str, item: dict[str, Any]) -> None:
    status, expected, actual, issue_count = parser_status(item)
    if status != "blocked":
        return
    table = db.table("review_exception_queue")
    exists = db.fetchone(
        f"SELECT 1 FROM {table} WHERE run_id=? AND candidate_key=? AND revision_id=? AND reason_code='parser_blocked'",
        (run_id, candidate_key, revision_id),
    )
    if not exists:
        db.execute(
            f"""INSERT INTO {table}
                (run_id, candidate_key, revision_id, owner_stage, reason_code, severity,
                 status, evidence_json, ai_advisory_only, created_at)
                VALUES (?, ?, ?, 'parser', 'parser_blocked', 'blocked', 'open', ?, 1, ?)""",
            (run_id, candidate_key, revision_id, canonical_json({"expected": expected, "actual": actual, "issue_count": issue_count}), now_iso()),
        )


def add_source_issue_exceptions(
    db: StagingDB,
    run_id: str,
    revision_map: dict[str, str],
    issues: list[dict[str, Any]],
) -> None:
    """Project deterministic parser issue rows into the staging human queue.

    The parser CSV is immutable evidence. This projection is append-safe and
    remains staging-only; it does not impersonate a human review event.
    """

    owner_by_code = {
        "image_hint_without_asset": "image",
        "missing_image_asset": "image",
        "empty_image_asset": "image",
        "markup_needs_review": "question",
        "amino_acid_translation_suspect": "question",
        "suspicious_ocr_chars": "question",
        "option_order_unusual": "question",
        "too_few_options": "question",
        "empty_option": "question",
        "missing_answer": "answer",
        "missing_answer_markdown": "answer",
    }
    exception_table = db.table("review_exception_queue")
    for issue in issues:
        candidate_key = str(issue.get("candidate_key") or "")
        if not candidate_key or candidate_key not in revision_map:
            continue
        code = str(issue.get("issue_code") or "parser_issue")
        revision_id = revision_map[candidate_key]
        owner_stage = owner_by_code.get(code, "parser")
        severity = str(issue.get("severity") or "warning")
        if severity not in {"info", "warning", "error", "blocked"}:
            severity = "warning"
        raw_issue_json = issue.get("issue_json") or "{}"
        try:
            issue_json = json.loads(raw_issue_json) if isinstance(raw_issue_json, str) else raw_issue_json
        except json.JSONDecodeError:
            issue_json = {"raw": raw_issue_json}
        reason_code = f"parser_{code}"[:120]
        exists = db.fetchone(
            f"SELECT 1 FROM {exception_table} WHERE run_id=? AND candidate_key=? AND revision_id=? AND owner_stage=? AND reason_code=?",
            (run_id, candidate_key, revision_id, owner_stage, reason_code),
        )
        if exists:
            continue
        db.execute(
            f"""INSERT INTO {exception_table}
                (run_id, candidate_key, revision_id, owner_stage, reason_code, severity,
                 status, evidence_json, ai_advisory_only, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?, 1, ?)""",
            (
                run_id,
                candidate_key,
                revision_id,
                owner_stage,
                reason_code,
                severity,
                canonical_json({
                    "source": "parser_issue_csv",
                    "issue_code": code,
                    "message": str(issue.get("message") or ""),
                    "question_number": issue.get("question_number"),
                    "issue_json": issue_json,
                }),
                now_iso(),
            ),
        )


def _json_object(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def _staging_feedback_rows(db: StagingDB, run_id: str) -> list[dict[str, Any]]:
    table = db.table("feedback_events")
    return [
        {
            "feedback_id": str(row["feedback_id"]),
            "run_id": str(row["run_id"]),
            "candidate_key": str(row["candidate_key"]),
            "revision_id": str(row["revision_id"] or ""),
            "source_kind": str(row["source_kind"]),
            "scope": str(row["scope"]),
            "lane": str(row["lane_key"] or ""),
            "event": _json_object(row["event_json"], {}),
        }
        for row in db.fetchall(
            f"SELECT feedback_id, run_id, candidate_key, revision_id, source_kind, scope, lane_key, event_json FROM {table} WHERE run_id=? ORDER BY created_at, feedback_id",
            (run_id,),
        )
    ]


def materialize_staging_feedback_events(
    db: StagingDB,
    run_id: str,
) -> tuple[list[dict[str, Any]], int]:
    """Turn only explicitly proven deterministic repairs into feedback rows.

    A normal deterministic patch is not automatically called a three-evidence
    correction.  The proposal must carry ``three_evidence=true`` and at least
    three independently named evidence families.  This prevents a convenient
    fixture or a single OCR guess from teaching a future guardrail.
    """

    proposal_table = db.table("correction_proposals")
    feedback_table = db.table("feedback_events")
    rows = db.fetchall(
        f"""SELECT proposal_id, candidate_key, parent_revision_id, proposed_revision_id,
                   before_json, after_json, evidence_json
            FROM {proposal_table}
            WHERE run_id=? AND source='deterministic_rule' AND status='materialized'
            ORDER BY proposal_id""",
        (run_id,),
    )
    events: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        evidence_payload = _json_object(row["evidence_json"], {})
        if not isinstance(evidence_payload, dict) or evidence_payload.get("three_evidence") is not True:
            skipped += 1
            continue
        evidence = evidence_payload.get("evidence")
        try:
            event = build_feedback_event(
                candidate_key=str(row["candidate_key"]),
                before=_json_object(row["before_json"], {}),
                after=_json_object(row["after_json"], {}),
                source_kind="three_evidence_correction",
                scope="question",
                lane="text_evidence",
                reviewer="deterministic_three_evidence",
                run_id=run_id,
                revision_id=str(row["proposed_revision_id"] or row["parent_revision_id"] or ""),
                event_ref=f"staging:correction_proposal:{row['proposal_id']}",
                evidence=evidence,
            )
        except (FeedbackContractError, NoVisibleChange):
            skipped += 1
            continue
        db.execute(
            f"""INSERT INTO {feedback_table}
                (feedback_id, run_id, candidate_key, revision_id, source_kind, scope,
                 lane_key, actor_kind, reviewer, event_ref, changed_fields, before_json,
                 after_json, diff_json, evidence_json, ai_task_json, event_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feedback_id) DO NOTHING""",
            (
                event["feedback_id"],
                run_id,
                event["candidate_key"],
                event["revision_id"],
                event["source_kind"],
                event["scope"],
                event["lane"],
                event["actor_kind"],
                event["reviewer"],
                event["event_ref"],
                canonical_json(event["changed_fields"]),
                canonical_json(event["before"]),
                canonical_json(event["after"]),
                canonical_json(event["diff"]),
                canonical_json(event["evidence"]),
                canonical_json(event["ai_task"]),
                canonical_json(event),
                event["created_at"],
            ),
        )
        events.append(event)
    db.commit()
    # Return the persisted view so a replay never creates a second logical
    # example even if a previous process stopped after the insert.
    persisted = [row["event"] for row in _staging_feedback_rows(db, run_id) if isinstance(row["event"], dict)]
    return persisted, skipped


def validate_guardrail_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Run fail-closed checks before an AI proposal enters staging history."""

    errors: list[str] = []
    policy = candidate.get("promotion_policy") if isinstance(candidate.get("promotion_policy"), dict) else {}
    if policy.get("active") is not False:
        errors.append("active_must_be_false")
    if policy.get("direct_file_write") is not False:
        errors.append("direct_file_write_must_be_false")
    rule = candidate.get("rule_proposal")
    if isinstance(rule, dict):
        if str(rule.get("status") or "").lower() not in {"observed", "proposed", "shadow", "retired", "revoked"}:
            errors.append("rule_status_not_non_active")
        if rule.get("rule_type") == "exact_replacement" and rule.get("source") == rule.get("target"):
            errors.append("exact_rule_source_equals_target")
    if candidate.get("status") == "proposed" and not candidate.get("negative_controls"):
        errors.append("negative_controls_missing")
    status = "pass" if not errors else "fail"
    return {
        "status": status,
        "safe_to_activate": False,
        "errors": errors,
        "checks": [
            "advisory_only",
            "owner_approval_required",
            "negative_controls_required",
            "no_direct_file_write",
        ],
    }


def _feedback_model_result(
    event: dict[str, Any],
    model_runtime: dict[str, Any],
    model_budget: dict[str, Any],
    evidence_cache_dir: Path,
) -> dict[str, Any]:
    """Call the selected provider once, with the same bounded extension rule."""

    if model_runtime.get("mode") == "mock":
        return {
            "status": "observed",
            "model_called": False,
            "advisory_only": True,
            "materialize": False,
            "rationale": "mock staging：只建立 feedback／guardrail contract，未呼叫模型。",
        }
    if model_budget["calls"] >= model_budget["max_calls"]:
        model_budget["errors"] += 1
        model_budget["feedback_errors"] = model_budget.get("feedback_errors", 0) + 1
        return {
            "status": "unclear",
            "error": "model_call_budget_exhausted",
            "model_called": False,
            "advisory_only": True,
            "materialize": False,
        }
    active_limit = int(model_runtime.get("context_limit_tokens", DEFAULT_CONTEXT_LIMIT_TOKENS))
    extension_limit = int(model_runtime.get("context_extension_limit_tokens", active_limit))
    extension_used = False
    while True:
        model_budget["attempts"] += 1
        model_budget["calls"] += 1
        model_budget["feedback_attempts"] = model_budget.get("feedback_attempts", 0) + 1
        try:
            result = call_feedback(
                base_url=str(model_runtime["base_url"]),
                model=str(model_runtime["model"]),
                feedback_packet=event["ai_task"],
                timeout=float(model_runtime["timeout"]),
                max_tokens=min(int(model_runtime.get("feedback_max_tokens", 768)), 2_048),
                transport=str(model_runtime["transport"]),
                provider=str(model_runtime.get("provider") or "litellm_glm"),
                api_key=(
                    os.environ.get(str(model_runtime.get("api_key_env")), "").strip()
                    if model_runtime.get("api_key_env")
                    else None
                ),
                reasoning_effort=model_runtime.get("reasoning_effort"),
                clear_thinking=model_runtime.get("clear_thinking"),
                allow_insecure_http=bool(model_runtime.get("allow_insecure_http", False)),
                context_limit_tokens=active_limit,
                context_safety_margin_tokens=int(model_runtime.get("context_safety_margin_tokens", 8_192)),
            )
            result["feedback_context_policy"] = {
                "hard_limit_tokens": int(model_runtime.get("context_limit_tokens", active_limit)),
                "effective_limit_tokens": active_limit,
                "context_extension_used": extension_used,
            }
            model_budget["feedback_calls"] = model_budget.get("feedback_calls", 0) + 1
            return result
        except ContextBudgetExceeded as exc:
            model_budget["calls"] -= 1
            model_budget["context_rejections"] += 1
            can_extend = (
                model_runtime.get("mode") == "litellm_glm"
                and not extension_used
                and extension_limit > active_limit
                and extension_limit <= MAX_LITELLM_CONTEXT_TOKENS
            )
            if can_extend:
                extension_used = True
                active_limit = extension_limit
                model_budget["context_extensions"] += 1
                continue
            model_budget["errors"] += 1
            model_budget["feedback_errors"] = model_budget.get("feedback_errors", 0) + 1
            return {
                "status": "unclear",
                "error": "context_budget_exceeded",
                "message": str(exc),
                "context_guard": exc.details,
                "model_called": False,
                "advisory_only": True,
                "materialize": False,
            }
        except LLMAdapterError as exc:
            model_budget["feedback_errors"] = model_budget.get("feedback_errors", 0) + 1
            model_budget["errors"] += 1
            return {
                "status": "unclear",
                "error": "provider_error",
                "message": str(exc),
                "model_called": False,
                "advisory_only": True,
                "materialize": False,
            }


def run_feedback_agents(
    db: StagingDB,
    run_id: str,
    model_runtime: dict[str, Any],
    model_budget: dict[str, Any],
    evidence_cache_dir: Path,
) -> list[dict[str, Any]]:
    """Curate staging feedback without applying any proposed guardrail."""

    feedback_rows = _staging_feedback_rows(db, run_id)
    guardrail_table = db.table("guardrail_candidates")
    output: list[dict[str, Any]] = []
    for row in feedback_rows:
        event = row["event"]
        if not isinstance(event, dict):
            continue
        ai_result = _feedback_model_result(event, model_runtime, model_budget, evidence_cache_dir)
        candidate = build_guardrail_candidate(
            event,
            ai_result=ai_result,
            validation={"status": "pending"},
            attempt=0,
        )
        validation = validate_guardrail_candidate(candidate)
        if validation["status"] == "fail":
            candidate["status"] = "rejected"
        candidate["validation"] = validation
        db.execute(
            f"""INSERT INTO {guardrail_table}
                (guardrail_id, feedback_id, run_id, candidate_key, scope, lane_key,
                 change_class, guardrail_type, status, candidate_json, validation_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guardrail_id) DO NOTHING""",
            (
                candidate["guardrail_id"],
                candidate["feedback_id"],
                run_id,
                candidate["candidate_key"],
                candidate["scope"],
                candidate.get("lane") or "",
                candidate["change_class"],
                candidate["guardrail_type"],
                candidate["status"],
                canonical_json(candidate),
                canonical_json(validation),
                candidate["created_at"],
            ),
        )
        output.append(candidate)
    db.commit()
    return output


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_formal_report(db: StagingDB, run_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    exceptions = db.table("review_exception_queue")
    blocked_keys = {
        str(row["candidate_key"])
        for row in db.fetchall(f"SELECT DISTINCT candidate_key FROM {exceptions} WHERE run_id=? AND status='open'", (run_id,))
    }
    all_keys = [str(item["candidate_key"]) for item in candidates]
    eligible = sorted(set(all_keys) - blocked_keys)
    blocked = sorted(blocked_keys)
    lane_table = db.table("review_lane_runs")
    lane_counts: dict[str, dict[str, int]] = {}
    for lane_row in db.fetchall(f"SELECT DISTINCT lane_key FROM {lane_table} WHERE run_id=? ORDER BY lane_key", (run_id,)):
        lane_key = str(lane_row["lane_key"])
        lane_counts[lane_key] = {
            str(status): int(count)
            for status, count in db.fetchall(
                f"SELECT status, count(*) AS count FROM {lane_table} WHERE run_id=? AND lane_key=? GROUP BY status",
                (run_id, lane_key),
            )
        }
    report = {
        "run_id": run_id,
        "scope_count": len(all_keys),
        "eligible_count": len(eligible),
        "blocked_count": len(blocked),
        "disjoint_coverage": len(set(eligible) | set(blocked)) == len(all_keys) and not (set(eligible) & set(blocked)),
        "eligible_candidates": eligible,
        "blocked_candidates": blocked,
        "lane_status_counts": lane_counts,
        "production_write_count": 0,
        "human_review_events_written": 0,
        "advisory_only": True,
        "feedback_event_count": int(db.fetchone(f"SELECT count(*) AS count FROM {db.table('feedback_events')} WHERE run_id=?", (run_id,))["count"]),
        "guardrail_candidate_count": int(db.fetchone(f"SELECT count(*) AS count FROM {db.table('guardrail_candidates')} WHERE run_id=?", (run_id,))["count"]),
        "guardrail_active_count": int(db.fetchone(f"SELECT count(*) AS count FROM {db.table('guardrail_candidates')} WHERE run_id=? AND status='active'", (run_id,))["count"]),
        "guardrail_owner_approval_required": True,
    }
    table = db.table("formal_dry_runs")
    db.execute(
        f"""INSERT INTO {table}
            (run_id, eligible_count, blocked_count, production_write_count, report_json, created_at)
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET eligible_count=excluded.eligible_count,
              blocked_count=excluded.blocked_count, production_write_count=0,
              report_json=excluded.report_json, created_at=excluded.created_at""",
        (run_id, len(eligible), len(blocked), canonical_json(report), now_iso()),
    )
    return report


def describe() -> dict[str, Any]:
    return {
        "component_id": "ai395_review_staging",
        "commands": {"doctor": "validate config and source/MinerU manifests", "e2e": "run parser, five advisory lanes, revision, exception queue, correction feedback and formal dry-run"},
        "inputs": ["source_manifest.json", "mineru_manifest.json", "candidate JSONL", "issue CSV"],
        "outputs": ["run_manifest.json", "summary.json", "formal_dry_run.json", "feedback_events.jsonl", "guardrail_candidates.jsonl", "staging database rows"],
        "config_keys": ["source_stage.enabled", "mineru_stage.enabled", "lane_stage.lanes", "feedback_stage.enabled", "feedback_stage.require_owner_approval", "revision_stage.allow_ai_materialization", "formal_stage.production_write"],
        "side_effect_class": "isolated_staging_only",
        "production_writes": False,
        "exit_codes": {"0": "success", "2": "contract failure", "3": "provider/retry failure", "4": "data failure", "5": "validation failure"},
    }


def locate_manifests(args: argparse.Namespace) -> tuple[Path, Path, str]:
    # An explicit artifact pair must win over the parser's backwards-compatible
    # mini20 default.  Without this ordering, a real existing-artifact run can
    # silently execute the fixture while still accepting the user's manifests.
    if args.source_manifest or args.mineru_manifest:
        if not args.source_manifest or not args.mineru_manifest:
            raise StagingContractError("provide both --source-manifest and --mineru-manifest")
        return args.source_manifest, args.mineru_manifest, "external-existing-artifact"
    if args.fixture:
        fixture = resolve_fixture(args.fixture)
        return fixture / "source_manifest.json", fixture / "mineru_manifest.json", args.fixture
    raise StagingContractError("provide --fixture or both --source-manifest and --mineru-manifest")


def run_e2e(args: argparse.Namespace) -> dict[str, Any]:
    config, base_config_hash = load_config(args.config_dir.resolve())
    model_runtime = build_model_runtime(config, args)
    llm_lane_policy = str(getattr(args, "llm_lane_policy", "residual"))
    if llm_lane_policy not in {"residual", "all"}:
        raise StagingContractError(f"unsupported llm_lane_policy: {llm_lane_policy}")
    max_calls = int(getattr(args, "llm_max_calls", 20))
    if max_calls < 0:
        raise StagingContractError("llm_max_calls must be >= 0")
    model_budget = {
        "attempts": 0,
        "calls": 0,
        "errors": 0,
        "max_calls": max_calls,
        "slow_calls": 0,
        "tool_turns": 0,
        "context_rejections": 0,
        "context_extensions": 0,
        "compact_level": 0,
        "feedback_attempts": 0,
        "feedback_calls": 0,
        "feedback_errors": 0,
    }
    effective_config_hash = sha256_text(canonical_json({
        "base_config_sha256": base_config_hash,
        "model_runtime": {key: value for key, value in model_runtime.items() if key != "base_url"},
        "model_endpoint": model_runtime.get("base_url"),
        "llm_lane_policy": llm_lane_policy,
        "llm_max_calls": max_calls,
    }))
    source_manifest, mineru_manifest, fixture_id = locate_manifests(args)
    source_mode = args.source_mode
    mineru_mode = args.mineru_mode
    if source_mode == "live_download" or mineru_mode == "run_mineru":
        raise StagingContractError("live download and MinerU execution are disabled in WP-00S; use existing_artifact or mock_fixture")
    verified = validate_manifests(source_manifest, mineru_manifest)
    candidate_path = Path(verified["source"]["candidate_jsonl"]["resolved_path"])
    issue_path = Path(verified["source"]["issue_csv"]["resolved_path"])
    candidates = load_candidates(candidate_path)
    issues = load_issues(issue_path)
    expected_total = None
    source_root = source_manifest.parent
    catalog_path = source_root / "source_catalog.json"
    if catalog_path.exists():
        catalog = load_json(catalog_path)
        expected_total = catalog.get("expected_question_count")
    if expected_total is not None and int(expected_total) != len(candidates):
        raise StagingContractError(f"fixture coverage mismatch: expected {expected_total}, got {len(candidates)}")
    fixture_root = Path(verified["mineru"].get("resolved_image_root") or source_manifest.resolve().parent)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("staging-%Y%m%dT%H%M%SZ")
    artifact_dir = (args.artifact_dir / run_id).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    evidence_cache_dir = artifact_dir / "evidence_cache"
    db = StagingDB(args.database_url)
    try:
        db.apply_migration()
        is_new = insert_run(db, run_id, effective_config_hash, source_mode, mineru_mode, fixture_id)
        if not is_new:
            existing = db.fetchone(f"SELECT report_json FROM {db.table('formal_dry_runs')} WHERE run_id=?", (run_id,))
            if existing:
                report = json.loads(existing["report_json"])
                report["idempotent_replay"] = True
                write_json(artifact_dir / "formal_dry_run.json", report)
                write_json(artifact_dir / "summary.json", report)
                return report
        register_artifacts(db, run_id, verified)
        revision_map: dict[str, str] = {}
        revised_items: dict[str, dict[str, Any]] = {}
        for item in candidates:
            revision_id = insert_candidate_and_revisions(db, run_id, item)
            revision_map[str(item["candidate_key"])] = revision_id
            row = db.fetchone(
                f"SELECT content_json FROM {db.table('question_revisions')} WHERE revision_id=?",
                (revision_id,),
            )
            revised_items[str(item["candidate_key"])] = json.loads(row["content_json"])
            enqueue_jobs(db, run_id, str(item["candidate_key"]), revision_id)
            add_parser_exception(db, run_id, str(item["candidate_key"]), revision_id, item)
        add_source_issue_exceptions(db, run_id, revision_map, issues)
        feedback_stage = config.get("pipeline", {}).get("feedback_stage") or {}
        if feedback_stage.get("enabled") is True:
            feedback_events, feedback_skipped = materialize_staging_feedback_events(db, run_id)
        else:
            feedback_events, feedback_skipped = [], 0
        db.commit()

        for item in candidates:
            key = str(item["candidate_key"])
            revision_id = revision_map[key]
            blocked = parser_status(item)[0] == "blocked"
            for lane in LANES:
                run_lane(
                    db,
                    run_id,
                    key,
                    revision_id,
                    item,
                    revised_items[key],
                    lane,
                    blocked,
                    model_runtime,
                    model_budget,
                    fixture_root,
                    llm_lane_policy,
                    evidence_cache_dir,
                )
        guardrail_candidates = (
            run_feedback_agents(
                db,
                run_id,
                model_runtime,
                model_budget,
                evidence_cache_dir,
            )
            if feedback_stage.get("enabled") is True
            else []
        )
        report = build_formal_report(db, run_id, candidates)
        report.update({
            "config_sha256": effective_config_hash,
            "base_config_sha256": base_config_hash,
            "source_mode": source_mode,
            "mineru_mode": mineru_mode,
            "fixture_id": fixture_id,
            "issue_rows": len(issues),
            "revisions_created": int(db.fetchone(f"SELECT count(*) AS count FROM {db.table('question_revisions')} WHERE run_id=?", (run_id,))["count"]),
            "model_mode": model_runtime["mode"],
            "model_provider": model_runtime["provider"],
            "model_name": model_runtime["model"],
            "model_transport": model_runtime.get("transport"),
            "model_endpoint_class": model_runtime.get("endpoint_class"),
            "model_profile_id": model_runtime["profile_id"],
            "model_prompt_version": model_runtime["prompt_version"],
            "llm_lane_policy": llm_lane_policy,
            "llm_attempts": model_budget["attempts"],
            "llm_calls": model_budget["calls"],
            "llm_errors": model_budget["errors"],
            "llm_slow_calls": model_budget["slow_calls"],
            "llm_tool_turns": model_budget["tool_turns"],
            "llm_context_rejections": model_budget["context_rejections"],
            "llm_context_extensions": model_budget["context_extensions"],
            "feedback_events": len(feedback_events),
            "feedback_skipped": feedback_skipped,
            "feedback_agent_attempts": model_budget["feedback_attempts"],
            "feedback_agent_calls": model_budget["feedback_calls"],
            "feedback_agent_errors": model_budget["feedback_errors"],
            "guardrail_candidates": len(guardrail_candidates),
            "guardrail_active": 0,
            "guardrail_owner_approval_required": True,
            "model_context_policy": {
                "hard_limit_tokens": model_runtime.get("context_limit_tokens"),
                "one_time_extension_limit_tokens": model_runtime.get("context_extension_limit_tokens"),
                "max_context_extensions_per_lane": model_runtime.get("max_context_extensions_per_lane", 0),
                "safety_margin_tokens": model_runtime.get("context_safety_margin_tokens"),
                "slow_threshold_seconds": model_runtime.get("slow_threshold_seconds"),
                "max_tool_turns": model_runtime.get("max_tool_turns"),
            },
        })
        db.execute(
            f"UPDATE {db.table('formal_dry_runs')} SET report_json=? WHERE run_id=?",
            (canonical_json(report), run_id),
        )
        write_json(artifact_dir / "formal_dry_run.json", report)
        write_jsonl(artifact_dir / "feedback_events.jsonl", feedback_events)
        write_jsonl(artifact_dir / "guardrail_candidates.jsonl", guardrail_candidates)
        run_manifest = {
            "run_id": run_id,
            "component_id": "ai395_review_staging",
            "script_path": "scripts/ai395_review_staging.py",
            "git_sha": git_sha(),
            "script_sha256": sha256_file(Path(__file__)),
            "config_sha256": effective_config_hash,
            "base_config_sha256": base_config_hash,
            "container_or_runtime": "python-local-or-staging-container",
            "container_image_digest": os.environ.get("AI395_CONTAINER_IMAGE_DIGEST"),
            "started_at": now_iso(),
            "input_artifacts": [verified["source"]["candidate_jsonl"], verified["source"]["issue_csv"], verified["mineru"]],
            "output_artifacts": [
                {"path": str(artifact_dir / "formal_dry_run.json"), "sha256": sha256_file(artifact_dir / "formal_dry_run.json")},
                {"path": str(artifact_dir / "feedback_events.jsonl"), "sha256": sha256_file(artifact_dir / "feedback_events.jsonl")},
                {"path": str(artifact_dir / "guardrail_candidates.jsonl"), "sha256": sha256_file(artifact_dir / "guardrail_candidates.jsonl")},
            ],
            "model_profile_id": model_runtime["profile_id"],
            "model_provider": model_runtime["provider"],
            "model_name": model_runtime["model"],
            "model_mode": model_runtime["mode"],
            "model_transport": model_runtime.get("transport"),
            "model_endpoint_class": model_runtime.get("endpoint_class"),
            "model_endpoint": model_runtime.get("base_url"),
            "prompt_version": model_runtime["prompt_version"],
            "llm_lane_policy": llm_lane_policy,
            "llm_attempts": model_budget["attempts"],
            "llm_calls": model_budget["calls"],
            "llm_errors": model_budget["errors"],
            "llm_slow_calls": model_budget["slow_calls"],
            "llm_tool_turns": model_budget["tool_turns"],
            "llm_context_rejections": model_budget["context_rejections"],
            "llm_context_extensions": model_budget["context_extensions"],
            "feedback_events": len(feedback_events),
            "feedback_skipped": feedback_skipped,
            "feedback_agent_attempts": model_budget["feedback_attempts"],
            "feedback_agent_calls": model_budget["feedback_calls"],
            "feedback_agent_errors": model_budget["feedback_errors"],
            "guardrail_candidates": len(guardrail_candidates),
            "guardrail_active": 0,
            "guardrail_owner_approval_required": True,
            "model_context_policy": {
                "hard_limit_tokens": model_runtime.get("context_limit_tokens"),
                "one_time_extension_limit_tokens": model_runtime.get("context_extension_limit_tokens"),
                "max_context_extensions_per_lane": model_runtime.get("max_context_extensions_per_lane", 0),
                "safety_margin_tokens": model_runtime.get("context_safety_margin_tokens"),
                "slow_threshold_seconds": model_runtime.get("slow_threshold_seconds"),
                "max_tool_turns": model_runtime.get("max_tool_turns"),
            },
            "parent_run_ids": [],
            "database_written": True,
            "review_events_written": False,
            "production_write_count": 0,
        }
        write_json(artifact_dir / "run_manifest.json", run_manifest)
        report["run_manifest_path"] = str(artifact_dir / "run_manifest.json")
        report["formal_dry_run_path"] = str(artifact_dir / "formal_dry_run.json")
        write_json(artifact_dir / "summary.json", report)
        db.execute(
            f"UPDATE {db.table('pipeline_runs')} SET status='dry_run', manifest_json=?, production_write_count=0, finished_at=? WHERE run_id=?",
            (canonical_json(run_manifest), now_iso(), run_id),
        )
        db.commit()
        return report
    finally:
        db.close()


def run_doctor(args: argparse.Namespace) -> dict[str, Any]:
    config, config_hash = load_config(args.config_dir.resolve())
    source_manifest, mineru_manifest, fixture_id = locate_manifests(args)
    verified = validate_manifests(source_manifest, mineru_manifest)
    return {
        "status": "pass",
        "config_sha256": config_hash,
        "source_mode": config["pipeline"]["source_stage"]["mode"],
        "mineru_mode": config["pipeline"]["mineru_stage"]["mode"],
        "source_manifest": str(source_manifest.resolve()),
        "mineru_manifest": str(mineru_manifest.resolve()),
        "fixture_id": fixture_id,
        "source_artifacts": len(verified["source"]["source_artifacts"]),
        "candidate_jsonl": verified["source"]["candidate_jsonl"]["resolved_path"],
        "mineru_output_root": verified["mineru"]["resolved_output_root"],
        "live_download_enabled": config["pipeline"]["source_stage"]["enabled"],
        "mineru_execution_enabled": config["pipeline"]["mineru_stage"]["enabled"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "e2e"), nargs="?", default="e2e")
    parser.add_argument("--fixture", default="mini20")
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--mineru-manifest", type=Path)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--artifact-dir", type=Path, default=PROJECT_ROOT / "tmp" / "ai395_review_staging")
    parser.add_argument("--database-url", default="sqlite:///tmp/ai395_review_staging.sqlite3")
    parser.add_argument("--run-id")
    parser.add_argument("--source-mode", choices=("existing_artifact", "mock_fixture", "live_download"), default="mock_fixture")
    parser.add_argument("--mineru-mode", choices=("existing_artifact", "mock_fixture", "run_mineru"), default="mock_fixture")
    parser.add_argument("--model-mode", choices=("mock", "local_qwen_mlx", "litellm_glm"), default="mock")
    parser.add_argument("--allow-live-provider", action="store_true", help="explicitly allow the configured staging provider to make network calls")
    parser.add_argument("--llm-lane-policy", choices=("residual", "all"), default="residual")
    parser.add_argument("--llm-max-calls", type=int, default=20)
    parser.add_argument("--model-timeout", type=float, default=120.0)
    parser.add_argument("--model-max-tokens", type=int, default=512)
    parser.add_argument("--context-limit-tokens", type=int, default=None, help="hard staging admission limit for the selected model provider")
    parser.add_argument("--context-safety-margin-tokens", type=int, default=None)
    parser.add_argument("--model-slow-threshold", type=float, default=None, help="seconds after which subsequent lanes use compact context")
    parser.add_argument("--max-tool-turns", type=int, default=None, help="maximum bounded evidence-tool follow-up turns per lane")
    parser.add_argument("--describe", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.describe:
        print(json.dumps(describe(), ensure_ascii=False, sort_keys=True))
        return 0
    try:
        result = run_doctor(args) if args.command == "doctor" else run_e2e(args)
    except (StagingContractError, ContractError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
