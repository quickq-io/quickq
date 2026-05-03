"""
Tests for import_fhir_response: FHIR QuestionnaireResponse → OLTP.

Covers:
  - Basic import: session created, respondent upserted
  - valueCoding answers resolved to option_id
  - valueDecimal / valueDate / valueBoolean / valueString
  - Grid answers: grid_row_id + grid_column_id set
  - Ranked answers: option_id resolved, response_numeric = ordinal rank
  - Multiple responses from same respondent share respondent_id
  - Duplicate import is idempotent on respondent but creates a new session
  - Full fixture round-trip: PHQ-9 fixture imports without error
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.loader import load_yaml
from quickq.library_loader import load_library_file
from quickq.parser_fhir_response import import_fhir_response

FIXTURES = Path(__file__).parent / "fixtures"
YAML_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


def _db(tmp_path):
    return init_oltp(tmp_path / "test.db")


LIBRARY_DIR = Path(__file__).parent.parent / "quickq" / "library"


def _load_fixture(conn, fixture_stem):
    """Load questionnaire YAML fixture into conn, loading the matching library first if it exists."""
    lib_path = LIBRARY_DIR / f"{fixture_stem}.yaml"
    if lib_path.exists():
        load_library_file(conn, lib_path)
    path = FIXTURES / f"{fixture_stem}.yaml"
    return load_yaml(conn, str(path))


# ------------------------------------------------------------------
# Minimal synthetic resource
# ------------------------------------------------------------------

_MINIMAL_Q = {
    "resourceType": "Questionnaire",
    "status": "active",
    "title": "Minimal Survey",
    "version": "1.0",
    "url": "http://quickq.io/instruments/minimal-test",
    "item": [
        {"linkId": "min.smoke", "text": "Do you smoke?", "type": "boolean"},
        {"linkId": "min.age",   "text": "Age",            "type": "decimal"},
        {"linkId": "min.notes", "text": "Notes",          "type": "string"},
        {"linkId": "min.dob",   "text": "Date of birth",  "type": "date"},
    ],
}

_MINIMAL_RESPONSE = {
    "resourceType": "QuestionnaireResponse",
    "status": "completed",
    "questionnaire": "http://quickq.io/instruments/minimal-test",
    "authored": "2026-01-01T10:00:00Z",
    "subject": {"reference": "Patient/p-001"},
    "item": [
        {"linkId": "min.smoke", "answer": [{"valueBoolean": True}]},
        {"linkId": "min.age",   "answer": [{"valueDecimal": 42}]},
        {"linkId": "min.notes", "answer": [{"valueString": "All good"}]},
        {"linkId": "min.dob",   "answer": [{"valueDate": "1984-03-15"}]},
    ],
}


def _setup_minimal(tmp_path):
    from quickq.parser_fhir import import_fhir
    conn = _db(tmp_path)
    import_fhir(conn, _MINIMAL_Q)
    conn.commit()
    return conn


def test_session_created(tmp_path):
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _MINIMAL_RESPONSE)
    row = conn.execute(
        "SELECT * FROM response_session WHERE session_id = ?", (sid,)
    ).fetchone()
    assert row is not None
    assert row["is_complete"] == 1
    assert row["admin_mode"] == "api"
    assert row["completed_at"] == "2026-01-01T10:00:00Z"


def test_respondent_upserted(tmp_path):
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _MINIMAL_RESPONSE)
    row = conn.execute(
        "SELECT r.external_id FROM respondent r "
        "JOIN response_session s ON s.respondent_id = r.respondent_id "
        "WHERE s.session_id = ?", (sid,)
    ).fetchone()
    assert row["external_id"] == "p-001"


def test_respondent_shared_across_sessions(tmp_path):
    conn = _setup_minimal(tmp_path)
    sid1 = import_fhir_response(conn, _MINIMAL_RESPONSE)
    sid2 = import_fhir_response(conn, {**_MINIMAL_RESPONSE, "authored": "2026-02-01T00:00:00Z"})
    r1 = conn.execute(
        "SELECT respondent_id FROM response_session WHERE session_id = ?", (sid1,)
    ).fetchone()
    r2 = conn.execute(
        "SELECT respondent_id FROM response_session WHERE session_id = ?", (sid2,)
    ).fetchone()
    assert r1["respondent_id"] == r2["respondent_id"]


def test_boolean_answer(tmp_path):
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _MINIMAL_RESPONSE)
    row = conn.execute(
        """
        SELECT r.response_text FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id = ? AND q.link_id = 'min.smoke'
        """,
        (sid,),
    ).fetchone()
    assert row["response_text"] == "true"


def test_numeric_answer(tmp_path):
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _MINIMAL_RESPONSE)
    row = conn.execute(
        """
        SELECT r.response_numeric FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id = ? AND q.link_id = 'min.age'
        """,
        (sid,),
    ).fetchone()
    assert row["response_numeric"] == 42.0


def test_string_answer(tmp_path):
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _MINIMAL_RESPONSE)
    row = conn.execute(
        """
        SELECT r.response_text FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id = ? AND q.link_id = 'min.notes'
        """,
        (sid,),
    ).fetchone()
    assert row["response_text"] == "All good"


def test_date_answer(tmp_path):
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _MINIMAL_RESPONSE)
    row = conn.execute(
        """
        SELECT r.response_date FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id = ? AND q.link_id = 'min.dob'
        """,
        (sid,),
    ).fetchone()
    assert row["response_date"] == "1984-03-15"


def test_wrong_resource_type_raises(tmp_path):
    conn = _setup_minimal(tmp_path)
    with pytest.raises(ValueError, match="QuestionnaireResponse"):
        import_fhir_response(conn, {"resourceType": "Patient"})


def test_unknown_questionnaire_raises(tmp_path):
    conn = _setup_minimal(tmp_path)
    with pytest.raises(ValueError, match="canonical_url"):
        import_fhir_response(conn, {
            **_MINIMAL_RESPONSE,
            "questionnaire": "http://nowhere.io/unknown",
        })


# ------------------------------------------------------------------
# PHQ-9 fixture round-trip (valueCoding → option_id)
# ------------------------------------------------------------------

def test_phq9_import(tmp_path):
    conn = _db(tmp_path)
    _load_fixture(conn, "phq9")
    responses = json.loads((FIXTURES / "phq9_fhir_responses.json").read_text())
    session_ids = [import_fhir_response(conn, r) for r in responses]
    assert len(session_ids) == 5

    # Every response row for a choice question should have option_id set
    rows = conn.execute(
        """
        SELECT r.option_id, q.question_type
        FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id IN ({})
          AND q.question_type IN ('single_choice', 'multiple_choice', 'likert')
        """.format(",".join(str(s) for s in session_ids))
    ).fetchall()
    assert len(rows) > 0
    unresolved = [r for r in rows if r["option_id"] is None]
    assert unresolved == [], f"{len(unresolved)} options unresolved"


def test_phq9_response_count(tmp_path):
    conn = _db(tmp_path)
    _load_fixture(conn, "phq9")
    responses = json.loads((FIXTURES / "phq9_fhir_responses.json").read_text())
    session_ids = [import_fhir_response(conn, r) for r in responses]

    # PHQ-9 fixture has 10 items (9 core + 1 difficulty question), each with one answer.
    # Session 1 has 9 answers (difficulty skipped — show_when not triggered).
    for sid in session_ids:
        n = conn.execute(
            "SELECT COUNT(*) FROM response WHERE session_id = ?", (sid,)
        ).fetchone()[0]
        assert n in (9, 10), f"session {sid}: expected 9 or 10 rows, got {n}"


# ------------------------------------------------------------------
# Gout fixture: grid + ranked + boolean + numeric + date + text
# ------------------------------------------------------------------

def test_gout_import(tmp_path):
    conn = _db(tmp_path)
    _load_fixture(conn, "gout_checkin")
    responses = json.loads((FIXTURES / "gout_checkin_fhir_responses.json").read_text())
    session_ids = [import_fhir_response(conn, r) for r in responses]
    assert len(session_ids) == len(responses)


def test_gout_grid_rows_cols_set(tmp_path):
    conn = _db(tmp_path)
    _load_fixture(conn, "gout_checkin")
    responses = json.loads((FIXTURES / "gout_checkin_fhir_responses.json").read_text())
    sid = import_fhir_response(conn, responses[0])

    rows = conn.execute(
        """
        SELECT r.grid_row_id, r.grid_column_id
        FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id = ? AND q.link_id = 'gout.joint_severity'
        """,
        (sid,),
    ).fetchall()
    assert len(rows) == 6, f"expected 6 grid rows, got {len(rows)}"
    for row in rows:
        assert row["grid_row_id"] is not None
        assert row["grid_column_id"] is not None


def test_gout_ranked_has_numeric(tmp_path):
    conn = _db(tmp_path)
    _load_fixture(conn, "gout_checkin")
    responses = json.loads((FIXTURES / "gout_checkin_fhir_responses.json").read_text())
    sid = import_fhir_response(conn, responses[0])

    rows = conn.execute(
        """
        SELECT r.response_numeric, r.option_id
        FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id = ? AND q.link_id = 'gout.treatment_priorities'
        ORDER BY r.response_numeric
        """,
        (sid,),
    ).fetchall()
    assert len(rows) == 5
    ranks = [r["response_numeric"] for r in rows]
    assert ranks == [1.0, 2.0, 3.0, 4.0, 5.0]
    # All 5 ranked options should resolve to option_ids
    assert all(r["option_id"] is not None for r in rows)


def test_gout_no_quality_flags(tmp_path):
    conn = _db(tmp_path)
    _load_fixture(conn, "gout_checkin")
    responses = json.loads((FIXTURES / "gout_checkin_fhir_responses.json").read_text())
    session_ids = [import_fhir_response(conn, r) for r in responses]

    flags = conn.execute(
        "SELECT COUNT(*) FROM data_quality_flag WHERE session_id IN ({})".format(
            ",".join(str(s) for s in session_ids)
        )
    ).fetchone()[0]
    assert flags == 0, f"unexpected data quality flags: {flags}"


# ------------------------------------------------------------------
# JSON string input
# ------------------------------------------------------------------

def test_accepts_json_string(tmp_path):
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, json.dumps(_MINIMAL_RESPONSE))
    assert sid is not None


# ------------------------------------------------------------------
# Slider response import
# ------------------------------------------------------------------

def test_datetime_answer_stored_as_response_date(tmp_path):
    from quickq.authoring import upsert_question, insert_questionnaire, place_question
    from quickq.models import QuestionDef, QuestionnaireDef
    from quickq.parser_fhir import import_fhir
    from quickq.renderer_fhir import export_fhir

    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(
        link_id="q.ts", text="When exactly?", type="datetime",
    ))
    qid = insert_questionnaire(conn, QuestionnaireDef(
        name="Datetime Test",
        canonical_url="http://quickq.io/instruments/datetime-test",
    ))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()

    fhir_response = {
        "resourceType": "QuestionnaireResponse",
        "questionnaire": "http://quickq.io/instruments/datetime-test",
        "status": "completed",
        "authored": "2026-03-10T09:00:00Z",
        "subject": {"identifier": {"value": "respondent-dt-001"}},
        "item": [{"linkId": "q.ts", "answer": [{"valueDateTime": "2026-03-09T14:30:00Z"}]}],
    }
    sid = import_fhir_response(conn, fhir_response)

    row = conn.execute(
        """
        SELECT r.response_date
        FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id = ? AND q.link_id = 'q.ts'
        """,
        (sid,),
    ).fetchone()
    assert row is not None
    assert row["response_date"] == "2026-03-09T14:30:00Z"


def test_slider_response_stored_as_numeric(tmp_path):
    from quickq.authoring import upsert_question, insert_questionnaire, place_question
    from quickq.models import QuestionDef, QuestionnaireDef
    from quickq.renderer_fhir import export_fhir

    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(
        link_id="q.vas", text="Pain level?", type="slider",
        numeric_min=0, numeric_max=100,
        slider_min_label="No pain", slider_max_label="Worst imaginable",
    ))
    qid = insert_questionnaire(conn, QuestionnaireDef(
        name="VAS",
        canonical_url="http://quickq.io/instruments/vas-test",
    ))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()

    fhir_response = {
        "resourceType": "QuestionnaireResponse",
        "questionnaire": "http://quickq.io/instruments/vas-test",
        "status": "completed",
        "authored": "2026-01-15T10:00:00Z",
        "subject": {"identifier": {"value": "respondent-vas-001"}},
        "item": [{"linkId": "q.vas", "answer": [{"valueInteger": 72}]}],
    }
    sid = import_fhir_response(conn, fhir_response)

    row = conn.execute(
        """
        SELECT r.response_numeric
        FROM response r
        JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE r.session_id = ? AND q.link_id = 'q.vas'
        """,
        (sid,),
    ).fetchone()
    assert row is not None
    assert row["response_numeric"] == 72.0


# ------------------------------------------------------------------
# Type-validation at FHIR import (closes ehg)
# ------------------------------------------------------------------

def _bad_response(linkid: str, value_key: str, value: object) -> dict:
    """Build a single-answer QuestionnaireResponse with a deliberately-wrong
    value type for the named question."""
    return {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "questionnaire": "http://quickq.io/instruments/minimal-test",
        "authored": "2026-01-01T10:00:00Z",
        "subject": {"reference": "Patient/typetest"},
        "item": [{"linkId": linkid, "answer": [{value_key: value}]}],
    }


def test_value_string_to_numeric_question_flagged_not_inserted(tmp_path):
    """A FHIR valueString to a numeric question should produce an error
    flag and skip the INSERT (silent miscategorization is the core
    failure mode this guard exists to prevent)."""
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _bad_response("min.age", "valueString", "forty-two"))

    # The response row should NOT have been inserted
    n_rows = conn.execute(
        "SELECT COUNT(*) FROM response WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    assert n_rows == 0, "type-mismatched answer should not write to response table"

    # A type_mismatch flag should exist
    flags = conn.execute(
        "SELECT rule_name, severity, message FROM data_quality_flag WHERE session_id = ?",
        (sid,),
    ).fetchall()
    assert len(flags) == 1
    rule_name, severity, message = flags[0]
    assert rule_name == "type_mismatch"
    assert severity == "error"
    assert "numeric" in message
    assert "valueString" in message


def test_value_decimal_to_boolean_question_flagged(tmp_path):
    """valueDecimal to a boolean question is also a type collision."""
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _bad_response("min.smoke", "valueDecimal", 1.0))

    n_rows = conn.execute(
        "SELECT COUNT(*) FROM response WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    assert n_rows == 0
    n_flags = conn.execute(
        "SELECT COUNT(*) FROM data_quality_flag "
        "WHERE session_id = ? AND rule_name = 'type_mismatch'",
        (sid,),
    ).fetchone()[0]
    assert n_flags == 1


def test_value_date_to_text_question_flagged(tmp_path):
    """valueDate to a text question."""
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _bad_response("min.notes", "valueDate", "2024-01-01"))

    assert conn.execute(
        "SELECT COUNT(*) FROM response WHERE session_id = ?", (sid,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM data_quality_flag "
        "WHERE session_id = ? AND rule_name = 'type_mismatch'",
        (sid,),
    ).fetchone()[0] == 1


def test_correct_value_types_no_flags(tmp_path):
    """The minimal fixture exercises all four value-type pathways correctly;
    after import there should be zero data_quality_flag rows."""
    conn = _setup_minimal(tmp_path)
    sid = import_fhir_response(conn, _MINIMAL_RESPONSE)

    n_flags = conn.execute(
        "SELECT COUNT(*) FROM data_quality_flag WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    assert n_flags == 0

    # All four answer rows landed in their correct typed columns
    rows = conn.execute(
        "SELECT response_text, response_numeric, response_date "
        "FROM response WHERE session_id = ? ORDER BY response_id",
        (sid,),
    ).fetchall()
    assert len(rows) == 4


def test_mixed_session_partial_failure_continues(tmp_path):
    """One bad answer in a multi-answer session should not abort the import:
    the other answers still land, and the bad one is flagged."""
    conn = _setup_minimal(tmp_path)
    bad_then_good = {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "questionnaire": "http://quickq.io/instruments/minimal-test",
        "authored": "2026-01-01T10:00:00Z",
        "subject": {"reference": "Patient/mixed"},
        "item": [
            {"linkId": "min.age",   "answer": [{"valueString": "old"}]},   # bad
            {"linkId": "min.smoke", "answer": [{"valueBoolean": False}]},  # good
            {"linkId": "min.notes", "answer": [{"valueString": "ok"}]},    # good
        ],
    }
    sid = import_fhir_response(conn, bad_then_good)

    # Two good answers landed
    n_responses = conn.execute(
        "SELECT COUNT(*) FROM response WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    assert n_responses == 2

    # One type_mismatch flag for the bad answer
    n_flags = conn.execute(
        "SELECT COUNT(*) FROM data_quality_flag "
        "WHERE session_id = ? AND rule_name = 'type_mismatch'",
        (sid,),
    ).fetchone()[0]
    assert n_flags == 1
