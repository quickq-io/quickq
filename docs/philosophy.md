# Design Philosophy

The design of **quickq** is guided by several core principles intended to ensure longevity, interoperability, and ease of use in research environments.

## 1. FHIR as the Lingua Franca

The internal representation of a questionnaire in **quickq** is designed to be a superset of the [HL7 FHIR Questionnaire](https://hl7.org/fhir/R4/questionnaire.html) resource. Every questionnaire authored in **quickq** must be exportable to valid FHIR JSON without loss of meaning.

## 2. SQLite as the Contract

The SQLite database file (`.db`) is the primary artifact of a study. It serves as a portable, self-documenting "file-level contract." Any tool in any language with SQLite bindings can read or write to a **quickq** database.

## 3. Separation of Concerns (OLTP vs. OLAP)

We maintain a strict boundary between **transactional** operations (authoring, serving, collecting) and **analytical** operations (scoring, aggregation, cross-study analysis).

* **OLTP (SQLite):** Normalized, FK-heavy, optimized for individual row writes and structural integrity.
* **OLAP (DuckDB):** Star schema, columnar, optimized for bulk queries and large-scale analysis.

## 4. Concept-Centric Analysis

Questions and options are mapped to standard vocabularies (LOINC, SNOMED, etc.) via `concept_id`s. This enables "harmonized" analysis across different studies that may use different question wording but measure the same underlying construct.

## 5. Portability and Simplicity

A complete study deliverable consists of exactly two files:

1. `study.db` (The SQLite OLTP database)
2. `study_analytics.duckdb` (The DuckDB OLAP database)

No complex backend, no Docker containers (unless you want them), and no cloud dependencies are required to run a full analysis pipeline.
