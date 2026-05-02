-- Demo analytical views for the perinatal mental health tutorial.
--
-- Applied to demo/analytics.duckdb by scripts/generate_demo.py after refresh.
-- All views depend on the standard OLAP tables (fact_response, dim_*, agg_*).
-- Views with dependencies are ordered so each one can reference the ones above it.
--
-- Views:
--   v_phq9_scores              — one row per PHQ-9 session with total and severity
--   v_phq9_severity_distribution — severity breakdown with percentages
--   v_phq9_by_admin_mode        — mean score and range by delivery mode
--   v_prenatal_visits           — one row per visit instance (pivot of repeating group)
--   v_prenatal_summary          — visit totals per respondent
--   v_phq9_prenatal_overlap     — cross-instrument join: PHQ-9 score + prenatal summary

-- ── PHQ-9 scored sessions ────────────────────────────────────────────────────
-- Joins scored results to respondent and session context.
-- severity comes from the PHQ-9 scoring rule categories defined in the YAML.

CREATE OR REPLACE VIEW v_phq9_scores AS
SELECT
    dr.external_id                  AS respondent,
    ars.score_raw                   AS phq9_total,
    ars.score_category              AS severity,
    ds.admin_mode,
    DATE(ds.completed_at)           AS completed_date
FROM      agg_respondent_scores ars
JOIN      dim_respondent        dr  USING (respondent_id)
JOIN      dim_session           ds  USING (session_id)
WHERE ars.scoring_rule_name = 'PHQ-9 Total Score';

-- ── Severity frequency distribution ─────────────────────────────────────────
-- Window function gives percentage within the whole sample.

CREATE OR REPLACE VIEW v_phq9_severity_distribution AS
SELECT
    severity,
    COUNT(*)                                                AS n,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)     AS pct,
    ROUND(AVG(phq9_total), 1)                               AS mean_score
FROM v_phq9_scores
GROUP BY severity
ORDER BY mean_score;

-- ── Score breakdown by administration mode ───────────────────────────────────
-- Useful for detecting mode effects (web vs. paper vs. phone).

CREATE OR REPLACE VIEW v_phq9_by_admin_mode AS
SELECT
    admin_mode,
    COUNT(*)                    AS n,
    ROUND(AVG(phq9_total), 1)   AS mean_score,
    MIN(phq9_total)             AS min_score,
    MAX(phq9_total)             AS max_score
FROM v_phq9_scores
GROUP BY admin_mode
ORDER BY admin_mode;

-- ── Prenatal visit detail ────────────────────────────────────────────────────
-- Pivots the repeating_group response rows into one row per visit instance.
-- repeat_index is the 0-based instance counter written by import_fhir_response.

CREATE OR REPLACE VIEW v_prenatal_visits AS
SELECT
    dr.external_id                  AS respondent,
    fr.repeat_index                 AS visit_number,
    MAX(CASE WHEN dq.link_id = 'visits.week'
             THEN fr.response_numeric END)  AS gestational_week,
    MAX(CASE WHEN dq.link_id = 'visits.provider'
             THEN fr.option_value END)       AS provider,
    MAX(CASE WHEN dq.link_id = 'visits.concern'
             THEN fr.response_text END)      AS concern_noted
FROM      fact_response fr
JOIN      dim_question   dq USING (question_id)
JOIN      dim_respondent dr USING (respondent_id)
WHERE dq.link_id IN ('visits.week', 'visits.provider', 'visits.concern')
GROUP BY dr.external_id, fr.session_id, fr.repeat_index
ORDER BY respondent, visit_number;

-- ── Prenatal visit summary per respondent ───────────────────────────────────

CREATE OR REPLACE VIEW v_prenatal_summary AS
SELECT
    respondent,
    COUNT(*)                                            AS total_visits,
    MIN(gestational_week)                               AS first_visit_week,
    MAX(gestational_week)                               AS last_visit_week,
    COUNT(CASE WHEN concern_noted = 'true' THEN 1 END)  AS visits_with_concern
FROM v_prenatal_visits
GROUP BY respondent;

-- ── Cross-instrument join ────────────────────────────────────────────────────
-- Links each PHQ-9 session to that respondent's prenatal care summary.
-- Only includes respondents who completed both instruments (inner join).

CREATE OR REPLACE VIEW v_phq9_prenatal_overlap AS
SELECT
    phq.respondent,
    phq.phq9_total,
    phq.severity,
    phq.admin_mode,
    pre.total_visits,
    pre.visits_with_concern
FROM v_phq9_scores       phq
JOIN v_prenatal_summary  pre USING (respondent)
ORDER BY phq.phq9_total;
