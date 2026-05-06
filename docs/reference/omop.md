# OMOP Interoperability

OMOP CDM is the standard data model for federated clinical research networks (PCORnet, TriNetX, All of Us). quickq projects concept-mapped responses into three OMOP-aligned tables on every `quickq refresh`.

---

## Overview

| Table | OMOP domain | Populated when |
|---|---|---|
| `omop_survey_conduct` | SurveyConducts | Always — one row per session |
| `omop_observation` | Observations | `question.concept_id` is not null |
| `omop_unmapped_questions` | — | `question.concept_id` is null (pre-flight checklist) |

These tables live in the OLAP (DuckDB) and are refreshed incrementally via `quickq refresh`. They are not OLTP tables.

---

## Concept mapping requirement

Only questions with a `concept_id` appear in `omop_observation`. Concept IDs come from the YAML authoring format, FHIR codings in imported questionnaires, or manual mapping via the Python SDK.

**PHQ-9 example** — questions carry LOINC codes, so all responses are exported:

```yaml
- link_id: phq9.1
  text: "Little interest or pleasure in doing things"
  type: single_choice
  concept: "LOINC:44250-9"
  options: $phq_frequency
```

**Prenatal visit log example** — questions use local codes and are not exported to OMOP:

```yaml
- link_id: visits.provider
  text: Type of provider seen
  type: single_choice
  options:
    - { text: "OB/GYN",  value: ob }
    - { text: "Midwife", value: midwife }
```

To fix this, add LOINC or SNOMED codings to the YAML and re-run `quickq refresh`.

---

## Survey conduct

Each response session becomes a row in `omop_survey_conduct`. The `person_id` column is populated from `person_map` — a table where each quickq `respondent_id` maps to the OMOP `person_id` used in the target network.

```sql
SELECT
    survey_conduct_id,
    person_id,
    survey_concept_id,
    survey_start_date,
    survey_source_value   AS admin_mode
FROM omop_survey_conduct
LIMIT 10;
```

`person_map` must be populated before export — typically by the coordinating center after receiving PHI-bearing participant rosters. In the demo, `generate_demo.py` seeds `person_map` so all 400 sessions are exported.

---

## Observation export

Each concept-mapped answer atom becomes an `omop_observation` row. PHQ-9 questions carry LOINC codes, so all PHQ-9 responses appear here.

```sql
SELECT
    ob.person_id,
    dc.concept_code    AS loinc_code,
    dc.concept_name    AS question,
    ob.value_as_number AS score,
    ob.observation_date
FROM omop_observation ob
JOIN dim_concept dc ON ob.observation_concept_id = dc.concept_id
WHERE ob.person_id = 1
ORDER BY ob.observation_date, dc.concept_code;
```

---

## Cross-study query using LOINC codes

The value of concept mapping is that instruments across studies can be queried without knowing their internal IDs. Any quickq database that maps PHQ-9 questions to the same LOINC codes will respond to this query identically:

```sql
-- PHQ-9 item 1 (anhedonia) across all participants
-- LOINC 44250-9 = "Little interest or pleasure in doing things"
SELECT
    ob.person_id,
    ob.value_as_number   AS item_score,
    ob.observation_date
FROM omop_observation ob
WHERE ob.observation_concept_id = (
    SELECT concept_id FROM dim_concept WHERE concept_code = '44250-9'
)
ORDER BY ob.person_id;
```

This is the core interoperability promise: a query written against OMOP concept IDs runs unchanged across sites, institutions, and time — as long as each site has mapped its questions to the same standard vocabulary.

---

## Unmapped questions checklist

Before any federated export, run the pre-flight check:

```sql
SELECT link_id, question_text, source_instrument, response_count
FROM omop_unmapped_questions
ORDER BY response_count DESC;
```

High `response_count` on an unmapped question means real data will be silently excluded from any federated query until the mapping is added. Remediation:

1. Look up the appropriate LOINC or SNOMED code for the question
2. Add the `concept` field to the YAML definition
3. Run `quickq refresh` — the question will appear in `omop_observation` on the next refresh

---

## Exporting to an OMOP CDM target

quickq's OMOP tables are a projection layer, not a full CDM. They provide the `SurveyConducts` and `Observations` domains — sufficient for federated survey queries. For full CDM export (Demographics, Visits, Drug Exposures, etc.), integrate quickq's OMOP tables with your institution's CDM pipeline.

The Parquet export (`quickq export parquet`) includes the OMOP tables:

```bash
quickq export parquet analytics.duckdb -o ./parquet_export/ \
    --table omop_survey_conduct \
    --table omop_observation \
    --table omop_unmapped_questions
```

Upload the Parquet files to your CDM staging area and load from there.

---

## Standards references

- [OMOP CDM concept model](https://ohdsi.github.io/CommonDataModel/)
- [OMOP SurveyConducts domain](https://ohdsi.github.io/CommonDataModel/cdm54.html#survey_conduct)
- [LOINC](https://loinc.org) — preferred vocabulary for clinical questions
- [SNOMED CT](https://www.snomed.org) — preferred vocabulary for clinical answers
