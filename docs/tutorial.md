# The Study Journey

A quickq study moves through five phases: authoring instruments, collecting responses, analyzing data, ensuring data quality, and sharing results. Each phase has a dedicated tutorial; this page is the map.

The scenario used throughout the tutorials is a perinatal mental health study: participants are screened for depression using the **PHQ-9** at enrollment and complete a **Prenatal Visit Log** tracking each clinic visit across their pregnancy.

---

## Generate the demo database

Before working through the phase tutorials, run the demo setup script. It loads both instruments, generates 250 PHQ-9 responses and 150 prenatal visit logs with realistic distributions, runs `quickq refresh`, and creates six analytical views.

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

## Phase 1 — Author instruments

Define questionnaires in YAML, load them into the database, and export for delivery.

quickq supports all common question types used in health and epi research — single choice, multiple choice, Likert scales, grids, boolean, numeric, date, and repeating groups (loops). Instruments are versioned, share reusable option sets, and carry scoring rules for validated scales like PHQ-9 and GAD-7.

**→ [Tutorial: Authoring an Instrument](tutorials/authoring.md)**

---

## Phase 2 — Collect responses

Export the questionnaire as FHIR R4 JSON, hand it to a delivery tool, and import the responses back.

quickq's responsibility ends at `export_fhir()` and resumes at `import_fhir_response()`. Any FHIR-compliant delivery tool works in between — a web app, a mobile app, LHC-Forms, REDCap, or a clinical portal. The FHIR file is the handoff.

**→ [Tutorial: Collecting Responses](tutorials/collect.md)**

---

## Phase 3 — Analyze

Run `quickq refresh` to build the DuckDB OLAP layer, then query scores, distributions, and cross-instrument joins.

The analytical model pre-computes PHQ-9 severity categories from the scoring rule, makes each loop instance a distinct row via `repeat_index`, and puts `admin_mode` on every session. Cross-instrument joins are a single SQL join on the shared `respondent_id`.

**→ [Tutorial: Analyzing Study Data](tutorials/analytics.md)**

---

## Phase 4 — Data quality

Check for unexpected sparsity, distinguish skip-logic non-responses from genuine missingness, audit concept mapping coverage before federated export, and review import flags.

**→ [Tutorial: Data Quality](tutorials/data-quality.md)**

---

## Phase 5 — Share & publish

Pseudonymize participant identifiers, refresh the OLAP, and export to Parquet for warehouse ingestion or data repository deposit.

`quickq pseudonymize` replaces `external_id` values with stable HMAC tokens and warns about free-text fields and institutional metadata that require manual review. The resulting database is analytically complete and safe to share.

**→ [Tutorial: Sharing & Publishing](tutorials/share.md)**

---

## Running a multi-site study

For studies that collect independently at multiple sites and merge at a coordinating center, see the multi-site operations tutorial. It covers the full lifecycle: initializing site databases, recording mid-collection errata, merging, pseudonymizing, and running cross-site analyses.

**→ [Tutorial: Multi-Site Study Operations](tutorials/multi-site.md)**
