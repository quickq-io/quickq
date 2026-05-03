# Tutorial: Multi-Site Study Operations

This tutorial covers the full lifecycle of a multi-site study: initializing independent collection databases at each site, handling a data quality issue discovered mid-collection, merging the sites into a combined database, pseudonymizing for sharing, refreshing the analytical layer, and exporting to a warehouse.

The scenario is a three-site community mental health study screening for depression with the PHQ-9. Collection happens independently at each site; a coordinating center assembles the combined dataset at the end of each collection period.

---

## Scenario

| Site | Name | Participants |
|------|------|-------------|
| A | Boston Medical Center | 60 |
| B | Cambridge Health Alliance | 45 |
| C | Lowell General Hospital | 30 |

Each site runs its own quickq database. The same PHQ-9 YAML definition is loaded at every site. The coordinating center runs the merge, pseudonymization, and analysis.

### Study design note: identifying sites after merge

Each site creates its study with a **unique name**. After merge the `study_id` column becomes a site identifier — every respondent and session carries the `study_id` of the database it came from.

```
study_name = "PHQ-9 Screening — Boston Medical Center"   → study_id 1
study_name = "PHQ-9 Screening — Cambridge Health Alliance" → study_id 2
study_name = "PHQ-9 Screening — Lowell General Hospital"  → study_id 3
```

This is the cleanest approach with the current schema. An alternative is to namespace `external_id` values with a site prefix (e.g. `BMC::P001`) — this gives you a human-readable site label before pseudonymization, at the cost of slightly more complex participant ID management.

---

## Step 1 — Initialize each site database

At each site, a coordinator runs:

```bash
# Site A
quickq init site_a.db

# Site B
quickq init site_b.db

# Site C
quickq init site_c.db
```

Each site then loads the shared PHQ-9 YAML. Using the same `canonical_url` and `version` in the YAML is critical — the merge deduplicates questionnaires by `(canonical_url, version)`, so all three sites must have an identical definition.

```bash
quickq load phq9.yaml site_a.db
quickq load phq9.yaml site_b.db
quickq load phq9.yaml site_c.db
```

Verify the load:

```bash
quickq list library site_a.db
```

---

## Step 2 — Create the study at each site

The study must be created with a unique name before collection begins. Use the Python SDK or a short setup script at each site:

```python
# run_once_site_a.py
from quickq.schema import open_oltp
from quickq.authoring import insert_study

conn = open_oltp("site_a.db")
insert_study(
    conn,
    name="PHQ-9 Screening — Boston Medical Center",
    principal_investigator="Dr. Jane Smith",
    irb_number="IRB-2025-BMC-042",
    start_date="2025-03-01",
)
conn.commit()
```

Run the equivalent script at each site with its own name, PI, and IRB number.

---

## Step 3 — Collect responses

Survey delivery is handled outside quickq — the PHQ-9 YAML is exported as FHIR and handed to a delivery tool (LHC-Forms, a clinical portal, a mobile app). The delivery tool returns `QuestionnaireResponse` JSON files.

```bash
# Export the FHIR Questionnaire for delivery
quickq fhir export site_a.db 1 --output phq9_fhir.json
```

As responses arrive, import them at each site:

```bash
quickq fhir import-response responses_batch_01.json site_a.db --study-id 1
quickq fhir import-response responses_batch_02.json site_a.db --study-id 1
```

Each site operates independently. There is no shared infrastructure — just files. The `--study-id` flag associates each respondent with the site's study row.

### The one-writer rule in practice

SQLite enforces a single concurrent writer. For automated ingestion pipelines, run a single ingestor process per site. The canonical reference implementation (a directory-watcher that processes incoming FHIR response files in arrival order, commits each atomically, and renames processed files so they are not reprocessed) lives in the [Collect Responses tutorial](collect.md#the-one-writer-rule). The same script applies per site here; replace the database path with `site_a.db` (etc.) and run one process per site.

---

## Step 4 — Handle a data quality issue at Site B

Mid-collection, Site B's coordinator discovers that a delivery platform bug caused the PHQ-9 response scale to display in reverse order for sessions 1 through 20. The raw responses cannot be corrected — there is no ground truth for what participants intended to select.

Record the issue in the errata log at Site B before the merge:

```python
from quickq.schema import open_oltp
from quickq.versioning import record_errata

conn = open_oltp("site_b.db")

# Retrieve the session IDs for the affected range
sess_from = conn.execute(
    "SELECT session_id FROM response_session ORDER BY session_id LIMIT 1"
).fetchone()[0]
sess_to = conn.execute(
    "SELECT session_id FROM response_session ORDER BY session_id LIMIT 1 OFFSET 19"
).fetchone()[0]

record_errata(
    conn,
    event_type="delivery_bug",
    title="PHQ-9 response scale reversed in sessions 1–20 at Site B",
    description=(
        "Delivery platform rendered the PHQ-9 frequency scale in reverse order "
        "(3→0 instead of 0→3) for the first 20 sessions. "
        "Root cause: LHC-Forms display_order indexing bug in build v2.1.4."
    ),
    severity="critical",
    affects_session_from=sess_from,
    affects_session_to=sess_to,
    analyst_guidance=(
        "Exclude Site B sessions 1–20 from all PHQ-9 scoring analyses. "
        "Non-scoring items (demographics, dates) in those sessions are unaffected."
    ),
    reported_by="coordinator@cha.org",
)
conn.commit()
```

After the merge, this errata entry will appear in the combined database's `study_errata_log`. Analysts running queries against the OLAP should check for open critical errata before any publication:

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

---

## Step 5 — Pre-merge checklist

Before merging, confirm that all three sites loaded the same questionnaire definition:

```bash
quickq fhir export site_a.db 1 | python3 -c "import sys,json,hashlib; d=json.load(sys.stdin); print(hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest())"
quickq fhir export site_b.db 1 | python3 -c "import sys,json,hashlib; d=json.load(sys.stdin); print(hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest())"
quickq fhir export site_c.db 1 | python3 -c "import sys,json,hashlib; d=json.load(sys.stdin); print(hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest())"
```

All three hashes must match. If they differ, investigate which YAML was loaded at each site before merging — a `MergeError` will result if the same `link_id` has different question text across sites.

---

## Step 6 — Merge at the coordinating center

The coordinating center receives the three `.db` files (via secure transfer — they contain PHI at this stage) and runs:

```bash
quickq federated merge site_a.db site_b.db site_c.db --output combined.db
```

Expected output:

```
Merged 3 source(s) into combined.db:
  135 respondents
  135 sessions
  1215 responses
  0 duplicate sessions skipped
```

### What the merge does

- **Instrument definitions** (questionnaire, questions, options, scoring rules) are deduplicated by natural key. The PHQ-9 appears once in `combined.db` regardless of how many sites loaded it.
- **Respondents** are deduplicated by `(study_id, external_id)`. Since each site has a unique study name, the same participant ID at two sites would create two separate respondents — which is correct for independent participants. If you are tracking the same individuals across sites, use a shared external ID scheme agreed on before collection begins.
- **Sessions** are deduplicated by `fhir_response_id`. If the same FHIR response JSON was imported at two sites, it appears once in the merged database.
- **Errata** from all sites are preserved. The Site B critical errata entry will be visible in `combined.db`.

### Verify the merge

```python
from quickq.schema import open_oltp

conn = open_oltp("combined.db", read_only=True)

# Respondents and sessions by site
for row in conn.execute("""
    SELECT s.name AS site, COUNT(DISTINCT r.respondent_id) AS respondents,
           COUNT(DISTINCT rs.session_id) AS sessions
    FROM respondent r
    JOIN study s USING (study_id)
    JOIN response_session rs USING (respondent_id)
    GROUP BY s.name
    ORDER BY s.name
""").fetchall():
    print(dict(row))

# Open errata
for row in conn.execute("""
    SELECT severity, title, affects_session_from, affects_session_to
    FROM study_errata_log WHERE status = 'open'
""").fetchall():
    print(dict(row))
```

---

## Step 7 — Pseudonymize for sharing

`combined.db` contains PHI (`external_id` values, potentially free-text responses). Before sharing with analysts outside the coordinating center — or depositing in a data repository — pseudonymize it:

```bash
quickq compliance pseudonymize combined.db \
    --output combined_anon.db \
    --key-file pseudonymization_key.bin
```

Expected output:

```
Pseudonymized combined.db → combined_anon.db
  135 respondents pseudonymized
```

Warnings printed to stderr (review each one):

```
warning: Free-text responses not redacted: link_id='phq9.comments' (text, 42 non-empty responses). Review for PHI before sharing.
warning: Study 'PHQ-9 Screening — Boston Medical Center' contains institutional fields (principal_investigator='Dr. Jane Smith', irb_number='IRB-2025-BMC-042') that were not redacted. Remove manually if not appropriate to share.
```

For the per-field changes the pseudonymizer makes, the key-management options, and what the warnings mean, see the canonical [Pseudonymize](share.md#step-1-pseudonymize) section in the single-site share tutorial. The behaviour is identical against `combined.db`; only the database path differs. In multi-site practice, the coordinating center holds the HMAC key and sharing partners do not.

The same key applied to the same source database always produces the same tokens, so pseudonymized IDs are stable across multiple exports (e.g. annual data releases).

---

## Step 8 — Refresh the OLAP

Generate the analytical layer from the pseudonymized database:

```bash
quickq refresh combined_anon.db analytics_anon.duckdb
```

```
Refresh complete: 1215 fact rows, 135 sessions, 135 scores computed.
```

The OLAP's `dim_respondent.external_id` will contain pseudonymized tokens, not original identifiers.

---

## Step 9 — Cross-site analysis

Open the DuckDB UI to explore the combined data:

```bash
quickq analytics analytics_anon.duckdb
```

**PHQ-9 score distribution by site:**

```sql
SELECT
    ds.name                                     AS site,
    sc.score_category,
    COUNT(*)                                    AS n,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*))
          OVER (PARTITION BY ds.name), 1)       AS pct
FROM agg_respondent_scores ars
JOIN dim_session sess  USING (session_id)
JOIN dim_study   ds    USING (study_id)
JOIN (
    SELECT scoring_rule_id, label AS score_category, min_score, max_score
    FROM scoring_category
) sc ON ars.score_raw BETWEEN sc.min_score AND sc.max_score
     AND ars.scoring_rule_id = sc.scoring_rule_id
GROUP BY ds.name, sc.score_category
ORDER BY ds.name, ars.score_raw;
```

**Excluding errata-flagged sessions:**

Site B's critical errata affects sessions 1–20 at that site. Filter them out of scoring analyses:

```sql
SELECT ds.name, ROUND(AVG(ars.score_raw), 1) AS mean_phq9
FROM agg_respondent_scores ars
JOIN dim_session sess USING (session_id)
JOIN dim_study   ds   USING (study_id)
WHERE ars.session_id NOT IN (
    SELECT DISTINCT rs.session_id
    FROM study_errata_log el
    JOIN response_session rs
      ON rs.session_id BETWEEN el.affects_session_from AND el.affects_session_to
    WHERE el.status = 'open'
      AND el.severity IN ('critical', 'major')
)
GROUP BY ds.name
ORDER BY ds.name;
```

**Item-level response distribution across all sites:**

```sql
SELECT
    dq.link_id,
    dq.question_text,
    fr.option_value,
    COUNT(*)                                                        AS n,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*))
          OVER (PARTITION BY dq.question_id), 1)                   AS pct
FROM fact_response fr
JOIN dim_question dq USING (question_id)
WHERE dq.question_type = 'likert'
GROUP BY dq.link_id, dq.question_text, fr.option_value
ORDER BY dq.link_id, fr.option_value;
```

---

## Step 10 — Export to a warehouse

If your institution's analytics infrastructure runs on BigQuery, Snowflake, or Databricks, export the OLAP to Parquet:

```bash
quickq export analytics_anon.duckdb ./parquet_export/
```

```
Exported 19 table(s) to parquet_export/ (1215 total rows)
  dim_date: 0 rows → dim_date.parquet
  dim_study: 3 rows → dim_study.parquet
  dim_questionnaire: 1 rows → dim_questionnaire.parquet
  dim_question: 9 rows → dim_question.parquet
  dim_respondent: 135 rows → dim_respondent.parquet
  dim_session: 135 rows → dim_session.parquet
  fact_response: 1215 rows → fact_response.parquet
  agg_respondent_scores: 135 rows → agg_respondent_scores.parquet
  ...
```

Upload `parquet_export/` to your cloud storage bucket and point your warehouse at it. The star schema is self-contained — no quickq dependency on the warehouse side.

To export only the tables needed for a specific analysis:

```bash
quickq export analytics_anon.duckdb ./parquet_export/ \
    --table fact_response \
    --table dim_question \
    --table dim_respondent \
    --table dim_session \
    --table dim_study \
    --table agg_respondent_scores \
    --overwrite
```

---

## Full workflow summary

```
Each site:
  quickq init site_N.db
  quickq load phq9.yaml site_N.db
  [create study with unique name]
  [collect responses via FHIR import]
  [record errata if data quality issues arise]

Coordinating center:
  quickq federated merge site_a.db site_b.db site_c.db --output combined.db
  quickq compliance pseudonymize combined.db --output combined_anon.db --key-file key.bin
  quickq refresh combined_anon.db analytics_anon.duckdb
  quickq export analytics_anon.duckdb ./parquet_export/   # optional
```

The data model does not change at any point in this pipeline. A query written against the OLAP of a single-site study runs unchanged against the merged, pseudonymized combined study.
