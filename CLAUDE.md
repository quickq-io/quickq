# CLAUDE.md — quickq

## Project Purpose

`quickq` is a health and epidemiology questionnaire tool built on SQLite (OLTP) and DuckDB (OLAP). It supports all common question types used in health and epi research, is fully compatible with HL7 FHIR Questionnaire R4/R5, and provides a standard analytical data model on which cohort queries, prevalence reports, and cross-study analyses can be built.

The tool has two distinct layers:

1. **Transactional layer (SQLite)** — survey definition, administration, and response collection
2. **Analytical layer (DuckDB)** — star schema data model, aggregate tables, and the surface on which all analytical tools and reports operate

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

## Design Philosophy

**FHIR first.** The OLTP schema is the source of truth and must always be exportable to valid FHIR Questionnaire JSON. When in doubt, follow FHIR naming and structure.

**SQLite is the file-level contract.** The `.db` file is the complete, portable study artifact. Any language with SQLite bindings can read and write it directly. The schema DDL is the specification; document it well enough that a non-Python implementor can build a compliant delivery tool against it.

**FHIR is the cross-language handoff.** Use `export_fhir()` to hand a questionnaire to any delivery tool; use `import_fhir_response()` (future) to receive responses back. Do not build an HTTP API — the file format is the interface.

**Analytical layer is the contract.** All reports and analytics are built on the DuckDB star schema, never directly on OLTP. The OLAP schema is stable; the OLTP schema can evolve.

**Concept IDs are optional but encouraged.** Questions and options can exist without a `concept_id` (for speed of authoring), but concept mapping is what enables cross-study analysis. The tool should make mapping easy, not required.

**One file, one study.** A `quickq.db` SQLite file plus a `quickq_analytics.duckdb` file should be the complete deliverable for a study — portable, committable, openable in any SQL tool.

**Never crash on a valid survey.** Admin flows and collection should be robust. Data quality issues go to a `data_quality_flag` table, not exceptions.

---

## Commands

```bash
uv sync                              # install dependencies
uv run pytest                        # run all tests
uv run quickq init study.db          # create new OLTP database
uv run quickq refresh study.db       # load OLTP → DuckDB OLAP
uv run quickq import-fhir q.json     # import FHIR Questionnaire
uv run quickq export-fhir 1          # export questionnaire id=1 as FHIR JSON
uv run quickq report study.db        # generate Markdown summary from OLAP
```

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

1. **End-to-end test harness** — seed a SQLite with a known questionnaire (PHQ-9), inject synthetic responses, run `quickq refresh`, assert OLAP outputs. This validates the core pipeline before any UX investment.
2. **Bundled question library with CLI search** — `quickq search "depression"` returns real questions with their `link_id`, concept codes, and source instrument. Researchers adapt existing validated instruments rather than authoring from scratch.
3. **YAML/TOML authoring format** — a human-readable alternative to the Python SDK for defining questionnaires. Simple enableWhen conditions must be expressible without Python.
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

## File Layout (planned)

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
