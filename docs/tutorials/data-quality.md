# Tutorial: Data Quality

This tutorial covers the data quality tools available after `quickq refresh`: checking for unexpected sparsity, diagnosing skip logic vs. genuine missingness, auditing concept mapping coverage, and reviewing import flags. The queries below use the demo database; if you have not generated it yet, see [Analytics phase tutorials](../tutorial.md#analytics-phase-tutorials) for the one-line setup.

Once the demo is generated, open the UI:

```bash
quickq analytics demo/analytics.duckdb
```

---

## Sparsity overview with SUMMARIZE

DuckDB's built-in `SUMMARIZE` gives null counts and percentages for every column in one shot:

```sql
SUMMARIZE fact_response;
```

The null pattern in the demo is instructive:

| Column | Null % | Why |
|---|---|---|
| `option_id` | ~30% | Numeric and boolean answers don't use options |
| `repeat_index` | ~60% | Non-repeating questions have no instance index |
| `response_text` | ~87% | Most answers are numeric or coded, not text |
| `grid_row_id` / `grid_column_id` | 100% | No grid questions in this study |
| `response_date` | 100% | No date questions in this study |
| `question_concept_id` | ~60% | Prenatal visit questions have no LOINC codes; PHQ-9 questions are mapped |

!!! tip "Sparsity is by design"
    The `fact_response` table stores one row per answer atom with typed value columns (`response_numeric`, `response_text`, `option_id`, `response_date`). Any question type uses exactly one or two of these columns and leaves the rest null. High null rates on value columns are expected — they reflect question type, not missing data.

---

## Skip logic vs. missingness

`phq9.difficulty` has a skip rule: it only appears when at least one of items 1–3 is non-zero. In the data, 22 respondents (8.8%) did not answer it. Before treating that as missingness, check whether those respondents simply scored zero:

```sql
SELECT
    CASE WHEN phq9_total = 0 THEN 'score = 0 (correctly skipped)'
         ELSE 'score > 0 (unexpectedly missing)'
    END                          AS explanation,
    COUNT(*)                     AS n
FROM v_phq9_scores
WHERE respondent_id NOT IN (
    SELECT DISTINCT dr.respondent_id
    FROM fact_response fr
    JOIN dim_question   dq USING (question_id)
    JOIN dim_respondent dr USING (respondent_id)
    WHERE dq.link_id = 'phq9.difficulty'
)
GROUP BY 1;
```

All 22 non-answers have `phq9_total = 0` — the skip logic worked correctly. A genuine missingness problem would show respondents with `score > 0` who never answered the item.

### Identifying items with unexpected missingness

To check all items for respondents who were expected to answer but did not:

```sql
SELECT
    dq.link_id,
    dq.question_text,
    COUNT(DISTINCT ds.session_id)                            AS total_sessions,
    COUNT(DISTINCT fr.session_id)                           AS sessions_answered,
    COUNT(DISTINCT ds.session_id) - COUNT(DISTINCT fr.session_id) AS missing
FROM dim_session ds
CROSS JOIN dim_question dq
LEFT JOIN fact_response fr
    ON fr.question_id = dq.question_id
   AND fr.session_id  = ds.session_id
WHERE dq.question_type NOT IN ('repeating_group')
GROUP BY dq.link_id, dq.question_text
HAVING missing > 0
ORDER BY missing DESC;
```

---

## Concept mapping audit

The demo seeds the LOINC vocabulary before loading the PHQ-9 YAML, so every PHQ-9 question resolves a `concept_id`. The prenatal visit fields have no LOINC codes, so they remain unmapped. Before contributing to a federated network query, inspect which questions would be excluded:

```sql
SELECT
    dq.link_id,
    dq.question_text,
    COUNT(*) AS response_count,
    MAX(dq.concept_id) AS concept_id
FROM fact_response fr
JOIN dim_question dq USING (question_id)
GROUP BY dq.link_id, dq.question_text
HAVING MAX(dq.concept_id) IS NULL
ORDER BY response_count DESC;
```

The `omop_unmapped_questions` table gives the same list post-refresh and is the recommended pre-export checklist:

```sql
SELECT link_id, question_text, source_instrument, response_count
FROM omop_unmapped_questions
ORDER BY response_count DESC;
```

High `response_count` on an unmapped question means real data will be silently excluded from any federated query until the mapping is added. In this demo, the 5 prenatal visit questions appear here — their FHIR codings use local codes (`ob`, `midwife`, `np`) rather than LOINC. Adding LOINC or SNOMED codes to the YAML and re-running `quickq refresh` would move them from unmapped to exported.

---

## Import flags

For any session where `import_fhir_response` encountered an unrecognised answer format or an unresolvable `linkId`, a row is written to `data_quality_flag` rather than raising an exception. Check it after any bulk import:

```sql
-- Query the OLTP directly; flags are not in the OLAP
SELECT rule_name, severity, message, COUNT(*) AS n
FROM data_quality_flag
WHERE is_resolved = 0
GROUP BY rule_name, severity, message
ORDER BY n DESC;
```

In this demo the table is empty — the synthetic responses were well-formed. In production, common sources of flags are:

- FHIR responses from delivery tools that omit optional fields
- Responses referencing a questionnaire version that has since been superseded
- Answer values outside the declared numeric range for a question

---

## Errata log

For documented data quality events — a delivery platform bug, a protocol deviation, a discovered instrument error — use the errata log rather than modifying the underlying responses:

```sql
SELECT errata_id, event_type, severity, title,
       affects_session_from, affects_session_to, analyst_guidance
FROM study_errata_log
WHERE status = 'open'
ORDER BY CASE severity
    WHEN 'critical'      THEN 1
    WHEN 'major'         THEN 2
    WHEN 'minor'         THEN 3
    WHEN 'informational' THEN 4
END;
```

Errata are preserved across merges and pseudonymization — an analyst querying a combined multi-site database sees all site-level errata entries. See the [Multi-Site Study Operations](multi-site.md) tutorial for an example of recording a critical errata entry mid-collection and filtering affected sessions from analysis.

For the full errata API and versioning model, see [Instrument Versioning & Data Governance](../reference/versioning.md).
