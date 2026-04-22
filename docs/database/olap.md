# OLAP Schema (DuckDB)

The OLAP schema is optimized for analytical performance and follows a star schema design.

## Diagram

```mermaid
erDiagram
    FACT_RESPONSE }o--|| DIM_QUESTION : question_id
    FACT_RESPONSE }o--|| DIM_RESPONDENT : respondent_id
    FACT_RESPONSE }o--|| DIM_SESSION : session_id
    FACT_RESPONSE }o--|| DIM_QUESTIONNAIRE : questionnaire_id
    FACT_RESPONSE }o--|| DIM_DATE : response_date_key
    FACT_RESPONSE }o--|| DIM_CONCEPT : question_concept_id

    DIM_QUESTIONNAIRE }o--|| DIM_STUDY : study_id
    DIM_RESPONDENT }o--|| DIM_STUDY : study_id
    DIM_SESSION }o--|| DIM_QUESTIONNAIRE : questionnaire_id

    FACT_RESPONSE {
        bigint response_id PK
        int session_id FK
        int question_id FK
        double response_numeric
        varchar response_text
        varchar option_value
        int question_concept_id FK
    }

    AGG_QUESTION_DISTRIBUTION {
        int study_id FK
        int question_id FK
        varchar option_value
        int n
        double pct
    }

    AGG_RESPONDENT_SCORES {
        int respondent_id FK
        int session_id FK
        int scoring_rule_id
        double score_raw
        varchar score_category
    }

    AGG_SESSION_COMPLETION {
        int study_id FK
        int questionnaire_id FK
        date date_key FK
        int n_started
        int n_completed
        double median_duration_sec
    }
```

## Features

### Fact-Dimension Design

The `fact_response` table contains the core "atoms" of data (answers). The dimensions (`dim_*`) provide the descriptive context needed for filtering and grouping.

### Denormalization

Unlike the OLTP schema, the OLAP schema is heavily denormalized. Concept names, questionnaire versions, and respondent IDs are pre-joined to ensure that analytical queries are fast and simple to write.

### Aggregates

Aggregate tables (`agg_*`) are materialized during the `refresh` process. These provide instant access to high-level study metrics without needing to scan the entire `fact_response` table.

### OMOP Compatibility

The `omop_survey_conduct` and `omop_observation` tables/views provide a projection of the study data that matches the OHDSI OMOP Common Data Model, facilitating participation in multi-center research networks.
