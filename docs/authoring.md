# Survey Authoring

Questionnaires are defined in YAML and loaded into the OLTP database with `quickq load`. FHIR Questionnaire JSON from an external source can be imported directly with `quickq fhir import`. Both paths produce the same internal representation.

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
quickq load phq9.yaml study.db
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

## Concept Workflows

Every question, response option, grid row, and grid column can carry a `concept_id` — a foreign key into the concept table that ties that item to a standard vocabulary code (LOINC, SNOMED, NCI, BRFSS) or an internally-assigned identifier. Concept mapping is what makes cross-study analysis possible: two studies that both use LOINC:44250-9 for "little interest or pleasure" can be harmonised automatically at the OLAP layer without any manual alignment step.

Concept mapping is **optional but encouraged**. An unmapped question works correctly in all collection and analysis workflows — it just does not participate in cross-study harmonisation and will appear in the `omop_unmapped_questions` view after `quickq refresh`.

There are three authoring patterns. Teams rarely pick one and stick to it — most use all three at different points in a study.

---

### Shop-first (use an existing validated question)

Before authoring a new question, search for an existing validated instrument that already covers the construct. This eliminates duplication and gives you external vocabulary codes for free.

```bash
# Search the local question bank (library questions + previously loaded instruments)
quickq search "little interest"

# If found — reference it by link_id instead of re-authoring
# In your YAML:
#   - { library: phq9.1 }
```

If the question exists in the library, reference it with `{ library: <link_id> }`. The loader places it in your questionnaire without creating a duplicate row. Both instruments share one `question` row and one concept mapping — responses are queryable together.

If you author a new question with the same external concept code as an existing question, quickq warns you at load time:

```
Warning: LOINC:44250-9 already mapped to phq9.1.
Consider using { library: phq9.1 } instead of authoring a new question.
```

This warning is controlled by `strict_concepts` (default `true`). Set it to `false` in `quickq.yml` if intentional reuse under a new `link_id` is part of your workflow.

**Concept string syntax.** External codes are written as `VOCAB:code`:

```yaml
- link_id: phq9.1
  text: Little interest or pleasure in doing things?
  type: single_choice
  concept: LOINC:44250-9
  options:
    - { text: "Not at all",    value: "0", concept: LOINC:LA6568-5 }
    - { text: "Several days",  value: "1", concept: LOINC:LA6569-3 }
```

Supported vocabularies: `LOINC`, `SNOMED`, `NCI`, `ICD10CM`, `BRFSS`, `Local`.

---

### Assign-first (generate internal codes, map later)

Some teams author questions and assign their own internal identifiers — either because they are creating novel constructs with no external equivalent, or because external mapping is a separate, later step in their workflow.

Enable `auto_concept` to have quickq generate a stable Local concept code for every unmapped question, option, grid row, and grid column at load time:

```yaml
# quickq.yml
authoring:
  auto_concept: true
```

Or per-run:

```bash
quickq load instrument.yaml study.db --auto-concept
```

Generated codes are in the OMOP local-concept range (2,000,000,001+), sequential within the database. They are stable across reloads — re-loading the same instrument twice does not create duplicate concepts.

```yaml
# No concept field needed — quickq assigns Local:2000000001, 2000000002, ...
- link_id: study.novel_q
  text: How many hospitalizations in the past year?
  type: numeric

- link_id: study.diet_freq
  text: How often do you eat fast food?
  type: single_choice
  options:
    - { text: "Daily",   value: "daily" }
    - { text: "Weekly",  value: "weekly" }
    - { text: "Rarely",  value: "rarely" }
```

After data collection, run `quickq data-dict study.db 1 --format csv` to get a spreadsheet with all assigned concept codes. Your mapping team can add LOINC equivalents in a separate column and then use `upsert_concept_relationship` to link them.

**Why OMOP range?** OMOP CDM reserves integers above 2,000,000,000 for locally-defined concepts. Using the same convention makes quickq databases compatible with OMOP pipelines without code changes.

---

### Hybrid (internal ID + external mapping)

Teams that work with existing institutional coding systems — or that want both a stable internal identifier and a LOINC mapping — can use both at once.

**Step 1 — create the internal concept:**

```python
from quickq.authoring import auto_upsert_local_concept, upsert_concept_relationship, upsert_concept, upsert_vocabulary

local_id = auto_upsert_local_concept(conn, "Depressed mood", "Survey", "Question")
```

Or seed it explicitly with your own code:

```python
upsert_vocabulary(conn, "STUDY01", "Study 01 Internal Codes")
local_id = upsert_concept(conn, "Depressed mood", "Survey", "STUDY01", "Question", "D-004")
```

**Step 2 — load the LOINC concept and link them:**

```python
upsert_vocabulary(conn, "LOINC", "LOINC", "https://loinc.org", "2.77")
loinc_id = upsert_concept(conn, "Feeling depressed", "Survey", "LOINC", "Survey", "44255-8")

# Both directions per OMOP convention
upsert_concept_relationship(conn, local_id, loinc_id, "Maps to")
upsert_concept_relationship(conn, loinc_id, local_id, "Mapped from")
```

**Step 3 — use the internal concept_id directly in YAML:**

```yaml
- link_id: study.depressed_mood
  text: In the past two weeks, have you felt depressed?
  type: single_choice
  concept_id: 42          # integer FK, bypasses string lookup entirely
```

The question is attached to your internal concept. The LOINC mapping is discoverable via `concept_relationship` for cross-study harmonisation without changing the question's primary concept.

**When to use each path:**
- `concept: LOINC:44255-8` — shop-first, single external vocabulary, simplest
- `auto_concept: true` — assign-first, no external mapping planned yet
- `concept_id: <int>` — hybrid or institutional, concept pre-seeded, bypass all lookups

---

### Merge-time collision handling

If two sites independently use `auto_concept` and assign Local:2000000001 to different constructs, `quickq merge` detects the collision (same code, different concept_name/domain) and surfaces it as a conflict to resolve before the merge completes. The resolution options are: remap one site's code to a new range, or declare them equivalent via `concept_relationship`. This is the same deduplication challenge that arises for external vocabulary codes shared between sites — the merge step handles it uniformly.

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

# PDF — for IRB submissions, archiving, or sharing with non-technical collaborators
# Requires: pip install quickq[pdf]
quickq render study.db 1 --format pdf --output instrument.pdf
```

See [Example: PHQ-9 Rendered Instrument](reference/example-phq9.md) for a complete example of the output with skip logic and a scoring appendix.

The [Gout Check-In Rendered Instrument](reference/example-gout-checkin.md) shows all nine question types in a single instrument: `date`, `datetime`, `multiple_choice`, `grid`, `slider`, `ranked`, `boolean`, `numeric`, and `text`. It is the reference to use when authoring or reviewing instruments that use any of these types.

The [PRAPARE Data Dictionary](reference/example-prapare-data-dict.md) shows what `quickq data-dict` produces for a mixed-type instrument: 21 questions across six types (`single_choice`, `boolean`, `numeric`, `text`, `likert`, `sata_other`), all LOINC-mapped. It is a useful reference for understanding how different question types appear in the table and what the Type and Valid Values columns look like across a real social determinants screener.

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
