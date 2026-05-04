# Analysis Recipes (R)

Per-question-type recipes for going from `fact_response` to a chart in R.
The SQL is the same as in the [Python recipes](analysis-recipes.md) and is
canonical in [Query Patterns by Question Type](query-patterns.md). This page
swaps the chart layer for [`ggplot2`](https://ggplot2.tidyverse.org/) and
the connection layer for the [`duckdb`](https://duckdb.org/docs/api/r) R
package.

`quickq` itself is a Python CLI, but its OLAP output is a DuckDB file that R
can query directly with no Python in the loop.

---

## Setup: get a database to query

Every recipe below assumes a DuckDB OLAP file (`analytics.duckdb`) produced
by `quickq refresh`. Two ways to get one:

### Option A: use the bundled demo

Run these once at the shell to produce a 500-respondent seeded study:

```bash
quickq init study.db
quickq load examples/health_intake_demo.yaml study.db
quickq seed study.db 1 --n 500 --seed 20260503
quickq refresh study.db analytics.duckdb
```

The `link_id` values in the recipes below match the demo (e.g.
`demo.general_health`, `demo.self_management`).

### Option B: use your own study

If you have a populated `study.db`, just refresh it:

```bash
quickq refresh study.db analytics.duckdb
```

Adjust the `link_id` in each recipe to a question that exists in your
instrument. If your study spans multiple questionnaires, add
`AND dq.questionnaire_id = ?` to each query's `WHERE` clause.

### R session setup

```r
library(duckdb)
library(DBI)
library(ggplot2)

con <- dbConnect(duckdb::duckdb(), "analytics.duckdb", read_only = TRUE)

q <- function(sql) dbGetQuery(con, sql)
```

When you're done: `dbDisconnect(con, shutdown = TRUE)`.

---

## `single_choice`

Bar chart of the option distribution.

```r
df <- q("
    SELECT opt.option_text AS choice, MIN(opt.option_value) AS sort_key, COUNT(*) AS n
    FROM fact_response fr
    JOIN dim_question dq         USING (question_id)
    JOIN dim_response_option opt USING (option_id)
    WHERE dq.link_id = 'demo.general_health'
    GROUP BY 1
    ORDER BY sort_key
")
df$choice <- factor(df$choice, levels = df$choice)

ggplot(df, aes(x = choice, y = n)) +
    geom_col() +
    labs(x = "Response", y = "Respondents")
```

---

## `multiple_choice`

Selection rate (% of respondents who picked each option). Distinct-session
count is the right denominator because one respondent can pick many options.

```r
df <- q("
    WITH n_resp AS (
        SELECT COUNT(DISTINCT session_id) AS total FROM fact_response
    )
    SELECT
        opt.option_text                                                 AS choice,
        COUNT(DISTINCT fr.session_id)                                   AS n_selected,
        ROUND(100.0 * COUNT(DISTINCT fr.session_id) / n_resp.total, 1)  AS pct
    FROM fact_response fr
    JOIN dim_question dq         USING (question_id)
    JOIN dim_response_option opt USING (option_id)
    CROSS JOIN n_resp
    WHERE dq.link_id = 'demo.management_strategies'
    GROUP BY 1, n_resp.total
    ORDER BY pct DESC
")

ggplot(df, aes(x = pct, y = reorder(choice, pct))) +
    geom_col() +
    labs(x = "% of respondents", y = NULL)
```

---

## `sata_other`

Same as multiple_choice for the structured options. The free-text "Other"
content arrives as rows where `option_id IS NULL` and `response_text` holds
the typed-in value.

```r
# Frequency, including the "Other" bucket
df <- q("
    SELECT
        COALESCE(opt.option_text, 'Other (free text)') AS choice,
        COUNT(*)                                       AS n
    FROM fact_response fr
    JOIN dim_question dq              USING (question_id)
    LEFT JOIN dim_response_option opt USING (option_id)
    WHERE dq.link_id = 'demo.current_symptoms'
    GROUP BY 1
    ORDER BY n DESC
")

# Inspect the Other free-text values for thematic coding
others <- q("
    SELECT response_text, COUNT(*) AS n
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'demo.current_symptoms'
      AND fr.option_id IS NULL
      AND fr.response_text IS NOT NULL
    GROUP BY 1
    ORDER BY n DESC
")
```

---

## `boolean`

Proportion positive with a Wilson 95% confidence interval.

```r
df <- q("
    SELECT
        dq.link_id,
        COUNT(*)                                              AS n,
        SUM(CASE WHEN fr.response_boolean = TRUE THEN 1 END)  AS n_true
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.question_type = 'boolean'
    GROUP BY dq.link_id
")

wilson_ci <- function(k, n, z = 1.96) {
    if (n == 0) return(c(NA_real_, NA_real_))
    p     <- k / n
    denom <- 1 + z^2 / n
    centre <- (p + z^2 / (2 * n)) / denom
    half   <- z * sqrt(p * (1 - p) / n + z^2 / (4 * n^2)) / denom
    c(centre - half, centre + half)
}

ci <- t(mapply(wilson_ci, df$n_true, df$n))
df$pct_true <- 100 * df$n_true / df$n
df$ci_low   <- 100 * ci[, 1]
df$ci_high  <- 100 * ci[, 2]

ggplot(df, aes(x = pct_true, y = link_id)) +
    geom_errorbarh(aes(xmin = ci_low, xmax = ci_high), height = 0.2) +
    geom_point(size = 3) +
    labs(x = "% true (95% CI)", y = NULL)
```

`response_boolean` is a typed BOOLEAN column on `fact_response`. The OLTP
stores boolean answers as `'true'` / `'false'` strings in `response_text`;
the OLAP refresh promotes them to a real BOOLEAN at load time.

---

## `text`

Free text is rarely chart-friendly. Two useful summaries are response rate
and length distribution; the actual content typically goes to qualitative
review or downstream NLP.

```r
df <- q("
    SELECT LENGTH(fr.response_text) AS chars
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'demo.additional_notes'
      AND fr.response_text IS NOT NULL
      AND LENGTH(fr.response_text) > 0
")

ggplot(df, aes(x = chars)) +
    geom_histogram(bins = 30) +
    labs(x = "Response length (characters)", y = "Responses")
```

---

## `numeric`

Histogram. The OLAP refresh pre-computes percentile and spread metrics in
`agg_numeric_stats` if you want them without rolling your own.

```r
df <- q("
    SELECT response_numeric AS value
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'demo.symptom_days'
      AND fr.response_numeric IS NOT NULL
")

ggplot(df, aes(x = value)) +
    geom_histogram(bins = 30) +
    labs(x = "Symptom days in past 30", y = "Respondents")
```

---

## `date`

Time series of when answers fell. For `dateTime` questions, swap
`response_date` for `response_text` (ISO 8601) and parse upstream.

```r
df <- q("
    SELECT
        DATE_TRUNC('month', fr.response_date) AS month,
        COUNT(*)                              AS n
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'demo.diagnosis_date'
      AND fr.response_date IS NOT NULL
    GROUP BY 1
    ORDER BY 1
")
df$month <- as.Date(df$month)

ggplot(df, aes(x = month, y = n)) +
    geom_line() +
    geom_point() +
    labs(x = "Diagnosis month", y = "Respondents")
```

---

## `slider`

Identical to `numeric` for analysis purposes. The slider's range and labels
matter for rendering, not aggregation. A density chart often reads better
than a histogram for slider distributions:

```r
df <- q("
    SELECT response_numeric AS value
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'demo.pain_vas'
      AND fr.response_numeric IS NOT NULL
")

ggplot(df, aes(x = value)) +
    geom_density(fill = "steelblue", alpha = 0.4) +
    xlim(0, 100) +
    labs(x = "Pain VAS (0-100)", y = "Density")
```

---

## `likert`

A Likert is an ordered single-choice. The canonical visualization is a
diverging stacked bar centered on the neutral midpoint. The simpler form is
a colored ordinal bar:

```r
df <- q("
    SELECT
        CAST(opt.option_value AS INTEGER) AS score,
        opt.option_text                   AS label,
        COUNT(*)                          AS n
    FROM fact_response fr
    JOIN dim_question dq         USING (question_id)
    JOIN dim_response_option opt USING (option_id)
    WHERE dq.link_id = 'demo.self_management'
    GROUP BY 1, 2
    ORDER BY 1
")
df$pct   <- 100 * df$n / sum(df$n)
df$label <- factor(df$label, levels = df$label)

ggplot(df, aes(x = label, y = pct, fill = score)) +
    geom_col() +
    scale_fill_distiller(palette = "RdYlGn", direction = 1, guide = "none") +
    labs(x = NULL, y = "% of respondents")
```

---

## `grid`

Heatmap with rows = grid rows and columns = grid columns. The OLAP fact
table carries `grid_row_id` / `grid_column_id`; the row/column text labels
live in the OLTP. Attach the OLTP read-only and join in one query:

```r
dbExecute(con, "ATTACH 'study.db' AS oltp (TYPE sqlite, READ_ONLY)")

df <- q("
    SELECT
        gr.row_text                    AS area,
        gc.column_text                 AS severity,
        CAST(gc.column_value AS INTEGER) AS severity_value,
        COUNT(*)                       AS n
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    JOIN oltp.grid_row    gr ON fr.grid_row_id    = gr.row_id
    JOIN oltp.grid_column gc ON fr.grid_column_id = gc.column_id
    WHERE dq.link_id = 'demo.daily_impact'
    GROUP BY 1, 2, 3
")
severity_order <- c("Not at all", "A little", "Moderately", "Quite a bit", "Extremely")
df$severity <- factor(df$severity, levels = severity_order)

ggplot(df, aes(x = severity, y = area, fill = n)) +
    geom_tile() +
    scale_fill_distiller(palette = "Blues", direction = 1) +
    labs(x = NULL, y = NULL, fill = "Respondents")
```

---

## `ranked`

`response_numeric` holds rank position (1 = first choice). Mean rank is the
natural summary; lower values indicate more frequent prioritization.

```r
df <- q("
    SELECT
        opt.option_text                       AS priority,
        ROUND(AVG(fr.response_numeric), 2)    AS mean_rank,
        COUNT(*)                              AS n
    FROM fact_response fr
    JOIN dim_question dq         USING (question_id)
    JOIN dim_response_option opt USING (option_id)
    WHERE dq.link_id = 'demo.care_priorities'
    GROUP BY 1
    ORDER BY mean_rank
")

ggplot(df, aes(x = mean_rank, y = reorder(priority, -mean_rank))) +
    geom_col() +
    xlim(1, max(df$mean_rank) + 0.5) +
    labs(x = "Mean rank (1 = most important)", y = NULL)
```

---

## `repeating_group`

Children of a repeating group carry a `repeat_index` (0-based instance
number). The natural unit of analysis is `(session_id, repeat_index)` — one
loop instance per row. Pivot to one row per instance, then summarize across
or within instances as needed.

The bundled demo doesn't include a repeating group, so the snippet below
uses placeholder `visits.*` link_ids. Substitute your own.

```r
df <- q("
    SELECT
        fr.respondent_id,
        fr.repeat_index   AS visit,
        MAX(CASE WHEN dq.link_id = 'visits.week'
                 THEN fr.response_numeric END) AS gestational_week
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id LIKE 'visits.%'
    GROUP BY 1, 2
")

# Distribution of visit count per respondent
visit_count <- aggregate(visit ~ respondent_id, df, max)
visit_count$n_visits <- visit_count$visit + 1

ggplot(visit_count, aes(x = factor(n_visits))) +
    geom_bar() +
    labs(x = "Visits recorded", y = "Respondents")
```

---

## Skip patterns

Two questions about a conditional question:

1. **Did the right people see it?** Compare the count of answers against the
   count of respondents whose trigger answer met the show-when rule.
2. **What does the answer look like for the people who saw it?** Standard
   per-type recipe, restricted to the conditional sub-cohort.

```r
# Integrity check: every "yes" to the trigger should produce one date answer
df <- q("
    SELECT
        SUM(CASE WHEN dq.link_id = 'demo.has_chronic_condition'
                  AND fr.response_boolean = TRUE  THEN 1 END) AS triggered,
        SUM(CASE WHEN dq.link_id = 'demo.has_chronic_condition'
                  AND fr.response_boolean = FALSE THEN 1 END) AS suppressed,
        SUM(CASE WHEN dq.link_id = 'demo.diagnosis_date'
                                                  THEN 1 END) AS conditional_answers
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
")
stopifnot(df$triggered == df$conditional_answers)

# Conditional analysis: only the triggered sub-cohort
df <- q("
    SELECT fr.response_date AS diagnosis_date
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id = 'demo.diagnosis_date'
      AND fr.session_id IN (
          SELECT fr2.session_id FROM fact_response fr2
          JOIN dim_question dq2 USING (question_id)
          WHERE dq2.link_id = 'demo.has_chronic_condition'
            AND fr2.response_boolean = TRUE
      )
")
```

The `EXISTS` / `IN (subquery)` pattern is the workhorse for skip-aware
denominators. If you cohort the same way often, materialize a session-level
flag table once and join against it.

---

## Cross-question comparisons

Two questions, one cohort. Pivot one row per session with both answers.

```r
df <- q("
    SELECT
        fr.session_id,
        MAX(CASE WHEN dq.link_id = 'demo.self_management'
                 THEN fr.response_numeric END) AS confidence,
        MAX(CASE WHEN dq.link_id = 'demo.symptom_days'
                 THEN fr.response_numeric END) AS symptom_days
    FROM fact_response fr
    JOIN dim_question dq USING (question_id)
    WHERE dq.link_id IN ('demo.self_management', 'demo.symptom_days')
    GROUP BY fr.session_id
")

ggplot(df, aes(x = symptom_days, y = confidence)) +
    geom_point(alpha = 0.5) +
    labs(x = "Symptom days (past 30)", y = "Self-management confidence (1-5)")
```

For instrument-level scores (e.g. PHQ-9), don't recompute — use
`agg_respondent_scores` which is materialized by `quickq refresh` from the
scoring rule defined in the YAML. See
[Analytics & Data Model](../analytics.md) for the score table schema.
