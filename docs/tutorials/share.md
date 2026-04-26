# Tutorial: Sharing & Publishing Study Data

This tutorial covers the final phase of a single-site study: pseudonymizing participant identifiers for safe sharing, generating the analytical layer, and exporting to Parquet for warehouse ingestion.

For the multi-site equivalent (merge → pseudonymize → share), see [Multi-Site Study Operations](multi-site.md).

---

## When to share

A `study.db` file at the end of collection contains PHI: `external_id` values (your participant identifiers), potentially free-text responses, and institutional fields like PI name and IRB number. It is not safe to share directly.

`quickq pseudonymize` produces a copy with participant identifiers replaced by stable HMAC tokens. The analytical structure is fully preserved — all queries that work on the original work on the pseudonymized copy.

---

## Step 1 — Pseudonymize

```bash
quickq pseudonymize study.db \
    --output study_anon.db \
    --key-file pseudonymization_key.bin
```

Expected output:

```
Pseudonymized study.db → study_anon.db
  135 respondents pseudonymized
  HMAC key written to pseudonymization_key.bin — keep this file secure.
```

Warnings are printed to stderr — review each one:

```
warning: Free-text responses not redacted: link_id='phq9.comments' (text, 42 non-empty responses). Review for PHI before sharing.
warning: Study 'PHQ-9 Screening — Boston Medical Center' contains institutional fields (principal_investigator='Dr. Jane Smith', irb_number='IRB-2025-BMC-042') that were not redacted. Remove manually if not appropriate to share.
```

### What was changed

| Field | Action |
|-------|--------|
| `respondent.external_id` | Replaced with a 32-char HMAC token |
| `person_map` | Cleared (OMOP identity bridge) |
| `response_session.interviewer_id` | Set to NULL |
| Free-text `response.response_text` | **Left in place** — flagged in warnings |
| `study.principal_investigator`, `irb_number` | **Left in place** — flagged in warnings |

Free-text fields are not auto-redacted because doing so blindly would destroy data. Review those responses manually before sharing.

### The pseudonymization key

`pseudonymization_key.bin` is a 32-byte HMAC key.

- **Keep it** if you need to re-identify participants later (e.g., for a data correction or adverse event follow-up).
- **Destroy it** if the study protocol calls for full anonymization. Once the key is gone, the mapping is irreversible.

The same key applied to the same source database always produces the same tokens, so pseudonymized IDs are stable across multiple exports (e.g., annual data releases from a longitudinal study).

If you do not pass `--key-file`, the key is printed to stderr in hex:

```bash
quickq pseudonymize study.db --output study_anon.db
# stderr: HMAC key (hex): a3f8...
```

---

## Step 2 — Refresh the OLAP

Generate the analytical layer from the pseudonymized database:

```bash
quickq refresh study_anon.db analytics_anon.duckdb
```

```
Refresh complete: 1215 fact rows, 135 sessions, 135 scores computed.
```

The OLAP's `dim_respondent.external_id` will contain pseudonymized tokens, not original identifiers.

---

## Step 3 — Explore before sharing

Open the DuckDB UI to verify the analytical database looks correct before exporting:

```bash
duckdb -ui analytics_anon.duckdb
```

Check participant counts, score distributions, and the errata log:

```sql
-- Verify respondent count
SELECT COUNT(*) FROM dim_respondent;

-- Verify external_ids are tokens, not original IDs
SELECT external_id FROM dim_respondent LIMIT 5;

-- Check for open errata
SELECT severity, title FROM study_errata_log WHERE status = 'open';
```

---

## Step 4 — Export to Parquet

For sharing with analysts using BigQuery, Snowflake, Databricks, or any columnar warehouse, export the OLAP to Parquet files:

```bash
quickq export-parquet analytics_anon.duckdb ./parquet_export/
```

```
Exported 19 table(s) to parquet_export/ (1215 total rows)
  dim_date: 0 rows → dim_date.parquet
  dim_study: 1 rows → dim_study.parquet
  dim_questionnaire: 1 rows → dim_questionnaire.parquet
  dim_question: 9 rows → dim_question.parquet
  dim_respondent: 135 rows → dim_respondent.parquet
  dim_session: 135 rows → dim_session.parquet
  fact_response: 1215 rows → fact_response.parquet
  agg_respondent_scores: 135 rows → agg_respondent_scores.parquet
  ...
```

To export only the tables needed for a specific analysis:

```bash
quickq export-parquet analytics_anon.duckdb ./parquet_export/ \
    --table fact_response \
    --table dim_question \
    --table dim_respondent \
    --table dim_session \
    --table dim_study \
    --table agg_respondent_scores \
    --overwrite
```

The `--overwrite` flag replaces existing files. Without it, the command raises an error if any output file exists.

Upload `parquet_export/` to your cloud storage bucket and point your warehouse at it. The star schema is self-contained — no quickq dependency on the warehouse side.

---

## What you are sharing

The pseudonymized Parquet export contains:

- **All response data** — coded answers, numeric scores, dates, and free-text (review free-text manually)
- **Pre-computed scores** — PHQ-9 totals and severity categories from `agg_respondent_scores`
- **Pseudonymized participant IDs** — stable tokens, not original identifiers
- **OMOP-aligned tables** — `omop_survey_conduct` and `omop_observation` for federated queries
- **No key** — the HMAC key stays with the coordinating center; sharing partners cannot reverse the pseudonymization

The star schema is documented in the [OLAP schema reference](../database/olap.md). Analysts working from the Parquet files do not need quickq installed.
