# Tutorial: Authoring an Instrument from Scratch

This tutorial walks through building a complete questionnaire from nothing: defining question items, sharing an option set across questions, adding skip logic, writing a scoring rule, and verifying the result. The scenario is the **GAD-7** (Generalized Anxiety Disorder 7-item scale), a standard clinical screening tool similar in structure to the PHQ-9 but independent of it, which makes it a good vehicle for learning the authoring workflow without leaning on the PHQ-9 fixtures already in the project.

By the end you will have a working `anxiety-study/` repository (its `instrument.yaml` containing the GAD-7), a loaded SQLite study database, and a FHIR export you can hand to any delivery tool.

For the full YAML format reference (every option on every field, all skip-logic operators, all scoring formulas, the question-bank shop-first/assign-first/hybrid concept workflows, and immutability rules) see the [Survey Authoring](../authoring.md) reference page. This tutorial covers what you need to build the instrument; the reference covers everything else you might want.

---

## 1. Scaffold a study repository

```bash
quickq new anxiety-study
cd anxiety-study
```

`quickq new` creates a study repo with the recommended layout (an `instrument.yaml`, `scripts/` to rebuild artifacts from sources, a `.gitignore` keeping runtime databases out of version control, README, and `docs/` + `library/` directories) and runs `git init`. See [Quickstart Step 2](end-to-end.md) for the full layout if you want a refresher.

The scaffold drops a starter `instrument.yaml` with one example question; we'll replace its contents in the next step.

---

## 2. Start the YAML

Open `instrument.yaml` (the scaffolded starter) and replace its contents. Every quickq instrument YAML starts with a questionnaire header:

```yaml
questionnaire:
  name: "GAD-7 Generalized Anxiety Disorder Scale"
  canonical_url: "http://quickq.io/instruments/gad7"
  version: "1.0"
  description: "7-item anxiety screening instrument (Spitzer et al., 2006)"
```

`canonical_url` is the stable identifier used in FHIR exports and in `import_fhir_response` to look up which questionnaire a response belongs to. It does not need to be a live URL, but it must be unique across all instruments in a study and stable across versions.

---

## 3. Add the option set

GAD-7 uses a 4-point frequency scale across all seven items. Define it once as a named option set so each question can reference it instead of repeating the definition:

```yaml
questionnaire:
  name: "GAD-7 Generalized Anxiety Disorder Scale"
  canonical_url: "http://quickq.io/instruments/gad7"
  version: "1.0"
  description: "7-item anxiety screening instrument (Spitzer et al., 2006)"

  option_sets:
    gad_frequency:
      - { text: "Not at all",             value: "0", concept: "LOINC:LA6568-5" }
      - { text: "Several days",           value: "1", concept: "LOINC:LA6569-3" }
      - { text: "More than half the days", value: "2", concept: "LOINC:LA6570-1" }
      - { text: "Nearly every day",       value: "3", concept: "LOINC:LA6571-9" }
```

The `concept` field on each option maps to a standard vocabulary code. `LOINC:LA6568-5` is the LOINC answer code for "Not at all" — the same codes used by the PHQ-9 in this project. When quickq loads this YAML and finds a `LOINC:` prefix it looks up the concept in the local concept table. If the concept is not yet seeded, the option loads without a `concept_id` and the field is left null — the instrument still works, but the option will not appear in OMOP observations.

!!! note "Seeding concepts before loading"
    If you want LOINC codes to resolve at load time, seed the vocabulary first:
    ```python
    from quickq.authoring import upsert_vocabulary, upsert_concept
    upsert_vocabulary(conn, "LOINC", "Logical Observation Identifiers Names and Codes",
                      "https://loinc.org", "2.77")
    # then upsert_concept() for each code you reference
    ```
    The demo script in `scripts/generate_demo.py` shows a complete example.

---

## 4. Add the first question

```yaml
  sections:
    - title: "Over the last 2 weeks, how often have you been bothered by the following problems?"
      questions:
        - link_id: gad7.1
          text: "Feeling nervous, anxious, or on edge"
          type: single_choice
          concept: "LOINC:69725-0"
          options: $gad_frequency
```

Three things to understand here:

**`link_id`** is the permanent identifier for this question. It is immutable once created — if you load this YAML, run a study, and later try to load a revised YAML with a different `text` for `gad7.1`, quickq will raise an error rather than silently overwrite the question. To revise a question's wording you create a new `link_id` and record the relationship via `record_question_lineage()`. This is intentional: response rows reference `link_id` — changing the question text while keeping the ID would make historical data uninterpretable.

**`concept: "LOINC:69725-0"`** maps this specific item to a standard clinical concept. At load time quickq looks it up in the local concept table. At refresh time it lands in `dim_question.concept_code` and in `omop_observation.observation_concept_id` for any session that answered this question. A question without a concept still works; it just won't appear in federated OMOP queries.

**`options: $gad_frequency`** references the shared option set by name. All seven GAD-7 items will use this same line. The `$` prefix is the YAML syntax for referencing a named option set.

---

## 5. Add the remaining items

Extend the questions list with items 2–7. The structure is identical to item 1 — only `link_id`, `text`, and `concept` change:

```yaml
        - link_id: gad7.2
          text: "Not being able to stop or control worrying"
          type: single_choice
          concept: "LOINC:68509-9"
          options: $gad_frequency

        - link_id: gad7.3
          text: "Worrying too much about different things"
          type: single_choice
          concept: "LOINC:69733-4"
          options: $gad_frequency

        - link_id: gad7.4
          text: "Trouble relaxing"
          type: single_choice
          concept: "LOINC:69734-2"
          options: $gad_frequency

        - link_id: gad7.5
          text: "Being so restless that it is hard to sit still"
          type: single_choice
          concept: "LOINC:69735-9"
          options: $gad_frequency

        - link_id: gad7.6
          text: "Becoming easily annoyed or irritable"
          type: single_choice
          concept: "LOINC:69736-7"
          options: $gad_frequency

        - link_id: gad7.7
          text: "Feeling afraid, as if something awful might happen"
          type: single_choice
          concept: "LOINC:69737-5"
          options: $gad_frequency
```

---

## 6. Add skip logic

The GAD-7 includes an optional follow-up question — "How difficult have these problems made it to do your work, take care of things at home, or get along with other people?" — that should only appear when the total score is above zero. In FHIR terms this is an `enableWhen` condition; in quickq YAML it is a `show_when` block.

Because we cannot compute a total score at the item level (scoring happens after all items are answered), the conventional approach is to show the difficulty question when *any* item is non-zero. We trigger on item 1 here as a proxy:

```yaml
        - link_id: gad7.difficulty
          text: "How difficult have these problems made it to do your work, take care of things at home, or get along with other people?"
          type: single_choice
          options:
            - { text: "Not difficult at all", value: "0" }
            - { text: "Somewhat difficult",   value: "1" }
            - { text: "Very difficult",        value: "2" }
            - { text: "Extremely difficult",   value: "3" }
          show_when:
            question: gad7.1
            operator: "!="
            value: "0"
```

`show_when` maps to FHIR `enableWhen`. See [Skip Logic](../authoring.md#skip-logic) in the reference for the full operator list (`=`, `!=`, `>`, `<`, `>=`, `<=`, `exists`) and the multi-condition / `enable_behavior: any` syntax.

The difficulty question deliberately has no `concept` field — it does not have a standard LOINC code in common clinical use. It will appear in `omop_unmapped_questions` after refresh, which is the expected and correct behavior for locally-defined items.

---

## 7. Add the scoring rule

```yaml
  scoring:
    - name: "GAD-7 Total Score"
      formula: sum
      items: [gad7.1, gad7.2, gad7.3, gad7.4, gad7.5, gad7.6, gad7.7]
      categories:
        - { label: "Minimal anxiety",  min: 0,  max: 4  }
        - { label: "Mild anxiety",     min: 5,  max: 9  }
        - { label: "Moderate anxiety", min: 10, max: 14 }
        - { label: "Severe anxiety",   min: 15, max: 21 }
```

`formula: sum` aggregates item `value` fields (numeric coercion). `quickq refresh` writes the total to `agg_respondent_scores.score_raw` and assigns a `score_category` from the bands defined here; `items_answered / items_total` captures partial completion. See [Scoring Rules](../authoring.md#scoring-rules) for other formulas (`mean`, `count`, arithmetic expressions) and item weighting / reverse-scoring.

---

## 8. The complete YAML

Putting it all together:

```yaml
questionnaire:
  name: "GAD-7 Generalized Anxiety Disorder Scale"
  canonical_url: "http://quickq.io/instruments/gad7"
  version: "1.0"
  description: "7-item anxiety screening instrument (Spitzer et al., 2006)"

  option_sets:
    gad_frequency:
      - { text: "Not at all",              value: "0", concept: "LOINC:LA6568-5" }
      - { text: "Several days",            value: "1", concept: "LOINC:LA6569-3" }
      - { text: "More than half the days", value: "2", concept: "LOINC:LA6570-1" }
      - { text: "Nearly every day",        value: "3", concept: "LOINC:LA6571-9" }

  sections:
    - title: "Over the last 2 weeks, how often have you been bothered by the following problems?"
      questions:
        - link_id: gad7.1
          text: "Feeling nervous, anxious, or on edge"
          type: single_choice
          concept: "LOINC:69725-0"
          options: $gad_frequency

        - link_id: gad7.2
          text: "Not being able to stop or control worrying"
          type: single_choice
          concept: "LOINC:68509-9"
          options: $gad_frequency

        - link_id: gad7.3
          text: "Worrying too much about different things"
          type: single_choice
          concept: "LOINC:69733-4"
          options: $gad_frequency

        - link_id: gad7.4
          text: "Trouble relaxing"
          type: single_choice
          concept: "LOINC:69734-2"
          options: $gad_frequency

        - link_id: gad7.5
          text: "Being so restless that it is hard to sit still"
          type: single_choice
          concept: "LOINC:69735-9"
          options: $gad_frequency

        - link_id: gad7.6
          text: "Becoming easily annoyed or irritable"
          type: single_choice
          concept: "LOINC:69736-7"
          options: $gad_frequency

        - link_id: gad7.7
          text: "Feeling afraid, as if something awful might happen"
          type: single_choice
          concept: "LOINC:69737-5"
          options: $gad_frequency

        - link_id: gad7.difficulty
          text: "How difficult have these problems made it to do your work, take care of things at home, or get along with other people?"
          type: single_choice
          options:
            - { text: "Not difficult at all", value: "0" }
            - { text: "Somewhat difficult",   value: "1" }
            - { text: "Very difficult",        value: "2" }
            - { text: "Extremely difficult",   value: "3" }
          show_when:
            question: gad7.1
            operator: "!="
            value: "0"

  scoring:
    - name: "GAD-7 Total Score"
      formula: sum
      items: [gad7.1, gad7.2, gad7.3, gad7.4, gad7.5, gad7.6, gad7.7]
      categories:
        - { label: "Minimal anxiety",  min: 0,  max: 4  }
        - { label: "Mild anxiety",     min: 5,  max: 9  }
        - { label: "Moderate anxiety", min: 10, max: 14 }
        - { label: "Severe anxiety",   min: 15, max: 21 }
```

---

## 9. Load and verify

```bash
bash scripts/load.sh
```

The scaffolded script runs `quickq init study.db && quickq load instrument.yaml study.db`. If there is a validation error — an unknown question type, a `show_when` reference to a `link_id` that does not exist in the same questionnaire, a duplicate `link_id` — quickq raises it here before any rows are written.

### For you: the data dictionary

The data dictionary is the analyst's reference. It shows every question in order with its type, concept code, valid response values, skip conditions, and scoring rule membership — all derived directly from the database, not from a separate document.

```bash
quickq data-dict study.db 1
```

To save it:

```bash
# Markdown table — for methods appendices, pull requests, code review
quickq data-dict study.db 1 --output gad7_data_dict.md

# CSV — for import into analysis pipelines, sharing with data managers
quickq data-dict study.db 1 --format csv --output gad7_data_dict.csv
```

The skip condition column confirms that your branching logic was recorded correctly — `show when gad7.1 ≠ 0` is more readable than tracing through a SQL join. The scoring rules column confirms which items contribute to the GAD-7 total.

### For everyone else: the rendered instrument

The rendered document presents the questionnaire the way a person would read it — sections and questions in order, response options as a list, skip conditions in plain English, and a scoring appendix. It is appropriate for audiences who should not need to open a database or read a data dictionary table.

```bash
quickq render study.db 1 --output gad7_instrument.md
```

Typical uses:

- **IRB submissions** — attach as the instrument specification. The rendered document shows exactly what participants will be asked, including conditional questions and how they are triggered.
- **Research lead or PI review** — a non-technical collaborator can read and approve the instrument before data collection begins, without needing to understand YAML or SQL.
- **Methods sections and preregistrations** — paste the scoring appendix directly; the formula and category thresholds are authoritative because they come from the same definition that drives `quickq refresh`.
- **Protocol documentation** — version-control the rendered output alongside the YAML. The diff tells you exactly what changed between instrument versions.

Both outputs come from the source of truth — the database. There is no separate document to maintain or keep in sync.

---

## 10. Export to FHIR

```bash
quickq fhir export study.db 1 > gad7_questionnaire.json
```

This produces a standard FHIR R4 Questionnaire JSON file. Inspect it to confirm:

- Each item has the correct `linkId` (matching `link_id`)
- The answer options carry `valueCoding` entries with the LOINC codes from the YAML
- The difficulty question has an `enableWhen` block referencing `gad7.1`
- The scoring rule is serialized as a FHIR extension

The JSON file can be handed directly to [LHC-Forms](https://lhncbc.nlm.nih.gov/LHC-forms/) or any other FHIR-compliant delivery tool. Responses come back as FHIR QuestionnaireResponse JSON and are imported with `import_fhir_response`.

---

## What's next

- **Add to an existing study** — pass `study_id` to `load_yaml` to associate the instrument with a specific study in a multi-instrument database.
- **Co-administer with another instrument** — the end-to-end tutorial uses a PHQ-9 and Prenatal Visit Log in the same study database; the same `respondent_id` links sessions across instruments automatically.
- **Collect responses** — `import_fhir_response(conn, response_json, admin_mode="web")` writes a FHIR QuestionnaireResponse to the OLTP. See [FHIR Interoperability](../fhir.md) for the import contract.
- **Refresh and score** — `quickq refresh study.db` loads the OLAP, computes GAD-7 scores, and materializes aggregate tables. The scoring result will be in `agg_respondent_scores` under the rule name `"GAD-7 Total Score"`.
