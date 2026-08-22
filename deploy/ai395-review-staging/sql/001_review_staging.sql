-- AI395 review workflow staging control plane.
-- This file is for the isolated staging database only. It is not a
-- production migration and must never be mounted into the AI395 production
-- writer without an owner-reviewed migration proposal.

CREATE SCHEMA IF NOT EXISTS review_staging;

CREATE TABLE IF NOT EXISTS review_staging.pipeline_runs (
    run_id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (environment = 'staging'),
    source_mode TEXT NOT NULL CHECK (source_mode IN ('live_download', 'existing_artifact', 'mock_fixture')),
    mineru_mode TEXT NOT NULL CHECK (mineru_mode IN ('run_mineru', 'existing_artifact', 'mock_fixture')),
    fixture_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('planned', 'running', 'succeeded', 'failed', 'dry_run')),
    config_sha256 TEXT NOT NULL,
    manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    production_write_count INTEGER NOT NULL DEFAULT 0 CHECK (production_write_count = 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE (pipeline_id, config_sha256, fixture_id, run_id)
);

CREATE TABLE IF NOT EXISTS review_staging.source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    status TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes BIGINT,
    producer TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    immutable BOOLEAN NOT NULL DEFAULT true CHECK (immutable),
    read_only BOOLEAN NOT NULL DEFAULT true CHECK (read_only),
    manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_staging.pipeline_candidates (
    run_id TEXT NOT NULL REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    source_registry_key TEXT NOT NULL,
    question_number TEXT NOT NULL,
    raw_candidate_json JSONB NOT NULL,
    input_sha256 TEXT NOT NULL,
    parser_status TEXT NOT NULL CHECK (parser_status IN ('pass', 'needs_review', 'blocked')),
    expected_question_count INTEGER,
    actual_question_count INTEGER NOT NULL,
    issue_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, candidate_key)
);

CREATE TABLE IF NOT EXISTS review_staging.question_revisions (
    revision_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL CHECK (revision_no >= 1),
    parent_revision_id TEXT REFERENCES review_staging.question_revisions(revision_id),
    content_hash TEXT NOT NULL,
    content_json JSONB NOT NULL,
    changed_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by_kind TEXT NOT NULL CHECK (created_by_kind IN ('parser', 'deterministic_rule', 'ai_proposal', 'human')),
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'stale', 'exception')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, candidate_key) REFERENCES review_staging.pipeline_candidates(run_id, candidate_key) ON DELETE CASCADE,
    UNIQUE (run_id, candidate_key, revision_no),
    UNIQUE (run_id, candidate_key, content_hash)
);

CREATE TABLE IF NOT EXISTS review_staging.pipeline_jobs (
    job_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT REFERENCES review_staging.question_revisions(revision_id) ON DELETE CASCADE,
    node_key TEXT NOT NULL,
    lane_key TEXT,
    resource_class TEXT NOT NULL DEFAULT 'deterministic_cpu',
    status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'succeeded', 'failed', 'stale', 'skipped')),
    attempt INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    lease_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    error_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, candidate_key) REFERENCES review_staging.pipeline_candidates(run_id, candidate_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_staging.review_lane_runs (
    lane_run_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT NOT NULL REFERENCES review_staging.question_revisions(revision_id) ON DELETE CASCADE,
    lane_key TEXT NOT NULL CHECK (lane_key IN ('text_evidence', 'notation', 'group', 'vision', 'answer')),
    route_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('machine_pass', 'finding', 'human_exception', 'failed', 'skipped')),
    advisory_only BOOLEAN NOT NULL DEFAULT true CHECK (advisory_only),
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, candidate_key) REFERENCES review_staging.pipeline_candidates(run_id, candidate_key) ON DELETE CASCADE,
    UNIQUE (run_id, candidate_key, revision_id, lane_key)
);

CREATE TABLE IF NOT EXISTS review_staging.review_findings (
    finding_id BIGSERIAL PRIMARY KEY,
    lane_run_id BIGINT NOT NULL REFERENCES review_staging.review_lane_runs(lane_run_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    lane_key TEXT NOT NULL,
    owner_stage TEXT NOT NULL CHECK (owner_stage IN ('parser', 'question', 'image', 'group', 'answer', 'ui')),
    finding_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'blocked')),
    disposition TEXT NOT NULL CHECK (disposition IN ('advisory', 'human_exception', 'machine_pass')),
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposal_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_staging.correction_proposals (
    proposal_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    parent_revision_id TEXT NOT NULL,
    proposed_revision_id TEXT,
    source TEXT NOT NULL CHECK (source IN ('deterministic_rule', 'ai_advisory', 'human')),
    materializable BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'materialized', 'rejected', 'needs_human')),
    changed_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    before_json JSONB NOT NULL,
    after_json JSONB NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_staging.review_exception_queue (
    exception_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    owner_stage TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'human_resolved', 'superseded')),
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_advisory_only BOOLEAN NOT NULL DEFAULT true CHECK (ai_advisory_only),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_staging.formal_dry_runs (
    run_id TEXT PRIMARY KEY REFERENCES review_staging.pipeline_runs(run_id) ON DELETE CASCADE,
    eligible_count INTEGER NOT NULL,
    blocked_count INTEGER NOT NULL,
    production_write_count INTEGER NOT NULL DEFAULT 0 CHECK (production_write_count = 0),
    report_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status
    ON review_staging.pipeline_jobs (run_id, status, node_key);
CREATE INDEX IF NOT EXISTS idx_lane_runs_candidate
    ON review_staging.review_lane_runs (candidate_key, lane_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_findings_queue
    ON review_staging.review_findings (run_id, disposition, severity, candidate_key);
CREATE INDEX IF NOT EXISTS idx_exception_queue_open
    ON review_staging.review_exception_queue (run_id, status, owner_stage, severity);
