"""
FHIR R4 QuestionnaireResponse import.

Parses a QuestionnaireResponse resource and writes it to the OLTP layer:
  - Upserts a respondent row (keyed on external_id from subject.reference)
  - Inserts a response_session row
  - Inserts one response row per answer atom

Answer mapping rules:
  valueCoding  → option_id (looked up by concept_code or option_value)
  valueBoolean → response_text = 'true' | 'false'
  valueDecimal / valueInteger → response_numeric
  valueDate    → response_date
  valueString  → response_text

Grid responses are encoded as nested items (child linkIds = parent.rN).
  Each child item produces response rows with grid_row_id + grid_column_id set.

Ranked responses carry ordinalValue extensions; the rank is stored in response_numeric.

Unknown answer types or unresolvable linkIds are written to data_quality_flag
rather than raising exceptions, keeping the import robust.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


# Expected FHIR value-key(s) per question_type. Used by the importer to
# verify that an incoming valueXxx field matches the question's declared
# type before routing it to a typed column. Mismatches produce a
# data_quality_flag with severity='error' and the response is not inserted.
#
# Empty tuple = qtype is not directly answered (handled via nested items)
# or has no enforced rule.
_EXPECTED_VALUE_KEYS: dict[str, tuple[str, ...]] = {
    "numeric":         ("valueDecimal", "valueInteger"),
    "slider":          ("valueDecimal", "valueInteger"),
    "likert":          ("valueCoding",),       # rendered as choice in FHIR
    "date":            ("valueDate",),
    "datetime":        ("valueDateTime", "valueDate"),
    "text":            ("valueString",),
    "boolean":         ("valueBoolean",),
    "single_choice":   ("valueCoding",),
    "multiple_choice": ("valueCoding",),
    "sata_other":      ("valueCoding", "valueString"),  # "Other" can be free text
    "ranked":          ("valueCoding",),
    "grid":            (),                     # composite, via nested items
    "repeating_group": (),                     # not directly answered
}


def _validate_answer_type(qtype: str, answer: dict) -> str | None:
    """Return None if the answer's value key matches the question's qtype;
    return an error message string otherwise.

    Skips validation for qtypes with no rule (grid, repeating_group, unknown).
    Skips validation when no valueXxx key is present (the existing
    "Unrecognised answer format" branch in _write_answer handles that case
    with its own warning flag).
    """
    expected = _EXPECTED_VALUE_KEYS.get(qtype, ())
    if not expected:
        return None
    present = [k for k in answer if k.startswith("value")]
    if not present:
        return None  # _write_answer's bottom branch handles "no valueXxx key"
    if any(k in expected for k in present):
        return None
    expected_str = " or ".join(expected)
    actual_str = ", ".join(present)
    return (
        f"Type mismatch: question_type={qtype!r} expects {expected_str}; "
        f"got {actual_str}"
    )


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def import_fhir_response(
    conn: sqlite3.Connection,
    resource: dict | str,
    *,
    study_id: int | None = None,
    admin_mode: str = "api",
) -> int:
    """
    Parse a FHIR QuestionnaireResponse and write to OLTP.

    Returns the new session_id.
    Raises ValueError if the referenced questionnaire is not found.
    """
    if isinstance(resource, str):
        resource = json.loads(resource)

    if resource.get("resourceType") != "QuestionnaireResponse":
        raise ValueError(
            f"Expected QuestionnaireResponse, got {resource.get('resourceType')!r}"
        )

    questionnaire_ref: str = resource.get("questionnaire", "")
    questionnaire_id = _resolve_questionnaire(conn, questionnaire_ref)

    subject_ref: str = resource.get("subject", {}).get("reference", "")
    external_id = _external_id_from_ref(subject_ref) if subject_ref else None

    respondent_id = _upsert_respondent(conn, external_id, study_id)

    authored: str | None = resource.get("authored")
    status: str = resource.get("status", "completed")
    fhir_id: str | None = resource.get("id")

    with conn:
        cur = conn.execute(
            """
            INSERT INTO response_session
                (questionnaire_id, respondent_id, started_at, completed_at,
                 is_complete, admin_mode, fhir_response_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                questionnaire_id,
                respondent_id,
                authored or _now(),
                authored if status == "completed" else None,
                1 if status == "completed" else 0,
                admin_mode,
                fhir_id,
            ),
        )
        session_id: int = cur.lastrowid  # type: ignore[assignment]

        ctx = _ImportContext(conn, questionnaire_id, session_id)
        for item in resource.get("item", []):
            ctx.process_item(item)

    return session_id


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _resolve_questionnaire(conn: sqlite3.Connection, ref: str) -> int:
    row = conn.execute(
        "SELECT questionnaire_id FROM questionnaire WHERE canonical_url = ?",
        (ref,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No questionnaire found with canonical_url={ref!r}")
    return row[0]


def _external_id_from_ref(ref: str) -> str:
    """'Patient/synthetic-001' → 'synthetic-001'; fall back to full string."""
    if "/" in ref:
        return ref.split("/", 1)[1]
    return ref


def _upsert_respondent(
    conn: sqlite3.Connection,
    external_id: str | None,
    study_id: int | None,
) -> int:
    with conn:
        conn.execute(
            """
            INSERT INTO respondent (study_id, external_id)
            VALUES (?, ?)
            ON CONFLICT (study_id, external_id) DO NOTHING
            """,
            (study_id, external_id),
        )
        row = conn.execute(
            "SELECT respondent_id FROM respondent WHERE study_id IS ? AND external_id IS ?",
            (study_id, external_id),
        ).fetchone()
    return row[0]


def _now() -> str:
    import datetime
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------
# Import context
# ------------------------------------------------------------------

class _ImportContext:
    """Holds per-import lookup caches and writes response rows."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        questionnaire_id: int,
        session_id: int,
    ) -> None:
        self.conn = conn
        self.session_id = session_id
        self._qq_cache: dict[str, int] = {}       # link_id → qq_id
        self._qtype_cache: dict[str, str] = {}    # link_id → question_type
        self._qid_cache: dict[str, int] = {}      # link_id → question_id
        # link_id → (numeric_min, numeric_max); either side can be None
        self._range_cache: dict[str, tuple[float | None, float | None]] = {}
        self._option_cache: dict[tuple, int | None] = {}  # (question_id, code) → option_id
        self._grid_row_cache: dict[str, int] = {}    # child_link_id → row_id
        self._grid_col_cache: dict[tuple, int] = {}  # (question_id, col_value) → column_id
        self._repeat_count: dict[str, int] = {}      # group link_id → next repeat_index

        # Pre-load qq / question info for this questionnaire
        rows = conn.execute(
            """
            SELECT qq.qq_id, q.link_id, q.question_type, q.question_id,
                   q.numeric_min, q.numeric_max
            FROM questionnaire_question qq
            JOIN question q ON qq.question_id = q.question_id
            WHERE qq.questionnaire_id = ?
            """,
            (questionnaire_id,),
        ).fetchall()
        for r in rows:
            self._qq_cache[r[1]]    = r[0]
            self._qtype_cache[r[1]] = r[2]
            self._qid_cache[r[1]]   = r[3]
            self._range_cache[r[1]] = (r[4], r[5])

    # ------------------------------------------------------------------

    def process_item(self, item: dict) -> None:
        link_id: str = item.get("linkId", "")
        answers: list[dict] = item.get("answer", [])
        sub_items: list[dict] = item.get("item", [])

        qq_id = self._qq_cache.get(link_id)
        qtype = self._qtype_cache.get(link_id)

        if qq_id is None:
            # Could be a grid child — handled via the parent's sub_items path
            return

        if qtype == "grid":
            for child in sub_items:
                self._process_grid_child(child, link_id, qq_id)
            return

        if qtype == "repeating_group":
            repeat_index = self._repeat_count.get(link_id, 0)
            self._repeat_count[link_id] = repeat_index + 1
            for child in sub_items:
                self._process_repeating_child(child, repeat_index)
            return

        for answer in answers:
            self._write_answer(qq_id, link_id, answer)

    def _process_repeating_child(self, child: dict, repeat_index: int) -> None:
        link_id: str = child.get("linkId", "")
        qq_id = self._qq_cache.get(link_id)
        qtype = self._qtype_cache.get(link_id)
        if qq_id is None:
            return

        # A grid as a repeating-group child has its own nested item[] of row
        # cells. Dispatch to the grid handler with the parent's repeat_index
        # threaded through so each cell is attributed to the correct instance.
        if qtype == "grid":
            for grid_cell in child.get("item", []):
                self._process_grid_child(grid_cell, link_id, qq_id, repeat_index=repeat_index)
            return

        # Nested repeating groups (a repeating_group as a child of a
        # repeating_group) are not currently supported: the schema does not
        # model a per-instance index pair, and no real epi instrument we
        # target requires it yet. Flag and skip so the data quality flag log
        # records the limitation rather than silently dropping the responses.
        if qtype == "repeating_group":
            self._flag(
                qq_id,
                f"Nested repeating_group children are not supported; child {link_id!r} skipped.",
                rule_name="nested_repeating_group",
                severity="error",
            )
            return

        for answer in child.get("answer", []):
            self._write_answer(qq_id, link_id, answer, repeat_index=repeat_index)

    def _process_grid_child(
        self,
        child: dict,
        parent_link_id: str,
        qq_id: int,
        repeat_index: int | None = None,
    ) -> None:
        child_link_id: str = child.get("linkId", "")
        # child_link_id looks like "gout.joint_severity.r2"
        # display_order is the suffix after the last ".r"
        try:
            row_order = int(child_link_id.rsplit(".r", 1)[1])
        except (IndexError, ValueError):
            self._flag(qq_id, f"Cannot parse grid child linkId: {child_link_id!r}")
            return

        question_id = self._qid_cache[parent_link_id]
        row_id = self._get_grid_row_id(question_id, row_order)

        for answer in child.get("answer", []):
            col_value = self._coding_value(answer)
            if col_value is None:
                self._flag(qq_id, f"Grid child {child_link_id!r}: unrecognised answer format")
                continue
            col_id = self._get_grid_col_id(question_id, col_value)
            self.conn.execute(
                """
                INSERT INTO response
                    (session_id, qq_id, grid_row_id, grid_column_id, repeat_index)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.session_id, qq_id, row_id, col_id, repeat_index),
            )

    def _write_answer(
        self, qq_id: int, link_id: str, answer: dict, repeat_index: int | None = None
    ) -> None:
        # Type validation: the FHIR value key must match the question's
        # declared type. A mismatch (e.g. valueString to a numeric question)
        # would silently land in the wrong typed column and corrupt downstream
        # analytics; flag it as an error and skip the INSERT instead.
        qtype = self._qtype_cache.get(link_id)
        if qtype:
            err = _validate_answer_type(qtype, answer)
            if err:
                self._flag(qq_id, err, rule_name="type_mismatch", severity="error")
                return

        # ranked: valueCoding + ordinalValue extension → response_numeric = rank
        if "valueCoding" in answer:
            question_id = self._qid_cache[link_id]
            code = self._coding_value(answer)
            option_id = self._resolve_option(question_id, code) if code else None

            rank: float | None = None
            for ext in answer.get("extension", []):
                if "ordinalValue" in ext.get("url", "") and "valueDecimal" in ext:
                    rank = float(ext["valueDecimal"])

            self.conn.execute(
                """
                INSERT INTO response (session_id, qq_id, option_id, response_numeric, repeat_index)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.session_id, qq_id, option_id, rank, repeat_index),
            )
            return

        if "valueBoolean" in answer:
            val = "true" if answer["valueBoolean"] else "false"
            self.conn.execute(
                "INSERT INTO response (session_id, qq_id, response_text, repeat_index) VALUES (?, ?, ?, ?)",
                (self.session_id, qq_id, val, repeat_index),
            )
            return

        if "valueDecimal" in answer or "valueInteger" in answer:
            num = float(answer.get("valueDecimal", answer.get("valueInteger")))
            # Range check: if the question declares numeric_min/max, flag
            # out-of-range values as a warning. The response is still inserted
            # so analysts can decide what to do; the flag is the audit trail.
            lo, hi = self._range_cache.get(link_id, (None, None))
            if lo is not None and num < lo:
                self._flag(
                    qq_id,
                    f"Out of range: response {num} below declared minimum {lo}",
                    rule_name="out_of_range",
                    severity="warning",
                )
            elif hi is not None and num > hi:
                self._flag(
                    qq_id,
                    f"Out of range: response {num} above declared maximum {hi}",
                    rule_name="out_of_range",
                    severity="warning",
                )
            self.conn.execute(
                "INSERT INTO response (session_id, qq_id, response_numeric, repeat_index) VALUES (?, ?, ?, ?)",
                (self.session_id, qq_id, num, repeat_index),
            )
            return

        if "valueDate" in answer or "valueDateTime" in answer:
            date_val = answer.get("valueDate", answer.get("valueDateTime"))
            self.conn.execute(
                "INSERT INTO response (session_id, qq_id, response_date, repeat_index) VALUES (?, ?, ?, ?)",
                (self.session_id, qq_id, date_val, repeat_index),
            )
            return

        if "valueString" in answer:
            self.conn.execute(
                "INSERT INTO response (session_id, qq_id, response_text, repeat_index) VALUES (?, ?, ?, ?)",
                (self.session_id, qq_id, answer["valueString"], repeat_index),
            )
            return

        self._flag(qq_id, f"Unrecognised answer format: {list(answer.keys())}")

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def _coding_value(self, answer: dict) -> str | None:
        coding = answer.get("valueCoding")
        if not coding:
            return None
        return coding.get("code") or coding.get("display")

    def _resolve_option(self, question_id: int, code: str) -> int | None:
        key = (question_id, code)
        if key in self._option_cache:
            return self._option_cache[key]
        # Try concept_code first, then option_value
        row = self.conn.execute(
            """
            SELECT option_id FROM response_option
            WHERE question_id = ?
              AND (concept_code = ? OR option_value = ?)
            LIMIT 1
            """,
            (question_id, code, code),
        ).fetchone()
        result = row[0] if row else None
        self._option_cache[key] = result
        return result

    def _get_grid_row_id(self, question_id: int, display_order: int) -> int:
        key = f"{question_id}:{display_order}"
        if key in self._grid_row_cache:
            return self._grid_row_cache[key]
        row = self.conn.execute(
            "SELECT row_id FROM grid_row WHERE question_id = ? AND display_order = ?",
            (question_id, display_order),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"grid_row not found for question_id={question_id}, display_order={display_order}"
            )
        self._grid_row_cache[key] = row[0]
        return row[0]

    def _get_grid_col_id(self, question_id: int, col_value: str) -> int:
        key = (question_id, col_value)
        if key in self._grid_col_cache:
            return self._grid_col_cache[key]
        row = self.conn.execute(
            "SELECT column_id FROM grid_column WHERE question_id = ? AND column_value = ?",
            (question_id, col_value),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"grid_column not found for question_id={question_id}, column_value={col_value!r}"
            )
        self._grid_col_cache[key] = row[0]
        return row[0]

    def _flag(
        self,
        qq_id: int,
        message: str,
        rule_name: str = "import_fhir_response",
        severity: str = "warning",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO data_quality_flag (session_id, qq_id, rule_name, message, severity)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self.session_id, qq_id, rule_name, message, severity),
        )
