-- quickq OLTP schema (SQLite)
-- Connection must set: PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=NORMAL;

-- ============================================================
-- CONCEPT PLANE
-- OMOP-inspired vocabulary and concept tables.
-- Every question, option, row, and column can carry a concept_id
-- that maps it to a standard vocabulary (LOINC, SNOMED, NCI, etc.).
-- ============================================================

CREATE TABLE IF NOT EXISTS vocabulary (
    vocabulary_id        TEXT PRIMARY KEY,          -- 'LOINC', 'SNOMED', 'NCI', 'BRFSS', 'Local'
    vocabulary_name      TEXT NOT NULL,
    vocabulary_reference TEXT,                      -- canonical URL for the vocabulary
    version              TEXT,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS concept (
    concept_id           INTEGER PRIMARY KEY,
    concept_name         TEXT    NOT NULL,
    domain_id            TEXT    NOT NULL,          -- 'Question', 'Answer', 'Measurement', 'Condition'
    vocabulary_id        TEXT    NOT NULL REFERENCES vocabulary (vocabulary_id),
    concept_class_id     TEXT    NOT NULL,          -- 'Survey', 'Clinical Question', 'Answer', 'Scale'
    standard_concept     TEXT    CHECK (standard_concept IN ('S', 'C') OR standard_concept IS NULL),
    concept_code         TEXT    NOT NULL,          -- source code, e.g. '72166-2' for LOINC
    valid_start_date     TEXT    NOT NULL DEFAULT '1970-01-01',
    valid_end_date       TEXT    NOT NULL DEFAULT '2099-12-31',
    UNIQUE (vocabulary_id, concept_code)
);

-- Maps to, Is a, Subsumes, Answer of, Tradename of, etc.
CREATE TABLE IF NOT EXISTS concept_relationship (
    concept_id_1         INTEGER NOT NULL REFERENCES concept (concept_id),
    concept_id_2         INTEGER NOT NULL REFERENCES concept (concept_id),
    relationship_id      TEXT    NOT NULL,
    valid_start_date     TEXT    NOT NULL DEFAULT '1970-01-01',
    valid_end_date       TEXT    NOT NULL DEFAULT '2099-12-31',
    PRIMARY KEY (concept_id_1, concept_id_2, relationship_id)
);

-- ============================================================
-- INSTRUMENT PLANE
-- Study → Questionnaire → Section → QuestionnaireQuestion → Question
-- ============================================================

CREATE TABLE IF NOT EXISTS study (
    study_id             INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL,
    description          TEXT,
    principal_investigator TEXT,
    irb_number           TEXT,
    start_date           TEXT,
    end_date             TEXT,
    -- FAIR / regulatory metadata
    population           TEXT,                      -- description of the study population
    license              TEXT,                      -- SPDX ID or URL, e.g. 'CC-BY-4.0'
    protocol_url         TEXT,                      -- ClinicalTrials.gov, OSF, etc.
    doi                  TEXT,                      -- assigned after repository deposit
    geographic_scope     TEXT,                      -- e.g. 'United States', 'Multi-country'
    data_collection_end  TEXT,                      -- ISO 8601 date
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- A versioned survey instrument. canonical_url is the FHIR identity URL.
-- Versioning: create a new row for each version; superseded_by tracks lineage.
CREATE TABLE IF NOT EXISTS questionnaire (
    questionnaire_id     INTEGER PRIMARY KEY,
    study_id             INTEGER REFERENCES study (study_id),
    name                 TEXT    NOT NULL,
    description          TEXT,
    canonical_url        TEXT,                      -- FHIR Questionnaire.url
    version              TEXT    NOT NULL DEFAULT '1.0',
    fhir_status          TEXT    NOT NULL DEFAULT 'draft'
                             CHECK (fhir_status IN ('draft', 'active', 'retired', 'unknown')),
    concept_id           INTEGER REFERENCES concept (concept_id),
    superseded_by        INTEGER REFERENCES questionnaire (questionnaire_id),
    license              TEXT,                      -- SPDX ID or URL; instruments can be licensed independently
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (canonical_url, version)                -- FHIR: url+version is the composite identity
);

-- Optional page/section grouping within a questionnaire.
CREATE TABLE IF NOT EXISTS section (
    section_id           INTEGER PRIMARY KEY,
    questionnaire_id     INTEGER NOT NULL REFERENCES questionnaire (questionnaire_id),
    title                TEXT,
    description          TEXT,
    display_order        INTEGER NOT NULL DEFAULT 0,
    display_condition    TEXT,                      -- FHIRPath expression for conditional section
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Reusable named option lists (FHIR answerValueSet equivalent).
-- When options on a question all share an option_set_id, the question exports
-- as answerValueSet rather than inline answerOption in FHIR.
CREATE TABLE IF NOT EXISTS response_option_set (
    option_set_id        INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL UNIQUE,   -- 'phq_frequency', 'yn', 'likert5'
    canonical_url        TEXT    UNIQUE,            -- FHIR ValueSet canonical URL
    description          TEXT,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Reusable question bank. Questions are authored once and can be placed
-- in multiple questionnaires via questionnaire_question.
CREATE TABLE IF NOT EXISTS question (
    question_id          INTEGER PRIMARY KEY,
    link_id              TEXT    NOT NULL UNIQUE,   -- FHIR item.linkId; stable, human-readable
    question_text        TEXT    NOT NULL,
    question_type        TEXT    NOT NULL
                             CHECK (question_type IN (
                                 'single_choice', 'multiple_choice', 'sata_other',
                                 'boolean', 'text', 'numeric', 'date', 'datetime',
                                 'likert', 'grid', 'ranked', 'slider', 'repeating_group')),
    help_text            TEXT,
    concept_id           INTEGER REFERENCES concept (concept_id),
    -- provenance: which standard instrument does this question come from?
    source_instrument    TEXT,                      -- 'PHQ-9', 'BRFSS-2022', 'NHANES'
    source_item_id       TEXT,                      -- 'PHQ-9-3'
    citation             TEXT,                      -- DOI or bibliographic reference
    -- which named option set was applied (authoring provenance for shared sets)
    option_set_id        INTEGER REFERENCES response_option_set (option_set_id),
    -- numeric / slider constraints
    numeric_min          REAL,
    numeric_max          REAL,
    numeric_step         REAL,
    slider_min_label     TEXT,
    slider_max_label     TEXT,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    is_active            INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    -- analyst-facing note, not shown to respondents (distinct from help_text)
    internal_note        TEXT
);

-- Answer choices for closed-ended questions.
-- Every option belongs to a question.
-- option_set_id tracks which named set the option came from (nullable = authored inline).
-- concept_code / concept_system are denormalized from concept for fast FHIR export.
CREATE TABLE IF NOT EXISTS response_option (
    option_id            INTEGER PRIMARY KEY,
    question_id          INTEGER NOT NULL REFERENCES question (question_id),
    option_set_id        INTEGER REFERENCES response_option_set (option_set_id),
    option_text          TEXT    NOT NULL,
    option_value         TEXT    NOT NULL,          -- stored code (e.g. '0', 'yes', 'LA33-6')
    display_order        INTEGER NOT NULL DEFAULT 0,
    concept_id           INTEGER REFERENCES concept (concept_id),
    concept_code         TEXT,                      -- denormalized for FHIR export
    concept_system       TEXT,                      -- e.g. 'http://snomed.info/sct'
    is_other             INTEGER NOT NULL DEFAULT 0 CHECK (is_other IN (0, 1)),   -- triggers free-text sibling
    is_exclusive         INTEGER NOT NULL DEFAULT 0 CHECK (is_exclusive IN (0, 1)), -- "None of the above"
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Row definitions for grid (matrix) questions.
CREATE TABLE IF NOT EXISTS grid_row (
    row_id               INTEGER PRIMARY KEY,
    question_id          INTEGER NOT NULL REFERENCES question (question_id),
    row_text             TEXT    NOT NULL,
    display_order        INTEGER NOT NULL DEFAULT 0,
    concept_id           INTEGER REFERENCES concept (concept_id)
);

-- Column definitions for grid questions.
-- column_type controls what kind of answer is expected in each column.
CREATE TABLE IF NOT EXISTS grid_column (
    column_id            INTEGER PRIMARY KEY,
    question_id          INTEGER NOT NULL REFERENCES question (question_id),
    column_text          TEXT    NOT NULL,
    column_value         TEXT,
    column_type          TEXT    NOT NULL DEFAULT 'single_choice'
                             CHECK (column_type IN ('single_choice', 'numeric', 'text', 'boolean')),
    display_order        INTEGER NOT NULL DEFAULT 0,
    concept_id           INTEGER REFERENCES concept (concept_id)
);

-- Placement of a question within a questionnaire and optional section.
-- This is the join table that makes questions reusable across instruments.
-- parent_qq_id enables follow-up / sub-question relationships.
CREATE TABLE IF NOT EXISTS questionnaire_question (
    qq_id                INTEGER PRIMARY KEY,
    questionnaire_id     INTEGER NOT NULL REFERENCES questionnaire (questionnaire_id),
    section_id           INTEGER REFERENCES section (section_id),
    question_id          INTEGER NOT NULL REFERENCES question (question_id),
    display_order        INTEGER NOT NULL DEFAULT 0,
    is_required          INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0, 1)),
    parent_qq_id         INTEGER REFERENCES questionnaire_question (qq_id),
    count_qq_id          INTEGER REFERENCES questionnaire_question (qq_id),  -- drives repeat count
    -- display_condition is a FHIRPath expression fallback for logic that cannot be
    -- expressed as structured skip_rule rows (complex boolean chains, cross-section logic).
    display_condition    TEXT,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- lifecycle: questions can be deprecated or suspended mid-study without
    -- creating a full new questionnaire version. Historical responses remain valid.
    status               TEXT    NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active', 'deprecated', 'suspended')),
    status_changed_at    TEXT,
    status_notes         TEXT                              -- reason: IRB, correction, redesign, etc.
);

-- Structured skip / branching logic. Maps to FHIR item.enableWhen[].
-- Multiple rules for the same qq_id are combined via enable_behavior (AND/OR).
CREATE TABLE IF NOT EXISTS skip_rule (
    rule_id              INTEGER PRIMARY KEY,
    qq_id                INTEGER NOT NULL REFERENCES questionnaire_question (qq_id),
    enable_behavior      TEXT    NOT NULL DEFAULT 'all' CHECK (enable_behavior IN ('all', 'any')),
    trigger_qq_id        INTEGER NOT NULL REFERENCES questionnaire_question (qq_id),
    operator             TEXT    NOT NULL
                             CHECK (operator IN ('exists', 'not_exists', '=', '!=', '>', '<', '>=', '<=')),
    trigger_value        TEXT,                      -- the expected value; NULL valid for exists/not_exists
    action               TEXT    NOT NULL DEFAULT 'show' CHECK (action IN ('show', 'hide', 'require')),
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Subscale scoring definitions (PHQ-9 total, GAD-7 severity, SF-12 PCS/MCS, etc.).
CREATE TABLE IF NOT EXISTS scoring_rule (
    scoring_rule_id      INTEGER PRIMARY KEY,
    questionnaire_id     INTEGER NOT NULL REFERENCES questionnaire (questionnaire_id),
    name                 TEXT    NOT NULL,
    description          TEXT,
    -- formula: 'sum' | 'mean' | 'count' | arithmetic expression referencing link_ids
    formula              TEXT    NOT NULL,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Which questionnaire_question items contribute to a score, and how.
CREATE TABLE IF NOT EXISTS scoring_rule_item (
    item_id              INTEGER PRIMARY KEY,
    scoring_rule_id      INTEGER NOT NULL REFERENCES scoring_rule (scoring_rule_id),
    qq_id                INTEGER NOT NULL REFERENCES questionnaire_question (qq_id),
    weight               REAL    NOT NULL DEFAULT 1.0,
    reverse_score        INTEGER NOT NULL DEFAULT 0 CHECK (reverse_score IN (0, 1))
);

-- Severity / interpretation categories for a scored scale.
CREATE TABLE IF NOT EXISTS scoring_category (
    category_id          INTEGER PRIMARY KEY,
    scoring_rule_id      INTEGER NOT NULL REFERENCES scoring_rule (scoring_rule_id),
    label                TEXT    NOT NULL,          -- 'Minimal', 'Mild', 'Moderate', 'Severe'
    min_score            REAL,
    max_score            REAL,
    display_order        INTEGER NOT NULL DEFAULT 0
);

-- ============================================================
-- RESPONSE PLANE
-- Respondent → ResponseSession → Response
-- ============================================================

CREATE TABLE IF NOT EXISTS respondent (
    respondent_id        INTEGER PRIMARY KEY,
    study_id             INTEGER REFERENCES study (study_id),
    -- external_id is the de-identified or anonymized participant identifier.
    -- Never store PII here; link to a separate identity store if needed.
    external_id          TEXT,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (study_id, external_id)
);

-- One session = one attempt to complete one questionnaire by one respondent.
-- admin_mode and is_proxy are important covariates for epi mode-effect analysis.
CREATE TABLE IF NOT EXISTS response_session (
    session_id           INTEGER PRIMARY KEY,
    questionnaire_id     INTEGER NOT NULL REFERENCES questionnaire (questionnaire_id),
    respondent_id        INTEGER NOT NULL REFERENCES respondent (respondent_id),
    started_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at         TEXT,
    is_complete          INTEGER NOT NULL DEFAULT 0 CHECK (is_complete IN (0, 1)),
    admin_mode           TEXT    CHECK (admin_mode IN ('web', 'paper', 'phone', 'kiosk', 'interviewer', 'api')),
    is_proxy             INTEGER NOT NULL DEFAULT 0 CHECK (is_proxy IN (0, 1)),
    interviewer_id       TEXT,                      -- opaque staff identifier; not an FK
    fhir_response_id     TEXT,                      -- QuestionnaireResponse.id if imported from FHIR
    -- Consent tracking: which consent form version was active at collection time.
    -- NULL means not recorded (valid for anonymous studies or pre-migration sessions).
    consent_version      TEXT,
    consented_at         TEXT,                      -- ISO 8601 timestamp
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- One row per answer atom.
--
-- single_choice:         one row, option_id set
-- multiple_choice/sata:  one row per selected option, option_id set each
-- sata "other":          one row with option_id → is_other option, response_text = free text
-- boolean:               one row, response_text = 'true' | 'false'
-- text:                  one row, response_text set
-- numeric:               one row, response_numeric set
-- date / datetime:       one row, response_date set (ISO 8601)
-- grid cell:             one row, grid_row_id + grid_column_id + one value column
-- slider:                one row, response_numeric set
CREATE TABLE IF NOT EXISTS response (
    response_id          INTEGER PRIMARY KEY,
    session_id           INTEGER NOT NULL REFERENCES response_session (session_id),
    qq_id                INTEGER NOT NULL REFERENCES questionnaire_question (qq_id),
    option_id            INTEGER REFERENCES response_option (option_id),
    response_text        TEXT,
    response_numeric     REAL,
    response_date        TEXT,                      -- ISO 8601
    grid_row_id          INTEGER REFERENCES grid_row (row_id),
    grid_column_id       INTEGER REFERENCES grid_column (column_id),
    repeat_index         INTEGER,                 -- 0-based instance index for repeating_group; NULL otherwise
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Audit log for survey administration events.
CREATE TABLE IF NOT EXISTS admin_event (
    event_id             INTEGER PRIMARY KEY,
    study_id             INTEGER REFERENCES study (study_id),
    questionnaire_id     INTEGER REFERENCES questionnaire (questionnaire_id),
    respondent_id        INTEGER REFERENCES respondent (respondent_id),
    session_id           INTEGER REFERENCES response_session (session_id),
    event_type           TEXT    NOT NULL
                             CHECK (event_type IN (
                                 'dispatched', 'reminded', 'opened', 'completed',
                                 'expired', 'withdrawn', 'reopened')),
    event_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    notes                TEXT
);

-- Soft validation failures. Issues land here instead of raising exceptions,
-- allowing data collection to continue while flagging items for review.
CREATE TABLE IF NOT EXISTS data_quality_flag (
    flag_id              INTEGER PRIMARY KEY,
    session_id           INTEGER REFERENCES response_session (session_id),
    response_id          INTEGER REFERENCES response (response_id),
    qq_id                INTEGER REFERENCES questionnaire_question (qq_id),
    rule_name            TEXT    NOT NULL,
    message              TEXT    NOT NULL,
    severity             TEXT    NOT NULL DEFAULT 'warning'
                             CHECK (severity IN ('info', 'warning', 'error')),
    is_resolved          INTEGER NOT NULL DEFAULT 0 CHECK (is_resolved IN (0, 1)),
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- STUDY ADMINISTRATION
-- Operational tables for managing a live study: errata, known bugs,
-- corrections, and IRB-required changes. Distinct from data_quality_flag
-- (per-response algorithmic flags) — errata are human-authored notes that
-- apply to a range of sessions or a date window.
-- ============================================================

CREATE TABLE IF NOT EXISTS study_errata_log (
    errata_id              INTEGER PRIMARY KEY,
    study_id               INTEGER REFERENCES study (study_id),
    questionnaire_id       INTEGER REFERENCES questionnaire (questionnaire_id),
    question_id            INTEGER REFERENCES question (question_id),
    event_type             TEXT    NOT NULL
                               CHECK (event_type IN (
                                   'delivery_bug',    -- tool/platform error
                                   'question_error',  -- wording, translation, or logic error
                                   'deprecation',     -- question or instrument retired
                                   'correction',      -- post-hoc data correction applied
                                   'irb_action',      -- IRB-required change
                                   'note')),          -- general informational entry
    severity               TEXT    NOT NULL DEFAULT 'minor'
                               CHECK (severity IN ('critical', 'major', 'minor', 'informational')),
    title                  TEXT    NOT NULL,
    description            TEXT    NOT NULL,
    -- optional scope: which sessions/dates were affected
    affects_session_from   INTEGER REFERENCES response_session (session_id),
    affects_session_to     INTEGER REFERENCES response_session (session_id),
    affects_date_from      TEXT,
    affects_date_to        TEXT,
    -- what analysts should do with affected data
    analyst_guidance       TEXT,
    status                 TEXT    NOT NULL DEFAULT 'open'
                               CHECK (status IN ('open', 'acknowledged', 'resolved')),
    reported_by            TEXT,
    reported_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    resolved_by            TEXT,
    resolved_at            TEXT
);

-- ============================================================
-- VERSIONING AND EQUIVALENCE
-- These tables are metadata overlays — responses are never moved or
-- rewritten. They let analysts declare relationships between questions
-- and instruments while keeping the collection layer immutable.
-- ============================================================

-- Tracks intentional revision ancestry when a question is reworded,
-- has options added/removed, or is split/merged into new items.
-- Questions are immutable once created; a change always produces a
-- new question row linked back here.
CREATE TABLE IF NOT EXISTS question_lineage (
    lineage_id            INTEGER PRIMARY KEY,
    question_id           INTEGER NOT NULL REFERENCES question (question_id),     -- new version
    parent_question_id    INTEGER NOT NULL REFERENCES question (question_id),     -- predecessor
    change_type           TEXT    NOT NULL
                              CHECK (change_type IN (
                                  'reword', 'option_added', 'option_removed',
                                  'option_reworded', 'split', 'merge', 'other')),
    change_description    TEXT,
    effective_date        TEXT,
    created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (question_id != parent_question_id)
);

-- Explicit researcher declarations that two questions measure the same
-- construct, with confidence and harmonization guidance.
-- Both directions are stored (like OMOP concept_relationship) so queries
-- never need UNION to find all equivalences for a given question.
CREATE TABLE IF NOT EXISTS question_equivalence (
    equivalence_id        INTEGER PRIMARY KEY,
    question_id_1         INTEGER NOT NULL REFERENCES question (question_id),
    question_id_2         INTEGER NOT NULL REFERENCES question (question_id),
    relationship          TEXT    NOT NULL
                              CHECK (relationship IN (
                                  'equivalent',       -- interchangeable for analysis
                                  'near_equivalent',  -- comparable; note differences
                                  'related',          -- same domain, not comparable
                                  'supersedes')),     -- q1 replaced q2
    confidence            TEXT    NOT NULL DEFAULT 'medium'
                              CHECK (confidence IN ('high', 'medium', 'low')),
    harmonization_notes   TEXT,                       -- what analysts must know
    declared_by           TEXT,
    declared_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (question_id_1, question_id_2, relationship),
    CHECK (question_id_1 != question_id_2)
);

-- Structured diff between two questionnaire versions.
-- Can be populated manually or auto-generated by diff_questionnaire_versions().
CREATE TABLE IF NOT EXISTS questionnaire_version_diff (
    diff_id               INTEGER PRIMARY KEY,
    from_questionnaire_id INTEGER NOT NULL REFERENCES questionnaire (questionnaire_id),
    to_questionnaire_id   INTEGER NOT NULL REFERENCES questionnaire (questionnaire_id),
    change_type           TEXT    NOT NULL
                              CHECK (change_type IN (
                                  'item_added', 'item_removed', 'item_reworded',
                                  'item_reordered', 'skip_rule_changed',
                                  'scoring_changed', 'option_changed')),
    qq_id_from            INTEGER REFERENCES questionnaire_question (qq_id),
    qq_id_to              INTEGER REFERENCES questionnaire_question (qq_id),
    notes                 TEXT,
    created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- OMOP person identity bridge. respondent.external_id is de-identified;
-- this table links it to an omop_person_id for OMOP extraction.
-- Populated by a study-specific ETL, not by quickq itself.
CREATE TABLE IF NOT EXISTS person_map (
    respondent_id         INTEGER PRIMARY KEY REFERENCES respondent (respondent_id),
    omop_person_id        INTEGER NOT NULL UNIQUE,
    created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- TOOL AUDIT LOG
-- Records key operations performed against the study database so that
-- provenance travels with the file. Useful for HIPAA audit trail
-- requirements and IRB data management documentation.
-- ============================================================

CREATE TABLE IF NOT EXISTS tool_audit_log (
    log_id               INTEGER PRIMARY KEY,
    study_id             INTEGER REFERENCES study (study_id),
    operation            TEXT    NOT NULL,          -- 'federated_query', 'pseudonymize', 'merge',
                                                    -- 'export_parquet', 'delete_respondent', etc.
    performed_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    performed_by         TEXT,                      -- username / process identifier if available
    details              TEXT                       -- JSON blob: query hash, output path, counts, etc.
);

CREATE INDEX IF NOT EXISTS idx_audit_operation ON tool_audit_log (operation);
CREATE INDEX IF NOT EXISTS idx_audit_study     ON tool_audit_log (study_id) WHERE study_id IS NOT NULL;

-- ============================================================
-- INDEXES
-- ============================================================

-- Response lookups (hot path during analysis and FHIR export)
CREATE INDEX IF NOT EXISTS idx_response_session      ON response (session_id);
CREATE INDEX IF NOT EXISTS idx_response_qq           ON response (qq_id);
CREATE INDEX IF NOT EXISTS idx_response_option       ON response (option_id) WHERE option_id IS NOT NULL;

-- Session lookups
CREATE INDEX IF NOT EXISTS idx_session_questionnaire ON response_session (questionnaire_id);
CREATE INDEX IF NOT EXISTS idx_session_respondent    ON response_session (respondent_id);

-- Instrument structure traversal
CREATE INDEX IF NOT EXISTS idx_qq_questionnaire      ON questionnaire_question (questionnaire_id);
CREATE INDEX IF NOT EXISTS idx_qq_question           ON questionnaire_question (question_id);
CREATE INDEX IF NOT EXISTS idx_qq_section            ON questionnaire_question (section_id) WHERE section_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_section_questionnaire ON section (questionnaire_id);
CREATE INDEX IF NOT EXISTS idx_skip_rule_qq          ON skip_rule (qq_id);
CREATE INDEX IF NOT EXISTS idx_skip_rule_trigger     ON skip_rule (trigger_qq_id);

-- Option lookups
CREATE INDEX IF NOT EXISTS idx_option_question       ON response_option (question_id);
CREATE INDEX IF NOT EXISTS idx_grid_row_question     ON grid_row (question_id);
CREATE INDEX IF NOT EXISTS idx_grid_col_question     ON grid_column (question_id);

-- Concept lookups (used by concept mapper and FHIR export)
CREATE INDEX IF NOT EXISTS idx_concept_vocab_code    ON concept (vocabulary_id, concept_code);
CREATE INDEX IF NOT EXISTS idx_concept_domain        ON concept (domain_id);

-- Scoring
CREATE INDEX IF NOT EXISTS idx_scoring_questionnaire ON scoring_rule (questionnaire_id);
CREATE INDEX IF NOT EXISTS idx_scoring_item_rule     ON scoring_rule_item (scoring_rule_id);
CREATE INDEX IF NOT EXISTS idx_scoring_item_qq       ON scoring_rule_item (qq_id);

-- Data quality
CREATE INDEX IF NOT EXISTS idx_dqf_session           ON data_quality_flag (session_id);
CREATE INDEX IF NOT EXISTS idx_dqf_unresolved        ON data_quality_flag (session_id) WHERE is_resolved = 0;

-- Study administration
CREATE INDEX IF NOT EXISTS idx_errata_study          ON study_errata_log (study_id);
CREATE INDEX IF NOT EXISTS idx_errata_questionnaire  ON study_errata_log (questionnaire_id) WHERE questionnaire_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_errata_question       ON study_errata_log (question_id) WHERE question_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_errata_open           ON study_errata_log (study_id) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_qq_status             ON questionnaire_question (questionnaire_id, status);

-- Versioning and equivalence
CREATE INDEX IF NOT EXISTS idx_lineage_question      ON question_lineage (question_id);
CREATE INDEX IF NOT EXISTS idx_lineage_parent        ON question_lineage (parent_question_id);
CREATE INDEX IF NOT EXISTS idx_equiv_q1              ON question_equivalence (question_id_1);
CREATE INDEX IF NOT EXISTS idx_equiv_q2              ON question_equivalence (question_id_2);
CREATE INDEX IF NOT EXISTS idx_qvd_from              ON questionnaire_version_diff (from_questionnaire_id);
CREATE INDEX IF NOT EXISTS idx_qvd_to                ON questionnaire_version_diff (to_questionnaire_id);
