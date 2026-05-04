# Analysis Recipes by Question Type

Per-question-type recipes for going from `fact_response` to a chart. Each
recipe pairs a SQL query against the OLAP star schema with an
[Altair](https://altair-viz.github.io/) visualization. Drop them into a
notebook (Jupyter, marimo, or any Python script) and adjust the `link_id` to
fit your study.

For the underlying SQL patterns without the visualization layer, see
[Query Patterns by Question Type](query-patterns.md). For a worked, interactive
example over a seeded survey, see the
[Anatomy of a Health Survey notebook](../notebooks/health-intake-demo/).

---

## Setup

Every recipe below assumes you have an OLAP database produced by
`quickq refresh`, plus the analytical libraries imported once:

```python
import duckdb
import pandas as pd
import altair as alt

con = duckdb.connect("analytics.duckdb", read_only=True)

def q(sql: str) -> pd.DataFrame:
    return con.execute(sql).df()
```

If your study spans multiple questionnaires, add `AND dq.questionnaire_id = ?`
to each query's `WHERE` clause.

---

## `single_choice`

Bar chart of the option distribution.

```python
df = q("""
    SELECT opt.option_text AS choice, COUNT(*) AS n
    FROM fact_response fr
    JOIN dim_question dq        USING (question_id)
    JOIN dim_response_option opt USING (option_id)
    WHERE dq.link_id = 'q.general_health'
    GROUP BY 1
    ORDER BY MIN(opt.option_value)
""")

alt.Chart(df).mark_bar().encode(
    x=alt.X("choice:N", sort=None, title="Response"),
    y=alt.Y("n:Q",      title="Respondents"),
    tooltip=["choice", "n"],
)
```

---

## `multiple_choice`

Selection rate (% of respondents who picked each option). Distinct-session
count is the right denominator because one respondent can pick many options.

```python
df = q("""
    WITH n_resp AS (
        SELECT COUNT(DISTINCT session_id) AS total FROM fact_response
    )
    SELECT
        opt.option_text                                            AS choice,
        COUNT(DISTINCT fr.session_id)                              AS n_selected,
        ROUND(100.0 * COUNT(DISTINCT fr.session_id) / n_resp.total, 1) AS pct
    FROM fact_response fr
    JOIN dim_question dq         USING (question_id)
    JOIN dim_response_option opt USING (option_id)
    CROSS JOIN n_resp
    WHERE dq.link_id = 'q.management_strategies'
    GROUP BY 1, n_resp.total
    ORDER BY pct DESC
""")

alt.Chart(df).mark_bar().encode(
    y=alt.Y("choice:N", sort="-x", title=None),
    x=alt.X("pct:Q",    title="% of respondents"),
    tooltip=["choice", "n_selected", "pct"],
)
```

---

## `sata_other`

Same as multiple_choice for the structured options. The free-text "Other"
content arrives as rows where `option_id IS NULL` and `response_text` holds
the typed-in value.

```python
# Frequency, including the "Other" bucket
df = q("""
    SELECT
        COALESCE(opt.option_text, 'Other (free text)') AS choice,
        COUNT(*)                                       AS n
    FROM fact_response fr
    JOIN dim_question dq            USING (question_id)
    LEFT JOIN dim_response_option opt USING (option_id)
    WHERE dq.link_id = 'q.symptoms'
    GROUP BY 1
    ORDER BY n DESC
""")

# Inspect the Other free-text values for thematic coding
others = q("""
    SELECT response_text, COUNT(*) AS n
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'q.symptoms'
      AND fr.option_id IS NULL
      AND fr.response_text IS NOT NULL
    GROUP BY 1
    ORDER BY n DESC
""")
```

---

## `boolean`

Proportion positive with a Wilson 95% confidence interval.

```python
import math

df = q("""
    SELECT
        dq.link_id,
        COUNT(*)                                                 AS n,
        SUM(CASE WHEN fr.response_boolean = TRUE THEN 1 END)     AS n_true
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.question_type = 'boolean'
    GROUP BY dq.link_id
""")

def wilson_ci(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return centre - half, centre + half

df["pct_true"] = 100 * df["n_true"] / df["n"]
df[["ci_low", "ci_high"]] = df.apply(
    lambda r: pd.Series([100 * v for v in wilson_ci(r["n_true"], r["n"])]),
    axis=1,
)

alt.Chart(df).mark_errorbar().encode(
    x=alt.X("ci_low:Q", title="% true (95% CI)"),
    x2="ci_high:Q",
    y=alt.Y("link_id:N", title=None),
) + alt.Chart(df).mark_circle(size=80).encode(
    x="pct_true:Q",
    y="link_id:N",
    tooltip=["link_id", "n", "pct_true", "ci_low", "ci_high"],
)
```

`response_boolean` is a typed BOOLEAN column on `fact_response`. The OLTP
stores boolean answers as `'true'` / `'false'` strings in `response_text`;
the OLAP refresh promotes them to a real BOOLEAN at load time.

---

## `text`

Free text is rarely chart-friendly. Two useful summaries are response rate
and length distribution; the actual content typically goes to qualitative
review or downstream NLP.

```python
df = q("""
    SELECT
        LENGTH(fr.response_text) AS chars
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'q.notes'
      AND fr.response_text IS NOT NULL
      AND LENGTH(fr.response_text) > 0
""")

alt.Chart(df).mark_bar().encode(
    x=alt.X("chars:Q", bin=alt.Bin(maxbins=30), title="Response length (characters)"),
    y=alt.Y("count():Q",                          title="Responses"),
)
```

---

## `numeric`

Histogram + summary stats. The OLAP refresh pre-computes percentile and
spread metrics in `agg_numeric_stats` if you want them without rolling your own.

```python
df = q("""
    SELECT response_numeric AS value
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'q.symptom_days'
      AND fr.response_numeric IS NOT NULL
""")

alt.Chart(df).mark_bar().encode(
    x=alt.X("value:Q", bin=alt.Bin(maxbins=30), title="Symptom days in past 30"),
    y=alt.Y("count():Q",                         title="Respondents"),
)
```

---

## `date`

Time series of when answers fell. For `dateTime` questions, swap
`response_date` for `response_text` (ISO 8601) and parse upstream.

```python
df = q("""
    SELECT
        DATE_TRUNC('month', fr.response_date) AS month,
        COUNT(*)                              AS n
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'q.diagnosis_date'
      AND fr.response_date IS NOT NULL
    GROUP BY 1
    ORDER BY 1
""")

alt.Chart(df).mark_line(point=True).encode(
    x=alt.X("month:T", title="Diagnosis month"),
    y=alt.Y("n:Q",     title="Respondents"),
)
```

---

## `slider`

Identical to `numeric` for analysis purposes. The slider's range and labels
matter for rendering, not aggregation. A density chart often reads better
than a histogram for slider distributions:

```python
df = q("""
    SELECT response_numeric AS value
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'q.pain_vas'
      AND fr.response_numeric IS NOT NULL
""")

alt.Chart(df).transform_density(
    "value", as_=["value", "density"], extent=[0, 100],
).mark_area(opacity=0.6).encode(
    x=alt.X("value:Q", title="Pain VAS (0-100)"),
    y=alt.Y("density:Q", title="Density"),
)
```

---

## `likert`

A Likert is an ordered single-choice. The canonical visualization is a
diverging stacked bar centered on the neutral midpoint. The simpler form is
a colored ordinal bar:

```python
df = q("""
    SELECT
        opt.option_value::INTEGER AS score,
        opt.option_text           AS label,
        COUNT(*)                  AS n
    FROM fact_response fr
    JOIN dim_question dq         USING (question_id)
    JOIN dim_response_option opt USING (option_id)
    WHERE dq.link_id = 'q.confidence'
    GROUP BY 1, 2
    ORDER BY 1
""")
df["pct"] = 100 * df["n"] / df["n"].sum()

alt.Chart(df).mark_bar().encode(
    x=alt.X("label:N", sort=df["label"].tolist(), title=None),
    y=alt.Y("pct:Q",   title="% of respondents"),
    color=alt.Color("score:O", scale=alt.Scale(scheme="redyellowgreen"), legend=None),
    tooltip=["label", "n", alt.Tooltip("pct:Q", format=".1f")],
)
```

---

## `grid`

Heatmap with rows = grid rows and columns = grid columns. The OLAP fact
table carries `grid_row_id` / `grid_column_id`; resolve their text labels
either by joining the OLTP (see [Query Patterns](query-patterns.md#grid))
or by pre-denormalizing them at notebook export time (the bundled demo
notebook does the latter).

```python
df = q("""
    SELECT
        grid_row_text                AS area,
        grid_column_text             AS severity,
        grid_column_value::INTEGER   AS severity_value,
        COUNT(*)                     AS n
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'q.daily_impact'
    GROUP BY 1, 2, 3
""")

severity_order = ["Not at all", "A little", "Moderately", "Quite a bit", "Extremely"]

alt.Chart(df).mark_rect().encode(
    x=alt.X("severity:N", sort=severity_order, title=None),
    y=alt.Y("area:N",      title=None),
    color=alt.Color("n:Q", scale=alt.Scale(scheme="blues"), title="Respondents"),
    tooltip=["area", "severity", "n"],
)
```

---

## `ranked`

`response_numeric` holds rank position (1 = first choice). Mean rank is the
natural summary; lower values indicate more frequent prioritization.

```python
df = q("""
    SELECT
        opt.option_text                       AS priority,
        ROUND(AVG(fr.response_numeric), 2)    AS mean_rank,
        COUNT(*)                              AS n
    FROM fact_response fr
    JOIN dim_question dq         USING (question_id)
    JOIN dim_response_option opt USING (option_id)
    WHERE dq.link_id = 'q.priorities'
    GROUP BY 1
    ORDER BY mean_rank
""")

alt.Chart(df).mark_bar().encode(
    y=alt.Y("priority:N", sort="x", title=None),
    x=alt.X("mean_rank:Q", scale=alt.Scale(domain=[1, df["mean_rank"].max() + 0.5]),
            title="Mean rank (1 = most important)"),
    tooltip=["priority", "mean_rank", "n"],
)
```

---

## `repeating_group`

Children of a repeating group carry a `repeat_index` (0-based instance
number). The natural unit of analysis is `(session_id, repeat_index)` — one
loop instance per row. Pivot to one row per instance, then summarize across
or within instances as needed.

```python
# One row per (respondent, visit) with the per-visit numeric metric
df = q("""
    SELECT
        fr.respondent_id,
        fr.repeat_index   AS visit,
        MAX(CASE WHEN dq.link_id = 'visits.week'
                 THEN fr.response_numeric END) AS gestational_week
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id LIKE 'visits.%'
    GROUP BY 1, 2
""")

# Distribution of visit count per respondent
visit_count = df.groupby("respondent_id")["visit"].max().reset_index(name="last_visit_idx")
visit_count["n_visits"] = visit_count["last_visit_idx"] + 1

alt.Chart(visit_count).mark_bar().encode(
    x=alt.X("n_visits:O", title="Visits recorded"),
    y=alt.Y("count():Q",  title="Respondents"),
)
```

---

## Skip patterns

Two questions about a conditional question:

1. **Did the right people see it?** Compare the count of answers against the
   count of respondents whose trigger answer met the show-when rule.
2. **What does the answer look like for the people who saw it?** Standard
   per-type recipe, restricted to the conditional sub-cohort.

```python
# Integrity check: every "yes" to the trigger should produce one date answer
df = q("""
    SELECT
        SUM(CASE WHEN dq.link_id = 'q.has_chronic'
                  AND fr.response_boolean = TRUE  THEN 1 END) AS triggered,
        SUM(CASE WHEN dq.link_id = 'q.has_chronic'
                  AND fr.response_boolean = FALSE THEN 1 END) AS suppressed,
        SUM(CASE WHEN dq.link_id = 'q.diagnosis_date'
                                                  THEN 1 END) AS conditional_answers
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
""")
assert df["triggered"][0] == df["conditional_answers"][0], "Skip-logic leak"

# Conditional analysis: only the triggered sub-cohort
df = q("""
    SELECT fr.response_date AS diagnosis_date
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'q.diagnosis_date'
      AND fr.session_id IN (
          SELECT fr2.session_id FROM fact_response fr2
          JOIN dim_question dq2 USING (question_id)
          WHERE dq2.link_id = 'q.has_chronic'
            AND fr2.response_boolean = TRUE
      )
""")
```

The `EXISTS` / `IN (subquery)` pattern is the workhorse for skip-aware
denominators. If you cohort the same way often, materialize a session-level
flag table once and join against it.

---

## Cross-question comparisons

Two questions, one cohort. Pivot one row per session with both answers.

```python
df = q("""
    SELECT
        fr.session_id,
        MAX(CASE WHEN dq.link_id = 'q.confidence'
                 THEN fr.response_numeric END) AS confidence,
        MAX(CASE WHEN dq.link_id = 'q.symptom_days'
                 THEN fr.response_numeric END) AS symptom_days
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id IN ('q.confidence', 'q.symptom_days')
    GROUP BY fr.session_id
""")

alt.Chart(df).mark_circle(opacity=0.5).encode(
    x="symptom_days:Q",
    y="confidence:Q",
    tooltip=list(df.columns),
)
```

For instrument-level scores (e.g. PHQ-9), don't recompute — use
`agg_respondent_scores` which is materialized by `quickq refresh` from the
scoring rule defined in the YAML. See
[Analytics & Data Model](../analytics.md) for the score table schema.
