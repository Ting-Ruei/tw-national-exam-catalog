-- PostgreSQL schema draft for Taiwan national exam assets and parsed questions.
-- This file is intentionally a draft: do not treat it as a migration history yet.

CREATE SCHEMA IF NOT EXISTS exam;

CREATE TABLE IF NOT EXISTS exam.source_systems (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    base_url TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS exam.exam_sessions (
    id BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES exam.source_systems(id),
    exam_code TEXT NOT NULL,
    roc_year INTEGER NOT NULL,
    exam_ordinal INTEGER,
    exam_label TEXT NOT NULL,
    source_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system_id, exam_code)
);

CREATE TABLE IF NOT EXISTS exam.categories (
    id BIGSERIAL PRIMARY KEY,
    category_code TEXT,
    official_category_name TEXT NOT NULL,
    normalized_category_name TEXT NOT NULL,
    group_name TEXT NOT NULL,
    is_locked27 BOOLEAN NOT NULL DEFAULT false,
    notes TEXT,
    UNIQUE (category_code, official_category_name)
);

CREATE TABLE IF NOT EXISTS exam.subjects (
    id BIGSERIAL PRIMARY KEY,
    category_id BIGINT NOT NULL REFERENCES exam.categories(id),
    subject_code TEXT NOT NULL,
    official_subject_name TEXT NOT NULL,
    normalized_subject_name TEXT NOT NULL,
    canonical_subject_name TEXT,
    notes TEXT,
    UNIQUE (category_id, subject_code, official_subject_name)
);

CREATE TABLE IF NOT EXISTS exam.official_documents (
    id BIGSERIAL PRIMARY KEY,
    registry_key TEXT NOT NULL UNIQUE,
    exam_session_id BIGINT NOT NULL REFERENCES exam.exam_sessions(id),
    category_id BIGINT NOT NULL REFERENCES exam.categories(id),
    subject_id BIGINT NOT NULL REFERENCES exam.subjects(id),
    question_set TEXT NOT NULL DEFAULT '1',
    document_role TEXT NOT NULL CHECK (document_role IN ('question', 'answer', 'correction')),
    source_url TEXT NOT NULL,
    official_category_name TEXT NOT NULL,
    official_subject_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.assets (
    id BIGSERIAL PRIMARY KEY,
    asset_key TEXT NOT NULL UNIQUE,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('pdf', 'page_image', 'question_image', 'table_image', 'markdown', 'json', 'other')),
    storage_backend TEXT NOT NULL DEFAULT 'filesystem',
    asset_path TEXT NOT NULL,
    relative_asset_path TEXT,
    sha256 TEXT,
    bytes BIGINT,
    mime_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.document_assets (
    official_document_id BIGINT NOT NULL REFERENCES exam.official_documents(id),
    asset_id BIGINT NOT NULL REFERENCES exam.assets(id),
    role TEXT NOT NULL DEFAULT 'primary_pdf',
    PRIMARY KEY (official_document_id, asset_id, role)
);

CREATE TABLE IF NOT EXISTS exam.question_answer_document_pairs (
    id BIGSERIAL PRIMARY KEY,
    pair_key TEXT NOT NULL UNIQUE,
    pair_status TEXT NOT NULL,
    question_document_id BIGINT NOT NULL REFERENCES exam.official_documents(id),
    primary_answer_document_id BIGINT REFERENCES exam.official_documents(id),
    ans_document_id BIGINT REFERENCES exam.official_documents(id),
    mod_document_id BIGINT REFERENCES exam.official_documents(id),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.mineru_runs (
    id BIGSERIAL PRIMARY KEY,
    official_document_id BIGINT NOT NULL REFERENCES exam.official_documents(id),
    input_asset_id BIGINT NOT NULL REFERENCES exam.assets(id),
    run_status TEXT NOT NULL CHECK (run_status IN ('planned', 'running', 'succeeded', 'failed', 'superseded')),
    mineru_version TEXT,
    output_root TEXT,
    output_manifest JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.question_groups (
    id BIGSERIAL PRIMARY KEY,
    official_document_id BIGINT REFERENCES exam.official_documents(id),
    group_key TEXT NOT NULL UNIQUE,
    shared_stem_text TEXT,
    shared_stem_json JSONB,
    display_markup_json JSONB,
    asset_policy_json JSONB,
    source_page_start INTEGER,
    source_page_end INTEGER,
    source_bbox JSONB,
    group_question_range TEXT,
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
);

CREATE TABLE IF NOT EXISTS exam.questions (
    id BIGSERIAL PRIMARY KEY,
    official_document_id BIGINT NOT NULL REFERENCES exam.official_documents(id),
    question_group_id BIGINT REFERENCES exam.question_groups(id),
    question_key TEXT NOT NULL UNIQUE,
    question_number TEXT NOT NULL,
    question_text TEXT,
    normalized_text TEXT,
    display_text TEXT,
    question_markup_json JSONB,
    question_raw_json JSONB,
    human_corrected_json JSONB,
    source_page_start INTEGER,
    source_page_end INTEGER,
    source_bbox JSONB,
    parse_confidence NUMERIC(5,4),
    question_json JSONB,
    parser_version TEXT,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.question_options (
    id BIGSERIAL PRIMARY KEY,
    question_id BIGINT NOT NULL REFERENCES exam.questions(id),
    option_label TEXT NOT NULL,
    option_text TEXT,
    normalized_text TEXT,
    display_text TEXT,
    option_markup_json JSONB,
    option_raw_json JSONB,
    human_corrected_json JSONB,
    option_json JSONB,
    UNIQUE (question_id, option_label)
);

CREATE TABLE IF NOT EXISTS exam.answers (
    id BIGSERIAL PRIMARY KEY,
    question_id BIGINT NOT NULL REFERENCES exam.questions(id),
    answer_source_document_id BIGINT REFERENCES exam.official_documents(id),
    answer_value TEXT,
    answer_json JSONB,
    is_correction BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.question_assets (
    question_id BIGINT NOT NULL REFERENCES exam.questions(id),
    asset_id BIGINT NOT NULL REFERENCES exam.assets(id),
    role TEXT NOT NULL CHECK (role IN ('page_image', 'figure', 'stem_figure', 'table', 'table_structured', 'table_manual_screenshot', 'option_image', 'source_pdf_region', 'answer_explanation_image', 'group_shared_asset', 'other')),
    page_number INTEGER,
    bbox JSONB,
    source_mineru_block_id TEXT,
    display_order INTEGER,
    asset_quality_status TEXT NOT NULL DEFAULT 'unreviewed',
    PRIMARY KEY (question_id, asset_id, role)
);

CREATE TABLE IF NOT EXISTS exam.question_candidates (
    id BIGSERIAL PRIMARY KEY,
    candidate_key TEXT NOT NULL UNIQUE,
    source_registry_key TEXT NOT NULL,
    source_document_id BIGINT REFERENCES exam.official_documents(id),
    answer_source_registry_key TEXT,
    answer_source_document_id BIGINT REFERENCES exam.official_documents(id),
    question_number TEXT NOT NULL,
    question_type TEXT,
    group_ref TEXT,
    stem_text TEXT,
    stem_markup_json JSONB,
    raw_candidate_json JSONB NOT NULL,
    normalized_candidate_json JSONB,
    parser_version TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'needs_review' CHECK (quality_status IN ('pass', 'needs_review', 'blocked')),
    review_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'accepted', 'corrected', 'needs_review', 'blocked')),
    issue_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.question_parse_issues (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT REFERENCES exam.question_candidates(id) ON DELETE CASCADE,
    candidate_key TEXT,
    source_registry_key TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'blocked')),
    message TEXT NOT NULL,
    issue_json JSONB,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.question_review_events (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT REFERENCES exam.question_candidates(id) ON DELETE SET NULL,
    candidate_key TEXT NOT NULL,
    reviewer TEXT,
    action TEXT NOT NULL CHECK (action IN ('accept', 'correct', 'needs_review', 'block', 'exclude', 'unblock', 'comment', 'reviewed', 'unreviewed', 'reset_review')),
    corrected_candidate_json JSONB,
    event_json JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.answer_review_events (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT REFERENCES exam.question_candidates(id) ON DELETE SET NULL,
    candidate_key TEXT NOT NULL,
    answer_source_registry_key TEXT,
    reviewer TEXT,
    action TEXT NOT NULL CHECK (action IN ('accept', 'correct', 'needs_review', 'block', 'unblock', 'comment', 'reviewed', 'unreviewed', 'reset_review')),
    reviewed_answer_json JSONB,
    corrected_answer_json JSONB,
    event_json JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.model_runs (
    id BIGSERIAL PRIMARY KEY,
    task_type TEXT NOT NULL CHECK (task_type IN ('question_format_audit', 'answer_audit', 'classification', 'explanation', 'relation', 'concept_map_spec', 'verification', 'embedding', 'rerank')),
    provider TEXT NOT NULL DEFAULT 'local',
    model_name TEXT,
    prompt_version TEXT,
    input_hash TEXT,
    input_token_count INTEGER,
    output_token_count INTEGER,
    estimated_cost_usd NUMERIC(12,6),
    status TEXT NOT NULL DEFAULT 'succeeded' CHECK (status IN ('planned', 'running', 'succeeded', 'failed', 'skipped')),
    request_json JSONB,
    response_json JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.question_ai_review_events (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT REFERENCES exam.question_candidates(id) ON DELETE SET NULL,
    candidate_key TEXT NOT NULL,
    model_run_id BIGINT REFERENCES exam.model_runs(id) ON DELETE SET NULL,
    action TEXT NOT NULL DEFAULT 'ai_audit',
    reviewer TEXT,
    provider TEXT NOT NULL DEFAULT 'local',
    model_name TEXT,
    prompt_version TEXT,
    input_hash TEXT,
    audit_status TEXT NOT NULL CHECK (audit_status IN ('pass', 'needs_review', 'block')),
    recommended_action TEXT,
    audit_json JSONB NOT NULL,
    event_json JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Human feedback on a specific AI audit is a separate append-only stream.
-- It must not become a new AI audit row or alter human question decisions.
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

-- Human-selected training examples are independent from thumbs up/down. A
-- question may be both well-rated and explicitly selected as learning data.
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

-- Curriculum classification is an auxiliary, versioned knowledge layer.
-- It never replaces or mutates formal question/answer content.
CREATE TABLE IF NOT EXISTS exam.curriculum_taxonomies (
    id BIGSERIAL PRIMARY KEY,
    taxonomy_code TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    category_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
    definition TEXT,
    source_path TEXT,
    source_sha256 TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (taxonomy_code, taxonomy_version)
);

CREATE TABLE IF NOT EXISTS exam.curriculum_nodes (
    id BIGSERIAL PRIMARY KEY,
    taxonomy_id BIGINT NOT NULL REFERENCES exam.curriculum_taxonomies(id) ON DELETE CASCADE,
    node_code TEXT NOT NULL,
    parent_node_code TEXT,
    node_kind TEXT NOT NULL CHECK (node_kind IN ('subject', 'domain', 'chapter')),
    subject_name TEXT NOT NULL,
    subject_aliases TEXT[] NOT NULL DEFAULT '{}',
    label TEXT NOT NULL,
    definition TEXT NOT NULL,
    depth INTEGER NOT NULL CHECK (depth BETWEEN 0 AND 8),
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_selectable BOOLEAN NOT NULL DEFAULT true,
    decision_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (taxonomy_id, node_code)
);

ALTER TABLE exam.curriculum_nodes ADD COLUMN IF NOT EXISTS subject_aliases TEXT[] NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS exam.question_classification_events (
    id BIGSERIAL PRIMARY KEY,
    question_id BIGINT NOT NULL REFERENCES exam.questions(id) ON DELETE CASCADE,
    question_key TEXT NOT NULL,
    taxonomy_id BIGINT NOT NULL REFERENCES exam.curriculum_taxonomies(id),
    model_run_id BIGINT REFERENCES exam.model_runs(id) ON DELETE SET NULL,
    reviewer TEXT,
    source TEXT NOT NULL DEFAULT 'human' CHECK (source IN ('human', 'ai', 'import', 'system')),
    action TEXT NOT NULL CHECK (action IN ('ai_suggest', 'accept', 'replace', 'needs_review', 'reset')),
    primary_node_code TEXT,
    selected_node_codes TEXT[] NOT NULL DEFAULT '{}',
    is_comprehensive BOOLEAN NOT NULL DEFAULT false,
    confidence TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT,
    event_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (primary_node_code IS NULL OR primary_node_code = ANY(selected_node_codes))
);

-- Current human-approved projection. All history remains in the append-only
-- event table; AI suggestions never update this table directly.
CREATE TABLE IF NOT EXISTS exam.question_classifications (
    question_id BIGINT NOT NULL REFERENCES exam.questions(id) ON DELETE CASCADE,
    taxonomy_id BIGINT NOT NULL REFERENCES exam.curriculum_taxonomies(id),
    primary_node_code TEXT,
    selected_node_codes TEXT[] NOT NULL DEFAULT '{}',
    is_comprehensive BOOLEAN NOT NULL DEFAULT false,
    review_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'accepted', 'needs_review')),
    source TEXT NOT NULL DEFAULT 'human' CHECK (source IN ('human', 'ai_accepted', 'import')),
    reviewer TEXT,
    latest_event_id BIGINT REFERENCES exam.question_classification_events(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (question_id, taxonomy_id),
    CHECK (primary_node_code IS NULL OR primary_node_code = ANY(selected_node_codes))
);

CREATE TABLE IF NOT EXISTS exam.review_ui_preferences (
    reviewer TEXT PRIMARY KEY,
    preferences_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.formal_sync_queue (
    candidate_key TEXT PRIMARY KEY REFERENCES exam.question_candidates(candidate_key) ON DELETE CASCADE,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    last_error TEXT,
    processed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS exam.canonical_subject_mappings (
    id BIGSERIAL PRIMARY KEY,
    category_group_name TEXT NOT NULL,
    official_category_name TEXT NOT NULL,
    official_subject_name TEXT NOT NULL,
    canonical_subject_name TEXT NOT NULL,
    valid_from_session TEXT,
    valid_to_session TEXT,
    change_note TEXT,
    UNIQUE (category_group_name, official_category_name, official_subject_name, canonical_subject_name)
);

CREATE TABLE IF NOT EXISTS exam.export_jobs (
    id BIGSERIAL PRIMARY KEY,
    export_type TEXT NOT NULL CHECK (export_type IN ('sqlite', 'jsonl', 'parquet', 'vector_chunks', 'postgres_dump')),
    status TEXT NOT NULL CHECK (status IN ('planned', 'running', 'succeeded', 'failed')),
    output_path TEXT,
    output_manifest JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_exam_sessions_year ON exam.exam_sessions (roc_year, exam_ordinal);
CREATE INDEX IF NOT EXISTS idx_categories_group_name ON exam.categories (group_name);
CREATE INDEX IF NOT EXISTS idx_subjects_canonical ON exam.subjects (canonical_subject_name);
CREATE INDEX IF NOT EXISTS idx_official_documents_role ON exam.official_documents (document_role);
CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON exam.assets (sha256);
CREATE INDEX IF NOT EXISTS idx_questions_review_status ON exam.questions (review_status);
CREATE INDEX IF NOT EXISTS idx_question_answer_pairs_status ON exam.question_answer_document_pairs (pair_status);
CREATE INDEX IF NOT EXISTS idx_question_candidates_source ON exam.question_candidates (source_registry_key);
CREATE INDEX IF NOT EXISTS idx_question_candidates_quality ON exam.question_candidates (quality_status, review_status);
CREATE INDEX IF NOT EXISTS idx_question_parse_issues_candidate ON exam.question_parse_issues (candidate_key);
CREATE INDEX IF NOT EXISTS idx_question_parse_issues_severity ON exam.question_parse_issues (severity, issue_code);
CREATE INDEX IF NOT EXISTS idx_question_review_events_candidate ON exam.question_review_events (candidate_key);
CREATE INDEX IF NOT EXISTS idx_answer_review_events_candidate ON exam.answer_review_events (candidate_key);
CREATE INDEX IF NOT EXISTS idx_question_ai_review_events_candidate ON exam.question_ai_review_events (candidate_key);
CREATE INDEX IF NOT EXISTS idx_question_review_events_latest ON exam.question_review_events (candidate_key, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_answer_review_events_latest ON exam.answer_review_events (candidate_key, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_question_ai_review_events_latest ON exam.question_ai_review_events (candidate_key, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_curriculum_nodes_subject ON exam.curriculum_nodes (subject_name, taxonomy_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_curriculum_nodes_parent ON exam.curriculum_nodes (taxonomy_id, parent_node_code, sort_order);
CREATE INDEX IF NOT EXISTS idx_question_classification_events_latest ON exam.question_classification_events (question_id, taxonomy_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_question_classification_events_source ON exam.question_classification_events (source, action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_question_classifications_status ON exam.question_classifications (taxonomy_id, review_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_formal_sync_queue_pending
ON exam.formal_sync_queue (requested_at, candidate_key)
WHERE processed_at IS NULL;

ALTER TABLE exam.question_review_events ADD COLUMN IF NOT EXISTS event_json JSONB;
ALTER TABLE exam.answer_review_events ADD COLUMN IF NOT EXISTS event_json JSONB;
ALTER TABLE exam.question_ai_review_events ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'ai_audit';
ALTER TABLE exam.question_ai_review_events ADD COLUMN IF NOT EXISTS event_json JSONB;

ALTER TABLE exam.model_runs DROP CONSTRAINT IF EXISTS model_runs_task_type_check;
ALTER TABLE exam.model_runs
ADD CONSTRAINT model_runs_task_type_check
CHECK (task_type IN ('question_format_audit', 'answer_audit', 'classification', 'explanation', 'relation', 'concept_map_spec', 'verification', 'embedding', 'rerank'));

ALTER TABLE exam.question_review_events DROP CONSTRAINT IF EXISTS question_review_events_action_check;
ALTER TABLE exam.question_review_events
ADD CONSTRAINT question_review_events_action_check
CHECK (action IN (
    'accept', 'correct', 'needs_review', 'block', 'exclude', 'unblock', 'comment', 'reviewed', 'unreviewed', 'reset_review',
    'confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual'
));

ALTER TABLE exam.answer_review_events DROP CONSTRAINT IF EXISTS answer_review_events_action_check;
ALTER TABLE exam.answer_review_events
ADD CONSTRAINT answer_review_events_action_check
CHECK (action IN ('accept', 'correct', 'needs_review', 'block', 'unblock', 'comment', 'reviewed', 'unreviewed', 'reset_review'));

CREATE INDEX IF NOT EXISTS idx_question_candidates_category_subject_year
ON exam.question_candidates (
    (raw_candidate_json->'metadata'->>'normalized_category_name'),
    (raw_candidate_json->'metadata'->>'normalized_subject_name'),
    (raw_candidate_json->'metadata'->>'year'),
    (raw_candidate_json->'metadata'->>'exam_ordinal')
);

CREATE INDEX IF NOT EXISTS idx_question_candidates_review_ui_filter_sort
ON exam.question_candidates (
    (COALESCE(raw_candidate_json->'metadata'->>'normalized_category_name', '')),
    (COALESCE(raw_candidate_json->'metadata'->>'normalized_subject_name', '')),
    ((CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
        THEN (raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END)) DESC,
    ((CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
        THEN (raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END)) DESC,
    ((CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END)),
    candidate_key
);

CREATE INDEX IF NOT EXISTS idx_question_candidates_question_number
ON exam.question_candidates (
    (CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE NULL END)
);
