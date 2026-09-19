-- SQLite test equivalent of 001_review_staging.sql.
-- It is used only by the dependency-free local walking-skeleton tests.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (environment = 'staging'),
    source_mode TEXT NOT NULL,
    mineru_mode TEXT NOT NULL,
    fixture_id TEXT,
    status TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL DEFAULT '{}',
    production_write_count INTEGER NOT NULL DEFAULT 0 CHECK (production_write_count = 0),
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    status TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER,
    producer TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable = 1),
    read_only INTEGER NOT NULL DEFAULT 1 CHECK (read_only = 1),
    manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_candidates (
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    source_registry_key TEXT NOT NULL,
    question_number TEXT NOT NULL,
    raw_candidate_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    parser_status TEXT NOT NULL,
    expected_question_count INTEGER,
    actual_question_count INTEGER NOT NULL,
    issue_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, candidate_key)
);

CREATE TABLE IF NOT EXISTS question_revisions (
    revision_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL,
    parent_revision_id TEXT REFERENCES question_revisions(revision_id),
    content_hash TEXT NOT NULL,
    content_json TEXT NOT NULL,
    changed_fields TEXT NOT NULL DEFAULT '[]',
    created_by_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, candidate_key) REFERENCES pipeline_candidates(run_id, candidate_key),
    UNIQUE (run_id, candidate_key, revision_no),
    UNIQUE (run_id, candidate_key, content_hash)
);

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT REFERENCES question_revisions(revision_id) ON DELETE CASCADE,
    node_key TEXT NOT NULL,
    lane_key TEXT,
    resource_class TEXT NOT NULL DEFAULT 'deterministic_cpu',
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    lease_token TEXT,
    lease_expires_at TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id, candidate_key) REFERENCES pipeline_candidates(run_id, candidate_key)
);

CREATE TABLE IF NOT EXISTS review_lane_runs (
    lane_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT NOT NULL REFERENCES question_revisions(revision_id) ON DELETE CASCADE,
    lane_key TEXT NOT NULL,
    route_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    status TEXT NOT NULL,
    advisory_only INTEGER NOT NULL DEFAULT 1 CHECK (advisory_only = 1),
    result_json TEXT NOT NULL DEFAULT '{}',
    output_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, candidate_key) REFERENCES pipeline_candidates(run_id, candidate_key),
    UNIQUE (run_id, candidate_key, revision_id, lane_key)
);

CREATE TABLE IF NOT EXISTS review_findings (
    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane_run_id INTEGER NOT NULL REFERENCES review_lane_runs(lane_run_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    lane_key TEXT NOT NULL,
    owner_stage TEXT NOT NULL,
    finding_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    disposition TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    proposal_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS correction_proposals (
    proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    parent_revision_id TEXT NOT NULL,
    proposed_revision_id TEXT,
    source TEXT NOT NULL,
    materializable INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    changed_fields TEXT NOT NULL DEFAULT '[]',
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

-- Immutable before/after examples sent to the bounded guardrail curator.
-- This is separate from thumbs-up/down feedback: a semantic pass has no
-- correction example, while a real edit must preserve both visible versions.
CREATE TABLE IF NOT EXISTS feedback_events (
    feedback_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('human_correction', 'three_evidence_correction')),
    scope TEXT NOT NULL CHECK (scope IN ('question', 'group', 'visual', 'answer')),
    lane_key TEXT,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('human', 'deterministic_system')),
    reviewer TEXT,
    event_ref TEXT,
    changed_fields TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    ai_task_json TEXT NOT NULL DEFAULT '{}',
    event_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, candidate_key) REFERENCES pipeline_candidates(run_id, candidate_key)
);

CREATE TABLE IF NOT EXISTS guardrail_candidates (
    guardrail_id TEXT PRIMARY KEY,
    feedback_id TEXT NOT NULL REFERENCES feedback_events(feedback_id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('question', 'group', 'visual', 'answer')),
    lane_key TEXT,
    change_class TEXT NOT NULL,
    guardrail_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('observed', 'proposed', 'no_generalization', 'ai_failed', 'rejected', 'needs_owner_approval', 'approved', 'active')),
    candidate_json TEXT NOT NULL,
    validation_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, candidate_key) REFERENCES pipeline_candidates(run_id, candidate_key)
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_candidate
    ON feedback_events (candidate_key, created_at DESC, feedback_id);
CREATE INDEX IF NOT EXISTS idx_feedback_events_source
    ON feedback_events (source_kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_guardrail_candidates_status
    ON guardrail_candidates (run_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS review_exception_queue (
    exception_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    owner_stage TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    ai_advisory_only INTEGER NOT NULL DEFAULT 1 CHECK (ai_advisory_only = 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS formal_dry_runs (
    run_id TEXT PRIMARY KEY REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    eligible_count INTEGER NOT NULL,
    blocked_count INTEGER NOT NULL,
    production_write_count INTEGER NOT NULL DEFAULT 0 CHECK (production_write_count = 0),
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
