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
    question_markup_json JSONB,
    question_raw_json JSONB,
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
    option_markup_json JSONB,
    option_raw_json JSONB,
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
    role TEXT NOT NULL CHECK (role IN ('page_image', 'figure', 'stem_figure', 'table', 'option_image', 'source_pdf_region', 'answer_explanation_image', 'other')),
    page_number INTEGER,
    bbox JSONB,
    source_mineru_block_id TEXT,
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
    action TEXT NOT NULL CHECK (action IN ('accept', 'correct', 'needs_review', 'block', 'unblock', 'comment', 'reviewed')),
    corrected_candidate_json JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam.answer_review_events (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT REFERENCES exam.question_candidates(id) ON DELETE SET NULL,
    candidate_key TEXT NOT NULL,
    answer_source_registry_key TEXT,
    reviewer TEXT,
    action TEXT NOT NULL CHECK (action IN ('accept', 'correct', 'needs_review', 'block', 'unblock', 'comment', 'reviewed')),
    reviewed_answer_json JSONB,
    corrected_answer_json JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
