"""
Markdown report generator.

Reads from the OLAP DuckDB database and produces a human-readable Markdown
summary for a given questionnaire.

Sections:
  1. Header — name, version, URL, report date
  2. Overview — respondents, sessions, completion rate, date range
  3. Scores — per scoring rule: raw stats + category breakdown table
  4. Questions — per question: choice distribution, numeric stats, or open count
"""
from __future__ import annotations

from datetime import date

import duckdb


def generate_report(
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
) -> str:
    """
    Return a Markdown string summarising questionnaire results.
    Raises ValueError if the questionnaire is not found in dim_questionnaire.
    """
    q = _fetch_questionnaire(oconn, questionnaire_id)
    lines: list[str] = []

    _render_header(lines, q)
    _render_overview(lines, oconn, questionnaire_id)
    _render_scores(lines, oconn, questionnaire_id)
    _render_questions(lines, oconn, questionnaire_id)

    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------
# Header
# ------------------------------------------------------------------

def _render_header(lines: list[str], q: dict) -> None:
    lines += [f"# {q['name']}", "", f"**Version:** {q['version']}  "]
    if q.get("canonical_url"):
        lines.append(f"**URL:** {q['canonical_url']}  ")
    lines += [f"**Report date:** {date.today().isoformat()}  ", ""]


# ------------------------------------------------------------------
# Overview
# ------------------------------------------------------------------

def _render_overview(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
) -> None:
    row = oconn.execute(
        """
        SELECT
            COUNT(DISTINCT respondent_id)                      AS n_respondents,
            COUNT(*)                                           AS n_sessions,
            ROUND(AVG(is_complete::INTEGER) * 100, 1)         AS pct_complete,
            MIN(session_date_key)                              AS first_date,
            MAX(session_date_key)                              AS last_date
        FROM dim_session
        WHERE questionnaire_id = ?
        """,
        [questionnaire_id],
    ).fetchone()

    if row is None or row[1] == 0:
        lines += ["## Overview", "", "_No responses recorded._", ""]
        return

    n_resp, n_sess, pct, first, last = row
    date_range = (
        f"{first} – {last}" if first and last and first != last
        else str(first or "—")
    )
    lines += [
        "## Overview",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Respondents | {n_resp} |",
        f"| Sessions | {n_sess} |",
        f"| Completion rate | {pct:.1f}% |",
        f"| Date range | {date_range} |",
        "",
    ]


# ------------------------------------------------------------------
# Scores
# ------------------------------------------------------------------

def _render_scores(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
) -> None:
    rules = oconn.execute(
        """
        SELECT DISTINCT scoring_rule_id, scoring_rule_name
        FROM agg_respondent_scores
        WHERE questionnaire_id = ?
        ORDER BY scoring_rule_id
        """,
        [questionnaire_id],
    ).fetchall()

    if not rules:
        return

    lines += ["## Scores", ""]

    for rule_id, rule_name in rules:
        lines += [f"### {rule_name}", ""]

        stats = oconn.execute(
            """
            SELECT COUNT(*), ROUND(AVG(score_raw), 2), ROUND(MEDIAN(score_raw), 2),
                   MIN(score_raw), MAX(score_raw)
            FROM agg_respondent_scores
            WHERE questionnaire_id = ? AND scoring_rule_id = ?
            """,
            [questionnaire_id, rule_id],
        ).fetchone()

        if stats and stats[0]:
            n, mean, median, min_val, max_val = stats
            lines += [
                f"**n={n}** · mean {mean} · median {median} · range {min_val}–{max_val}",
                "",
            ]

        cats = oconn.execute(
            """
            SELECT COALESCE(score_category, '(uncategorised)'),
                   COUNT(*),
                   ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)
            FROM agg_respondent_scores
            WHERE questionnaire_id = ? AND scoring_rule_id = ?
            GROUP BY score_category
            ORDER BY MIN(score_raw)
            """,
            [questionnaire_id, rule_id],
        ).fetchall()

        if cats:
            lines += ["| Category | n | % |", "|----------|---|---|"]
            for cat, n, pct in cats:
                lines.append(f"| {cat} | {n} | {pct:.1f}% |")
            lines.append("")


# ------------------------------------------------------------------
# Questions
# ------------------------------------------------------------------

def _render_questions(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
) -> None:
    # Pull each answered question with its display order plus any parent
    # repeating_group context (parent_text and parent_qq_id). The questionnaire
    # structure (parent_qq_id, display_order) lives in the attached OLTP.
    questions = oconn.execute(
        """
        SELECT DISTINCT
            dq.question_id, dq.link_id, dq.question_text, dq.question_type,
            qq.display_order,
            qq.parent_qq_id,
            parent_q.question_text AS parent_text
        FROM fact_response fr
        JOIN dim_question dq USING (question_id)
        JOIN oltp.questionnaire_question qq
             ON qq.question_id = dq.question_id
            AND qq.questionnaire_id = ?
        LEFT JOIN oltp.questionnaire_question parent_qq
             ON parent_qq.qq_id = qq.parent_qq_id
        LEFT JOIN oltp.question parent_q
             ON parent_q.question_id = parent_qq.question_id
        WHERE fr.questionnaire_id = ?
        ORDER BY qq.display_order
        """,
        [questionnaire_id, questionnaire_id],
    ).fetchall()

    if not questions:
        return

    lines += ["## Questions", ""]

    seen_groups: set[int] = set()  # parent_qq_ids whose header has already been rendered

    for (question_id, link_id, question_text, question_type,
         display_order, parent_qq_id, parent_text) in questions:

        # If this question is a child of a repeating_group, ensure the group
        # header is emitted once (with per-respondent instance summary), then
        # render the child as a nested heading prefixed with the group label.
        if parent_qq_id is not None:
            if parent_qq_id not in seen_groups:
                _render_repeating_group_header(
                    lines, oconn, questionnaire_id, parent_qq_id, parent_text or ""
                )
                seen_groups.add(parent_qq_id)
            lines += [f"#### {parent_text} / {question_text}", ""]
        else:
            lines += [f"### {question_text}", ""]

        if question_type in ("numeric", "slider"):
            _render_numeric_question(lines, oconn, questionnaire_id, question_id)
        elif question_type == "boolean":
            _render_boolean_question(lines, oconn, questionnaire_id, question_id)
        elif question_type in ("text", "date", "datetime"):
            _render_open_question(lines, oconn, questionnaire_id, question_id, question_type)
        elif question_type == "grid":
            _render_grid_question(lines, oconn, questionnaire_id, question_id)
        elif question_type == "repeating_group":
            # Parent group rows themselves have no responses; their children
            # are rendered above. Skip silently if one slipped into the loop.
            continue
        else:
            _render_choice_question(lines, oconn, questionnaire_id, question_id)


def _render_repeating_group_header(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
    parent_qq_id: int,
    parent_text: str,
) -> None:
    """Emit a section header for a repeating_group with per-instance summary.

    Reports total sessions that touched the group, total instance count
    (max repeat_index + 1, summed across sessions), and mean instances per
    respondent.
    """
    # Count instances per session for this group (max repeat_index + 1).
    row = oconn.execute(
        """
        WITH per_session AS (
            SELECT fr.session_id,
                   MAX(fr.repeat_index) + 1 AS n_instances
            FROM   fact_response fr
            JOIN   oltp.questionnaire_question qq USING (qq_id)
            WHERE  fr.questionnaire_id = ?
              AND  qq.parent_qq_id    = ?
              AND  fr.repeat_index IS NOT NULL
            GROUP BY fr.session_id
        )
        SELECT COUNT(*)        AS n_sessions,
               SUM(n_instances) AS total_instances,
               ROUND(AVG(n_instances), 1) AS avg_instances
        FROM per_session
        """,
        [questionnaire_id, parent_qq_id],
    ).fetchone()
    n_sessions, total_instances, avg_instances = row or (0, 0, 0.0)

    lines += [f"### {parent_text}", ""]
    if n_sessions:
        lines += [
            f"_{n_sessions} session(s) reported a total of {int(total_instances or 0)} "
            f"instance(s); average {avg_instances or 0} per respondent._",
            "",
        ]
    else:
        lines += ["_No instances reported._", ""]


def _render_choice_question(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
    question_id: int,
) -> None:
    rows = oconn.execute(
        """
        SELECT aqd.option_value, dro.option_text, aqd.n, aqd.pct
        FROM agg_question_distribution aqd
        LEFT JOIN dim_response_option dro
               ON dro.question_id = aqd.question_id
              AND dro.option_value = aqd.option_value
        WHERE aqd.questionnaire_id = ? AND aqd.question_id = ?
        ORDER BY dro.display_order, aqd.option_value
        """,
        [questionnaire_id, question_id],
    ).fetchall()

    if not rows:
        lines += ["_No responses._", ""]
        return

    lines += ["| Response | n | % |", "|----------|---|---|"]
    for val, label, n, pct in rows:
        lines.append(f"| {label or val} | {n} | {pct:.1f}% |")
    lines.append("")


def _render_boolean_question(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
    question_id: int,
) -> None:
    rows = oconn.execute(
        """
        SELECT response_text, COUNT(*) AS n,
               ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
        FROM fact_response
        WHERE questionnaire_id = ? AND question_id = ?
          AND response_text IS NOT NULL
        GROUP BY response_text
        ORDER BY response_text DESC
        """,
        [questionnaire_id, question_id],
    ).fetchall()

    if not rows:
        lines += ["_No responses._", ""]
        return

    lines += ["| Response | n | % |", "|----------|---|---|"]
    for val, n, pct in rows:
        lines.append(f"| {val.capitalize()} | {n} | {pct:.1f}% |")
    lines.append("")


def _render_numeric_question(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
    question_id: int,
) -> None:
    row = oconn.execute(
        """
        SELECT n, mean, median, std_dev, min_val, max_val, p25, p75
        FROM agg_numeric_stats
        WHERE questionnaire_id = ? AND question_id = ?
        """,
        [questionnaire_id, question_id],
    ).fetchone()

    if not row or row[0] == 0:
        lines += ["_No responses._", ""]
        return

    n, mean, median, std_dev, min_val, max_val, p25, p75 = row
    lines += [
        "| Statistic | Value |",
        "|-----------|-------|",
        f"| n | {n} |",
        f"| Mean | {_fmt(mean)} |",
        f"| Median | {_fmt(median)} |",
        f"| Std dev | {_fmt(std_dev)} |",
        f"| Range | {_fmt(min_val)} – {_fmt(max_val)} |",
        f"| IQR (p25–p75) | {_fmt(p25)} – {_fmt(p75)} |",
        "",
    ]


def _render_open_question(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
    question_id: int,
    question_type: str,
) -> None:
    n = oconn.execute(
        """
        SELECT COUNT(*) FROM fact_response
        WHERE questionnaire_id = ? AND question_id = ?
          AND (response_text IS NOT NULL OR response_date IS NOT NULL)
        """,
        [questionnaire_id, question_id],
    ).fetchone()[0]
    lines += [f"_{n} response(s) — open-ended {question_type}._", ""]


def _render_grid_question(
    lines: list[str],
    oconn: duckdb.DuckDBPyConnection,
    questionnaire_id: int,
    question_id: int,
) -> None:
    """Render a grid as a row × column distribution table."""
    rows = oconn.execute(
        """
        SELECT gr.row_text, gc.column_text, COUNT(*) AS n
        FROM fact_response fr
        JOIN oltp.grid_row   gr ON gr.row_id    = fr.grid_row_id
        JOIN oltp.grid_column gc ON gc.column_id = fr.grid_column_id
        WHERE fr.questionnaire_id = ? AND fr.question_id = ?
        GROUP BY gr.row_id, gr.row_text, gc.display_order, gc.column_text
        ORDER BY gr.row_id, gc.display_order
        """,
        [questionnaire_id, question_id],
    ).fetchall()

    if not rows:
        lines += ["_No responses._", ""]
        return

    # Pivot: collect unique columns
    col_texts: list[str] = []
    seen: set[str] = set()
    for _, col_text, _ in rows:
        if col_text not in seen:
            col_texts.append(col_text)
            seen.add(col_text)

    # Build per-row totals for pct
    row_totals: dict[str, int] = {}
    for row_text, _, n in rows:
        row_totals[row_text] = row_totals.get(row_text, 0) + n

    header = "| |" + "".join(f" {c} |" for c in col_texts)
    sep = "|---|" + "".join("---|" for _ in col_texts)
    lines += [header, sep]

    # Group counts by (row_text, col_text)
    counts: dict[tuple[str, str], int] = {}
    for row_text, col_text, n in rows:
        counts[(row_text, col_text)] = n

    current_row = None
    for row_text, col_text, _ in rows:
        if row_text != current_row:
            current_row = row_text
            total = row_totals[row_text]
            cells = ""
            for c in col_texts:
                n = counts.get((row_text, c), 0)
                pct = round(100 * n / total) if total else 0
                cells += f" {n} ({pct}%) |"
            lines.append(f"| **{row_text}** |{cells}")

    lines += [""]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fetch_questionnaire(oconn: duckdb.DuckDBPyConnection, questionnaire_id: int) -> dict:
    row = oconn.execute(
        "SELECT questionnaire_id, name, version, canonical_url, fhir_status "
        "FROM dim_questionnaire WHERE questionnaire_id = ?",
        [questionnaire_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"Questionnaire {questionnaire_id} not found in OLAP")
    return dict(zip(
        ["questionnaire_id", "name", "version", "canonical_url", "fhir_status"], row
    ))


def _fmt(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:.2f}"
