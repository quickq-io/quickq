"""
Produce a pseudonymized copy of a quickq OLTP database for data sharing.

PHI handling:
  - respondent.external_id      → replaced with a stable HMAC token
  - person_map                  → cleared (OMOP identity bridge)
  - response_session.interviewer_id → set to NULL (staff identifier)
  - response.response_text (free-text question types) → left in place, flagged in warnings
  - study.principal_investigator → left in place, flagged in warnings

The HMAC key is returned in the result and is NOT stored in the output database.
Keep it if you need to reverse the pseudonymization. Destroy it to convert
the output into a fully anonymous dataset.

After pseudonymizing, regenerate the OLAP:
    quickq refresh study_anon.db analytics_anon.duckdb
"""
from __future__ import annotations

import hashlib
import hmac
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .schema import open_oltp


@dataclass
class PseudonymizeResult:
    source: str
    output: str
    respondents_pseudonymized: int = 0
    key: bytes = field(default_factory=bytes)
    warnings: list[str] = field(default_factory=list)


def pseudonymize(
    source_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
    key: bytes | None = None,
) -> PseudonymizeResult:
    """
    Write a pseudonymized copy of source_path to output_path.

    If key is not provided, a random 32-byte key is generated. The same key
    always produces the same token for a given external_id, so longitudinal
    analysis within the pseudonymized dataset is preserved.

    Raises FileExistsError if output_path exists and overwrite=False.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if output_path.exists():
        if not overwrite:
            raise FileExistsError(
                f"{output_path} already exists. Pass overwrite=True to replace it."
            )
        output_path.unlink()

    if key is None:
        key = os.urandom(32)

    result = PseudonymizeResult(
        source=str(source_path),
        output=str(output_path),
        key=key,
    )

    shutil.copy2(source_path, output_path)

    conn = open_oltp(output_path)
    try:
        _pseudonymize_respondents(conn, key, result)
        _clear_person_map(conn)
        _clear_interviewer_ids(conn)
        _warn_free_text(conn, result)
        _warn_institutional_fields(conn, result)
        conn.commit()
    except Exception:
        conn.close()
        output_path.unlink(missing_ok=True)
        raise

    conn.close()
    return result


# ---------------------------------------------------------------------------
# Internal steps
# ---------------------------------------------------------------------------

def _token(key: bytes, external_id: str) -> str:
    """Stable 32-hex-char HMAC token for an external_id."""
    return hmac.new(key, external_id.encode(), hashlib.sha256).hexdigest()[:32]


def _pseudonymize_respondents(conn, key: bytes, result: PseudonymizeResult) -> None:
    rows = conn.execute(
        "SELECT respondent_id, external_id FROM respondent WHERE external_id IS NOT NULL"
    ).fetchall()
    for row in rows:
        tok = _token(key, row["external_id"])
        conn.execute(
            "UPDATE respondent SET external_id = ? WHERE respondent_id = ?",
            (tok, row["respondent_id"]),
        )
        result.respondents_pseudonymized += 1


def _clear_person_map(conn) -> None:
    conn.execute("DELETE FROM person_map")


def _clear_interviewer_ids(conn) -> None:
    conn.execute("UPDATE response_session SET interviewer_id = NULL")


def _warn_free_text(conn, result: PseudonymizeResult) -> None:
    """Flag questions whose responses land in response_text — may contain PHI."""
    rows = conn.execute(
        """
        SELECT q.link_id, q.question_type, COUNT(r.response_id) AS n
        FROM question q
        JOIN questionnaire_question qq ON qq.question_id = q.question_id
        JOIN response r ON r.qq_id = qq.qq_id
        WHERE q.question_type IN ('text', 'sata_other')
          AND r.response_text IS NOT NULL
          AND LENGTH(r.response_text) > 0
        GROUP BY q.link_id, q.question_type
        ORDER BY n DESC
        """
    ).fetchall()
    for row in rows:
        result.warnings.append(
            f"Free-text responses not redacted: link_id={row['link_id']!r} "
            f"({row['question_type']}, {row['n']} non-empty responses). "
            "Review for PHI before sharing."
        )


def _warn_institutional_fields(conn, result: PseudonymizeResult) -> None:
    """Flag study-level fields that may identify the institution or PI."""
    rows = conn.execute(
        "SELECT name, principal_investigator, irb_number FROM study"
    ).fetchall()
    for row in rows:
        parts = []
        if row["principal_investigator"]:
            parts.append(f"principal_investigator={row['principal_investigator']!r}")
        if row["irb_number"]:
            parts.append(f"irb_number={row['irb_number']!r}")
        if parts:
            result.warnings.append(
                f"Study {row['name']!r} contains institutional fields "
                f"({', '.join(parts)}) that were not redacted. "
                "Remove manually if not appropriate to share."
            )
