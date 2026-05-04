# Federated Analytics

quickq supports a federated analysis pattern for multi-institution studies where individual-level data cannot leave each institution's boundary. Each site runs the same query against its local OLAP database; only aggregate results (counts, means, distributions) leave the site, and rows whose cell counts are below a configurable threshold are suppressed entirely.

This sidesteps the data use agreement and IRB amendment process that direct data sharing requires, which is the primary adoption barrier for multi-institution studies.

> *For the design rationale (why federated, scaling considerations) see [Design Decisions](../design_decisions.md#federated-analytics).*

---

## `quickq federated query`

Runs an aggregate SELECT query against a local OLAP database, suppresses small cells per disclosure-control rules, and writes a JSON document suitable for sharing with a coordinating center.

```bash
quickq federated query analysis.sql analytics.duckdb --output result.json
```

Arguments:

- `QUERY_PATH` (required): a `.sql` file containing a single `SELECT` statement.
- `OLAP_PATH` (required): the local `analytics.duckdb` to query against.
- `--min-cell N` (default: 5): minimum cell size; rows with counts below this threshold are suppressed entirely.
- `--output PATH` / `-o`: write JSON to file instead of stdout.

### Query format

The `.sql` file contains a single `SELECT` statement. Aggregations should produce summary rows (counts, means, distributions); individual-identifier columns are blocked by the executor.

Example query: PHQ-9 severity distribution by site.

```sql
SELECT
    s.name        AS site,
    ars.score_category,
    COUNT(*)      AS n,
    ROUND(AVG(ars.score_raw), 1) AS mean_score
FROM agg_respondent_scores ars
JOIN dim_session sess USING (session_id)
JOIN dim_study   s    USING (study_id)
WHERE ars.scoring_rule_name = 'PHQ-9 Total Score'
GROUP BY s.name, ars.score_category;
```

Because every quickq deployment shares the same `fact_response` / `dim_question` / `dim_respondent` star schema, a query written once runs identically at every site. See [Query Patterns by Question Type](query-patterns.md) for the canonical patterns to build from.

### Output JSON

```json
{
  "query_hash": "sha256:abc123...",
  "min_cell": 5,
  "rows_total": 42,
  "rows_suppressed": 3,
  "columns": ["site", "score_category", "n", "mean_score"],
  "rows": [
    ["Site A", "Minimal",  87, 1.4],
    ["Site A", "Mild",     34, 6.7],
    ["Site B", "Minimal", 102, 1.6]
  ],
  "disclosure_control": {
    "min_cell": 5,
    "rows_suppressed": 3,
    "note": "Rows with cell counts below min_cell were excluded entirely."
  }
}
```

The `disclosure_control` block makes the result self-describing: a coordinating center receiving this JSON knows exactly how it was produced and can document the suppression in its own analysis.

### Disclosure control

The default `--min-cell 5` matches the NCHS standard used in BRFSS and NHANES. Some IRBs require 10 or higher; verify the threshold for your specific study and override with `--min-cell N`.

Suppression removes entire rows rather than blanking individual cells. This prevents information leakage via subtraction (known total minus visible cells equals the suppressed cell value), at the cost of slightly less granular output.

### Audit trail

Every federated query run is recorded in the local study's `tool_audit_log` table (best-effort: skipped if no companion OLTP file exists alongside the OLAP). The audit row records the query hash, `--min-cell` threshold, total rows, suppressed rows, and output destination. This gives each site a complete record of which queries it has run on its data.

---

## Workflow: coordinating center and sites

A typical multi-site federated analysis:

1. **Coordinating center authors the query** as a `.sql` file against the standard OLAP schema. Tests it on a single-site database (or the demo) first.
2. **Distributes the query file** to each site via email, secure file share, or git.
3. **Each site runs the query locally**: `quickq federated query analysis.sql analytics.duckdb --output result.json`. Reviewers check the suppressed-row count before sending.
4. **Each site sends its `result.json`** back to the coordinating center.
5. **Coordinating center assembles** the per-site JSONs into a combined view. No individual-level data has moved.

This pattern is distinct from `quickq merge`, which combines site databases into a single combined OLTP for studies where individual-level pooling is permitted under a DUA. Use `merge` when records can move; use `query` when they cannot.

---

## Companion commands: `quickq fork` and `quickq merge`

These are top-level study-management commands (not under the `federated` namespace) that complete the multi-site lifecycle. `fork` distributes a study's structure to collection sites; `merge` reassembles their populated databases.

### `quickq fork`

Scaffolds a new study database from an existing one's structure (questions, options, scoring rules, skip rules, lineage records). Responses, sessions, respondents, audit history, and compliance records are not copied. The new database carries a provenance entry in its `tool_audit_log` linking back to the source.

```bash
quickq fork prod.db --questionnaire-id 1 --output site_a.db --site-id site_a
```

Useful for distributing a canonical instrument to sites at the start of a multi-site study, scaffolding dev or staging copies of prod, or handing a study to another investigator without exposing respondent data.

### `quickq merge`

Combines multiple site databases into a single combined OLTP study database. Deduplicates by natural key (instrument definitions by canonical URL, sessions by FHIR `QuestionnaireResponse.id`, respondents by `(study_id, external_id)`). Schema divergence is detected at merge time, not at analysis time.

```bash
quickq merge site_a.db site_b.db site_c.db --output combined.db
```

See the [Multi-Site Study Operations tutorial](../tutorials/multi-site.md) for the full lifecycle.

---

## See also

- [Multi-Site Study Operations](../tutorials/multi-site.md): full lifecycle including merge, errata, and pseudonymization.
- [Compliance & Governance](compliance.md): pseudonymization, FAIR metadata, IRB-style withdrawal.
- [Design Decisions: Federated analytics](../design_decisions.md#federated-analytics): the rationale.
