"""Anatomy of a Health Survey: 500 Respondents, 3 Questions Worth Asking.

A self-contained marimo notebook that demonstrates three things you can do
with a `quickq` study without writing any custom analysis code:

  1. Verify a skip-logic pattern actually held
  2. Render a Likert distribution
  3. Summarize a ranked-choice question by mean rank

Sibling layout:
  examples/health_intake_demo.yaml         questionnaire definition
  examples/notebooks/public/*.parquet      seeded OLAP exports (auto-generated)

The data is regenerated automatically when the docs site is built. To
regenerate locally:

    uv run python scripts/build_notebook_data.py \\
        --yaml examples/health_intake_demo.yaml \\
        --output-dir examples/notebooks/public \\
        --n 500 --seed 20260503

Run locally:
    uv run marimo edit examples/notebooks/health_intake_demo.py

Export to a self-contained interactive HTML page:
    uv run marimo export html-wasm examples/notebooks/health_intake_demo.py \\
        -o site/notebooks/health-intake-demo
"""
import marimo

__generated_with = "0.23.4"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _intro(mo):
    mo.md(
        r"""
        # Anatomy of a Health Survey

        ### 500 respondents · 3 questions worth asking · zero custom ETL

        Below: a skip pattern, a Likert distribution, and a ranked-choice
        summary, all from one OLAP fact table. SQL plus a thin Altair layer.

        Recipes for every other question type live in the
        [analysis cookbook](https://quickq-io.github.io/quickq/reference/analysis-recipes/).
        """
    )
    return


@app.cell(hide_code=True)
def _setup():
    import io
    import urllib.request
    import marimo as mo
    import duckdb
    import pandas as pd
    import pyarrow.parquet as pq
    import altair as alt

    base = mo.notebook_location() / "public"

    def _read_parquet(loc):
        s = str(loc)
        if "://" in s:
            with urllib.request.urlopen(s) as resp:
                return pq.read_table(io.BytesIO(resp.read()))
        return pq.read_table(s)

    con = duckdb.connect(":memory:")
    for table in (
        "fact_response",
        "dim_question",
        "dim_response_option",
    ):
        arrow_table = _read_parquet(base / f"{table}.parquet")
        con.register(table, arrow_table)

    def q(sql: str) -> pd.DataFrame:
        return con.execute(sql).df()

    return alt, mo, q


@app.cell(hide_code=True)
def _section_skip(mo):
    mo.md(
        r"""
        ---
        ## 1. Did the skip logic actually hold?

        `demo.diagnosis_date` only renders when `demo.has_chronic_condition`
        is `true`. The OLAP should contain exactly one date answer per "yes"
        response and zero from the "no" branch.
        """
    )
    return


@app.cell
def _skip_check(mo, q):
    skip = q(
        """
        SELECT
            SUM(CASE WHEN dq.link_id = 'demo.has_chronic_condition'
                      AND fr.response_boolean = TRUE  THEN 1 END) AS chronic_yes,
            SUM(CASE WHEN dq.link_id = 'demo.has_chronic_condition'
                      AND fr.response_boolean = FALSE THEN 1 END) AS chronic_no,
            SUM(CASE WHEN dq.link_id = 'demo.diagnosis_date'
                                                     THEN 1 END) AS diagnosis_dates
        FROM fact_response fr
        JOIN dim_question dq USING (question_id)
        """
    )
    yes = int(skip["chronic_yes"][0])
    no = int(skip["chronic_no"][0])
    dates = int(skip["diagnosis_dates"][0])
    integrity = "OK" if dates == yes else f"MISMATCH ({dates} vs {yes})"

    skip_summary = mo.md(
        f"""
        | Branch | n |
        |---|---|
        | `chronic_condition = true`  (date question shown)  | **{yes}** |
        | `chronic_condition = false` (date question hidden) | **{no}** |
        | `diagnosis_date` answers in fact_response          | **{dates}** |

        Skip-logic integrity: **{integrity}**
        """
    )
    skip_summary
    return


@app.cell(hide_code=True)
def _section_likert(mo):
    mo.md(
        r"""
        ---
        ## 2. How confident are respondents managing their condition?

        A 5-point Likert is stored as an ordered single-choice question.
        Numeric coercion happens during refresh, so `option_value` is ready
        to sort and color.
        """
    )
    return


@app.cell
def _likert_chart(alt, q):
    likert = q(
        """
        SELECT
            opt.option_value::INTEGER AS score,
            opt.option_text           AS label,
            COUNT(*)                  AS n
        FROM fact_response fr
        JOIN dim_question dq         USING (question_id)
        JOIN dim_response_option opt USING (option_id)
        WHERE dq.link_id = 'demo.self_management'
        GROUP BY 1, 2
        ORDER BY 1
        """
    )
    likert["pct"] = likert["n"] / likert["n"].sum() * 100

    likert_chart = (
        alt.Chart(likert)
        .mark_bar()
        .encode(
            x=alt.X("label:N", sort=likert["label"].tolist(), title=None),
            y=alt.Y("pct:Q", title="% of respondents"),
            color=alt.Color(
                "score:O",
                scale=alt.Scale(scheme="redyellowgreen"),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Response"),
                alt.Tooltip("n:Q", title="Count"),
                alt.Tooltip("pct:Q", title="%", format=".1f"),
            ],
        )
        .properties(
            width=520,
            height=260,
            title="“I feel confident managing my condition day-to-day.”",
        )
    )
    likert_chart
    return


@app.cell(hide_code=True)
def _section_ranked(mo):
    mo.md(
        r"""
        ---
        ## 3. What do patients prioritize?

        Ranked questions store one row per option with `response_numeric`
        holding the rank position (1 = first choice). Mean rank is the
        natural summary; lower means more frequently prioritized.
        """
    )
    return


@app.cell
def _ranked_chart(alt, q):
    ranked = q(
        """
        SELECT
            opt.option_text                    AS priority,
            ROUND(AVG(fr.response_numeric), 2) AS mean_rank,
            COUNT(*)                           AS n
        FROM fact_response fr
        JOIN dim_question dq         USING (question_id)
        JOIN dim_response_option opt USING (option_id)
        WHERE dq.link_id = 'demo.care_priorities'
        GROUP BY 1
        ORDER BY mean_rank
        """
    )

    ranked_chart = (
        alt.Chart(ranked)
        .mark_bar()
        .encode(
            y=alt.Y("priority:N", sort="x", title=None),
            x=alt.X(
                "mean_rank:Q",
                title="Mean rank (1 = most important)",
                scale=alt.Scale(domain=[1, 5]),
            ),
            tooltip=["priority", "mean_rank", "n"],
        )
        .properties(width=520, height=220, title="Care priorities, mean rank")
    )
    ranked_chart
    return


@app.cell(hide_code=True)
def _outro(mo):
    mo.md(
        r"""
        ---
        ### What this is showing

        Three analyses, three SQL queries, one fact table. No custom ETL,
        no per-instrument pipeline, no JSON parsing. The skip-pattern
        integrity check, the ordered-categorical visualization, and the
        ranked-choice summary all pull from `fact_response` joined to a
        couple of dimensions.

        Recipes for every other question type (boolean with confidence
        intervals, grid heatmaps, repeating groups, free-text response
        rates, cross-question pivots) are in the
        [analysis cookbook](https://quickq-io.github.io/quickq/reference/analysis-recipes/).

        Notebook source:
        [`examples/notebooks/health_intake_demo.py`](https://github.com/quickq-io/quickq/blob/main/examples/notebooks/health_intake_demo.py).
        Questionnaire that generated this data:
        [`examples/health_intake_demo.yaml`](https://github.com/quickq-io/quickq/blob/main/examples/health_intake_demo.yaml).
        """
    )
    return


if __name__ == "__main__":
    app.run()
