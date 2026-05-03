# Tutorial: Analyzing Study Data

This tutorial covers the analytical layer of a quickq study: running scoring queries, exploring response distributions, and joining across instruments. The queries below assume the demo database (PHQ-9 + prenatal, 400 sessions). If you have not generated it yet, see [Analytics phase tutorials](../tutorial.md#analytics-phase-tutorials) for the one-line setup.

Once the demo is generated, open the UI:

```bash
quickq analytics demo/analytics.duckdb
```

---

## The analytical database

After `quickq refresh`, the DuckDB OLAP contains:

- **`fact_response`** — one row per answer atom, with typed value columns (`response_numeric`, `response_text`, `option_id`, `response_date`)
- **`dim_*`** tables — respondent, question, session, study, questionnaire, concept, response option, date
- **`agg_*`** tables — pre-computed distributions, scores, and completion stats materialized on refresh
- **`omop_*`** tables — OMOP-aligned projection for federated network queries (see [OMOP Interoperability](../reference/omop.md))

The six demo views (`v_phq9_scores`, `v_phq9_severity_distribution`, `v_phq9_by_admin_mode`, `v_prenatal_visits`, `v_prenatal_summary`, `v_phq9_prenatal_overlap`) are pre-loaded from `quickq/sql/demo_views.sql` and available immediately.

Two design points worth noting before diving into the queries:

- **`v_phq9_scores`** joins `agg_respondent_scores` to respondent and session context. No scoring logic lives in the view — `score_raw` and `score_category` were already computed by `quickq refresh` from the YAML scoring rule.
- **`v_phq9_prenatal_overlap`** is one `JOIN ... USING (respondent_id)` — possible because both instruments share the same `respondent_id`. The multi-instrument linking happened at import time, not in the view.

---

## PHQ-9 severity distribution

The scoring rule — defined once in the YAML, computed automatically on refresh — turns raw item scores into a severity category for every respondent. No scoring logic needed in the query.

```sql
SELECT severity, n, pct, mean_score
FROM v_phq9_severity_distribution;
```

| severity | n | pct | mean_score |
|---|---|---|---|
| Minimal depression | 104 | 41.6% | 1.4 |
| Mild depression | 50 | 20.0% | 7.0 |
| Moderate depression | 49 | 19.6% | 11.8 |
| Moderately severe depression | 26 | 10.4% | 16.8 |
| Severe depression | 21 | 8.4% | 22.3 |

!!! tip "Scoring is automatic"
    The PHQ-9 total and severity category come from `agg_respondent_scores`, populated by `quickq refresh` using the scoring rule defined at authoring time. There is no scoring logic to maintain in your analysis code.

To query scores directly without the view:

```sql
SELECT
    dr.external_id,
    ars.score_raw,
    ars.score_category
FROM agg_respondent_scores ars
JOIN dim_respondent dr USING (respondent_id)
WHERE ars.scoring_rule_name = 'PHQ-9 Total Score'
ORDER BY ars.score_raw DESC;
```

---

## Score by delivery mode

`admin_mode` is a first-class column on every session — not buried in metadata. Mode-effect analysis is one `GROUP BY` away.

```sql
SELECT admin_mode, n, mean_score, min_score, max_score
FROM v_phq9_by_admin_mode;
```

| admin_mode | n | mean_score | min_score | max_score |
|---|---|---|---|---|
| paper | 47 | 7.3 | 0 | 25 |
| phone | 70 | 7.9 | 0 | 25 |
| web | 133 | 8.6 | 0 | 25 |

In a real study you would investigate whether the score difference across modes reflects a genuine mode effect or selection bias (e.g., phone interviews reaching sicker participants).

---

## Repeating group data: prenatal visits

Each row in `fact_response` for a repeating group child carries a `repeat_index` — the 0-based visit number within that respondent's session. This is what makes loop data queryable with standard SQL instead of JSON parsing or custom ETL.

```sql
SELECT respondent_id, visit_number, gestational_week, provider, concern_noted
FROM v_prenatal_visits
WHERE respondent_id = 'respondent-001';
```

| respondent_id | visit_number | gestational_week | provider | concern_noted |
|---|---|---|---|---|
| respondent-001 | 0 | 8.0 | midwife | false |
| respondent-001 | 1 | 20.0 | midwife | false |
| respondent-001 | 2 | 24.0 | ob | false |
| respondent-001 | 3 | 28.0 | ob | false |
| respondent-001 | 4 | 36.0 | midwife | true |

Provider mix across all visits:

```sql
SELECT provider, COUNT(*) AS n,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM v_prenatal_visits
GROUP BY provider
ORDER BY n DESC;
```

!!! tip "Repeating groups are just rows"
    The `repeat_index` column makes each visit a distinct, queryable row. Pivoting, filtering by visit number, or computing per-visit statistics requires no special handling — it is ordinary SQL.

---

## Cross-instrument join: PHQ-9 score vs visit attendance

The 150 respondents who completed both instruments share the same `respondent_id`. Joining across instruments is one SQL join.

```sql
SELECT
    severity,
    ROUND(AVG(total_visits), 1)  AS avg_visits,
    COUNT(*)                     AS n
FROM v_phq9_prenatal_overlap
GROUP BY severity
ORDER BY AVG(phq9_total);
```

This query asks: do participants with higher depression scores attend fewer prenatal visits? In a real cohort you would adjust for gestational age at enrollment and other covariates — but the data is already joined and queryable with no additional ETL.

```sql
-- Participants with concerns at any visit, stratified by PHQ-9 severity
SELECT
    severity,
    COUNT(CASE WHEN visits_with_concern > 0 THEN 1 END)  AS had_concern,
    COUNT(*)                                              AS total,
    ROUND(100.0 * COUNT(CASE WHEN visits_with_concern > 0 THEN 1 END) / COUNT(*), 1) AS pct
FROM v_phq9_prenatal_overlap
GROUP BY severity
ORDER BY AVG(phq9_total);
```

!!! tip "Cross-instrument joins are free"
    Because all respondents share a single `respondent_id` regardless of which instruments they completed, joining across instruments is a standard SQL join. No custom mapping tables, no manual ID reconciliation.

---

## Exploring by question type

Each question type stores responses in a specific column in `fact_response`. Use `dim_question.question_type` to build a catalog of what is in the study:

```sql
SELECT question_type, link_id, question_text, COUNT(fr.response_id) AS response_count
FROM dim_question dq
LEFT JOIN fact_response fr USING (question_id)
GROUP BY question_type, link_id, question_text
ORDER BY question_type, response_count DESC;
```

Note that `repeating_group` shows `response_count = 0` — it is a container whose children carry the responses, not an answer-bearing item itself.

For query patterns by type (`single_choice`, `boolean`, `numeric`, `likert`, `grid`, and others), see the [Query Patterns reference](../reference/query-patterns.md).

---

## What the data model does for you

| Challenge | Typical approach | quickq |
|---|---|---|
| Scale scoring | Custom scoring script per instrument | Scoring rule in YAML; auto-computed on refresh |
| Repeating/loop data | JSON parsing or bespoke ETL | `repeat_index` — each instance is a standard row |
| Mode-effect analysis | Join to a separate metadata table | `admin_mode` on every session |
| Cross-instrument joins | Manual ID reconciliation | Shared `respondent_id` across all instruments |
| FHIR export | Custom mapping per instrument | `quickq fhir export` — lossless, one command |
| Federated analysis | Manual OMOP mapping | `omop_observation` table on refresh (when concepts mapped) |
