# CLAUDE.md — quickq

## Project Purpose

`quickq` is a health and epidemiology questionnaire tool built on SQLite (OLTP) and DuckDB (OLAP). It supports all common question types used in health and epi research, is fully compatible with HL7 FHIR Questionnaire R4/R5, and provides a standard analytical data model on which cohort queries, prevalence reports, and cross-study analyses can be built.

The tool has two distinct layers:

1. **Transactional layer (SQLite)** — survey definition, administration, and response collection
2. **Analytical layer (DuckDB)** — star schema data model, aggregate tables, and the surface on which all analytical tools and reports operate

---

## The Design Standard: Copy/Paste Study Propagation

At an international workshop, someone asked: *"If we wanted to run the Connect for Cancer Prevention Cohort Study in our country, how would we do that?"*

That question sets the bar. Not data sharing. Not harmonization of an existing dataset. Running the same study — same instruments, same skip logic, same scoring rules, same analytical queries, same provenance — at a new site in a different country, from a standing start.

The Connect study was designed to be as FAIR as possible. The question still felt like a high bar. It should not be. If the study is properly encoded — instruments versioned and portable, analytical schema standardized, collection infrastructure deployable — the answer should be: *download this file, run this command, and you are running the same study.*

**This is the portability standard quickq is designed to clear.** Every architectural decision should be evaluated against it:

- Can a researcher in another country receive the complete study definition and load it without manual transcription? → FHIR export + `.db` as the study artifact
- Can they deploy collection infrastructure without bespoke engineering? → `quickq serve` / quickq-forms
- Can they run the same analytical queries against their local data? → the star schema is identical at every site
- Can they contribute to a federated analysis without transferring individual-level data? → `quickq federated query` with aggregate-only enforcement
- Can they pool their data with the original study? → `quickq merge` with concept-code-based harmonization
- Can they cite and register the study locally with full provenance? → `quickq compliance export-metadata` + FAIR metadata fields

The SQLite file is not just a database. It is the encoded study — instruments, skip logic, scoring rules, concept mappings, provenance, and metadata — in a form that any institution can receive, inspect, deploy, and extend without proprietary software, without a vendor relationship, and without losing the thread back to the original study.

**Any feature that makes this harder is a design defect. Any feature that makes this easier is a priority.**

### Study replication as a design principle

The workshop question is, in software terms, a fork request: take an existing study definition as a starting point, adapt it for a new context, run it, and preserve the ability to pool data with the original on questions that remained equivalent.

This maps cleanly to quickq's architecture. In user-facing language it is "study replication" or "study adaptation" — plain terms for what PIs and coordinators actually want to do. The git analogy is useful for developers reasoning about the architecture but should not appear in user-facing documentation.

**What the architecture already supports:**

- `question_lineage` tracks rewords, option changes, splits, and merges — the provenance of every change from original to adaptation
- `question_equivalence` records researcher-declared equivalences across instrument versions — the basis for pooling adapted questions with originals
- Concept codes are the harmonization key — a question unchanged in the adaptation retains its LOINC or SNOMED code; that structural fact enables pooling without a manual crosswalk
- `quickq merge` already handles sites with adapted instruments by harmonizing on concept codes

**What is missing (additive, no breaking changes):**

- `derived_from` field on `questionnaire` — FHIR already has this; quickq needs to expose and persist it
- `adapted_from` as a change type in `question_lineage` alongside reword, split, merge
- `quickq fork study.db --output adaptation.db` — creates a new `.db` with copied instruments, new canonical URLs, `derived_from` set; makes the starting point explicit and lineage unambiguous
- `quickq diff study_a.db study_b.db` — shows what changed between original and adaptation; the coordinating center needs this to understand what a site modified before deciding what can be pooled

**What this does not solve:**

The technical workflow is a small fraction of the total friction in study replication. IRB approval at the new site, translated consent forms, data governance agreements, and cross-cultural construct validity assessment are all outside quickq's scope and are the hard parts. `quickq fork` makes the instrument portable — it does not make the regulatory and scientific dimensions easier. Document this honestly wherever study replication is discussed.

**Implementation note:** Add `quickq fork` and `quickq diff` to the roadmap. Treat `derived_from` on `questionnaire` and `adapted_from` in `question_lineage` as schema additions for the same session.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   OLTP (SQLite)                         │
│   Survey serving · Response collection · FHIR compat   │
│   Normalized · FK-heavy · WAL mode                      │
└────────────────────────┬────────────────────────────────┘
                         │  on-demand ETL  (`quickq refresh`)
                         │  DuckDB reads SQLite directly
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   OLAP (DuckDB)                         │
│   Star schema · Columnar · Analytical standard model    │
│   fact_response · dim_* · agg_* tables                  │
└────────────────────────┬────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         Analytics CLI         Report generator
         Cohort builder         FHIR export
         Data quality           Cross-study harmonizer
```

### Refresh model

On-demand: `quickq refresh` reads new OLTP rows (incremental by `response_id`) and upserts into DuckDB. No streaming, no triggers. Appropriate for batch/research use.

DuckDB attaches the SQLite file directly:
```sql
ATTACH 'quickq.db' AS oltp (TYPE sqlite, READ_ONLY);
```

---

## FHIR Compatibility

All questionnaire definitions must round-trip to/from valid HL7 FHIR Questionnaire JSON (R4) without loss. Key FHIR fields have native columns in the OLTP schema:

| OLTP column | FHIR field |
|---|---|
| `question.link_id` | `item.linkId` |
| `questionnaire.canonical_url` | `Questionnaire.url` |
| `questionnaire.fhir_version` | `Questionnaire.version` |
| `response_option.concept_code` + `.concept_system` | `valueCoding.code` + `system` |
| `response_option_set.canonical_url` | `answerValueSet` |
| `skip_rule` rows | `item.enableWhen[]` |
| `response_session` | `QuestionnaireResponse` header |
| `response` rows | `QuestionnaireResponse.item[].answer[]` |

Two operations to implement explicitly:
- `export_fhir(questionnaire_id) → Questionnaire JSON`
- `import_fhir(json) → questionnaire_id`

Non-FHIR fields (provenance, scoring rules, admin tracking) are stored natively in the OLTP and serialized as FHIR extensions on export.

---

## OLTP Schema (SQLite)

### Pragmas (always set on connection open)
```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
```

### Planes

**Instrument plane** — what you're asking:
```
study
  └── questionnaire (versioned, has canonical_url)
        └── section
              └── questionnaire_question  (placement, display_order, skip logic)
                    └── question          (reusable bank item, has link_id)
                          ├── response_option      (choice questions)
                          ├── response_option_set  (shared option lists)
                          ├── grid_row             (grid questions)
                          └── grid_column
skip_rule                 (structured enableWhen rows, per questionnaire_question)
scoring_rule              (subscale scoring: PHQ-9, GAD-7, etc.)
```

**Concept plane** — standard vocabulary mapping:
```
concept             (OMOP-inspired: concept_id, name, domain, vocabulary, code)
concept_relationship (Maps to, Is a, Subsumes, Answer of)
vocabulary          (metadata per vocabulary: LOINC, SNOMED, NCI, BRFSS, Local)
```

Every `question`, `response_option`, `grid_row`, and `grid_column` has an optional `concept_id`.

**Response plane** — collected answers:
```
respondent
  └── response_session  (questionnaire + timestamps + completion status + admin context)
        └── response    (one row per answer atom: option_id or text or numeric or date)
                        ⚠ repeat_index column needed for repeating_group support (planned)
admin_event             (dispatch, reminder, interviewer assignment, mode)
```

### Question types

| `question_type` | Notes |
|---|---|
| `single_choice` | radio / MCQ |
| `multiple_choice` | SATA |
| `sata_other` | SATA + free-text "Other" |
| `boolean` | Yes/No or True/False |
| `text` | open-ended string |
| `numeric` | integer or float with optional range |
| `date` | date or datetime |
| `likert` | ordered scale |
| `grid` | matrix; uses grid_row + grid_column |
| `ranked` | drag-to-rank |
| `slider` | visual analog scale |
| `repeating_group` | *(planned)* loop — same sub-question set repeats N times; see below |

### Repeating group (loop questions)

A `repeating_group` question is a container whose sub-questions repeat N times — once per pregnancy, medication, job, family member, etc. The count may be free-form (respondent adds instances) or driven by a prior numeric answer.

**FHIR mapping:** `group` item with `repeats: true`. Sub-questions are nested `item` entries. Optionally linked to a count question via the SDC `questionnaire-maxOccurs` extension.

**ODK/XLSForm equivalent:** `begin_repeat` / `end_repeat` with `repeat_count: ${count_question}`.

**Schema readiness:**

| Layer | Status | Detail |
|---|---|---|
| Questionnaire definition | ✓ Ready | `questionnaire_question.parent_qq_id` already exists for sub-question trees |
| Question type field | ✓ Ready | `question.question_type` — add `'repeating_group'` as a valid value |
| FHIR export | ✓ Ready | Renderer already handles `group`; needs child-item enumeration via `parent_qq_id` |
| Count linkage | ⚠ Gap | `questionnaire_question` needs `count_qq_id INTEGER REFERENCES questionnaire_question(qq_id)` to link a repeating group to its driving count question |
| Response collection | ⚠ Gap | `response` table needs `repeat_index INTEGER` — without it responses from different instances (pregnancy 1 vs 2) are indistinguishable |
| OLAP analytics | ⚠ Gap | `fact_response` needs the same `repeat_index` column so per-instance aggregation works |

Both gaps are clean additive migrations (`ALTER TABLE ADD COLUMN`). The definition / export side can be implemented before the collection side — useful for questionnaire authoring and FHIR round-trip testing even without live data collection.

**Implement together with the response collection layer.** Do not add `repeat_index` to the response table in isolation — design it alongside `quickq collect` / `import_fhir_response` so the storage contract is settled once.

### Skip logic

`skip_rule` rows map to FHIR `enableWhen`. Each row has:
- `trigger_qq_id` — the question whose answer is tested
- `operator` — `exists | = | != | > | < | >= | <=`
- `trigger_value` — the value to test against
- `action` — `show | hide | require`
- `enable_behavior` — `all | any` (AND/OR across multiple rules for same qq_id)

For complex multi-condition logic beyond AND/OR, a `display_condition` freetext expression field is available as fallback (FHIRPath-compatible string).

---

## OLAP Schema (DuckDB)

This is the **standard analytical data model**. All analytical tools, reports, and views must be built on top of this layer — never query OLTP directly for analysis.

### Fact table

```sql
fact_response (
    response_id         BIGINT,
    session_id          BIGINT,
    respondent_id       BIGINT,
    questionnaire_id    INTEGER,
    study_id            INTEGER,
    question_id         INTEGER,
    qq_id               INTEGER,
    option_id           INTEGER,
    grid_row_id         INTEGER,
    grid_column_id      INTEGER,
    repeat_index        INTEGER,    -- NULL for non-repeating; 0-based instance index for repeating_group

    response_text       VARCHAR,
    response_numeric    DOUBLE,
    response_date       DATE,
    option_value        VARCHAR,

    question_concept_id INTEGER,
    option_concept_id   INTEGER,

    response_date_key   DATE,
    session_start_key   DATE,

    admin_mode          VARCHAR,    -- web | paper | phone | kiosk
    is_proxy            BOOLEAN,
    interviewer_id      INTEGER
)
```

### Dimension tables

```
dim_respondent      (respondent_id, study_id, external_id, enrollment_date)
dim_question        (question_id, link_id, text, type, source_instrument,
                     source_item_id, concept_id, concept_name, vocabulary_id, concept_code)
dim_response_option (option_id, question_id, text, value, concept_id, concept_name,
                     is_other, is_exclusive)
dim_questionnaire   (questionnaire_id, name, version, canonical_url, study_id)
dim_study           (study_id, name, pi, irb_number, start_date)
dim_concept         (concept_id, name, domain_id, vocabulary_id, concept_code,
                     standard_concept)
dim_session         (session_id, respondent_id, questionnaire_id, started_at,
                     completed_at, is_complete, admin_mode, is_proxy,
                     duration_sec)
dim_date            (date_key, year, month, week, quarter, day_of_week)
```

### Aggregate tables (materialized on refresh)

```
agg_question_distribution   (study_id, questionnaire_id, question_id, option_id,
                              option_value, n, pct)
agg_session_completion      (study_id, questionnaire_id, date_key, n_started,
                              n_completed, median_duration_sec)
agg_respondent_scores       (respondent_id, questionnaire_id, scoring_rule_id,
                              score_raw, score_category, scored_at)
```

---

## Tool Layer

| Tool | Reads | Purpose |
|---|---|---|
| Survey server / CLI | OLTP | Serves questions, writes responses |
| `quickq refresh` | OLTP → OLAP | Incremental load, scoring, aggregates |
| FHIR export | OLTP | `export_fhir(questionnaire_id)` → JSON |
| FHIR import | OLTP | Ingests external Questionnaire resources |
| Concept mapper | OLTP | Assigns `concept_id` to questions/options |
| Analytics CLI | OLAP | Cohort queries, prevalence, cross-tabs |
| Report generator | OLAP | Markdown/HTML summaries |
| Data quality checker | OLAP | Range violations, cross-question rules |
| Cross-study harmonizer | OLAP | Aligns questions across studies via `concept_id` |
| `quickq pseudonymize` | OLTP | *(Planned)* Generates de-identified study file for sharing |

---

## Interoperability Architecture

quickq has two distinct interoperability goals that shape every design decision:

**1. Delivery interoperability** — survey delivery does not have to happen in Python. A JS webapp, a mobile app, a WASM binary, or a third-party platform should be able to deliver a quickq-authored questionnaire and collect responses without depending on the Python package.

The decoupling mechanism is **FHIR as the delivery protocol**:

```
quickq Python SDK
  ↓ export_fhir()
FHIR Questionnaire JSON          ← hand to any survey delivery tool
  ↓ (JS webapp, mobile app, REDCap, LimeSurvey, etc.)
FHIR QuestionnaireResponse JSON  ← hand back to quickq
  ↑ import_fhir_response()
quickq Python SDK → OLAP analytics
```

For same-language direct access (e.g. a Python or JS tool that wants to read/write the database directly), the **SQLite file is the contract**. SQLite has first-class bindings in every major language. The schema DDL is the spec; quickq Python is the reference implementation.

**2. Data/analysis interoperability** — study data must be shareable across tools, registries, and institutions. This is served by FHIR QuestionnaireResponse export, OMOP CDM mapping, and concept IDs on questions and options.

The practical consequence: quickq Python is the **authoring, administration, and analytics SDK**. Delivery is intentionally out of scope — we hand off a FHIR JSON file and accept one back.

---

## Architecture Decision Record: Python SDK and Web Delivery

*Captured for a future session. Do not implement without revisiting this discussion.*

### Why Python for the SDK

The survey research and data engineering communities are Python-native. dbt, DuckDB, pandas, OMOP tooling, Jupyter — all Python. The decision to build the SDK in Python was deliberate: it centers data engineers in the survey ecosystem rather than treating them as an afterthought. A TypeScript SDK would serve web developers, not the researchers and analysts who need to score instruments, run OMOP mappings, and build analytical pipelines.

This motivation is correct and should not be revisited lightly.

### What we would do differently if designing for web delivery from day one

**1. Treat the SQLite schema as a first-class specification, not just "Python is the reference implementation."**

The current CLAUDE.md says "the schema DDL is the spec; quickq Python is the reference implementation" but in practice the Python SDK is the only implementation. If web delivery had been in scope from the start, the schema would be documented formally enough that a TypeScript implementation could be written independently — same tables, same constraints, same FHIR import/export logic — and tested against the same fixture suite.

This would let the quickq-forms server write directly to SQLite via `better-sqlite3` (Node.js) without calling the Python SDK at all, even in local mode. No Python runtime required for web delivery. The maintenance risk is `import_fhir_response` logic existing in two languages; this is managed by the Python SDK owning the canonical implementation and the TypeScript version conforming to the same test fixtures.

**2. Design the FHIR handoff as the primary interface from day one.**

quickq-forms is being designed after the fact. If delivery had been in scope from the start, the FHIR export format would have been validated against a real renderer earlier, `import_fhir_response` would be more hardened, and `quickq serve` would have a natural home rather than being an optional extension.

### The architecture we would land on if starting over

```
Python SDK (quickq)
  — owns the SQLite schema spec and test fixtures
  — data engineering: scoring, OMOP, dbt, analytics, CLI
  — FHIR export/import as the canonical Python implementation

TypeScript/Node.js (quickq-forms server)
  — reads/writes SQLite directly via better-sqlite3
  — no Python runtime dependency, even in local mode
  — React frontend co-located
  — FHIR remains the only contract between the two repos

SQLite schema
  — the shared artifact both sides conform to
  — formally specified, tested against both implementations
```

The philosophical split stays the same — Python for data engineering, JavaScript for web delivery — but the seam moves from the FastAPI adapter to the SQLite schema itself. That is a cleaner boundary and removes the Python runtime requirement from the delivery path entirely.

### Current state and implication

The current quickq-forms design uses a FastAPI + Pydantic server with a local adapter that calls the Python SDK. This is a correct and working path to `quickq serve`. The question for a future session is whether the hosted deployment path should shed the Python runtime dependency — and if so, the schema-as-spec approach sets that up better than the current adapter model.

**When to act on this:** Before building the hosted adapter in quickq-forms. That is the point at which the Python runtime dependency either gets locked in or eliminated.

---

## Architecture Decision Record: OLAP Refresh and dbt

*Captured for a future session. Do not implement without revisiting this discussion.*

### Current state

The OLAP refresh layer (`quickq/olap_schema.py`, `quickq refresh`) is Python code that generates and executes SQL strings against DuckDB. It works incrementally via a `response_id` watermark, computes scoring rules, and materializes aggregate tables. The transformations are correct but opaque — no lineage, no tests on individual steps, no way for a downstream data engineer to audit how a value in `agg_respondent_scores` was derived from a raw response row.

### Why this matters

The survey-data-challenges article makes a specific claim: the wide-table model made dbt impossible because every instrument had a different schema. The row-per-response model was designed to fix exactly that. `fact_response`, `dim_question`, and the aggregate tables are a stable, predictable schema — the precondition for dbt is satisfied.

The current ETL does not take advantage of this. It is Python generating SQL strings, which is the same opacity problem the wide-table model created, just at a different layer.

### The intended end state

Replace the ETL logic in `olap_schema.py` with dbt models. Keep Python as the orchestrator. The transformation from OLTP responses to OLAP outputs becomes:

- Version-controlled SQL models, not Python strings
- End-to-end lineage — any value in any output table is traceable to a source row
- Built-in dbt tests on the data model (not-null, unique, referential integrity, range checks)
- Composable — an institutional dbt pipeline can `ref()` quickq models directly without knowing anything about quickq internals
- Self-documenting via dbt docs

The `dbt-duckdb` adapter supports this directly. DuckDB attaches the SQLite source, dbt reads it, transformations produce the star schema.

### The honest tradeoff

Scoring logic with severity categories, reverse-scored items, and conditional weights is awkward in pure SQL. The likely design is a thin Python pre-processing step that evaluates scoring rules and seeds a staging table (`stg_scores`), then dbt handles the rest of the pipeline from staging to output. Not purely dbt, but the majority of the ETL — all the fact and dimension materialization, all the aggregates — moves to transparent SQL.

### What does not change

- The OLAP schema itself (`fact_response`, `dim_*`, `agg_*`) — dbt is just the mechanism that produces it, not a change to what it contains
- Python as the orchestrator — `quickq refresh` still calls dbt under the hood
- The `quickq refresh` CLI interface — callers see no difference

### When to act on this

When the first external data engineer wants to build on top of quickq in an institutional dbt pipeline. At that point the current Python-string ETL becomes an adoption barrier. Before that point, the current implementation is serviceable and the rewrite is not worth the disruption.

Do not rewrite the OLAP layer for TypeScript compatibility reasons — the analytics path belongs in Python. This rewrite is motivated by transparency and composability, not by the web delivery architecture.

---

## Design Philosophy

**FHIR first.** The OLTP schema is the source of truth and must always be exportable to valid FHIR Questionnaire JSON. When in doubt, follow FHIR naming and structure.

**SQLite is the file-level contract.** The `.db` file is the complete, portable study artifact. Any language with SQLite bindings can read and write it directly. The schema DDL is the specification; document it well enough that a non-Python implementor can build a compliant delivery tool against it.

**FHIR is the cross-language handoff.** Use `export_fhir()` to hand a questionnaire to any delivery tool; use `import_fhir_response()` (future) to receive responses back. Do not build an HTTP API — the file format is the interface.

**Analytical layer is the contract.** All reports and analytics are built on the DuckDB star schema, never directly on OLTP. The OLAP schema is stable; the OLTP schema can evolve.

**Concept IDs are optional but encouraged.** Questions and options can exist without a `concept_id` (for speed of authoring), but concept mapping is what enables cross-study analysis. The tool should make mapping easy, not required.

**One file, one study.** A `quickq.db` SQLite file plus a `quickq_analytics.duckdb` file should be the complete deliverable for a study — portable, committable, openable in any SQL tool.

**Never crash on a valid survey.** Admin flows and collection should be robust. Data quality issues go to a `data_quality_flag` table, not exceptions.

---

## Scaling Architecture

quickq is designed for a spectrum from a solo PhD project on a laptop to a multi-site study with 200,000 participants. The data model does not change at any stage. What changes is the operational pattern around it.

### The four tiers

**Tier 1 — Solo / Local (up to ~5,000 participants)**
One researcher, one `.db` file, everything on a laptop. `quickq refresh` takes seconds. No infrastructure required.

**Tier 2 — Small multi-site (up to ~20,000 participants, 2–5 sites)**
Survey delivery via LHC-Forms or any FHIR-compliant tool. Responses arrive as `QuestionnaireResponse` JSON; a single Python ingestor process imports them. SQLite with WAL mode sustains thousands of sequential writes per second — the bottleneck is never SQLite, it's the delivery pipeline. `quickq refresh` still runs in under a minute.

**Tier 3 — Medium multi-site (up to ~100,000 participants, 5–20 sites)**
Each site collects into its own `site_N.db`. A nightly or weekly `quickq merge` job assembles a combined database for cross-site analysis. This is the most IRB-friendly pattern: each site retains custody of its own data; only the merged file crosses the institutional boundary, after review. Where central collection is preferred, a queue-backed ingestor (SQS or equivalent → single-threaded writer → S3) handles concurrent submissions cleanly.

**Tier 4 — Large / institutional (200,000+ participants)**
Site sharding remains viable at this scale (e.g. 50 sites × 4,000 participants). The new challenge is institutional analytics infrastructure. `quickq export` dumps the OLAP star schema to Parquet files for ingestion into BigQuery, Snowflake, or Databricks — without those tools needing to know anything about quickq. `quickq pseudonymize` is required before any data crosses an institutional boundary.

### The one-writer rule

SQLite enforces a single concurrent writer. This is a feature, not a limitation: it guarantees ACID correctness on every FHIR response import. At Tier 2 and above, enforce it explicitly — either by design (one ingestor process) or by a queue that serializes concurrent submissions.

### The 10-year rule

If a cloud provider shuts down or a university loses a software license, a researcher must be able to download their data and have a fully functional study on a laptop within minutes. This rules out any architecture where the data model is owned by the platform. SQLite + DuckDB + Parquet keeps the study portable regardless of where it runs.

---

## Federated Analytics

For multi-institution studies where individual-level data cannot leave each site's boundary, quickq supports a federated analytics pattern:

1. A coordinating center defines analysis queries against the standard OLAP schema (`fact_response`, `dim_question`, `dim_respondent`)
2. Each site runs `quickq federated query --query analysis.sql` locally — results are aggregate only (counts, means, distributions)
3. Only the aggregate results leave the site; individual rows never move
4. The coordinating center assembles site results

This sidesteps the DUA and IRB amendment process that direct data sharing requires, which is the primary adoption barrier for multi-institution studies. Because every quickq deployment shares the same star schema, a query written once runs identically at every site.

The query executor must enforce aggregate-only output and minimum cell sizes for disclosure control. This is a small amount of code with significant privacy and adoption implications.

---

## Anonymization & Data Sharing

**Pseudonymization** (removing direct identifiers: names, contact info, dates of birth) is straightforward. `quickq pseudonymize` strips the `respondent` table of PHI and replaces `external_id` with a stable random token. The pseudonymized file retains the full OLAP analytical model and can be shared under a standard DUA.

**Re-identification risk** is harder and is out of scope for quickq. Even without direct identifiers, combinations of responses can uniquely identify participants (age + rare condition + region). Researchers sharing pseudonymized data should run an external k-anonymity analysis (ARX, pycanon) before release. quickq should make this easy by documenting the workflow, not by trying to implement it.

**Anonymous studies** (where no PHI was collected by design) need no pseudonymization step — the `.db` file is already safe to share. Many epi studies are designed this way.

---

## FAIR Compliance

FAIR (Findable, Accessible, Interoperable, Reusable) is the relevant framework for evaluating quickq as a research data tool. The current architecture has a strong story on interoperability but gaps on findability and reusability that require dedicated work.

### Current state

**Strong:**
- **Interoperable** — FHIR Questionnaire/QuestionnaireResponse as the exchange protocol; LOINC and SNOMED concept codes on questions and options; OMOP concept model for cross-study harmonization. Both vocabularies are themselves FAIR-compliant with persistent identifiers.
- **Accessible (format)** — SQLite is open, free, and universally readable. The `.db` file is the complete study artifact with no proprietary software required. The 10-year rule addresses long-term accessibility directly.
- **Reusable (instrument layer)** — question types, concept codes, skip logic, scoring rules, and `question_lineage` are rich provenance for the instrument. `study_errata_log` and `admin_event` support collection-level provenance.

**Gaps:**
- **F1** — No persistent identifier (DOI, ARK) for the study or its instruments. `canonical_url` is self-assigned and unregistered.
- **F2 / R1** — `dim_study` is sparse: PI and IRB number exist; population description, methods, geographic scope, and inclusion/exclusion criteria do not. No recognised metadata schema (DataCite, Dublin Core).
- **R1.1** — No license field on study or questionnaire. A downstream researcher has no machine-readable way to know how the data can be reused.
- **R1.2** — No link to a registered study protocol (ClinicalTrials.gov, OSF). Instrument provenance is good; study-level provenance is not.
- **A2** — Metadata and data are coupled in the same `.db` file. If the file is inaccessible, the metadata is too. No separable metadata record.
- **F4** — No mechanism to register a study with a data repository (Zenodo, OSF, Dataverse).

### Planned additions

**1. Expand the `study` table**

```sql
ALTER TABLE study ADD COLUMN description         TEXT;
ALTER TABLE study ADD COLUMN population          TEXT;
ALTER TABLE study ADD COLUMN license             TEXT;   -- SPDX ID or URL, e.g. "CC-BY-4.0"
ALTER TABLE study ADD COLUMN protocol_url        TEXT;   -- ClinicalTrials.gov, OSF, etc.
ALTER TABLE study ADD COLUMN doi                 TEXT;   -- assigned post-registration
ALTER TABLE study ADD COLUMN geographic_scope    TEXT;
ALTER TABLE study ADD COLUMN data_collection_end DATE;
```

Add `license` to `questionnaire` as well — instruments should be licenseable independently of the study data.

**2. `quickq compliance export-metadata`**

Produces a separable metadata record in DataCite JSON or Dublin Core XML. This record can be submitted to Zenodo, OSF, or Dataverse independently of the data file — satisfying A2 (metadata accessible even when data is not).

```bash
quickq compliance export-metadata study.db --format datacite --output study_metadata.json
quickq compliance export-metadata study.db --format dublin-core --output study_metadata.xml
```

**3. `quickq compliance fair-check`**

Audits a study against FAIR sub-principles and reports what is satisfied, what is partial, and what is missing — with specific guidance on which fields to populate.

```bash
quickq compliance fair-check study.db
# F1: canonical_url set ✓  |  doi missing ✗
# F2: description missing ✗  |  population missing ✗
# R1.1: license missing ✗
# R1.2: protocol_url missing ✗
```

**4. FAIR reference documentation**

A docs page explaining how quickq maps to each FAIR sub-principle, which fields satisfy which requirements, and a publication-preparation workflow: fill metadata → `compliance fair-check` → `compliance export-metadata` → register with repository → record DOI back into `study.doi`.

### Implementation note

The schema additions are purely additive (`ALTER TABLE ADD COLUMN`) — no breaking changes to existing databases. `compliance export-metadata` and `compliance fair-check` are new CLI surface only. Scope this as a dedicated session; do not interleave with other roadmap items.

---

## Regulatory and Compliance Landscape

This section describes the compliance obligations that research teams commonly encounter when deploying quickq. It is organized into three tiers: what quickq directly addresses, what quickq partially addresses, and what is outside quickq's scope entirely. Understanding this boundary is essential for writing IRB protocols, data management plans, and data use agreements that accurately describe the tool.

### What quickq directly addresses

**Cell-size suppression (disclosure control)**
`quickq federated query` enforces minimum cell sizes before any aggregate result leaves a site. The default threshold (`--min-cell 5`) aligns with the NCHS standard used in BRFSS and NHANES. Researchers should verify the threshold required by their specific IRB — some require 10 or higher. The suppression mechanism removes entire rows rather than nulling individual cells, which prevents information leakage via subtraction (known total − visible cells = suppressed cell value). The output JSON includes a `disclosure_control` block reporting how many rows were suppressed so the coordinating center knows the result set is incomplete.

**Pseudonymization**
`quickq pseudonymize` removes direct identifiers from the `respondent` table and replaces `external_id` with a stable HMAC token. This is the correct first step toward a limited dataset under HIPAA and toward data sharing under a DUA. It does not constitute Safe Harbor de-identification — see the HIPAA section below.

**Federated analysis without data transfer**
The federated query pattern (each site runs queries locally, only aggregates leave) directly reduces the data sharing surface that IRBs, DUAs, and GDPR data transfer restrictions govern. For multi-institution studies where individual-level sharing is blocked by data governance or regulation, this is often the only viable path to pooled analysis.

**FAIR metadata**
`quickq compliance export-metadata` *(planned)* produces DataCite or Dublin Core records that satisfy NIH Data Management and Sharing Plan requirements for structured metadata. `quickq compliance fair-check` *(planned)* audits a study against FAIR sub-principles and reports gaps. See the FAIR Compliance section for detail.

**Portable, inspectable study artifact**
The SQLite `.db` file is readable by any SQL tool without proprietary software. This satisfies the "accessible format" requirement in NIH data sharing plans and supports long-term preservation. It does not substitute for deposition in a recognized data repository (Zenodo, ICPSR, OSF) — that step is outside quickq's scope.

---

### What quickq partially addresses

**HIPAA**
quickq handles PHI at the storage layer (SQLite) and provides tools to reduce the PHI surface (`pseudonymize`, federated queries). What it does not do:

- **Safe Harbor de-identification** requires removing all 18 identifier types specified in 45 CFR §164.514(b). quickq strips the `respondent` table of direct identifiers but does not audit response data for quasi-identifiers (rare conditions, small geographic units, unusual date combinations). Researchers must assess re-identification risk independently — tools such as ARX or pycanon are appropriate here.
- **Covered Entity obligations** (access controls, audit logs, BAAs with business associates) are operational requirements that apply to the institution running quickq, not to the tool itself. quickq does not implement role-based access control or audit logging.
- **Limited datasets** shared under a DUA still require the DUA to be in place. `pseudonymize` produces a file that is eligible for a limited dataset DUA; it does not establish the agreement.

For studies that collect no PHI by design (anonymous epi surveys), none of this applies — the `.db` file is safe to share without further steps.

**IRB compliance**
quickq provides the technical infrastructure that IRB protocols describe, but the protocol itself is the researcher's responsibility. Key points to document accurately in an IRB application:

- Data are stored in a SQLite file on the institution's infrastructure (or researcher's laptop for Tier 1 studies). quickq does not transmit data to any external service.
- For multi-site studies using `quickq federated query`, individual-level data remain at each site; only aggregate results with cell-size suppression applied are shared with the coordinating center.
- For multi-site studies using `quickq merge`, individual-level data are combined. This typically requires a DUA between all contributing sites and is the data sharing event that IRBs review.
- The 2018 Common Rule (45 CFR 46) requires single IRB review for federally funded multi-site studies in the US. Each site's local IRB must cede review to the designated single IRB. quickq's architecture does not change this requirement, but the federated query pattern can reduce the scope of what is shared.
- FHIR QuestionnaireResponse resources generated by third-party delivery tools (quickq-forms, LHC-Forms) should be described in the data flow section of the IRB protocol.

**GDPR and international equivalents**
The federated query pattern directly supports GDPR data minimization and purpose limitation principles — only aggregate results leave each site. However:

- Cross-border transfer of even pseudonymized individual-level data (e.g., `quickq merge` across EU/US institutional boundaries) requires a legal transfer mechanism: standard contractual clauses, adequacy decision, or binding corporate rules. quickq does not provide or verify these.
- The right to erasure applies to response data in the SQLite file. quickq does not implement a deletion workflow. Researchers operating under GDPR must implement one outside quickq.
- GDPR applies based on the location of data subjects, not the researcher. A US study collecting data from European participants is subject to GDPR.
- National equivalents (UK GDPR, Canada PIPEDA, Australia Privacy Act, Brazil LGPD) have similar structures with local variations. Researchers should verify local requirements.

**NIH Data Management and Sharing Policy (2023)**
All NIH-funded research requires a Data Management and Sharing Plan. quickq supports this in several ways: the SQLite + DuckDB + Parquet stack uses open formats, FHIR provides a standard exchange format, and the planned `export-metadata` command produces machine-readable metadata for repository submission. What quickq does not provide:

- Repository deposition. The researcher must submit the data to an approved repository (NIMH Data Archive, ICPSR, OSF, Zenodo, or similar). quickq can produce the files; it does not handle submission.
- A DOI for the dataset or instrument. The planned `study.doi` field stores a DOI once assigned; quickq does not mint one.

---

### What is outside quickq's scope

**Section 508 / WCAG accessibility**
Section 508 of the Rehabilitation Act requires that electronic information and communications technology used by federal agencies (and federally funded programs) meet accessibility standards equivalent to WCAG 2.1 AA. This applies to the survey delivery layer — quickq-forms and LHC-Forms — not to the Python CLI or OLAP analytics tools. LHC-Forms is developed by NLM and has its own accessibility posture; quickq-forms does not yet have a formal accessibility audit. Research teams deploying quickq-forms in federally funded studies should conduct a WCAG audit before participant-facing deployment.

**FERPA**
The Family Educational Rights and Privacy Act applies when research involves student education records. Studies collecting school performance data (e.g., the adolescent PHQ-9 adaptation) from minors in an educational setting may be subject to FERPA in addition to IRB requirements. quickq has no FERPA-specific features; compliance is the researcher's responsibility.

**FDA 21 CFR Part 11**
FDA-regulated clinical trials require validated electronic records systems with audit trails and electronic signatures. quickq is not designed for FDA-regulated research and does not implement these controls. Researchers conducting FDA-regulated trials should not use quickq as the primary data capture system without independent validation and an audit trail layer.

**Re-identification risk assessment**
Even after pseudonymization, combinations of responses (age + rare condition + geographic region) can uniquely identify participants. quickq surfaces this risk by documenting it but does not implement k-anonymity analysis, l-diversity checking, or differential privacy. Researchers sharing data should run an independent re-identification risk assessment — ARX and pycanon are appropriate tools — before release.

**Data Use Agreements and legal instruments**
quickq can produce the data files that DUAs govern. It does not draft, execute, or track DUAs. The coordinating center is responsible for establishing DUAs with all contributing sites before any data exchange occurs.

**Consent management**
quickq records that a session occurred and whether it was completed. It does not implement a consent workflow, consent versioning, consent withdrawal, or consent audit trail. Studies with complex consent requirements (re-consent for new uses, withdrawal of consent with data deletion) must implement these outside quickq.

---

### A note on the federated pattern and institutional adoption

The primary adoption barrier for multi-institution studies is not technical — it is the legal and governance overhead of data sharing. A DUA between two institutions can take months to negotiate. IRB amendments to cover a new data sharing relationship add further delay. The federated query pattern (`quickq federated query`) is designed to sidestep this entirely: if individual-level data never leave a site, there is nothing to share, no DUA to negotiate, and no IRB amendment required for the data transfer itself. The aggregate results are summary statistics, not human subjects data.

This is the most practically significant compliance feature in quickq. It is worth explaining explicitly in any study protocol or data management plan that describes the multi-site analysis workflow.

---

### Compliance feature implementation status

Tracks what has been built, what is in progress, and what is planned. Update this table as work is completed.

| Feature | Command / location | Status | Regulation addressed |
|---|---|---|---|
| Cell-size suppression | `quickq federated query --min-cell` | ✅ Done | IRB disclosure control, NCHS standard |
| Re-identification warning (low row count) | `federated query` output JSON | ✅ Done | IRB disclosure control |
| Federated analysis without data transfer | `quickq federated query` | ✅ Done | GDPR data minimisation, DUA avoidance |
| Pseudonymisation | `quickq pseudonymize` | ✅ Done | HIPAA limited dataset, GDPR |
| Participant deletion | `quickq compliance delete` | ✅ Done | GDPR right to erasure, consent withdrawal |
| Withdrawal without deletion | `quickq compliance withdraw` | ✅ Done | IRB withdrawal protocol |
| Consent version tracking | `response_session.consent_version` | ✅ Done | IRB consent documentation |
| Tool audit log | `tool_audit_log` table | ✅ Done | HIPAA audit trail, IRB data management |
| Study metadata fields | `quickq compliance set-metadata` | ✅ Done | NIH DMS plan, FAIR F2/R1 |
| FAIR self-audit | `quickq compliance fair-check` | ✅ Done | NIH DMS plan, FAIR compliance |
| Machine-readable metadata export | `quickq compliance export-metadata` | ✅ Done | NIH DMS plan, Zenodo/OSF deposit |
| Questionnaire license field | `questionnaire.license` | ✅ Done | FAIR R1.1 |

**All compliance features complete.** The full workflow is:

```bash
quickq compliance set-metadata study.db --license CC-BY-4.0 --protocol-url <URL> ...
quickq compliance fair-check study.db          # verify no failures before export
quickq compliance export-metadata study.db --format datacite --output metadata.json
# Deposit metadata.json to Zenodo/OSF; record the assigned DOI
quickq compliance set-metadata study.db --doi 10.5281/zenodo.XXXXXXX
```

---

Ordered by adoption impact:

1. **`quickq merge`** — combine multiple site `.db` files into a single study database. Unblocks Tier 3 multi-site adoption. Deduplication key is the FHIR `QuestionnaireResponse.id`; integer PKs are remapped on merge using external IDs as stable keys. Schema divergence (site imported a different questionnaire version) must be detected and surfaced clearly.

2. **Federated query executor** — `quickq federated query` with aggregate-only enforcement and minimum cell size disclosure control. Makes quickq viable for institutional use without requiring data to leave any site.

3. **`quickq pseudonymize`** — produces a PHI-free copy of the study database for direct data sharing. Strips the `respondent` table, replaces external IDs with stable tokens, preserves the full OLAP model.

4. **`quickq export`** — dumps the OLAP star schema to Parquet. Enables ingestion into BigQuery, Snowflake, Databricks, or any columnar warehouse without those tools depending on quickq.

These four commands, together with documented operational recipes for each tier, are what make quickq adoptable as a standard rather than a personal tool.

---

## Configuration

quickq reads an optional `quickq.yml` file from the current working directory (typically alongside the study `.db` file). All settings can also be passed as CLI flags. **Priority: CLI flag > `quickq.yml` > built-in default.**

```yaml
# quickq.yml — study-level configuration, commit alongside study.db

authoring:
  strict_concepts: true      # warn when a concept_code already exists under a different link_id
                             # default: true; set false for assign-first teams
  auto_concept: false        # auto-assign Local OMOP-range codes (2000000001+) to unmapped items
                             # default: false; set true for assign-first teams

render:
  format: md                 # default output format: md | pdf
                             # pdf requires: pip install quickq[pdf]

data_dict:
  format: markdown           # default output format: markdown | csv
```

Any key left out inherits the built-in default. An absent `quickq.yml` is identical to an empty one.

### CLI override

Every config key has a corresponding CLI flag. The flag always wins regardless of what the config says:

```bash
# Config says strict_concepts: false, but this invocation checks anyway
quickq load instrument.yaml study.db --strict-concepts

# Config says strict_concepts: true, but suppress for this run
quickq load instrument.yaml study.db --no-strict-concepts

# Auto-assign Local OMOP codes to all unmapped items in this run
quickq load instrument.yaml study.db --auto-concept

# Config says format: md, but emit PDF for this run
quickq render study.db 1 --format pdf --output instrument.pdf
```

### Implementation notes *(Planned)*

- Config loading lives in `quickq/config.py`: `load_config(search_path) -> QuickqConfig`
- Searches upward from `search_path` (defaulting to `cwd`) until `quickq.yml` is found or root is reached, matching how tools like `pyproject.toml` are discovered
- `QuickqConfig` is a dataclass with typed fields and explicit defaults
- Each CLI command merges config values with click options before executing; click's `default=None` sentinel distinguishes "user passed this flag" from "user omitted it"

---

## Commands

```bash
uv sync                                               # install dependencies
uv run pytest                                         # run all tests
uv run quickq init study.db                           # create new OLTP database
uv run quickq load instrument.yaml study.db           # load YAML instrument definition
uv run quickq preview study.db 1                      # preview questionnaire in browser
uv run quickq refresh study.db analytics.duckdb       # load OLTP → DuckDB OLAP
uv run quickq fhir import q.json study.db             # import FHIR Questionnaire
uv run quickq fhir export study.db 1                  # export questionnaire id=1 as FHIR JSON
uv run quickq fhir import-response response.json study.db  # import QuestionnaireResponse
uv run quickq data-dict study.db 1                    # print data dictionary (Markdown)
uv run quickq data-dict study.db 1 --format csv --output dict.csv  # CSV with concept codes
uv run quickq render study.db 1                       # print rendered instrument (Markdown)
uv run quickq render study.db 1 --format pdf --output instrument.pdf  # PDF (requires quickq[pdf])
uv run quickq report analytics.duckdb study.db 1      # generate Markdown summary from OLAP
uv run quickq list studies study.db                   # list studies in database
uv run quickq list surveys study.db                   # list questionnaires in database
uv run quickq list library study.db                   # list bundled library instruments
uv run python scripts/generate_fixtures.py            # regenerate FHIR test fixtures
```

### Test fixture notes

Fixture JSON files in `tests/fixtures/` are checked into the repo and generated by `scripts/generate_fixtures.py`. Each run uses a fresh temp SQLite database, so the `date` field in questionnaire fixtures will drift on every regeneration. This is cosmetic noise — the question content and codings are stable. If the churn becomes annoying, strip `date` from the generator output or pin it to a constant before committing.

---

## Survey Delivery & Reference Integration

### Design position

Survey delivery is out of scope for quickq. The FHIR handoff is the interface: quickq exports a `Questionnaire` JSON, a delivery tool renders and collects it, and quickq ingests the resulting `QuestionnaireResponse` JSON. quickq owns authoring, administration, and analytics — not the respondent-facing layer.

### Reference delivery target: LHC-Forms

**[LHC-Forms](https://lhncbc.nlm.nih.gov/LHC-forms/)** (National Library of Medicine) is designated as the reference delivery tool. Rationale:

- Purpose-built for FHIR Questionnaire R4 rendering — closest thing to a reference implementation
- Open source, actively maintained by NLM
- JavaScript widget with no server dependency — embeddable anywhere
- Widely used in clinical and epi research settings
- Enables headless Playwright testing without standing up server infrastructure

This does not mean quickq requires LHC-Forms. Any FHIR-compliant delivery tool works. LHC-Forms is the tool we test against, document against, and recommend to users as the default path.

### End-to-end test strategy

The FHIR handoff must be validated against a real delivery tool, not just by schema checks. The E2E test pipeline:

```
quickq export_fhir()
  → FHIR Questionnaire JSON
  → LHC-Forms renders (Playwright headless)
  → synthetic responses submitted via Playwright
  → FHIR QuestionnaireResponse JSON captured
  → quickq import_fhir_response()
  → quickq refresh
  → assert OLAP outputs match expected values
```

This validates both the export format and the response import in a single pipeline. It is the only test that can confirm the FHIR contract is correct end-to-end.

The LHC-Forms E2E test suite lives in `tests/test_e2e_lhcforms.py` and requires Playwright. It is kept separate from the unit test suite and gated in CI so it does not block fast local iteration.

---

## Authoring UX & SLM-Assisted Drafting

### Target user

quickq targets a spectrum from developer-researchers (comfortable with Python/YAML) to non-technical study coordinators (who need plain-English interaction). The authoring UX must be accessible to both without becoming a full survey platform.

### Planned authoring surface (in priority order)

1. **End-to-end test harness** — seed a SQLite with a known questionnaire (PHQ-9), inject synthetic responses, run `quickq refresh`, assert OLAP outputs. This validates the core pipeline before any UX investment. ✓ *Done.*
2. **Bundled question library with CLI search** — `quickq search "depression"` returns real questions with their `link_id`, concept codes, and source instrument. Researchers adapt existing validated instruments rather than authoring from scratch. See *Concept hygiene* section below for the full lookup strategy (local library → LOINC vocabulary table → null/unmapped).
3. **YAML/TOML authoring format** — a human-readable alternative to the Python SDK for defining questionnaires. Simple enableWhen conditions must be expressible without Python. ✓ *Done.*
4. **`quickq draft` — SLM-assisted authoring** — researcher describes intent in plain English; an SLM generates a draft YAML; researcher reviews and edits; `quickq import` validates and loads.
5. **`quickq preview`** — local read-only browser renderer of the FHIR export. Spin up a local server, render the questionnaire as it will appear, no response collection. Critical for catching skip logic gaps and layout issues before deployment.

### SLM-assisted drafting: design constraints

The SLM handles **language and intent only** — question wording, instrument structure, adapting validated scales. It never generates identifiers. All codes and IDs come from hard function/tool calls against the actual database.

**Hard rules:**
- `concept_id`, `link_id`, `concept_code`, and `concept_system` are **never generated by the SLM**. They are always returned by a tool call or left unmapped.
- The SLM calls typed retrieval tools: `search_question_library(query)`, `lookup_concept(name, vocabulary)`, `get_instrument(name)`. These return real rows or empty results — never fabricated values.
- If a concept lookup returns no match, the field is explicitly marked `concept_id: null` in the draft YAML. Unmapped is acceptable; hallucinated is not.
- After generation, `quickq import` runs a validation pass that rejects any concept code not present in the local concept table. This is the hard backstop against hallucination slipping through.
- The preview step (`quickq preview`) is surfaced prominently after every draft — the researcher must visually confirm the instrument before it can be used in a study.

**Interaction model:**
```
quickq draft
> I want to adapt the PHQ-9 for adolescents. Replace "little interest" with
  age-appropriate language and add a question about school performance.

Drafting questionnaire...
  ✓ Retrieved PHQ-9 base instrument (9 questions, concept codes verified)
  ✓ Adapted items 1, 3 — wording updated
  ✓ Added Q10 "school performance" — no concept match found, marked unmapped
Draft saved to draft.yaml. Review and run: quickq preview draft.yaml
```

**Grounding strategy:** the SLM is given the YAML schema, a few canonical examples, and retrieves question-bank context via tool calls (RAG over the local concept and question library). It does not rely on training-data knowledge of LOINC or SNOMED codes.

---

## Concept Hygiene

There are two distinct authoring workflows, each with a different deduplication problem:

**Assign-first** — the team authors questions and assigns internal concept IDs specific to the study. External vocabulary mapping (LOINC, SNOMED) happens later as a separate step, or not at all. The deduplication risk is two researchers independently creating semantically equivalent questions with different `link_id`s and different internal concept IDs — nothing structural flags this. The guardrail is `quickq search` before authoring and team naming conventions, not a validator.

**Shop-first** — the researcher searches for an existing validated question before authoring a new one. External concept codes (LOINC) are assigned at authoring time. The deduplication risk is the same validated question being pulled from two instruments under different `link_id`s. The guardrail is a concept code collision check at load time.

Both workflows are valid. quickq defaults to shop-first behavior (`strict_concepts: true`) — the check is a no-op for assign-first teams who haven't assigned external vocabulary codes, so the default causes no noise for them. Assign-first teams who want to silence it explicitly can set `strict_concepts: false` in `quickq.yml`.

### Guardrails (shop-first, on by default)

At load time, if a new question's `concept_code` matches an existing question in the same database under a different `link_id`, emit a warning (not an error — intentional versioning is allowed):

```
Warning: LOINC:44250-9 already mapped to phq9.1. Consider using { library: phq9.1 } instead of authoring a new question.
```

Controlled via `quickq.yml` or CLI flag — see *Configuration* section below.

Implementation: one SQL check per question in the loader before insert, gated on a boolean. Zero external dependencies.

### Finding existing validated questions (shop-first lookup chain)

1. **`quickq search <terms>`** — full-text search over questions already in the local database (link_id, text, concept name). No network, no setup. Covers all pre-loaded library instruments. *(Planned.)*
2. **Local LOINC vocabulary table** — download the LOINC terms CSV once (`quickq init --with-loinc`) and load it into the local concept table. Enables `quickq search` to return LOINC codes for concepts not yet in the question bank. ~50MB, no network dependency at query time. Feeds the SLM `lookup_concept()` tool call. *(Planned.)*
3. **Null/unmapped** — if no match exists, leave `concept_id: null`. The question works correctly; it surfaces in `omop_unmapped_questions` after refresh as a prompt to map it later.

The LOINC API as a live query is intentionally excluded — the local file covers the same ground without adding a network dependency to the authoring path.

### Assign-first teams: what helps instead

- **`quickq search <terms>`** before creating a new question — surfaces similar questions already in the local bank by text. A workflow convention, not a validator.
- **`link_id` naming conventions** — e.g., `study.<domain>.<construct>` enforced at PR review. The question bank already prevents the same `link_id` from being created twice with different definitions.
- **Mapping as a deliberate phase** — run `quickq data-dict --format csv` after data collection to get the full question list, then assign LOINC codes in bulk. The CSV Concept column makes unmapped questions visible.

### Authoring truly novel questions

When a concept genuinely has no standard vocabulary equivalent, leave it unmapped and note it in the methods section. Consider submitting the concept to LOINC if it has broad reuse potential.

```
quickq/
  quickq/
    models.py          # OLTP dataclasses
    schema.py          # SQLite DDL and connection helpers
    olap_schema.py     # DuckDB DDL and refresh logic
    parser_fhir.py     # FHIR import
    renderer_fhir.py   # FHIR export
    renderer_md.py     # Markdown report generation
    parser_yaml.py     # YAML/TOML authoring format import
    draft.py           # SLM-assisted drafting: orchestration + tool definitions
    library.py         # Bundled question library: search, lookup, retrieval tools
    preview.py         # Local browser renderer (read-only FHIR questionnaire)
    cli.py             # click CLI
  tests/
    test_schema.py
    test_fhir_roundtrip.py
    test_refresh.py
    test_draft.py         # SLM tool call stubs + YAML validation
    test_e2e_lhcforms.py  # Playwright E2E: export → LHC-Forms → import → OLAP assert
  pyproject.toml
  CLAUDE.md
```

---

## Standards References

- [HL7 FHIR Questionnaire R4](https://hl7.org/fhir/R4/questionnaire.html)
- [HL7 FHIR SDC Implementation Guide](https://hl7.org/fhir/uv/sdc/)
- [OMOP CDM concept model](https://ohdsi.github.io/CommonDataModel/)
- [LOINC](https://loinc.org) — preferred vocabulary for clinical questions
- [SNOMED CT](https://www.snomed.org) — preferred vocabulary for clinical answers
- [NCI Thesaurus](https://ncithesaurus.nci.nih.gov) — preferred for cancer/epi constructs
- [BRFSS](https://www.cdc.gov/brfss/) — source for many epi question banks
