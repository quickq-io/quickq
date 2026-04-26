# Tutorial: Perinatal Mental Health Study

This tutorial walks through a complete quickq workflow: defining two instruments, exporting them for delivery, importing synthetic responses, refreshing the analytical model, and running analytical queries in the DuckDB UI.

The scenario is a perinatal mental health study. Participants are screened for depression using the **PHQ-9** at enrollment, and they fill out a **Prenatal Visit Log** tracking each clinic visit across their pregnancy. Both instruments are administered via a FHIR-compatible web app; quickq handles authoring and analysis on either side of that handoff.

---

## Generate the demo database

The demo script loads both instruments, generates 250 PHQ-9 responses and 150 prenatal visit logs with realistic distributions, runs `quickq refresh`, and creates six analytical views defined in `sql/demo_views.sql`.

```bash
uv run python scripts/generate_demo.py
```

Expected output:

```
Seeding LOINC concepts...
Loading instruments...
  PHQ-9 Patient Health Questionnaire
  Prenatal Visit Log

Importing 250 PHQ-9 responses...
Importing 150 prenatal visit logs...
Populating person_map for OMOP export...

Running quickq refresh...
Creating analytical views...

── Demo data ready ─────────────────────────────────────────
  OLTP:             demo/study.db
  OLAP:             demo/analytics.duckdb
  Sessions:         400
  Responses:        4362
  Scored:           250 PHQ-9 sessions
  OMOP SurveyConduct rows:  400
  OMOP Observation rows:    2478
  Unmapped questions:       5

── Open the DuckDB UI ──────────────────────────────────────
  duckdb -ui demo/analytics.duckdb
```

---

## Step 1 — Define the instruments

Both instruments are defined in YAML and live in `tests/fixtures/`. Here is an abbreviated view of each.

**PHQ-9** — a scored scale with a shared option set, skip logic, and a scoring rule:

```yaml
name: "PHQ-9 Patient Health Questionnaire"
canonical_url: "http://quickq.io/instruments/phq9"
version: "1.0"

option_sets:
  phq_frequency:
    - { text: "Not at all",               value: "0", concept: "LOINC:LA6568-5" }
    - { text: "Several days",             value: "1", concept: "LOINC:LA6569-3" }
    - { text: "More than half the days",  value: "2", concept: "LOINC:LA6570-1" }
    - { text: "Nearly every day",         value: "3", concept: "LOINC:LA6571-9" }

sections:
  - title: "Over the last 2 weeks..."
    questions:
      - link_id: phq9.1
        text: "Little interest or pleasure in doing things"
        type: single_choice
        concept: "LOINC:44250-9"
        options: $phq_frequency
      # ... items 2–9

scoring:
  - name: "PHQ-9 Total Score"
    formula: sum
    items: [phq9.1, phq9.2, phq9.3, phq9.4, phq9.5, phq9.6, phq9.7, phq9.8, phq9.9]
    categories:
      - { label: "Minimal depression",          min: 0,  max: 4  }
      - { label: "Mild depression",             min: 5,  max: 9  }
      - { label: "Moderate depression",         min: 10, max: 14 }
      - { label: "Moderately severe depression",min: 15, max: 19 }
      - { label: "Severe depression",           min: 20, max: 27 }
```

**Prenatal Visit Log** — a repeating group: one loop instance per clinic visit:

```yaml
name: Prenatal Visit Log
canonical_url: http://quickq.io/instruments/prenatal-visits

questions:
  - link_id: visit_count
    text: How many prenatal visits did you have in total?
    type: numeric

  - link_id: visits
    text: Visit details
    type: repeating_group
    items:
      - link_id: visits.week
        text: Week of pregnancy at visit
        type: numeric

      - link_id: visits.provider
        text: Type of provider seen
        type: single_choice
        options:
          - { text: "OB/GYN",  value: ob }
          - { text: "Midwife", value: midwife }
          - { text: "NP/PA",   value: np }

      - link_id: visits.concern
        text: Were any concerns documented?
        type: boolean
```

Load both into a study database:

```bash
quickq init study.db
quickq load-yaml tests/fixtures/phq9.yaml study.db
quickq load-yaml tests/fixtures/prenatal_visits.yaml study.db
```

---

## Step 2 — Export and deliver via FHIR

quickq exports each instrument as a standard FHIR Questionnaire JSON file. Any FHIR-compliant delivery tool can render it — a web app, a mobile app, a clinical platform:

```bash
quickq export-fhir 1 study.db > phq9_questionnaire.json
quickq export-fhir 2 study.db > prenatal_questionnaire.json
```

The reference delivery tool is **[LHC-Forms](https://lhncbc.nlm.nih.gov/LHC-forms/)** (NLM), an open-source JavaScript widget that renders FHIR Questionnaires in a browser with no server dependency. Participants complete the form; the tool returns a standard FHIR QuestionnaireResponse JSON file that quickq can import directly.

This is the boundary between quickq's responsibilities and the delivery tool's. quickq owns authoring and analysis; the FHIR file is the handoff.

---

## Step 3 — Import responses

When responses come back as FHIR QuestionnaireResponse JSON, `import_fhir_response` writes them to the OLTP:

```python
from quickq.parser_fhir_response import import_fhir_response

# response is a standard FHIR QuestionnaireResponse dict
session_id = import_fhir_response(conn, response, admin_mode="web")
```

For this tutorial, the demo script generates 250 synthetic PHQ-9 responses (right-skewed severity distribution, mix of web/phone/paper delivery) and 150 prenatal visit logs. The first 150 respondents completed both instruments — enabling cross-instrument analysis later.

---

## Step 4 — Refresh the analytical model

```bash
quickq refresh demo/study.db
```

This reads all new responses from the SQLite OLTP into the DuckDB OLAP: loading `fact_response`, computing PHQ-9 scores via the scoring rule, materializing aggregate tables. It runs incrementally — subsequent refreshes only process new rows.

---

## Step 5 — Open the DuckDB UI

```bash
duckdb -ui demo/analytics.duckdb
```

This opens a browser-based SQL interface connected to the analytical database. The six views created by the demo script are available immediately.

---

## Step 6 — Analytics

The demo pre-loads six analytical views from `sql/demo_views.sql`. They form a dependency chain: simpler views (`v_phq9_scores`, `v_prenatal_visits`) feed into aggregate views (`v_phq9_severity_distribution`, `v_prenatal_summary`) which feed into the cross-instrument join (`v_phq9_prenatal_overlap`). All six are queryable immediately after the demo script runs.

Two design points worth noting before diving into the queries:

- **`v_phq9_scores`** joins `agg_respondent_scores` to respondent and session context. No scoring logic lives in the view — `score_raw` and `score_category` were already computed by `quickq refresh` from the YAML scoring rule. `v_phq9_severity_distribution` and `v_phq9_by_admin_mode` both build on it, so a date filter added here propagates to both.
- **`v_phq9_prenatal_overlap`** is one `JOIN ... USING (respondent)` — possible because both instruments share the same `respondent_id`. The multi-instrument linking happened at import time, not in the view.

---

### PHQ-9 severity distribution

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

---

### Score by delivery mode

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

### Prenatal visit detail

Each row in `fact_response` for a repeating group child carries a `repeat_index` — the 0-based visit number within that respondent's session. This is what makes loop data queryable with standard SQL instead of JSON parsing or custom ETL.

```sql
SELECT respondent, visit_number, gestational_week, provider, concern_noted
FROM v_prenatal_visits
WHERE respondent = 'respondent-001';
```

| respondent | visit_number | gestational_week | provider | concern_noted |
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

### Perinatal mental health: PHQ-9 score vs visit attendance

The 150 respondents who completed both instruments share the same `respondent` identifier. Joining across instruments is one SQL join.

```sql
SELECT
    severity,
    ROUND(AVG(total_visits), 1)         AS avg_visits,
    COUNT(*)                            AS n
FROM v_phq9_prenatal_overlap
GROUP BY severity
ORDER BY AVG(phq9_total);
```

This query asks: do participants with higher depression scores attend fewer prenatal visits? In a real cohort you would adjust for gestational age at enrollment and other covariates — but the data is all in one place, already joined, and queryable without any additional ETL.

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

### Exploring by question type

Each question type stores responses in a specific column in `fact_response`. Use `dim_question.question_type` to build a catalog of what is in the study and how much data each item has collected:

```sql
SELECT question_type, link_id, question_text, COUNT(fr.response_id) AS response_count
FROM dim_question dq
LEFT JOIN fact_response fr USING (question_id)
GROUP BY question_type, link_id, question_text
ORDER BY question_type, response_count DESC;
```

Note that `repeating_group` shows `response_count = 0` — it is a container whose children carry the responses, not an answer-bearing item itself.

For query patterns by type (`single_choice`, `boolean`, `numeric`, `likert`, `grid`, and others), see the [Query Patterns reference](reference/query-patterns.md).

---

## Step 7 — Data quality

### Sparsity overview with SUMMARIZE

DuckDB's built-in `SUMMARIZE` gives null counts and percentages for every column in one shot:

```sql
SUMMARIZE fact_response;
```

The null pattern in this demo is instructive:

| Column | Null % | Why |
|---|---|---|
| `option_id` | ~30% | Numeric and boolean answers don't use options |
| `repeat_index` | ~60% | Non-repeating questions have no instance index |
| `response_text` | ~87% | Most answers are numeric or coded, not text |
| `grid_row_id` / `grid_column_id` | 100% | No grid questions in this study |
| `response_date` | 100% | No date questions in this study |
| `question_concept_id` | ~60% | Prenatal visit questions have no LOINC codes; PHQ-9 questions are mapped |

!!! tip "Sparsity is by design"
    The `response` table stores one row per answer atom with typed value columns (`response_numeric`, `response_text`, `option_id`, `response_date`). Any question type uses exactly one or two of these columns and leaves the rest null. High null rates on value columns are expected — they reflect question type, not missing data.

---

### Skip logic vs. missingness

`phq9.difficulty` has a skip rule: it only appears when at least one of items 1–3 is non-zero. In the data, 22 respondents (8.8%) did not answer it. Before treating that as missingness, check whether those respondents simply scored zero:

```sql
SELECT
    CASE WHEN phq9_total = 0 THEN 'score = 0 (correctly skipped)'
         ELSE 'score > 0 (unexpectedly missing)'
    END                          AS explanation,
    COUNT(*)                     AS n
FROM v_phq9_scores
WHERE respondent NOT IN (
    SELECT DISTINCT dr.external_id
    FROM fact_response fr
    JOIN dim_question   dq USING (question_id)
    JOIN dim_respondent dr USING (respondent_id)
    WHERE dq.link_id = 'phq9.difficulty'
)
GROUP BY 1;
```

All 22 non-answers have `phq9_total = 0` — the skip logic worked correctly. A genuine missingness problem would show respondents with `score > 0` who never answered the item.

---

### Concept mapping audit

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

The `omop_unmapped_questions` table gives the same list post-refresh and is the recommended pre-export checklist. High `response_count` on an unmapped question means real data will be silently excluded from any federated query until the mapping is added.

---

### Import flags

For any session where `import_fhir_response` encountered an unrecognised answer format or an unresolvable `linkId`, a row is written to `data_quality_flag` rather than raising an exception. Check it after any bulk import:

```sql
-- Query the OLTP directly; flags are not in the OLAP
SELECT rule_name, severity, message, COUNT(*) AS n
FROM data_quality_flag
WHERE is_resolved = 0
GROUP BY rule_name, severity, message
ORDER BY n DESC;
```

In this demo the table is empty — the synthetic responses were well-formed. In production, common sources of flags are FHIR responses from delivery tools that omit optional fields, or responses referencing a questionnaire version that has since been superseded.

---

## Step 8 — OMOP interoperability

OMOP CDM is the standard data model for federated clinical research networks (PCORnet, TriNetX, All of Us). quickq projects concept-mapped responses into three OMOP-aligned tables on every `quickq refresh`:

| Table | OMOP domain | Populated when |
|---|---|---|
| `omop_survey_conduct` | SurveyConducts | Always — one row per session |
| `omop_observation` | Observations | `question.concept_id` is not null |
| `omop_unmapped_questions` | — | `question.concept_id` is null (pre-flight checklist) |

### Survey conduct

Each response session becomes a row in `omop_survey_conduct`. The `person_id` column is populated from `person_map` — a table where each quickq `respondent_id` maps to the OMOP `person_id` used in the target network.

```sql
SELECT
    survey_conduct_id,
    person_id,
    survey_concept_id,
    survey_start_date,
    survey_source_value   AS admin_mode
FROM omop_survey_conduct
LIMIT 10;
```

In this demo all 400 sessions are exported — 250 PHQ-9 and 150 prenatal visit log sessions. `person_id` is populated for all respondents because the demo seeds `person_map`.

### Observation export

Each concept-mapped answer atom becomes an `omop_observation` row. PHQ-9 questions carry LOINC codes, so all PHQ-9 responses appear here. The prenatal visit questions have no LOINC mappings, so they are excluded (they show up in `omop_unmapped_questions` instead).

```sql
-- PHQ-9 observations for one respondent
SELECT
    ob.person_id,
    dc.concept_code    AS loinc_code,
    dc.concept_name    AS question,
    ob.value_as_number AS score,
    ob.observation_date
FROM omop_observation ob
JOIN dim_concept dc ON ob.observation_concept_id = dc.concept_id
WHERE ob.person_id = 1
ORDER BY ob.observation_date, dc.concept_code;
```

### Cross-study query using LOINC codes

The value of concept mapping is that instruments across studies can be queried without knowing their internal IDs. Any quickq database that maps PHQ-9 questions to the same LOINC codes will respond to this query identically:

```sql
-- PHQ-9 item 1 (anhedonia) across all participants
-- LOINC 44250-9 = "Little interest or pleasure in doing things"
SELECT
    ob.person_id,
    ob.value_as_number   AS item_score,
    ob.observation_date
FROM omop_observation ob
WHERE ob.observation_concept_id = (
    SELECT concept_id FROM dim_concept WHERE concept_code = '44250-9'
)
ORDER BY ob.person_id;
```

This is the core interoperability promise: a query written against OMOP concept IDs runs unchanged across sites, institutions, and time — as long as each site has mapped its questions to the same standard vocabulary.

### Unmapped questions checklist

Before any federated export, run the pre-flight check:

```sql
SELECT link_id, question_text, source_instrument, response_count
FROM omop_unmapped_questions
ORDER BY response_count DESC;
```

In this demo, the 5 prenatal visit questions appear here. Their FHIR codings use local codes (`ob`, `midwife`, `np`) rather than LOINC — a realistic situation for clinic-specific data elements. Adding LOINC or SNOMED codes to the YAML and re-running `quickq refresh` would move them from unmapped to exported.

---

## What the data model does for you

Most survey platforms store responses as JSON blobs, key-value strings, or wide pivot tables. quickq's response model is purpose-built for analytical access:

| Challenge | Typical approach | quickq |
|---|---|---|
| Scale scoring | Custom scoring script per instrument | Scoring rule in YAML; auto-computed on refresh |
| Repeating/loop data | JSON parsing or bespoke ETL | `repeat_index` — each instance is a standard row |
| Mode-effect analysis | Join to a separate metadata table | `admin_mode` on every session |
| Cross-instrument joins | Manual ID reconciliation | Shared `respondent_id` across all instruments |
| FHIR export | Custom mapping per instrument | `quickq export-fhir` — lossless, one command |
| Federated analysis | Manual OMOP mapping | `omop_observation` table on refresh (when concepts mapped) |
