# Welcome to quickq

**quickq** is a lightweight, interoperable survey authoring and analytics tool designed for health and epidemiology research.

It bridges the gap between flexible survey authoring, standard healthcare data exchange (FHIR), and high-performance analytical modeling (DuckDB).

## Key Features

*   **FHIR First:** Fully compatible with HL7 FHIR Questionnaire (R4/R5).
*   **OLTP & OLAP:** Uses SQLite for transactional data (authoring/administration) and DuckDB for high-speed analytical modeling.
*   **Interoperable:** Supports OMOP CDM mapping and standard vocabularies (LOINC, SNOMED, NCI).
*   **Portable:** The entire study is contained in a single SQLite file.
*   **Simple:** No complex server infrastructure required — just Python, SQLite, and DuckDB.

## Get Started

```bash
# Initialize a new study database
quickq init study.db

# Import a FHIR Questionnaire
quickq import-fhir questionnaire.json study.db

# Refresh the analytical model
quickq refresh study.db
```
