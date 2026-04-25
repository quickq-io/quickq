# quickq

**quickq** is a survey authoring and analytics toolkit for health and epidemiology research. It is designed around one idea: that a well-designed data model, expressed as a portable SQLite file, is a better foundation for research software than a bespoke web application.

There is no application server. No cloud account. No proprietary format. A complete study lives in two files that any SQL tool can open.

---

## The Mental Model

quickq separates two concerns that survey platforms typically conflate:

**Authoring and analysis** are quickq's job. You define instruments, collect responses, score scales, and query results — all through a well-specified schema and a Python SDK that wraps it.

**Delivery** is not quickq's job. quickq exports a standard [FHIR Questionnaire](https://hl7.org/fhir/R4/questionnaire.html), hands it to any delivery tool (a web app, a mobile app, a clinical system), and imports back a standard [FHIR QuestionnaireResponse](https://hl7.org/fhir/R4/questionnaireresponse.html). The schema is the interface; FHIR is the handoff protocol.

```
quickq export_fhir()
  → FHIR Questionnaire JSON  →  any delivery tool
                                  ↓ responses
quickq import_fhir_response()
  ← FHIR QuestionnaireResponse JSON
  → quickq refresh → DuckDB OLAP → analysis
```

---

## Two Layers

| Layer | Technology | Purpose |
|---|---|---|
| OLTP | SQLite | Instrument definition, response collection, data quality |
| OLAP | DuckDB | Star schema, scoring, aggregates, OMOP extraction |

The OLTP layer is normalized, FK-heavy, and designed for correctness. The OLAP layer is columnar, denormalized, and designed for analysis. They are connected by `quickq refresh`, which reads the SQLite file directly without an intermediary service.

---

## Quick Start

```bash
uv sync                              # install dependencies

quickq init study.db                 # create a new OLTP database
quickq import-fhir q.json study.db   # import a FHIR Questionnaire
quickq refresh study.db              # load OLTP → DuckDB analytical model
quickq report study.db               # generate a Markdown summary
quickq export-fhir 1 study.db        # export questionnaire id=1 as FHIR JSON
```

Authoring from a YAML definition:

```yaml
name: PHQ-9
version: "1.0"
canonical_url: http://quickq.io/instruments/phq-9

questions:
  - link_id: phq-1
    text: Little interest or pleasure in doing things?
    type: single_choice
    required: true
    options:
      - { text: "Not at all",             value: "0" }
      - { text: "Several days",           value: "1" }
      - { text: "More than half the days",value: "2" }
      - { text: "Nearly every day",       value: "3" }
```

---

## What quickq Is Good At

- Authoring validated instruments (PHQ-9, GAD-7, BRFSS scales) in YAML or Python and loading them into a portable `.db`
- Importing and exporting FHIR Questionnaire and QuestionnaireResponse resources for interoperability with clinical platforms
- Computing subscale scores and population-level distributions on refresh
- Cross-study harmonization via OMOP concept mappings and declared question equivalences
- Capturing data quality issues without interrupting collection

## What quickq Is Not

- A survey web application — use LHC-Forms, REDCap, or a FHIR-compliant platform for delivery
- A patient portal or EMR integration layer — quickq hands off FHIR and accepts it back
- An always-on service — the refresh model is batch/on-demand, not streaming
