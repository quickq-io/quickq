# Tutorial: Multi-Site Study Operations

This tutorial covers the full lifecycle of a multi-site study: initializing independent collection databases at each site, handling a data quality issue discovered mid-collection, merging the sites into a combined database, refreshing the analytical layer, and exporting to a warehouse.

The scenario is a three-site community mental health study screening for depression with the PHQ-9. Collection happens independently at each site; a coordinating center assembles the combined dataset at the end of each collection period.

---

## Scenario

| Site | Name | Participants |
|------|------|-------------|
| A | Boston Medical Center | 60 |
| B | Cambridge Health Alliance | 45 |
| C | Lowell General Hospital | 30 |

Each site runs its own quickq database. The same PHQ-9 YAML definition is loaded at every site. The coordinating center runs the merge and analysis.

### Study design note: identifying sites after merge

Each site creates its study with a **unique name**. After merge the `study_id` column becomes a site identifier — every respondent and session carries the `study_id` of the database it came from.

```
study_name = "PHQ-9 Screening — Boston Medical Center"   → study_id 1
study_name = "PHQ-9 Screening — Cambridge Health Alliance" → study_id 2
study_name = "PHQ-9 Screening — Lowell General Hospital"  → study_id 3
```

This is the cleanest approach with the current schema. An alternative is to namespace `external_id` values with a site prefix (e.g. `BMC::P001`) — this gives you a human-readable site label embedded in the participant ID, at the cost of slightly more complex participant ID management.

---

## Step 1 — Build the canonical study and fork it to each site

The cleanest setup uses `quickq fork` to distribute the canonical instrument from a single source database to each site. The fork copies the questionnaire, questions, options, scoring rules, and skip rules; it does not copy responses, sessions, or respondents. Each site receives a structurally identical database to collect into, and the fork records explicit provenance back to the canonical source.

Build the canonical database once at the coordinating center:

```bash
quickq init canonical.db
quickq load phq9.yaml canonical.db
```

Then fork the canonical study to each site, blanking the PI / IRB / dates so each site fills in its own:

```bash
quickq fork canonical.db --questionnaire-id 1 --output site_a.db \
    --site-id A --reset-study-metadata --note "Boston Medical Center"

quickq fork canonical.db --questionnaire-id 1 --output site_b.db \
    --site-id B --reset-study-metadata --note "Cambridge Health Alliance"

quickq fork canonical.db --questionnaire-id 1 --output site_c.db \
    --site-id C --reset-study-metadata --note "Lowell General Hospital"
```

Each site now has a `site_X.db` containing the PHQ-9 instrument definition (with shared `canonical_url` and `version`) and an empty respondent / session / response space. The fork operation is recorded in each site DB's `tool_audit_log` so the provenance back to `canonical.db` is queryable later if needed.

Why fork is preferable to independent loads: the merge step in Step 6 deduplicates the instrument by `(canonical_url, version)`, which means every site must have a structurally identical definition. With fork, identity is guaranteed by construction. With independent `quickq load` runs at each site, drift becomes possible (a typo fix at one site, a missed library update at another), and the merge will surface those as conflicts.

!!! note "Alternative: independent setup at each site"

    If a canonical source database is not feasible (each site authors locally from a shared YAML file, the YAML is generated dynamically per site, etc.), each site can run `quickq init site_X.db` followed by `quickq load phq9.yaml site_X.db` independently. This works as long as every site uses the same YAML — the merge deduplicates by `(canonical_url, version)` either way. The risk is structural drift between the YAML copies; the fork-based path eliminates that risk by construction.

---

## Step 2 — Set site-specific study metadata

Each forked database carries the study row from the canonical source, but with PI / IRB / dates blanked by `--reset-study-metadata`. Each site fills in its own values before collection begins:

```python
# run_once_site_a.py
from quickq.schema import open_oltp

conn = open_oltp("site_a.db")
conn.execute(
    """UPDATE study SET
           name = 'PHQ-9 Screening — Boston Medical Center',
           principal_investigator = 'Dr. Jane Smith',
           irb_number = 'IRB-2025-BMC-042',
           start_date = '2025-03-01'"""
)
conn.commit()
```

Run the equivalent script at each site with its own name, PI, and IRB number. The `study_id` becomes the site identifier after merge — every respondent and session carries the `study_id` of the database it came from, so a unique site name per database is sufficient for site-level analysis later.

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
quickq merge site_a.db site_b.db site_c.db --output combined.db
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

## Step 7 — Refresh the OLAP

Generate the analytical layer from the combined database:

```bash
quickq refresh combined.db analytics.duckdb
```

```
Refresh complete: 1215 fact rows, 135 sessions, 135 scores computed.
```

---

## Step 8 — Cross-site analysis

Open the DuckDB UI to explore the combined data:

```bash
quickq analytics analytics.duckdb
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

## Step 9 — Export to a warehouse

If your institution's analytics infrastructure runs on BigQuery, Snowflake, or Databricks, export the OLAP to Parquet:

```bash
quickq export parquet analytics.duckdb -o ./parquet_export/
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
quickq export parquet analytics.duckdb -o ./parquet_export/ \
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
Coordinating center (one-time):
  quickq init canonical.db
  quickq load phq9.yaml canonical.db
  quickq fork canonical.db -q 1 -o site_a.db --site-id A --reset-study-metadata
  quickq fork canonical.db -q 1 -o site_b.db --site-id B --reset-study-metadata
  quickq fork canonical.db -q 1 -o site_c.db --site-id C --reset-study-metadata
  [distribute site_X.db files to each site]

Each site:
  [set site-specific study metadata: name, PI, IRB number]
  [collect responses via FHIR import]
  [record errata if data quality issues arise]

Coordinating center (per collection cycle):
  quickq merge site_a.db site_b.db site_c.db --output combined.db
  quickq refresh combined.db analytics.duckdb
  quickq export parquet analytics.duckdb -o ./parquet_export/   # optional
```

The data model does not change at any point in this pipeline. A query written against the OLAP of a single-site study runs unchanged against the merged combined study.
