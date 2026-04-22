# OLTP Schema (SQLite)

The OLTP schema is designed for structural integrity and transactional safety.

## Diagram

```mermaid
erDiagram
    STUDY ||--o{ QUESTIONNAIRE : contains
    QUESTIONNAIRE ||--o{ SECTION : contains
    QUESTIONNAIRE ||--o{ QUESTIONNAIRE_QUESTION : places
    SECTION ||--o{ QUESTIONNAIRE_QUESTION : contains
    QUESTION ||--o{ QUESTIONNAIRE_QUESTION : used_in
    QUESTION ||--o{ RESPONSE_OPTION : has
    RESPONSE_OPTION_SET ||--o{ RESPONSE_OPTION : groups
    QUESTIONNAIRE_QUESTION ||--o{ SKIP_RULE : "has (subject)"
    QUESTIONNAIRE_QUESTION ||--o{ SKIP_RULE : "has (trigger)"

    RESPONDENT ||--o{ RESPONSE_SESSION : starts
    QUESTIONNAIRE ||--o{ RESPONSE_SESSION : "administered in"
    RESPONSE_SESSION ||--o{ RESPONSE : contains
    QUESTIONNAIRE_QUESTION ||--o{ RESPONSE : answers

    CONCEPT ||--o{ QUESTION : "maps to"
    CONCEPT ||--o{ RESPONSE_OPTION : "maps to"
    VOCABULARY ||--o{ CONCEPT : contains

    STUDY {
        int study_id PK
        text name
    }
    QUESTIONNAIRE {
        int questionnaire_id PK
        text name
        text canonical_url
        text version
    }
    QUESTION {
        int question_id PK
        text link_id
        text question_text
        text question_type
    }
    RESPONSE_SESSION {
        int session_id PK
        int respondent_id FK
        text started_at
        int is_complete
    }
    RESPONSE {
        int response_id PK
        int session_id FK
        int qq_id FK
        text response_text
        real response_numeric
    }
```

## Key Planes

### Instrument Plane

*   `study`: The top-level container.
*   `questionnaire`: A versioned survey instrument.
*   `section`: A grouping of questions within a questionnaire.
*   `question`: The reusable bank of questions.
*   `questionnaire_question`: The join table that places a question into a questionnaire version.

### Concept Plane

*   `vocabulary`: Standards like LOINC, SNOMED.
*   `concept`: Individual codes from a vocabulary.
*   `concept_relationship`: Maps between concepts (e.g., "Maps to").

### Response Plane

*   `respondent`: Participants in the study.
*   `response_session`: A single attempt to complete a questionnaire.
*   `response`: The individual answers provided by the respondent.
*   `data_quality_flag`: Soft validation errors captured during collection.
