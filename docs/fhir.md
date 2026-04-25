# FHIR Interoperability

quickq uses HL7 FHIR R4 as its cross-language handoff protocol. The OLTP schema is a superset of FHIR Questionnaire — every questionnaire authored in quickq can be exported to valid FHIR JSON without loss, and any valid FHIR Questionnaire can be imported.

---

## The Handoff Model

Survey delivery is out of scope for quickq. The FHIR exchange is the interface:

```
quickq  →  export_fhir()  →  FHIR Questionnaire JSON
                                  ↓
                          any FHIR-compliant delivery tool
                          (LHC-Forms, REDCap, mobile app, clinical system)
                                  ↓
                          FHIR QuestionnaireResponse JSON
                                  ↓
quickq  →  import_fhir_response()  →  OLTP response rows  →  quickq refresh
```

This means quickq Python is not required at delivery time. A JavaScript app, a WASM binary, or a third-party clinical platform can deliver the questionnaire using only the exported JSON. quickq re-enters the picture when the responses come back.

The reference delivery tool is **[LHC-Forms](https://lhncbc.nlm.nih.gov/LHC-forms/)** (NLM) — purpose-built for FHIR Questionnaire rendering, open source, embeddable as a JavaScript widget with no server dependency.

---

## Column Mapping

The OLTP schema maps directly to FHIR fields. Fields that have no FHIR equivalent are serialized as extensions on export.

| OLTP column | FHIR field |
|---|---|
| `questionnaire.canonical_url` | `Questionnaire.url` |
| `questionnaire.version` | `Questionnaire.version` |
| `questionnaire.fhir_status` | `Questionnaire.status` |
| `question.link_id` | `item.linkId` |
| `question.question_text` | `item.text` |
| `question.help_text` | `item._text` / extension |
| `question.question_type` | `item.type` (see table below) |
| `questionnaire_question.is_required` | `item.required` |
| `response_option.option_text` | `answerOption.valueCoding.display` |
| `response_option.option_value` | `answerOption.valueCoding.code` |
| `response_option.concept_code` + `.concept_system` | `valueCoding.code` + `system` |
| `response_option_set.canonical_url` | `item.answerValueSet` |
| `skip_rule` rows | `item.enableWhen[]` |
| `questionnaire_question.parent_qq_id` | nested `item` arrays |
| `response_session` | `QuestionnaireResponse` header |
| `response` rows | `QuestionnaireResponse.item[].answer[]` |
| `response.repeat_index` | repeated `item` entries with the same `linkId` |

### Question type mapping

| quickq type | FHIR type | Notes |
|---|---|---|
| `single_choice` | `choice` | |
| `multiple_choice` | `choice` | `repeats: true` |
| `boolean` | `boolean` | |
| `text` | `text` | |
| `numeric` | `decimal` | |
| `date` | `date` | |
| `datetime` | `dateTime` | |
| `likert` | `choice` | |
| `grid` | `group` | rows as nested items |
| `ranked` | `choice` | `ordinalValue` extensions |
| `slider` | `decimal` | min/max extensions |
| `repeating_group` | `group` | `repeats: true`; children nested in `item` |

---

## Extensions

Research fields with no FHIR R4 base equivalent are serialized as extensions under `https://quickq.io/fhir/StructureDefinition/`:

| Extension | Source column |
|---|---|
| `help-text` | `question.help_text` |
| `internal-note` | `question.internal_note` |
| `source-instrument` | `question.source_instrument` |
| `source-item-id` | `question.source_item_id` |
| `scoring-rule` | `scoring_rule` rows |
| `count-question` | `questionnaire_question.count_qq_id` (repeating group count link) |

---

## Import

`import_fhir(conn, fhir_json)` imports a FHIR Questionnaire into the OLTP. Non-repeating `group` items are flattened to their leaf questions. Repeating groups (`type: group, repeats: true`) are imported as `repeating_group` questions with their children linked via `parent_qq_id`.

`import_fhir_response(conn, resource)` parses a QuestionnaireResponse and writes response rows. The questionnaire is matched by `canonical_url`. Repeated group instances — multiple `item` entries with the same `linkId` — are assigned sequential `repeat_index` values (0-based). Unknown `linkId` values and unrecognized answer formats are written to `data_quality_flag` rather than raising exceptions.

---

## Round-Trip Guarantee

Any FHIR Questionnaire that can be imported can be exported back to valid FHIR without loss of structure or semantics. The round-trip is tested against the HL7 reference example suite, including the US Surgeon General Family Health Portrait (USSG-FHT, LOINC 54127-6) which exercises nested repeating groups at two levels.

```bash
quickq import-fhir ussg_fht.json study.db
quickq export-fhir 1 study.db > ussg_exported.json
```

The FHIR E2E test suite (in `tests/test_e2e_lhcforms.py`) validates the full pipeline against LHC-Forms using Playwright headless rendering.
