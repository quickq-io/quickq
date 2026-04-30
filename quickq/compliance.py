"""
Compliance utilities: participant deletion and study metadata management.

delete_respondent — hard-deletes all data for a participant identified by
    external_id. Satisfies GDPR right to erasure and consent withdrawal.
    Cascades through response → response_session → respondent in FK order.
    Returns a DeleteResult reporting what was removed.

set_study_metadata — sets regulatory and FAIR metadata fields on a study row
    (license, protocol_url, doi, population, geographic_scope). Purely additive;
    fields left as None are not touched. Returns the updated field dict.
"""
from __future__ import annotations

import getpass
import json
import sqlite3
from dataclasses import dataclass


def record_audit_event(
    conn: sqlite3.Connection,
    operation: str,
    *,
    study_id: int | None = None,
    details: dict | None = None,
) -> None:
    """
    Append an entry to tool_audit_log. Called by CLI commands after
    successful operations. Silently skips if the table does not exist
    (pre-migration databases).
    """
    try:
        performed_by = getpass.getuser()
    except Exception:
        performed_by = None

    try:
        conn.execute(
            "INSERT INTO tool_audit_log (study_id, operation, performed_by, details) VALUES (?, ?, ?, ?)",
            (study_id, operation, performed_by, json.dumps(details) if details else None),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # table absent on old databases — skip silently


@dataclass
class DeleteResult:
    external_id: str
    respondent_id: int
    sessions_deleted: int
    responses_deleted: int
    flags_deleted: int


def delete_respondent(
    conn: sqlite3.Connection,
    external_id: str,
    *,
    study_id: int | None = None,
) -> DeleteResult:
    """
    Hard-delete all data for the participant identified by external_id.

    If the same external_id appears in multiple studies, study_id must be
    provided to disambiguate. Raises ValueError if the respondent is not
    found or if the ID is ambiguous and study_id is omitted.

    Deletion order respects FK constraints:
        data_quality_flag → response → admin_event → response_session
        → person_map → respondent
    """
    if study_id is not None:
        row = conn.execute(
            "SELECT respondent_id FROM respondent WHERE external_id = ? AND study_id = ?",
            (external_id, study_id),
        ).fetchone()
    else:
        rows = conn.execute(
            "SELECT respondent_id FROM respondent WHERE external_id = ?",
            (external_id,),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError(
                f"external_id {external_id!r} matches respondents in multiple studies. "
                "Provide --study-id to disambiguate."
            )
        row = rows[0] if rows else None

    if row is None:
        raise ValueError(f"No respondent found with external_id {external_id!r}.")

    respondent_id = row[0]

    session_ids = [
        r[0] for r in conn.execute(
            "SELECT session_id FROM response_session WHERE respondent_id = ?",
            (respondent_id,),
        ).fetchall()
    ]

    flags_deleted = 0
    responses_deleted = 0

    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        flags_deleted = conn.execute(
            f"SELECT COUNT(*) FROM data_quality_flag WHERE session_id IN ({placeholders})",
            session_ids,
        ).fetchone()[0]
        conn.execute(
            f"DELETE FROM data_quality_flag WHERE session_id IN ({placeholders})",
            session_ids,
        )
        responses_deleted = conn.execute(
            f"SELECT COUNT(*) FROM response WHERE session_id IN ({placeholders})",
            session_ids,
        ).fetchone()[0]
        conn.execute(
            f"DELETE FROM response WHERE session_id IN ({placeholders})",
            session_ids,
        )
        conn.execute(
            f"DELETE FROM admin_event WHERE session_id IN ({placeholders})",
            session_ids,
        )

    conn.execute("DELETE FROM admin_event WHERE respondent_id = ?", (respondent_id,))
    conn.execute("DELETE FROM response_session WHERE respondent_id = ?", (respondent_id,))
    conn.execute("DELETE FROM person_map WHERE respondent_id = ?", (respondent_id,))
    conn.execute("DELETE FROM respondent WHERE respondent_id = ?", (respondent_id,))
    conn.commit()

    return DeleteResult(
        external_id=external_id,
        respondent_id=respondent_id,
        sessions_deleted=len(session_ids),
        responses_deleted=responses_deleted,
        flags_deleted=flags_deleted,
    )


@dataclass
class WithdrawResult:
    external_id: str
    respondent_id: int
    event_id: int


def withdraw_respondent(
    conn: sqlite3.Connection,
    external_id: str,
    *,
    study_id: int | None = None,
    notes: str | None = None,
) -> WithdrawResult:
    """
    Record a withdrawal for the participant identified by external_id.

    Withdrawal means: stop collecting data from this person; retain all
    previously collected responses. This is the legally distinct operation
    from delete_respondent — IRB protocols typically state that withdrawal
    does not trigger data deletion for already-consented responses.

    Writes a 'withdrawn' event to admin_event. Future sessions can be
    blocked by checking for a withdrawn event before creating new ones.

    Raises ValueError if the respondent is not found or is ambiguous.
    """
    if study_id is not None:
        row = conn.execute(
            "SELECT respondent_id FROM respondent WHERE external_id = ? AND study_id = ?",
            (external_id, study_id),
        ).fetchone()
    else:
        rows = conn.execute(
            "SELECT respondent_id FROM respondent WHERE external_id = ?",
            (external_id,),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError(
                f"external_id {external_id!r} matches respondents in multiple studies. "
                "Provide --study-id to disambiguate."
            )
        row = rows[0] if rows else None

    if row is None:
        raise ValueError(f"No respondent found with external_id {external_id!r}.")

    respondent_id = row[0]

    existing = conn.execute(
        "SELECT event_id FROM admin_event WHERE respondent_id = ? AND event_type = 'withdrawn'",
        (respondent_id,),
    ).fetchone()
    if existing:
        raise ValueError(
            f"Respondent {external_id!r} has already been withdrawn (event_id={existing[0]})."
        )

    conn.execute(
        "INSERT INTO admin_event (study_id, respondent_id, event_type, notes) VALUES (?, ?, 'withdrawn', ?)",
        (study_id, respondent_id, notes),
    )
    event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    return WithdrawResult(
        external_id=external_id,
        respondent_id=respondent_id,
        event_id=event_id,
    )


def is_withdrawn(
    conn: sqlite3.Connection,
    respondent_id: int,
) -> bool:
    """Return True if a withdrawal event exists for this respondent."""
    row = conn.execute(
        "SELECT 1 FROM admin_event WHERE respondent_id = ? AND event_type = 'withdrawn'",
        (respondent_id,),
    ).fetchone()
    return row is not None


# Columns on the study table that set_study_metadata may update.
_STUDY_METADATA_FIELDS = frozenset({
    "description",
    "population",
    "license",
    "protocol_url",
    "doi",
    "geographic_scope",
    "data_collection_end",
    "principal_investigator",
    "irb_number",
    "start_date",
    "end_date",
})


def set_study_metadata(
    conn: sqlite3.Connection,
    study_id: int,
    **fields: str | None,
) -> dict:
    """
    Set metadata fields on a study row. Unknown field names raise ValueError.
    Fields passed as None are skipped (not set to NULL).
    Returns a dict of the fields that were actually updated.
    """
    unknown = set(fields) - _STUDY_METADATA_FIELDS
    if unknown:
        raise ValueError(f"Unknown study metadata field(s): {', '.join(sorted(unknown))}")

    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return {}

    row = conn.execute(
        "SELECT study_id FROM study WHERE study_id = ?", (study_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No study found with study_id={study_id}.")

    set_clause = ", ".join(f"{col} = ?" for col in updates)
    conn.execute(
        f"UPDATE study SET {set_clause} WHERE study_id = ?",
        [*updates.values(), study_id],
    )
    conn.commit()
    return updates
