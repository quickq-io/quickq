# Tutorial: Perinatal Mental Health Study

This tutorial walks through a complete quickq workflow: defining two instruments, exporting them for delivery, importing synthetic responses, refreshing the analytical model, and running analytical queries in the DuckDB UI.

The scenario is a perinatal mental health study. Participants are screened for depression using the **PHQ-9** at enrollment, and they fill out a **Prenatal Visit Log** tracking each clinic visit across their pregnancy. Both instruments are administered via a FHIR-compatible web app; quickq handles authoring and analysis on either side of that handoff.

---

## Generate the demo database

The demo script loads both instruments, generates 250 PHQ-9 responses and 150 prenatal visit logs with realistic distributions, runs `quickq refresh`, and creates five analytical views.

```bash
uv run python scripts/generate_demo.py
```

Expected output:

```
Loading instruments...
  PHQ-9 Patient Health Questionnaire
  Prenatal Visit Log

Importing 250 PHQ-9 responses...
Importing 150 prenatal visit logs...

Running quickq refresh...
Creating analytical views...

── Demo data ready ─────────────────────────────────────────
  OLTP:       demo/study.db
  OLAP:       demo/analytics.duckdb
  Sessions:   400
  Responses:  4362
  Scored:     250 PHQ-9 sessions

── Open the DuckDB UI ──────────────────────────────────────
  duckdb demo/analytics.duckdb
  D CALL start_ui();
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
duckdb demo/analytics.duckdb
D CALL start_ui();
```

This opens a browser-based SQL interface connected to the analytical database. The five views created by the demo script are available immediately.

---

## Step 6 — Analytics

All five views are pre-built and queryable. The queries below work in the DuckDB UI or any SQL client.

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
