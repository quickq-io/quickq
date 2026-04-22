"""
Study administration helpers.

Covers the operational lifecycle of a live study:
  - Question status changes within a questionnaire (deprecate, suspend, reactivate)
  - Errata logging for delivery bugs, corrections, IRB actions, and general notes
  - Data dictionary assembly
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------
# Question lifecycle within a questionnaire
# ------------------------------------------------------------------

def _set_qq_status(
    conn: sqlite3.Connection,
    qq_id: int,
    status: str,
    reason: str | None,
    changed_at: str | None,
) -> None:
    conn.execute(
        """
        UPDATE questionnaire_question
           SET status = ?, status_changed_at = ?, status_notes = ?
         WHERE qq_id = ?
        """,
        (status, changed_at or _now(), reason, qq_id),
    )
    if conn.execute("SELECT changes()").fetchone()[0] == 0:
        raise ValueError(f"questionnaire_question qq_id={qq_id} not found")


def deprecate_questionnaire_question(
    conn: sqlite3.Connection,
    qq_id: int,
    reason: str,
    changed_at: str | None = None,
) -> None:
    """
    Permanently retire a question from a questionnaire.

    Historical responses collected before changed_at remain valid and queryable.
    New sessions after changed_at should not present this question.
    Use suspend() for a temporary hold.
    """
    _set_qq_status(conn, qq_id, "deprecated", reason, changed_at)


def suspend_questionnaire_question(
    conn: sqlite3.Connection,
    qq_id: int,
    reason: str,
    changed_at: str | None = None,
) -> None:
    """
    Temporarily hold a question (e.g. pending IRB review or bug fix).
    Can be reactivated with reactivate_questionnaire_question().
    """
    _set_qq_status(conn, qq_id, "suspended", reason, changed_at)


def reactivate_questionnaire_question(
    conn: sqlite3.Connection,
    qq_id: int,
    reason: str | None = None,
) -> None:
    """Restore a suspended or deprecated question to active status."""
    _set_qq_status(conn, qq_id, "active", reason, None)


# ------------------------------------------------------------------
# Errata log
# ------------------------------------------------------------------

def log_errata(
    conn: sqlite3.Connection,
    title: str,
    description: str,
    event_type: str,
    severity: str = "minor",
    study_id: int | None = None,
    questionnaire_id: int | None = None,
    question_id: int | None = None,
    affects_session_from: int | None = None,
    affects_session_to: int | None = None,
    affects_date_from: str | None = None,
    affects_date_to: str | None = None,
    analyst_guidance: str | None = None,
    reported_by: str | None = None,
) -> int:
    """
    Record an errata entry for a study. Returns the new errata_id.

    event_type: delivery_bug | question_error | deprecation | correction |
                irb_action | note
    severity:   critical | major | minor | informational
    """
    conn.execute(
        """
        INSERT INTO study_errata_log (
            study_id, questionnaire_id, question_id,
            event_type, severity, title, description,
            affects_session_from, affects_session_to,
            affects_date_from, affects_date_to,
            analyst_guidance, reported_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            study_id, questionnaire_id, question_id,
            event_type, severity, title, description,
            affects_session_from, affects_session_to,
            affects_date_from, affects_date_to,
            analyst_guidance, reported_by,
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def acknowledge_errata(
    conn: sqlite3.Connection,
    errata_id: int,
    acknowledged_by: str | None = None,
) -> None:
    conn.execute(
        "UPDATE study_errata_log SET status='acknowledged', resolved_by=? WHERE errata_id=?",
        (acknowledged_by, errata_id),
    )


def resolve_errata(
    conn: sqlite3.Connection,
    errata_id: int,
    resolved_by: str | None = None,
    resolved_at: str | None = None,
) -> None:
    """Mark an errata entry as resolved."""
    conn.execute(
        """
        UPDATE study_errata_log
           SET status = 'resolved', resolved_by = ?, resolved_at = ?
         WHERE errata_id = ?
        """,
        (resolved_by, resolved_at or _now(), errata_id),
    )


def get_errata(
    conn: sqlite3.Connection,
    study_id: int | None = None,
    questionnaire_id: int | None = None,
    question_id: int | None = None,
    status: str | None = None,
    severity: str | None = None,
) -> list[dict]:
    """Return errata entries filtered by any combination of the above."""
    clauses: list[str] = []
    params: list = []

    if study_id is not None:
        clauses.append("study_id = ?"); params.append(study_id)
    if questionnaire_id is not None:
        clauses.append("questionnaire_id = ?"); params.append(questionnaire_id)
    if question_id is not None:
        clauses.append("question_id = ?"); params.append(question_id)
    if status is not None:
        clauses.append("status = ?"); params.append(status)
    if severity is not None:
        clauses.append("severity = ?"); params.append(severity)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM study_errata_log {where} ORDER BY reported_at DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Data dictionary
# ------------------------------------------------------------------

def data_dictionary(
    conn: sqlite3.Connection,
    questionnaire_id: int,
    include_deprecated: bool = False,
) -> list[dict]:
    """
    Assemble a data dictionary for a questionnaire.

    Returns one dict per question placement, ordered by display_order.
    Columns: order, variable, label, type, required, status, status_notes,
             source_instrument, source_item, concept, valid_values,
             respondent_note, analyst_note.
    """
    status_filter = "" if include_deprecated else "AND qq.status = 'active'"
    rows = conn.execute(
        f"""
        SELECT
            qq.display_order           AS "order",
            q.link_id                  AS variable,
            q.question_text            AS label,
            q.question_type            AS type,
            CASE qq.is_required
                WHEN 1 THEN 'yes' ELSE 'no'
            END                        AS required,
            qq.status                  AS status,
            qq.status_notes            AS status_notes,
            q.source_instrument        AS source_instrument,
            q.source_item_id           AS source_item,
            CASE WHEN c.concept_code IS NOT NULL
                 THEN c.vocabulary_id || ':' || c.concept_code
                 ELSE NULL
            END                        AS concept,
            GROUP_CONCAT(
                ro.option_value || '=' || ro.option_text, ' | '
            )                          AS valid_values,
            q.help_text                AS respondent_note,
            q.internal_note            AS analyst_note
        FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        LEFT JOIN concept c ON q.concept_id = c.concept_id
        LEFT JOIN response_option ro ON q.question_id = ro.question_id
        WHERE qq.questionnaire_id = ? {status_filter}
        GROUP BY qq.qq_id
        ORDER BY qq.display_order
        """,
        (questionnaire_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def format_data_dict_markdown(rows: list[dict], title: str = "Data Dictionary") -> str:
    """Render a data dictionary as a GitHub-flavored Markdown table."""
    if not rows:
        return f"# {title}\n\n_No active questions._\n"

    lines = [f"# {title}\n"]
    header = "| # | Variable | Label | Type | Required | Status | Concept | Valid Values | Analyst Note |"
    sep    = "|---|---|---|---|---|---|---|---|---|"
    lines += [header, sep]

    for r in rows:
        def _cell(v: object) -> str:
            if v is None:
                return ""
            return str(v).replace("|", "\\|").replace("\n", " ")

        lines.append(
            f"| {r['order']} "
            f"| `{_cell(r['variable'])}` "
            f"| {_cell(r['label'])[:80]} "
            f"| {_cell(r['type'])} "
            f"| {_cell(r['required'])} "
            f"| {_cell(r['status'])} "
            f"| {_cell(r['concept'])} "
            f"| {_cell(r['valid_values'])[:60]} "
            f"| {_cell(r['analyst_note'])} |"
        )

    return "\n".join(lines) + "\n"


def format_data_dict_csv(rows: list[dict]) -> str:
    """Render a data dictionary as CSV."""
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
