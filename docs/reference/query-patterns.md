# Query Patterns by Question Type

`fact_response` stores one row per answer atom. The value column that holds the answer depends on the question's `type` as declared in the YAML. Use `dim_question.question_type` to know which column to aggregate before writing any query.

```sql
-- What types are in this study, and how many responses does each have?
SELECT question_type, COUNT(fr.response_id) AS response_count
FROM dim_question dq
LEFT JOIN fact_response fr USING (question_id)
GROUP BY question_type
ORDER BY response_count DESC;
```

---

## `single_choice` and `likert`

Responses land in `option_value` (the string value declared in the YAML or FHIR coding). For scored Likert scales, `response_numeric` also holds the numeric value via `option_value` coercion.

**Distribution for one question:**

```sql
SELECT
    dq.link_id,
    fr.option_value,
    COUNT(*)                                                                    AS n,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY dq.link_id), 1)  AS pct
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.link_id = 'visits.provider'
GROUP BY dq.link_id, fr.option_value
ORDER BY n DESC;
```

**Distribution for all single-choice questions at once:**

```sql
SELECT
    dq.link_id,
    dq.question_text,
    fr.option_value,
    COUNT(*)                                                                    AS n,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY dq.question_id), 1) AS pct
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.question_type IN ('single_choice', 'likert')
GROUP BY dq.link_id, dq.question_text, fr.option_value
ORDER BY dq.link_id, n DESC;
```

**Mean numeric score across all likert items (e.g. PHQ-9):**

```sql
SELECT dq.link_id, ROUND(AVG(fr.response_numeric), 2) AS mean_score
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.question_type = 'likert'
  AND fr.response_numeric IS NOT NULL
GROUP BY dq.link_id
ORDER BY mean_score DESC;
```

---

## `multiple_choice` and `sata_other`

Same storage as `single_choice` — one row per selected option. A respondent who selected three options in a SATA question has three rows. Count distinct sessions for denominators.

```sql
SELECT
    dq.link_id,
    fr.option_value,
    COUNT(*)                                                                     AS n_selections,
    COUNT(DISTINCT fr.session_id)                                                AS n_respondents,
    COUNT(DISTINCT total.session_id)                                             AS total_respondents,
    ROUND(100.0 * COUNT(DISTINCT fr.session_id)
               / COUNT(DISTINCT total.session_id), 1)                           AS pct_selected
FROM fact_response fr
JOIN dim_question dq USING (question_id)
JOIN (SELECT DISTINCT session_id FROM fact_response) total ON TRUE
WHERE dq.question_type IN ('multiple_choice', 'sata_other')
  AND dq.link_id = 'your_question_link_id'
GROUP BY dq.link_id, fr.option_value
ORDER BY pct_selected DESC;
```

For `sata_other`, rows where `option_value IS NULL` and `response_text IS NOT NULL` are the free-text "Other" entries.

---

## `boolean`

Responses land in `response_text` as the string `'true'` or `'false'`. Use a conditional aggregate for the proportion positive.

```sql
SELECT
    dq.link_id,
    dq.question_text,
    COUNT(*)                                                                    AS n,
    ROUND(100.0 * SUM(CASE WHEN fr.response_text = 'true' THEN 1 ELSE 0 END)
                / COUNT(*), 1)                                                  AS pct_true
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.question_type = 'boolean'
GROUP BY dq.link_id, dq.question_text;
```

---

## `numeric` and `slider`

Responses land in `response_numeric`. Use standard aggregates — or `agg_numeric_stats`, which is pre-computed by `quickq refresh`.

**Quick stats from `fact_response`:**

```sql
SELECT
    dq.link_id,
    COUNT(*)                            AS n,
    ROUND(AVG(fr.response_numeric), 1)  AS mean,
    MEDIAN(fr.response_numeric)         AS median,
    MIN(fr.response_numeric)            AS min,
    MAX(fr.response_numeric)            AS max
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.question_type IN ('numeric', 'slider')
GROUP BY dq.link_id;
```

**Or use the pre-computed aggregate (faster on large datasets):**

```sql
SELECT link_id, n, mean, median, std_dev, p25, p75, min_val, max_val
FROM agg_numeric_stats
JOIN dim_question USING (question_id);
```

---

## `text`

Responses land in `response_text`. Analytical aggregation is limited — typically these are reviewed qualitatively or exported for coding. Count non-empty responses:

```sql
SELECT
    dq.link_id,
    COUNT(*)                                                    AS n_responses,
    COUNT(CASE WHEN LENGTH(fr.response_text) > 0 THEN 1 END)   AS n_non_empty
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.question_type = 'text'
GROUP BY dq.link_id;
```

---

## `date`

Responses land in `response_date`. Use standard date functions.

```sql
SELECT
    dq.link_id,
    DATE_TRUNC('month', fr.response_date)   AS month,
    COUNT(*)                                AS n
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.question_type = 'date'
  AND fr.response_date IS NOT NULL
GROUP BY dq.link_id, month
ORDER BY dq.link_id, month;
```

---

## `repeating_group`

The container item itself has `response_count = 0`. Its children are ordinary rows in `fact_response` with a `repeat_index` identifying the loop instance (0-based). Filter or group by `repeat_index` to work with specific instances.

```sql
-- Responses from the second visit (repeat_index = 1) only
SELECT dr.external_id AS respondent, dq.link_id, fr.response_numeric, fr.option_value
FROM fact_response fr
JOIN dim_question   dq USING (question_id)
JOIN dim_respondent dr USING (respondent_id)
WHERE fr.repeat_index = 1
  AND dq.link_id LIKE 'visits.%'
ORDER BY respondent;
```

**Pivot a repeating group into one row per instance:**

```sql
SELECT
    dr.external_id                  AS respondent,
    fr.repeat_index                 AS visit_number,
    MAX(CASE WHEN dq.link_id = 'visits.week'
             THEN fr.response_numeric END)  AS gestational_week,
    MAX(CASE WHEN dq.link_id = 'visits.provider'
             THEN fr.option_value END)       AS provider
FROM fact_response fr
JOIN dim_question   dq USING (question_id)
JOIN dim_respondent dr USING (respondent_id)
WHERE dq.link_id IN ('visits.week', 'visits.provider')
GROUP BY dr.external_id, fr.session_id, fr.repeat_index
ORDER BY respondent, visit_number;
```

The demo pre-builds this pivot as `v_prenatal_visits`. See `quickq/sql/demo_views.sql`.

---

## `grid`

Grid responses have `grid_row_id` and `grid_column_id` populated. Join `grid_row` and `grid_column` from the OLTP to get display labels, or use the option values directly.

```sql
SELECT
    dr.external_id          AS respondent,
    gr.row_text             AS symptom,
    gc.column_value         AS severity,
    COUNT(*)                AS n
FROM fact_response fr
JOIN dim_question   dq  USING (question_id)
JOIN dim_respondent dr  USING (respondent_id)
-- grid_row and grid_column live in the OLTP
JOIN oltp.grid_row  gr  ON fr.grid_row_id    = gr.row_id
JOIN oltp.grid_column gc ON fr.grid_column_id = gc.column_id
WHERE dq.question_type = 'grid'
GROUP BY respondent, symptom, severity
ORDER BY respondent, severity;
```

---

## `ranked`

Each selected option is a row; `response_numeric` holds the rank (1 = first choice). Lower is better.

```sql
SELECT
    dq.link_id,
    fr.option_value,
    ROUND(AVG(fr.response_numeric), 2)  AS mean_rank,
    COUNT(*)                            AS n
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.question_type = 'ranked'
GROUP BY dq.link_id, fr.option_value
ORDER BY mean_rank;
```
