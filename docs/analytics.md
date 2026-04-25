# Analytics & Refresh

The analytical layer is a DuckDB star schema populated on demand from the SQLite OLTP file. All reports, cohort queries, and cross-study analyses run here. The OLTP is never queried directly for analysis.

---

## Refresh

```bash
quickq refresh study.db
```

Refresh is incremental. It reads `refresh_log` to find the high-water mark from the last run, loads only new `response` and `response_session` rows, recomputes scores and aggregates, and updates the watermark on success. A failed run leaves the watermark unchanged so the next run retries the same window cleanly.

DuckDB attaches the SQLite file directly — no intermediate export, no staging database:

```sql
ATTACH 'study.db' AS oltp (TYPE sqlite, READ_ONLY);
```

---

## What Refresh Produces

| Output | Description |
|---|---|
| `fact_response` | One row per answer atom, pre-joined with concept and dimension keys |
| `dim_question`, `dim_respondent`, `dim_session`, etc. | Dimension tables for filtering and grouping |
| `agg_question_distribution` | Response frequency and percentage per question per option |
| `agg_numeric_stats` | Mean, median, SD, percentiles for numeric questions |
| `agg_session_completion` | Daily enrollment, completion rate, and median session duration by admin mode |
| `agg_respondent_scores` | Computed scale scores (PHQ-9 total, GAD-7 severity, etc.) per respondent per session |
| `omop_survey_conduct` | OMOP CDM SurveyConducts projection (when `person_map` is populated) |
| `omop_observation` | OMOP CDM Observations projection for concept-mapped responses |
| `omop_unmapped_questions` | Questions excluded from OMOP export due to missing `concept_id` |

---

## Querying

The fact table is the starting point for most analytical queries. Dimensions provide the labels.

**Prevalence of a symptom:**
```sql
SELECT
    option_value,
    COUNT(*) AS n,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.link_id = 'phq-1'
GROUP BY option_value
ORDER BY option_value;
```

**PHQ-9 scores by admin mode:**
```sql
SELECT
    ds.admin_mode,
    ROUND(AVG(score_raw), 2) AS mean_phq9,
    COUNT(*) AS n
FROM agg_respondent_scores ars
JOIN dim_session ds USING (session_id)
WHERE ars.scoring_rule_name = 'PHQ-9 Total'
GROUP BY ds.admin_mode;
```

**Repeating group: per-visit data across pregnancies:**
```sql
SELECT
    fr.respondent_id,
    fr.repeat_index     AS visit_number,
    fr.response_numeric AS gestational_week
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.link_id = 'visits.week'
ORDER BY respondent_id, visit_number;
```

---

## Cross-Study Harmonization

`dim_question.equivalence_group_id` is a computed cluster ID — the connected-component ID of the `question_equivalence` graph declared in the OLTP. Questions in the same group measure the same construct across different instruments or studies.

```sql
-- PHQ-9 item 1 prevalence pooled across instruments with different link_ids
SELECT
    dq.source_instrument,
    AVG(fr.response_numeric) AS mean_score
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.equivalence_group_id = (
    SELECT equivalence_group_id FROM dim_question WHERE link_id = 'phq-1'
)
GROUP BY dq.source_instrument;
```

For concept-based queries, `question_concept_id` and `option_concept_id` are pre-joined onto `fact_response` — no extra join required:

```sql
-- All responses to any LOINC 44250-9 question (PHQ-9 item 1, any instrument)
SELECT respondent_id, response_numeric
FROM fact_response
WHERE question_concept_id = (
    SELECT concept_id FROM dim_concept
    WHERE vocabulary_id = 'LOINC' AND concept_code = '44250-9'
);
```

---

## OMOP Extraction

Studies participating in federated networks (PCORnet, TriNetX, i2b2) can export OMOP CDM tables via `quickq refresh` once `person_map` is populated in the OLTP.

`omop_unmapped_questions` is the first data quality check before any federated query: it lists every question excluded from OMOP output due to a missing `concept_id`, with its response count. High response counts on unmapped questions indicate data that will be silently excluded from network queries.

```sql
SELECT link_id, question_text, response_count
FROM omop_unmapped_questions
ORDER BY response_count DESC;
```

---

## Reports

```bash
quickq report study.db
```

Generates a Markdown summary from the OLAP: enrollment counts, completion rates, question distributions, and scored scale summaries. The report reads exclusively from aggregate tables and requires a prior `quickq refresh`.
