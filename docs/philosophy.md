# Design Philosophy

quickq is built on a small number of firm design decisions. Understanding them makes the architecture legible and prevents a recurring class of mistakes when extending the tool.

> *This page is the invariants: the principles every other doc assumes. For the architectural choices that follow from these principles, see [Design Decisions](design_decisions.md). For the schema-level mechanics those choices realize, see [Architecture](architecture.md).*

---

## The schema is the contract

The SQLite `.db` file is the primary deliverable of a study — not a dashboard, not an export, not a PDF report. Any language with SQLite bindings can read and write it directly. The schema DDL is the specification; the quickq Python package is the reference implementation.

This principle has practical consequences. It means the schema must be expressive enough to stand alone: every field has a clear semantic, constraints are enforced at the database level (not just application logic), and the data is interpretable without the quickq package installed. A researcher handing a `.db` file to a collaborator is handing them the full study artifact.

---

## FHIR is the cross-language handoff

Survey delivery does not have to happen in Python. A JavaScript web app, a mobile app, a WASM binary, or a clinical platform should be able to render a quickq-authored questionnaire and return responses without depending on the quickq package.

The decoupling mechanism is FHIR. `export_fhir()` produces a standard [FHIR Questionnaire R4](https://hl7.org/fhir/R4/questionnaire.html) resource. Any FHIR-compliant delivery tool can render it. `import_fhir_response()` ingests a standard [QuestionnaireResponse](https://hl7.org/fhir/R4/questionnaireresponse.html). quickq neither knows nor cares how delivery happened.

This is not a FHIR server integration. There is no REST endpoint. FHIR is used as a file format — the lingua franca for handing a questionnaire to a delivery tool and accepting responses back.

---

## Two layers, strict boundary

Transactional operations (authoring, collecting) and analytical operations (scoring, aggregation, cross-study querying) have different correctness requirements and different performance characteristics. Conflating them in a single schema produces either a normalized schema that is painful to query or a denormalized one that is painful to write to safely.

quickq maintains a strict boundary:

- **OLTP (SQLite):** Normalized, FK-heavy, optimized for individual row writes. The source of truth.
- **OLAP (DuckDB):** Star schema, columnar, pre-joined for fast reads. Populated from the OLTP on demand.

Nothing in the OLAP layer is authoritative — it can always be rebuilt from the OLTP. This makes the refresh process safe: a failed load, a schema migration, or a bug in the ETL can be fixed and re-run without touching the source data.

---

## Questions are immutable; instruments are versioned

A `question` row, once created and used in a study, is never modified. If a question is reworded, a new row with a new `link_id` is created, and a `question_lineage` entry records the relationship. This invariant means historical responses always point to the question exactly as it was asked — there is no ambiguity about what "PHQ-9 item 1" meant in a session from two years ago.

Instruments version via `questionnaire.superseded_by`. The `questionnaire_question.status` column lets individual items be deprecated or suspended mid-study without creating a full new questionnaire version, and without invalidating any historical responses.

---

## Concept IDs are the key to interoperability

Questions and options can exist without a `concept_id` — most do, at the start of a study. But concept mapping is what enables cross-study analysis. Two instruments that phrase the same construct differently become comparable the moment both questions share a `concept_id` or are linked via `question_equivalence`.

The OMOP-inspired concept model (LOINC, SNOMED CT, NCI, BRFSS, Local) provides the vocabulary layer. The `question_equivalence` graph provides the researcher-declared layer. Together they produce `dim_question.equivalence_group_id` in the OLAP — a computed cluster ID that lets a single analytical query span multiple instruments without knowing their internal `link_id` values.

Concept mapping is encouraged, not required. The tool makes it easy; it never blocks authoring on it.

---

## Never crash on a valid survey

Data collection is a live operation. A bad answer format, an unrecognized `linkId` in a FHIR response, a missing concept code — none of these should interrupt the import of an otherwise valid session. Issues are written to `data_quality_flag` with a severity level and a structured message. The import continues. The flag is available for review without any data having been lost.

The same principle applies to study errata: the `study_errata_log` table provides a structured, human-authored audit trail of known issues and IRB actions, separate from the automated flags. Analysts can query both when deciding how to treat a batch of responses.
