# FHIR Interoperability

Interoperability is a core mandate of **quickq**. We use HL7 FHIR as the primary mechanism for exchanging questionnaire definitions and response data.

## FHIR Mapping

**quickq** maps its internal SQLite tables directly to FHIR Questionnaire elements:

| quickq (OLTP) | FHIR (R4) |
|---|---|
| `questionnaire.canonical_url` | `Questionnaire.url` |
| `questionnaire.version` | `Questionnaire.version` |
| `question.link_id` | `item.linkId` |
| `question.question_text` | `item.text` |
| `question.question_type` | `item.type` (e.g., `choice`, `decimal`, `text`) |
| `response_option` | `item.answerOption` |
| `response_option_set` | `item.answerValueSet` |
| `skip_rule` | `item.enableWhen` |

## Extensions

Fields required for research but not natively present in the base FHIR Questionnaire resource are serialized as FHIR Extensions under the namespace `https://quickq.io/fhir/StructureDefinition/`:

*   `help-text`: Additional instructions for the respondent.
*   `internal-note`: Researcher-facing notes about the question.
*   `source-instrument`: Provenance information (e.g., "PHQ-9").
*   `scoring-rule`: Definitions for subscale scoring.

## Round-tripping

A key feature of **quickq** is the ability to round-trip data:

1. **Import** a FHIR JSON file into `study.db`.
2. **Modify** or add metadata in **quickq**.
3. **Export** back to a valid FHIR JSON file without losing any information.

## Delivery Interoperability

By exporting to FHIR, **quickq** can delegate survey delivery to any FHIR-compliant platform (e.g., a React webapp, a mobile app, or a third-party clinical system). The responses can then be imported back into **quickq** as `QuestionnaireResponse` resources.
