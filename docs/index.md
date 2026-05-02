# quickq

quickq is a survey authoring and analytics toolkit for health and epidemiology research, built on two open file formats with no server required.

*A well-designed data model is the best foundation for a survey study.* It encodes claims about what exists in your research, claims that determine what data quality can be enforced at collection time and what analyses become possible later. quickq makes those claims explicit in a two-layer architecture.

**`study.db` is the portable study artifact.** It is a standard SQLite file — any SQL tool or language with SQLite bindings can read it directly. The framework is built around open standards:

- Instruments are authored in YAML and validated against existing instruments in the database to avoid duplicating established questions; a preview renderer shows the instrument before deployment
- Delivery is via FHIR: quickq exports a `Questionnaire.json`, any compliant tool renders and collects responses, and quickq ingests the `QuestionnaireResponse.json` back
- Questions and response options carry standard vocabulary codes (LOINC, SNOMED, OMOP) for cross-study harmonization
- A Python SDK provides a clean interface to both databases; the SQLite schema is the contract for non-Python implementations

Together these are the building blocks of a complete, portable, questionnaire-driven study — hook up a delivery tool, collect responses, and the rest follows from the data model.

**OLTP layer (`study.db`, SQLite): correctness and provenance**

- Questions are immutable once used in a study; a reword or option change produces a new versioned definition, so every response points to exactly what was asked
- Skip logic is stored as structured rules in the database, not in external documentation, making it auditable and testable
- Foreign key constraints and typed response storage enforce integrity at collection time; data quality issues are flagged without interrupting collection

**OLAP layer (`analytics.duckdb`, DuckDB): standardized analysis**

- Every answered question is one row in `fact_response`; the same query pattern works for every question type and every instrument, with no instrument-specific code
- Skip logic violations, out-of-range values, and unexpected missing data are standard SQL queries against the star schema, not custom scripts per instrument
- Subscale scores (PHQ-9, GAD-7, SF-12) are computed from versioned scoring definitions on refresh and can be recomputed against historical data at any time
- Questions and response options carry OMOP-compatible concept IDs; cross-study harmonization of shared LOINC or SNOMED codes is a join

---

## Quick Start

```bash
uv sync                                          # install dependencies
quickq init study.db                             # create a new database
quickq load instrument.yaml study.db             # load a YAML instrument definition
quickq refresh study.db analytics.duckdb         # build the analytical layer
quickq report analytics.duckdb study.db 1        # generate a Markdown summary
```

Authoring from YAML:

```yaml
name: PHQ-9
version: "1.0"
canonical_url: http://quickq.io/instruments/phq-9

questions:
  - link_id: phq-1
    text: Little interest or pleasure in doing things?
    type: single_choice
    concept: LOINC:44250-9
    required: true
    options:
      - { text: "Not at all",              value: "0", concept: LOINC:LA6568-5 }
      - { text: "Several days",            value: "1", concept: LOINC:LA6569-3 }
      - { text: "More than half the days", value: "2", concept: LOINC:LA6570-1 }
      - { text: "Nearly every day",        value: "3", concept: LOINC:LA6571-9 }
```

---

## What quickq is not

- A survey web application — use LHC-Forms, REDCap, or any FHIR-compliant platform for delivery
- A patient portal or EMR integration layer — quickq hands off FHIR and accepts it back
- An always-on service — the refresh model is batch/on-demand, appropriate for research use

---

## Going deeper

- [Design Decisions](design_decisions.md) — delivery independence, scaling patterns, federated analytics, and data sovereignty
- [The Study Journey](tutorial.md) — end-to-end walkthrough from instrument authoring to published results
- [Survey Authoring](authoring.md) — YAML format, question types, skip logic, scoring rules, concept mapping
- [What We Struggle With](articles/survey-data-challenges.md) — a field perspective on the structural problems this tool addresses
