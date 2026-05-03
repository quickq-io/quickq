# Question Type Reference

quickq supports 12 question types covering the full range of health and epidemiology survey instruments. This page describes each type, its YAML authoring syntax, and its current pipeline status.

---

## Pipeline coverage

The table below reflects a formal audit of each type across the five pipeline layers.

| Type | YAML loader | FHIR export | Seed | OLAP refresh | Report | Status |
|---|---|---|---|---|---|---|
| `single_choice` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `multiple_choice` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `boolean` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `text` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `numeric` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `date` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `slider` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `sata_other` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `likert` | ✅ | ✅ | ✅ | ✅ | ⚠️ | **Partial** |
| `grid` | ✅ | ✅ | ✅ | ✅ | ⚠️ | **Partial** |
| `ranked` | ✅ | ✅ | ✅ | ✅ | ✅ | **Full** |
| `repeating_group` | ✅ | ✅ | ✅ | ✅ | ❌ | **Partial** |

**Partial — likert:** Collected and exported correctly. The report renders it as a categorical distribution rather than an ordinal scale; `agg_numeric_stats` excludes it. Scoring and analysis work correctly via `fact_response`.

**Partial — grid:** Definition, FHIR export, seed, and OLAP storage all work. The report renders grid cells as a flat list rather than a row × column matrix. The underlying data is correct and queryable.

**Partial — repeating_group:** YAML definition, FHIR export, FHIR import, and OLAP storage all work end-to-end. Responses arriving via the FHIR-import path populate `fact_response.repeat_index` correctly; see the bundled demo views (`v_prenatal_visits`, `v_prenatal_summary`) for example pivots. Two real gaps: `quickq seed` does not generate repeating instances synthetically (the seed pathway is the only entry point that misses), and the Markdown report does not yet render repeating groups as nested tables. For studies whose responses arrive via FHIR import (the realistic production path), the data flow is complete and queryable.

---

## Demo instrument

`examples/health_intake_demo.yaml` in the repository covers all fully supported and partial types in a coherent clinical context. Load and seed it to exercise the full pipeline:

```bash
quickq load examples/health_intake_demo.yaml study.db
quickq seed study.db <questionnaire_id> --n 50
quickq refresh study.db analytics.duckdb
quickq report analytics.duckdb study.db <questionnaire_id>
```

---

## Type reference

### `single_choice`

One answer from a fixed list. Rendered as radio buttons.

```yaml
- link_id: q.health
  text: "How would you rate your general health today?"
  type: single_choice
  options:
    - { text: "Excellent", value: "5" }
    - { text: "Good",      value: "3" }
    - { text: "Poor",      value: "1" }
```

---

### `multiple_choice`

One or more answers from a fixed list. Rendered as checkboxes.

```yaml
- link_id: q.strategies
  text: "Which do you currently use to manage your condition?"
  type: multiple_choice
  options:
    - { text: "Medication",       value: "rx" }
    - { text: "Physical therapy", value: "pt" }
    - { text: "Exercise",         value: "exercise" }
```

---

### `sata_other`

Select-all-that-apply with a free-text "Other" option. Mark the other option with `is_other: true`.

```yaml
- link_id: q.symptoms
  text: "Which symptoms are you experiencing? Select all that apply."
  type: sata_other
  options:
    - { text: "Fatigue", value: "fatigue" }
    - { text: "Pain",    value: "pain" }
    - { text: "Other",   value: "other", is_other: true }
```

---

### `boolean`

Yes / No. Stored as `"true"` or `"false"` in `response_text`.

```yaml
- link_id: q.chronic
  text: "Have you been diagnosed with a chronic condition?"
  type: boolean
```

---

### `text`

Open-ended free text.

```yaml
- link_id: q.notes
  text: "Any additional notes for your care team?"
  type: text
```

---

### `numeric`

Integer or decimal with an optional range constraint.

```yaml
- link_id: q.symptom_days
  text: "How many days in the past 30 did symptoms limit your activities?"
  type: numeric
  range: [0, 30]
```

---

### `date`

A calendar date. Stored as ISO 8601.

```yaml
- link_id: q.diagnosis_date
  text: "When were you first diagnosed?"
  type: date
```

---

### `slider`

A visual analog scale rendered as a draggable slider. Range and endpoint labels are optional.

```yaml
- link_id: q.pain_vas
  text: "Rate your current pain level."
  type: slider
  range: [0, 100]
  slider_min_label: "No pain"
  slider_max_label: "Worst imaginable"
```

---

### `likert`

An ordered agreement or frequency scale. Functionally identical to `single_choice` in storage; the `likert` type signals to delivery tools that ordered rendering is appropriate.

```yaml
option_sets:
  agreement:
    - { text: "Strongly disagree", value: "1" }
    - { text: "Disagree",          value: "2" }
    - { text: "Neutral",           value: "3" }
    - { text: "Agree",             value: "4" }
    - { text: "Strongly agree",    value: "5" }

questions:
  - link_id: q.confidence
    text: "I feel confident managing my condition day-to-day."
    type: likert
    options: $agreement
```

---

### `grid`

A matrix question. Each row is a domain; each column is a rating or attribute. The column `value` is stored per cell.

```yaml
- link_id: q.impact
  text: "How much has your condition affected each area in the past week?"
  type: grid
  rows:
    - { text: "Work or daily activities" }
    - { text: "Sleep quality" }
    - { text: "Physical mobility" }
  columns:
    - { text: "Not at all",  value: "0" }
    - { text: "A little",    value: "1" }
    - { text: "Moderately",  value: "2" }
    - { text: "Quite a bit", value: "3" }
```

---

### `ranked`

Drag-to-rank ordering. Each option is stored as a `response` row with `option_id` and `response_numeric` = rank position (1 = most important).

```yaml
- link_id: q.priorities
  text: "Rank your health goals from most to least important."
  type: ranked
  options:
    - { text: "Reducing pain",        value: "pain" }
    - { text: "Improving sleep",      value: "sleep" }
    - { text: "Maintaining mobility", value: "mobility" }
```

---

### `repeating_group` *(partial)*

A loop of sub-questions that repeats N times: once per pregnancy, medication, family member, etc. Definition, FHIR export, FHIR import, and OLAP storage all work end-to-end; `fact_response.repeat_index` is the per-instance counter.

Two repetition patterns are supported:

- **Count-driven:** declare `count_from: <link_id>` on the group, pointing at a numeric question defined earlier in the questionnaire. The delivery layer reads that question's answer and renders that many instances. Stored as `questionnaire_question.count_qq_id`.
- **Free-add:** omit `count_from`. The delivery tool offers an "add another" affordance and the respondent picks N.

Both patterns produce identical storage. See [Repeating Groups](../authoring.md#repeating-groups) for the YAML syntax.

One gap remains: the Markdown report does not yet render repeating groups as nested tables. The bundled demo views (`v_prenatal_visits`, `v_prenatal_summary`) show example pivots over `repeat_index`. `quickq seed` honors `count_from` (using the seeded count question's answer to drive N) and falls back to a small random N for free-add groups.
