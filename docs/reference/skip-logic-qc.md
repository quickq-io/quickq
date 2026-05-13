# Skip-Logic Recipes

Epidemiologists draw a sharp distinction between **structurally missing** data (a question wasn't asked because skip logic legitimately routed the respondent past it) and **truly missing** data (a question *should* have been asked but the respondent declined, abandoned, or the delivery tool failed to record it). The two demand different analytical responses: structurally missing data is part of the study design and needs no imputation, while truly missing data threatens validity and may need to be reported, imputed, or excluded from analysis.

The challenge in practice is telling them apart. A blank cell in a response table doesn't say which kind of missing it is. When skip rules live in the database alongside the responses, the distinction becomes a SQL query rather than a hand-coded interpretation per instrument.

This page walks through two recipes that exercise the same idea: walk each `skip_rule` row, evaluate it against the respondent's session, and use the result as an eligibility predicate. The recipes are *generic* — they work across any quickq study without modification.

!!! info "Why this matters"
    In tools where skip logic lives in the survey configuration (REDCap branching logic, Qualtrics display logic, etc.), an analyst computing a completion rate has two options: hand-encode the rules in their analysis script (brittle, easy to forget), or accept that "blank" means "we don't know whether this was skipped or truly missing." quickq stores skip rules as structured rows in `skip_rule`, joined to the same star schema as the responses. Eligibility, integrity, and the structurally-missing / truly-missing distinction fall out as standard SQL.

The recipes below run against the bundled demo (`demo/analytics.duckdb`, produced by `scripts/generate_demo.py`). They generalize unchanged to any quickq study.

---

## Setup

The `skip_rule` table lives in the OLTP (SQLite). Attach it to the OLAP DuckDB session:

```sql
ATTACH 'study.db' AS oltp (TYPE sqlite, READ_ONLY);
```

The shared CTE below walks every skip rule in the study, evaluates it against each session's responses to the trigger question, and combines rules per gated question using `enable_behavior` (`all` = AND, `any` = OR). The result is a row per `(session_id, gated_qq_id, eligible)`. Each recipe below re-includes this CTE inline so every code block is self-contained and copy-paste runnable.

```sql
WITH rule_evaluation AS (
    SELECT
        ds.session_id,
        sr.qq_id                                                    AS gated_qq_id,
        MAX(sr.enable_behavior) OVER (PARTITION BY sr.qq_id)        AS combinator,
        CASE
            -- COALESCE picks up trigger_default_value when the trigger
            -- response is absent (NULL after the LEFT JOIN below). For
            -- exists / not_exists the default doesn't apply — those
            -- operators test response presence directly.
            WHEN sr.operator = '='  AND COALESCE(fr.option_value, sr.trigger_default_value) =  sr.trigger_value THEN TRUE
            WHEN sr.operator = '!=' AND COALESCE(fr.option_value, sr.trigger_default_value) <> sr.trigger_value THEN TRUE
            WHEN sr.operator = '>'  AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) >  TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '<'  AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) <  TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '>=' AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) >= TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '<=' AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) <= TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = 'exists'     AND fr.response_id IS NOT NULL THEN TRUE
            WHEN sr.operator = 'not_exists' AND fr.response_id IS NULL     THEN TRUE
            ELSE FALSE
        END AS rule_satisfied
    FROM      dim_session    ds
    CROSS JOIN oltp.skip_rule sr
    LEFT JOIN fact_response   fr  ON fr.session_id = ds.session_id
                                  AND fr.qq_id     = sr.trigger_qq_id
),
eligibility AS (
    SELECT
        session_id,
        gated_qq_id,
        CASE WHEN MAX(combinator) = 'any' THEN BOOL_OR(rule_satisfied)
                                          ELSE BOOL_AND(rule_satisfied)
        END AS eligible
    FROM rule_evaluation
    GROUP BY session_id, gated_qq_id
)
SELECT * FROM eligibility LIMIT 5;
```

**Eligibility contract.** A gated question is `eligible` for a session when its skip rules — combined according to `enable_behavior` — evaluate to `TRUE` against that session's responses. If the trigger question was never answered, the rule's declared `trigger_default_value` is substituted via `COALESCE`. If no default is set, every comparison evaluates to `FALSE` (the responses are joined `LEFT` and unmatched rows produce a `FALSE` from the `CASE`'s `ELSE` branch). Authors set the default per-rule by adding `on_missing: <value>` to a `show_when` condition in YAML.

### Authoring shorthand: multi-value gates

A common pattern is "show this question if the trigger was any one of N values." Author this once in YAML with `operator: in` and a `values` list:

```yaml
show_when:
  question: condition_history
  operator: in
  values: ["cancer", "heart_disease", "diabetes"]
```

The loader expands this into one `=` rule per value, combined with `enable_behavior: any`. The shape that lands in `skip_rule` is identical to writing the three rules by hand, so the eligibility CTE above and every downstream recipe work unchanged.

---

## Recipe 1: Eligibility-aware completion (separating true missingness from structural missingness)

For each gated question, count separately the respondents who *should* have answered (eligible) and the ones who *did* answer. The difference is the **truly missing** count — the actionable QC number. Respondents who didn't answer because the skip rule routed them past the question are **structurally missing** and are not counted as a problem.

```sql
WITH rule_evaluation AS (
    SELECT
        ds.session_id,
        sr.qq_id                                                    AS gated_qq_id,
        MAX(sr.enable_behavior) OVER (PARTITION BY sr.qq_id)        AS combinator,
        CASE
            -- COALESCE picks up trigger_default_value when the trigger
            -- response is absent (NULL after the LEFT JOIN below). For
            -- exists / not_exists the default doesn't apply — those
            -- operators test response presence directly.
            WHEN sr.operator = '='  AND COALESCE(fr.option_value, sr.trigger_default_value) =  sr.trigger_value THEN TRUE
            WHEN sr.operator = '!=' AND COALESCE(fr.option_value, sr.trigger_default_value) <> sr.trigger_value THEN TRUE
            WHEN sr.operator = '>'  AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) >  TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '<'  AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) <  TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '>=' AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) >= TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '<=' AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) <= TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = 'exists'     AND fr.response_id IS NOT NULL THEN TRUE
            WHEN sr.operator = 'not_exists' AND fr.response_id IS NULL     THEN TRUE
            ELSE FALSE
        END AS rule_satisfied
    FROM      dim_session    ds
    CROSS JOIN oltp.skip_rule sr
    LEFT JOIN fact_response   fr  ON fr.session_id = ds.session_id
                                  AND fr.qq_id     = sr.trigger_qq_id
),
eligibility AS (
    SELECT
        session_id,
        gated_qq_id,
        CASE WHEN MAX(combinator) = 'any' THEN BOOL_OR(rule_satisfied)
                                          ELSE BOOL_AND(rule_satisfied)
        END AS eligible
    FROM rule_evaluation
    GROUP BY session_id, gated_qq_id
)
SELECT
    dq.link_id,
    SUM(CASE WHEN e.eligible THEN 1 ELSE 0 END)                                 AS n_eligible,
    SUM(CASE WHEN e.eligible AND fr.session_id IS NOT NULL THEN 1 ELSE 0 END)   AS n_answered,
    SUM(CASE WHEN e.eligible AND fr.session_id IS NULL     THEN 1 ELSE 0 END)   AS n_truly_missing,
    ROUND(100.0 * SUM(CASE WHEN e.eligible AND fr.session_id IS NOT NULL THEN 1 ELSE 0 END)
                / NULLIF(SUM(CASE WHEN e.eligible THEN 1 ELSE 0 END), 0), 1)    AS completion_pct
FROM      eligibility                                              e
JOIN      oltp.questionnaire_question                              qq  ON e.gated_qq_id = qq.qq_id
JOIN      dim_question                                             dq  ON qq.question_id = dq.question_id
LEFT JOIN (SELECT DISTINCT session_id, qq_id FROM fact_response)   fr  ON fr.session_id  = e.session_id
                                                                      AND fr.qq_id       = e.gated_qq_id
GROUP BY dq.link_id;
```

Result against the bundled demo:

| link_id | n_eligible | n_answered | n_truly_missing | completion_pct |
|---|---|---|---|---|
| phq9.difficulty | 190 | 190 | 0 | 100.0 |

Of the 400 PHQ-9 sessions, 190 had a non-zero answer to at least one of items 1-3 and therefore qualified to see the difficulty follow-up question. All 190 of those answered it. The remaining 210 sessions are **structurally missing** — they were correctly routed past the question by skip logic. There are zero **truly missing** responses: every absence is explained by the study design, not by data quality.

The contrast with a naive completion-rate query is instructive. A naive query would compute `190 / 400 = 47.5%` completion for `phq9.difficulty`, falsely suggesting more than half of respondents abandoned the question. The eligibility-aware recipe correctly reports 100% completion among respondents who should have seen it, and zero truly-missing cases that would need investigation, imputation, or reporting as a data-quality concern.


---

## Recipe 2: Skip-rule integrity (violations)

The inverse: count respondents who answered the gated question *despite* the rule saying they shouldn't have. The expected number for a tested delivery tool is zero.

```sql
WITH rule_evaluation AS (
    SELECT
        ds.session_id,
        sr.qq_id                                                    AS gated_qq_id,
        MAX(sr.enable_behavior) OVER (PARTITION BY sr.qq_id)        AS combinator,
        CASE
            -- COALESCE picks up trigger_default_value when the trigger
            -- response is absent (NULL after the LEFT JOIN below). For
            -- exists / not_exists the default doesn't apply — those
            -- operators test response presence directly.
            WHEN sr.operator = '='  AND COALESCE(fr.option_value, sr.trigger_default_value) =  sr.trigger_value THEN TRUE
            WHEN sr.operator = '!=' AND COALESCE(fr.option_value, sr.trigger_default_value) <> sr.trigger_value THEN TRUE
            WHEN sr.operator = '>'  AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) >  TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '<'  AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) <  TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '>=' AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) >= TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = '<=' AND TRY_CAST(COALESCE(fr.option_value, sr.trigger_default_value) AS DOUBLE) <= TRY_CAST(sr.trigger_value AS DOUBLE) THEN TRUE
            WHEN sr.operator = 'exists'     AND fr.response_id IS NOT NULL THEN TRUE
            WHEN sr.operator = 'not_exists' AND fr.response_id IS NULL     THEN TRUE
            ELSE FALSE
        END AS rule_satisfied
    FROM      dim_session    ds
    CROSS JOIN oltp.skip_rule sr
    LEFT JOIN fact_response   fr  ON fr.session_id = ds.session_id
                                  AND fr.qq_id     = sr.trigger_qq_id
),
eligibility AS (
    SELECT
        session_id,
        gated_qq_id,
        CASE WHEN MAX(combinator) = 'any' THEN BOOL_OR(rule_satisfied)
                                          ELSE BOOL_AND(rule_satisfied)
        END AS eligible
    FROM rule_evaluation
    GROUP BY session_id, gated_qq_id
)
SELECT
    dq.link_id,
    SUM(CASE WHEN NOT e.eligible AND fr.session_id IS NOT NULL THEN 1 ELSE 0 END) AS n_violations,
    SUM(CASE WHEN NOT e.eligible THEN 1 ELSE 0 END)                                AS n_ineligible,
    ROUND(100.0 * SUM(CASE WHEN NOT e.eligible AND fr.session_id IS NOT NULL THEN 1 ELSE 0 END)
                / NULLIF(SUM(CASE WHEN NOT e.eligible THEN 1 ELSE 0 END), 0), 1)   AS violation_rate_pct
FROM      eligibility                                              e
JOIN      oltp.questionnaire_question                              qq ON e.gated_qq_id = qq.qq_id
JOIN      dim_question                                             dq ON qq.question_id = dq.question_id
LEFT JOIN (SELECT DISTINCT session_id, qq_id FROM fact_response)   fr ON fr.session_id  = e.session_id
                                                                      AND fr.qq_id      = e.gated_qq_id
GROUP BY dq.link_id;
```

Result against the bundled demo:

| link_id | n_violations | n_ineligible | violation_rate_pct |
|---|---|---|---|
| phq9.difficulty | 38 | 210 | 18.1 |

Out of 210 sessions that *should not* have seen the difficulty question (because all of items 1-3 were zero), 38 of them answered it anyway. The bundled demo's synthetic-data generator (`quickq seed`) is somewhat permissive about skip logic and produced these inconsistencies on purpose to give the integrity recipe something to detect.

The same query against the gout-symptoms end-to-end walkthrough (where responses come from a real LHC-Forms / quickq-forms session, not a seeder) returns zero violations — see [Did skip logic fire correctly?](../tutorials/end-to-end.md#step-11-explore-the-analytics-layer) at the bottom of that walkthrough for the simpler variant.

When `n_violations > 0` in your real study, investigate the delivery path: most often, the delivery tool didn't honor the FHIR `enableWhen` correctly, or hand-entered data was imported via a path that bypassed the structured rules.

---

## Known limits

quickq's `skip_rule` table is a flat-predicate model. It handles the common shape (one or more rules per gated question, combined via AND/OR) but not the deeply nested boolean conditions that some large prospective cohorts use. The recipes above run unchanged across instruments that fit the flat-predicate model; for instruments that don't, the limits below shape what the recipes can verify.

| Limit | What this means for the recipes |
|---|---|
| **Nested boolean composition** deeper than one AND/OR level is not natively supported. A condition like `and(or(A, B), and(C, D))` requires either flattening into multiple rules or expressing it as a FHIRPath string in the unstructured `display_condition` column, which the recipes do not evaluate. | Any rule expressed in `display_condition` is invisible to these recipes. |
| **Cross-instrument skip references** are not supported. Skip rules can only reference other `qq_id`s in the same questionnaire. | A questionnaire that should gate Q2 on a response from Q1 in a *different* questionnaire (e.g., baseline vs. follow-up) cannot be expressed in `skip_rule` and is invisible to these recipes. |
| **Demographic / profile attributes** (`age`, `sex_at_birth`) cannot be skip-rule triggers. Only other questions in the same questionnaire can. | Recipes don't apply to age-bracket gates or similar; use the workaround below. |

The structural roadmap for closing these gaps is tracked in [the data-sharing tooling design constraints issue](https://github.com/quickq-io/quickq/issues) (search `quickq-io-ap8`).

### Workaround: demographics as questions

For age-gated and sex-specific questions, the practical pattern is to *include demographics as questions* in each questionnaire that needs to gate on them — typically a small intake block at the top:

```yaml
questions:
  - link_id: demo.age
    text: How old are you?
    type: numeric
    range: [0, 120]
    required: true

  - link_id: demo.sex_at_birth
    text: What was your sex assigned at birth?
    type: single_choice
    options:
      - { text: "Female", value: "female" }
      - { text: "Male",   value: "male" }
      - { text: "Prefer not to answer", value: "decline" }

  # ... then gate downstream questions on these like any other trigger:
  - link_id: clinical.pregnancy_history
    text: Have you ever been pregnant?
    type: boolean
    show_when:
      question: demo.sex_at_birth
      operator: "="
      value: "female"

  - link_id: clinical.colonoscopy_screening
    text: When was your last colonoscopy?
    type: date
    show_when:
      question: demo.age
      operator: ">="
      value: 45
```

This is **FHIR-clean** — the demographics ride along with every QuestionnaireResponse, so any FHIR-compliant tool can render the gates correctly without quickq-specific extensions. The cost is a small amount of duplication when the same demographics appear in multiple instruments; cross-instrument harmonization on demographics works through standard concept codes (LOINC for age, SNOMED for sex) the same way it does for any other question.

Native attribute-typed gates (e.g. `show_when: { attribute: age, operator: ">=", value: 65 }`) would remove the duplication but introduce a FHIR round-trip gap. The honest path forward is FHIR SDC's `enableWhenExpression` extension; that's a substantive design commitment we'll take on when a real study surfaces demand. Until then, model demographics as questions.

---

## Going deeper

- **The data model**: [Data Model overview](../database/data-model.md), specifically the [instrument definition](../database/data-model.md#2-instrument-definition) section which shows how `skip_rule` connects to `questionnaire_question`.
- **Query patterns by question type**: [Query Patterns Reference](query-patterns.md).
- **A worked walkthrough that ends with an integrity check**: [End-to-End Walkthrough §11](../tutorials/end-to-end.md#step-11-explore-the-analytics-layer).
- **Data quality across the OLAP star schema** (not specifically skip-logic): [Data Quality Tutorial](../tutorials/data-quality.md).
