# Survey Authoring

Questionnaires are defined in YAML and loaded into the OLTP database with `quickq load-yaml`. FHIR Questionnaire JSON from an external source can be imported directly with `quickq import-fhir`. Both paths produce the same internal representation.

---

## YAML Format

A YAML definition maps closely to the OLTP schema. The top-level keys set questionnaire metadata; `questions` is the ordered list of items.

```yaml
name: Patient Health Questionnaire (PHQ-9)
version: "1.0"
canonical_url: http://quickq.io/instruments/phq-9
description: Nine-item depression severity scale.

questions:
  - link_id: phq-1
    text: Little interest or pleasure in doing things?
    type: single_choice
    required: true
    options:
      - { text: "Not at all",              value: "0" }
      - { text: "Several days",            value: "1" }
      - { text: "More than half the days", value: "2" }
      - { text: "Nearly every day",        value: "3" }

  - link_id: phq-total
    text: Total score
    type: numeric
```

Load it:

```bash
quickq load-yaml phq9.yaml study.db
```

---

## Question Types

| Type | FHIR equivalent | Notes |
|---|---|---|
| `single_choice` | `choice` | Radio / MCQ; one answer |
| `multiple_choice` | `choice` (multiple) | Select all that apply |
| `sata_other` | `choice` + open-choice | SATA with free-text "Other" |
| `boolean` | `boolean` | Yes/No; stored as `'true'`/`'false'` |
| `text` | `text` | Open-ended string |
| `numeric` | `decimal` | Integer or float; optional `min`, `max`, `step` |
| `date` / `datetime` | `date` / `dateTime` | ISO 8601 |
| `likert` | `choice` | Ordered scale |
| `grid` | `group` | Matrix; rows × columns |
| `ranked` | `choice` (ordered) | Drag-to-rank |
| `slider` | `decimal` + extensions | Visual analog scale |
| `repeating_group` | `group` + `repeats: true` | Looped sub-question set |

---

## Repeating Groups

A `repeating_group` question is a container whose sub-questions repeat once per instance — once per medication, family member, pregnancy, etc. Sub-questions are listed under `items`:

```yaml
  - link_id: visits
    text: Visit details
    type: repeating_group
    items:
      - link_id: visits.week
        text: Week of pregnancy at visit
        type: numeric
        required: true

      - link_id: visits.provider
        text: Type of provider seen
        type: single_choice
        options:
          - { text: "OB/GYN",  value: ob }
          - { text: "Midwife", value: midwife }

      - link_id: visits.concern
        text: Were any concerns documented?
        type: boolean
```

In the OLTP, sub-questions are stored as `questionnaire_question` rows with `parent_qq_id` pointing to the group. Each response row for a sub-question carries a `repeat_index` (0-based) that identifies which instance it belongs to. In FHIR, the group exports as `type: group, repeats: true` with children nested in its `item` array.

Repeating groups can be nested — a family member loop can itself contain a disease history loop. The same `parent_qq_id` mechanism handles any depth.

---

## The Question Bank

Questions are authored once and reused across instruments via `questionnaire_question`. If the same construct appears in two questionnaires, both placements point to the same `question` row. This means:

- Concept mappings are set once on the question, not per-instrument
- Response data from both instruments is queryable against the same `question_id`
- `question_equivalence` can link the question to its counterpart in a different instrument

`link_id` is the stable human-readable key. It maps directly to FHIR `item.linkId` and is the reference used in skip rules, scoring formulas, and FHIR response imports.

---

## Immutability and Versioning

Questions are immutable once used in a study. A rewording or option change requires a new `question` row with a new `link_id`. The relationship back to the original is recorded in `question_lineage` with a typed `change_type` (`reword`, `option_added`, `option_removed`, `split`, `merge`).

This invariant means historical responses always point to the question exactly as it was asked. There is no ambiguity about what a question said at collection time.

Importing the same `canonical_url` + `version` twice is a no-op — the import is idempotent.

---

## Skip Logic

Branching logic is expressed as `skip_rules` in YAML (or populated via the Python SDK). Each rule names a trigger question, an operator, and a value. Multiple rules for the same question are combined with `enable_behavior: all` (AND) or `any` (OR).

```yaml
  - link_id: alcohol-frequency
    text: How often do you have a drink?
    type: single_choice
    skip_if:
      - trigger: drinks-alcohol
        operator: "="
        value: "no"
```

Skip rules map directly to FHIR `item.enableWhen`. For complex multi-condition logic that exceeds what structured rules can express, `display_condition` accepts a FHIRPath expression as a fallback.

---

## Scoring Rules

Subscale scores (PHQ-9 total, GAD-7 severity, SF-12 PCS/MCS) are defined alongside the instrument and computed automatically during `quickq refresh`. A scoring rule names a formula (`sum`, `mean`, `count`, or an arithmetic expression referencing `link_id` values), lists which questions contribute (with optional weights and reverse-score flags), and defines severity bands.

Results land in `agg_respondent_scores` in the OLAP — one row per respondent per scoring rule per session.

---

## Instrument documentation

quickq generates two complementary documents from the database after loading an instrument. Neither requires a separate file to maintain — both are derived from the source of truth on demand.

### Data dictionary (`quickq data-dict`)

A tabular reference for technical audiences: analysts, data managers, programmers reviewing a pull request. Each row is one question, with columns for link ID, label, type, concept code, valid response values (with vocabulary codes in CSV), skip conditions, and scoring rule membership.

```bash
# Markdown — rendered table, suitable for methods appendices and PR review
quickq data-dict study.db 1 --output instrument_data_dict.md

# CSV — full spec including option concept codes, for pipelines and data managers
quickq data-dict study.db 1 --format csv --output instrument_data_dict.csv
```

The skip conditions column (`show when gad7.1 ≠ 0`) makes branching logic auditable without tracing through the schema. The scoring rules column confirms which items contribute to each computed score.

### Rendered instrument (`quickq render`)

A narrative document for non-technical audiences: IRB reviewers, principal investigators, non-technical research leads, or anyone who needs to understand and approve the instrument without reading YAML or SQL.

```bash
quickq render study.db 1 --output instrument.md
```

Output structure:

- Instrument metadata (name, version, canonical URL)
- Sections and questions in display order
- Each question: text, `link_id`, type, concept code, response options
- Skip conditions in plain English (`Show when: phq9.1 ≠ 0 or phq9.2 ≠ 0`)
- Scoring appendix: formula, contributing items, severity category thresholds

**IRB submissions.** Attach the rendered output as the instrument specification exhibit. It shows exactly what participants will be asked, including conditional questions and their trigger conditions, in a form any reviewer can read.

**PI or research lead approval.** A non-technical collaborator can review and sign off on the instrument before collection begins. The rendered document is the instrument — not a summary of it.

**Methods sections and preregistrations.** The scoring appendix is authoritative: it comes from the same definition that drives `quickq refresh`, so the thresholds in your paper match the thresholds applied to your data.

**Protocol versioning.** Commit the rendered output alongside the YAML. The diff between two versions shows exactly what changed — question wording, a new option, a revised skip condition — in a format any collaborator can review.
