# quickq

> Health and epidemiology questionnaire toolkit — author in YAML, deliver via FHIR, analyze via DuckDB. The `.db` file is the portable study artifact.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Status: beta](https://img.shields.io/badge/status-beta-orange.svg)](https://github.com/quickq-io/quickq/issues)

> **Status:** v0.1.x · beta. APIs may change before 1.0. Feedback welcome via [Issues](https://github.com/quickq-io/quickq/issues).

quickq is a survey research toolkit built around two open file formats:

- **`study.db`** (SQLite) — the transactional layer. Versioned questionnaires, structured skip logic, FHIR-compatible response collection. The complete portable study artifact.
- **`analytics.duckdb`** (DuckDB) — the analytical layer. A standardized star schema for cohort queries, prevalence reports, scoring, and cross-study harmonization.

`quickq refresh` connects them. Survey delivery happens through the FHIR R4 contract — quickq exports a `Questionnaire` resource, any compliant tool collects responses, quickq imports the `QuestionnaireResponse` back. The reference delivery server is [`quickq-forms`](https://github.com/quickq-io/quickq-forms).

## Quickstart

```bash
# Install (Python 3.11+, uv recommended). Pulls in quickq-forms for `quickq serve`.
uv tool install --reinstall ./quickq --with ./quickq-forms

# Author a study
mkdir my-study && cd my-study
quickq init study.db --with-library                # bundled question bank: PHQ-9, GAD-7, PRAPARE, …
quickq load instrument.yaml study.db
quickq preview study.db 1                          # render the questionnaire in your browser

# Collect responses (opens a form at localhost:8000)
quickq serve study.db

# Build the analytical layer and inspect it
quickq refresh study.db analytics.duckdb
quickq report  analytics.duckdb study.db 1
quickq analytics                                   # interactive DuckDB UI in your browser
```

For a complete end-to-end walkthrough (authoring a gout symptoms questionnaire from scratch, collecting a response, running analytics), see the [tutorial](docs/tutorials/end-to-end.md). Full documentation site: **<https://quickq-io.github.io/quickq/>**.

## Why

A well-designed data model is the best foundation for a survey study. quickq encodes the structural decisions (immutable question definitions, structured skip logic, OMOP-compatible concept codes) in a portable file format. The same SQL query pattern works for every question type and every instrument; PHQ-9 scoring, distribution charts, and cross-study harmonization are queries against a stable schema, not custom code per instrument.

The standard quickq is designed to clear is portability of the questionnaire layer of a study: a researcher in another country should be able to receive a `.db` file, deploy collection, and refresh analytics without rebuilding the data infrastructure. The non-technical work of running a study (recruitment, regulatory approval, translated consent) remains; the data plumbing does not. Every architectural decision is judged against that bar.

## What's in this repo

- `quickq/` — the Python package (CLI + SDK)
- `quickq/library/` — bundled YAML library of validated instruments (PHQ-9, GAD-7, PRAPARE, BRFSS, etc.)
- `quickq/sql/` — the OLTP and OLAP DDL
- `docs/` — mkdocs source for [quickq-io.github.io/quickq](https://quickq-io.github.io/quickq/) (preview locally with `uv run mkdocs serve`)
- `tests/` — `uv run pytest` for the fast suite, `-m e2e` for end-to-end

## Related repos

- **[quickq-forms](https://github.com/quickq-io/quickq-forms)** — FHIR delivery server (`quickq serve` lives here)
- **[quickq-docs](https://github.com/quickq-io/quickq-docs)** — placeholder repo for documentation hosting. The canonical mkdocs source is in `docs/` here, published at <https://quickq-io.github.io/quickq/>.

## License

Apache License 2.0 — see [LICENSE](LICENSE). Patent grant included; intended for unrestricted institutional and academic use.
