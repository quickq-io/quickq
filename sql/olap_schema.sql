-- quickq OLAP schema (DuckDB)
-- Standard analytical data model. All analytical tools, reports, and views
-- must be built on this layer — never query the OLTP SQLite database directly
-- for analysis.
--
-- Populated on-demand via: quickq refresh
-- DuckDB reads SQLite directly:
--   ATTACH 'quickq.db' AS oltp (TYPE sqlite, READ_ONLY);

-- ============================================================
-- DIMENSION TABLES
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_date (
    date_key             DATE        PRIMARY KEY,
    year                 INTEGER     NOT NULL,
    quarter              INTEGER     NOT NULL,
    month                INTEGER     NOT NULL,
    week                 INTEGER     NOT NULL,
    day_of_week          INTEGER     NOT NULL,   -- 0=Sunday
    day_of_year          INTEGER     NOT NULL,
    is_weekend           BOOLEAN     NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_study (
    study_id             INTEGER     PRIMARY KEY,
    name                 VARCHAR     NOT NULL,
    description          VARCHAR,
    principal_investigator VARCHAR,
    irb_number           VARCHAR,
    start_date           DATE,
    end_date             DATE
);

CREATE TABLE IF NOT EXISTS dim_questionnaire (
    questionnaire_id     INTEGER     PRIMARY KEY,
    study_id             INTEGER     NOT NULL,
    name                 VARCHAR     NOT NULL,
    version              VARCHAR     NOT NULL,
    canonical_url        VARCHAR,
    fhir_status          VARCHAR     NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_concept (
    concept_id           INTEGER     PRIMARY KEY,
    concept_name         VARCHAR     NOT NULL,
    domain_id            VARCHAR     NOT NULL,
    vocabulary_id        VARCHAR     NOT NULL,
    concept_class_id     VARCHAR     NOT NULL,
    standard_concept     VARCHAR,
    concept_code         VARCHAR     NOT NULL
);

-- Questions denormalized with their concept labels.
-- source_instrument / source_item_id support provenance-based cross-study queries.
-- equivalence_group_id is a computed cluster ID (connected components of the
-- question_equivalence graph). Questions in the same group can be treated as
-- measuring the same construct; use confidence tier to control sensitivity.
CREATE TABLE IF NOT EXISTS dim_question (
    question_id          INTEGER     PRIMARY KEY,
    link_id              VARCHAR     NOT NULL,
    question_text        VARCHAR     NOT NULL,
    question_type        VARCHAR     NOT NULL,
    help_text            VARCHAR,
    source_instrument    VARCHAR,
    source_item_id       VARCHAR,
    citation             VARCHAR,
    concept_id           INTEGER,
    concept_name         VARCHAR,
    vocabulary_id        VARCHAR,
    concept_code         VARCHAR,
    equivalence_group_id INTEGER                 -- NULL = no declared equivalences
);

-- Response options denormalized with concept labels.
CREATE TABLE IF NOT EXISTS dim_response_option (
    option_id            INTEGER     PRIMARY KEY,
    question_id          INTEGER     NOT NULL,
    option_text          VARCHAR     NOT NULL,
    option_value         VARCHAR     NOT NULL,
    display_order        INTEGER     NOT NULL,
    option_set_id        INTEGER,
    is_other             BOOLEAN     NOT NULL,
    is_exclusive         BOOLEAN     NOT NULL,
    concept_id           INTEGER,
    concept_name         VARCHAR,
    concept_code         VARCHAR,
    concept_system       VARCHAR
);

-- Respondents. external_id is the de-identified participant key.
CREATE TABLE IF NOT EXISTS dim_respondent (
    respondent_id        INTEGER     PRIMARY KEY,
    study_id             INTEGER     NOT NULL,
    external_id          VARCHAR,
    enrollment_date      DATE
);

-- Sessions denormalized with derived metrics.
CREATE TABLE IF NOT EXISTS dim_session (
    session_id           INTEGER     PRIMARY KEY,
    questionnaire_id     INTEGER     NOT NULL,
    respondent_id        INTEGER     NOT NULL,
    study_id             INTEGER     NOT NULL,
    started_at           TIMESTAMP,
    completed_at         TIMESTAMP,
    is_complete          BOOLEAN     NOT NULL,
    admin_mode           VARCHAR,
    is_proxy             BOOLEAN     NOT NULL,
    duration_sec         INTEGER,                -- NULL if not complete
    session_date_key     DATE
);

-- ============================================================
-- FACT TABLE
-- One row per answer atom. Mirrors the OLTP response table but
-- pre-joined with concept and dimension keys for fast analytics.
-- ============================================================

CREATE TABLE IF NOT EXISTS fact_response (
    -- surrogate key
    response_id          BIGINT      PRIMARY KEY,

    -- foreign keys into dimensions
    session_id           INTEGER     NOT NULL,
    respondent_id        INTEGER     NOT NULL,
    questionnaire_id     INTEGER     NOT NULL,
    study_id             INTEGER     NOT NULL,
    question_id          INTEGER     NOT NULL,
    qq_id                INTEGER     NOT NULL,   -- placement key; preserves version context
    option_id            INTEGER,                -- NULL for open/numeric/date answers
    grid_row_id          INTEGER,
    grid_column_id       INTEGER,

    -- typed answer columns (same semantics as OLTP response)
    response_text        VARCHAR,
    response_numeric     DOUBLE,
    response_date        DATE,
    option_value         VARCHAR,                -- denormalized from response_option for speed

    -- concept keys (pre-joined; enable concept-based cross-study queries without joins)
    question_concept_id  INTEGER,
    option_concept_id    INTEGER,

    -- date keys
    response_date_key    DATE,                   -- date of session completion or response creation
    session_start_key    DATE,

    -- admin context (important covariates for mode-effect analysis in epi)
    admin_mode           VARCHAR,
    is_proxy             BOOLEAN,
    interviewer_id       VARCHAR,

    -- load metadata
    loaded_at            TIMESTAMP   NOT NULL DEFAULT now()
);

-- ============================================================
-- AGGREGATE TABLES
-- Materialized on each refresh. Views and reports should prefer
-- these over querying fact_response directly when possible.
-- ============================================================

-- Response frequency distribution per question per study.
-- For closed-ended questions: one row per (question, option).
-- For boolean: option_value is 'true' or 'false'.
-- For open/numeric questions: omitted (use agg_numeric_stats).
CREATE TABLE IF NOT EXISTS agg_question_distribution (
    study_id             INTEGER     NOT NULL,
    questionnaire_id     INTEGER     NOT NULL,
    question_id          INTEGER     NOT NULL,
    question_concept_id  INTEGER,
    option_id            INTEGER,
    option_value         VARCHAR,
    option_concept_id    INTEGER,
    n                    INTEGER     NOT NULL,
    pct                  DOUBLE      NOT NULL,   -- 0.0–100.0; denominator = sessions with any answer to this question
    refreshed_at         TIMESTAMP   NOT NULL DEFAULT now(),
    PRIMARY KEY (study_id, questionnaire_id, question_id, option_value)
);

-- Descriptive statistics for numeric questions.
CREATE TABLE IF NOT EXISTS agg_numeric_stats (
    study_id             INTEGER     NOT NULL,
    questionnaire_id     INTEGER     NOT NULL,
    question_id          INTEGER     NOT NULL,
    question_concept_id  INTEGER,
    n                    INTEGER     NOT NULL,
    mean                 DOUBLE,
    median               DOUBLE,
    std_dev              DOUBLE,
    min_val              DOUBLE,
    max_val              DOUBLE,
    p25                  DOUBLE,
    p75                  DOUBLE,
    refreshed_at         TIMESTAMP   NOT NULL DEFAULT now(),
    PRIMARY KEY (study_id, questionnaire_id, question_id)
);

-- Session completion rates by study / questionnaire / day.
CREATE TABLE IF NOT EXISTS agg_session_completion (
    study_id             INTEGER     NOT NULL,
    questionnaire_id     INTEGER     NOT NULL,
    session_date_key     DATE        NOT NULL,
    admin_mode           VARCHAR,
    n_started            INTEGER     NOT NULL,
    n_completed          INTEGER     NOT NULL,
    completion_rate      DOUBLE      NOT NULL,   -- 0.0–1.0
    median_duration_sec  INTEGER,
    refreshed_at         TIMESTAMP   NOT NULL DEFAULT now(),
    PRIMARY KEY (study_id, questionnaire_id, session_date_key, admin_mode)
);

-- Computed scores per respondent per scoring rule.
-- Populated during refresh by evaluating scoring_rule + scoring_rule_item from OLTP.
CREATE TABLE IF NOT EXISTS agg_respondent_scores (
    respondent_id        INTEGER     NOT NULL,
    questionnaire_id     INTEGER     NOT NULL,
    scoring_rule_id      INTEGER     NOT NULL,
    scoring_rule_name    VARCHAR     NOT NULL,
    session_id           INTEGER     NOT NULL,
    score_raw            DOUBLE,
    score_category       VARCHAR,                -- 'Minimal', 'Mild', etc.
    items_answered       INTEGER     NOT NULL,
    items_total          INTEGER     NOT NULL,
    scored_at            TIMESTAMP   NOT NULL DEFAULT now(),
    PRIMARY KEY (respondent_id, session_id, scoring_rule_id)
);

-- ============================================================
-- VERSIONING AND EQUIVALENCE ANALYTICAL TABLES
-- ============================================================

-- All declared equivalence pairs, mirrored from OLTP question_equivalence.
-- Both directions stored so that WHERE question_id_1 = X finds everything.
-- Populated during quickq refresh.
CREATE TABLE IF NOT EXISTS dim_question_equivalence (
    question_id_1         INTEGER     NOT NULL,
    question_id_2         INTEGER     NOT NULL,
    relationship          VARCHAR     NOT NULL,
    confidence            VARCHAR     NOT NULL,
    harmonization_notes   VARCHAR,
    refreshed_at          TIMESTAMP   NOT NULL DEFAULT now(),
    PRIMARY KEY (question_id_1, question_id_2, relationship)
);

-- Question lineage mirror for OLAP provenance queries.
CREATE TABLE IF NOT EXISTS dim_question_lineage (
    lineage_id            INTEGER     PRIMARY KEY,
    question_id           INTEGER     NOT NULL,
    parent_question_id    INTEGER     NOT NULL,
    change_type           VARCHAR     NOT NULL,
    change_description    VARCHAR,
    effective_date        DATE,
    refreshed_at          TIMESTAMP   NOT NULL DEFAULT now()
);

-- ============================================================
-- OMOP EXTRACTION TABLES
-- Populated during quickq refresh for studies that export to OMOP CDM.
-- Requires person_map to be populated in the OLTP database.
-- Questions without a concept_id are excluded and surfaced in
-- omop_unmapped_questions.
-- ============================================================

CREATE TABLE IF NOT EXISTS omop_survey_conduct (
    survey_conduct_id    INTEGER     PRIMARY KEY,   -- = response_session.session_id
    person_id            INTEGER,                   -- NULL if person_map not populated
    survey_concept_id    INTEGER,
    survey_start_date    DATE,
    survey_end_date      DATE,
    assisted_concept_id  INTEGER,                   -- 532063=Proxy, 0=self
    survey_source_value  VARCHAR,                   -- admin_mode
    refreshed_at         TIMESTAMP   NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS omop_observation (
    observation_id            BIGINT      PRIMARY KEY,  -- = response.response_id
    person_id                 INTEGER,
    observation_concept_id    INTEGER     NOT NULL,     -- question.concept_id
    observation_date          DATE,
    questionnaire_response_id INTEGER,                  -- = session_id
    value_as_concept_id       INTEGER,
    value_as_string           VARCHAR,
    value_as_number           DOUBLE,
    observation_source_value  VARCHAR,
    observation_type_concept_id INTEGER  NOT NULL DEFAULT 32836,  -- 'Survey'
    refreshed_at              TIMESTAMP  NOT NULL DEFAULT now()
);

-- Questions that could not be mapped to OMOP due to missing concept_id.
-- Surfaced here so studies know what to map before export.
CREATE TABLE IF NOT EXISTS omop_unmapped_questions (
    question_id          INTEGER     PRIMARY KEY,
    link_id              VARCHAR     NOT NULL,
    question_text        VARCHAR     NOT NULL,
    source_instrument    VARCHAR,
    response_count       INTEGER     NOT NULL DEFAULT 0,
    refreshed_at         TIMESTAMP   NOT NULL DEFAULT now()
);

-- ============================================================
-- REFRESH WATERMARK
-- Tracks the last successful incremental load from OLTP.
-- quickq refresh reads this to know where to start.
-- ============================================================

CREATE TABLE IF NOT EXISTS refresh_log (
    refresh_id           INTEGER     PRIMARY KEY,
    started_at           TIMESTAMP   NOT NULL,
    completed_at         TIMESTAMP,
    max_response_id      BIGINT      NOT NULL DEFAULT 0,
    max_session_id       INTEGER     NOT NULL DEFAULT 0,
    rows_loaded          INTEGER     NOT NULL DEFAULT 0,
    status               VARCHAR     NOT NULL DEFAULT 'running'
                             CHECK (status IN ('running', 'complete', 'failed')),
    error_message        VARCHAR
);
