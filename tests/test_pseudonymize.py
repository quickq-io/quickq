"""
Tests for quickq.pseudonymize.
"""
import pytest
from pathlib import Path

from quickq.schema import init_oltp
from quickq.authoring import insert_study, insert_questionnaire, upsert_question, place_question
from quickq.models import QuestionnaireDef, QuestionDef
from quickq.pseudonymize import pseudonymize, PseudonymizeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path) -> None:
    conn = init_oltp(path)

    study_id = insert_study(
        conn,
        name="Prenatal Study",
        principal_investigator="Dr. Jane Smith",
        irb_number="IRB-2025-001",
    )
    q_id = insert_questionnaire(
        conn,
        QuestionnaireDef(
            name="PHQ-2",
            canonical_url="http://quickq.io/instruments/phq2",
            version="1.0",
        ),
        study_id=study_id,
    )
    q1 = upsert_question(conn, QuestionDef(
        link_id="phq2.1",
        text="Little interest or pleasure in doing things",
        type="likert",
    ))
    q2 = upsert_question(conn, QuestionDef(
        link_id="notes",
        text="Any additional comments?",
        type="text",
    ))
    qq1 = place_question(conn, q_id, q1, display_order=1)
    qq2 = place_question(conn, q_id, q2, display_order=2)

    for i in range(3):
        conn.execute(
            "INSERT INTO respondent (study_id, external_id) VALUES (?, ?)",
            (study_id, f"MRN-{i:04d}"),
        )
        r_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO response_session
               (questionnaire_id, respondent_id, started_at, is_complete, interviewer_id)
               VALUES (?, ?, '2025-01-01T10:00:00Z', 1, 'staff-007')""",
            (q_id, r_id),
        )
        s_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO response (session_id, qq_id, response_text) VALUES (?, ?, ?)",
            (s_id, qq2, "Patient mentioned name John Doe"),
        )

    # Add a person_map entry
    respondent_id = conn.execute("SELECT respondent_id FROM respondent LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO person_map (respondent_id, omop_person_id) VALUES (?, ?)",
        (respondent_id, 99999),
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

def test_external_ids_replaced(tmp_path):
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)

    result = pseudonymize(src, out)

    conn = init_oltp(out)
    ids = [r[0] for r in conn.execute("SELECT external_id FROM respondent").fetchall()]
    conn.close()

    assert result.respondents_pseudonymized == 3
    # No original MRN values remain
    assert all(not eid.startswith("MRN-") for eid in ids)
    # Tokens are 32 hex chars
    assert all(len(eid) == 32 and all(c in "0123456789abcdef" for c in eid) for eid in ids)
    # All tokens are distinct
    assert len(set(ids)) == 3


def test_tokens_are_stable(tmp_path):
    """Same key + same external_id must always produce the same token."""
    src = tmp_path / "study.db"
    out1 = tmp_path / "anon1.db"
    out2 = tmp_path / "anon2.db"
    _make_db(src)

    key = b"x" * 32
    result1 = pseudonymize(src, out1, key=key)
    result2 = pseudonymize(src, out2, key=key)

    conn1 = init_oltp(out1)
    conn2 = init_oltp(out2)
    ids1 = sorted(r[0] for r in conn1.execute("SELECT external_id FROM respondent").fetchall())
    ids2 = sorted(r[0] for r in conn2.execute("SELECT external_id FROM respondent").fetchall())
    conn1.close()
    conn2.close()

    assert ids1 == ids2


def test_different_keys_produce_different_tokens(tmp_path):
    src = tmp_path / "study.db"
    out1 = tmp_path / "anon1.db"
    out2 = tmp_path / "anon2.db"
    _make_db(src)

    result1 = pseudonymize(src, out1, key=b"a" * 32)
    result2 = pseudonymize(src, out2, key=b"b" * 32)

    conn1 = init_oltp(out1)
    conn2 = init_oltp(out2)
    ids1 = set(r[0] for r in conn1.execute("SELECT external_id FROM respondent").fetchall())
    ids2 = set(r[0] for r in conn2.execute("SELECT external_id FROM respondent").fetchall())
    conn1.close()
    conn2.close()

    assert ids1.isdisjoint(ids2)


def test_person_map_cleared(tmp_path):
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)

    # Confirm source has a person_map entry
    conn_src = init_oltp(src)
    assert conn_src.execute("SELECT COUNT(*) FROM person_map").fetchone()[0] == 1
    conn_src.close()

    pseudonymize(src, out)

    conn = init_oltp(out)
    assert conn.execute("SELECT COUNT(*) FROM person_map").fetchone()[0] == 0
    conn.close()


def test_interviewer_ids_cleared(tmp_path):
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)

    # Confirm source has interviewer_id set
    conn_src = init_oltp(src)
    n = conn_src.execute(
        "SELECT COUNT(*) FROM response_session WHERE interviewer_id IS NOT NULL"
    ).fetchone()[0]
    assert n == 3
    conn_src.close()

    pseudonymize(src, out)

    conn = init_oltp(out)
    n_after = conn.execute(
        "SELECT COUNT(*) FROM response_session WHERE interviewer_id IS NOT NULL"
    ).fetchone()[0]
    assert n_after == 0
    conn.close()


def test_source_database_unchanged(tmp_path):
    """Pseudonymize must not modify the source database."""
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)

    conn_src = init_oltp(src)
    original_ids = sorted(
        r[0] for r in conn_src.execute("SELECT external_id FROM respondent").fetchall()
    )
    conn_src.close()

    pseudonymize(src, out)

    conn_src = init_oltp(src)
    ids_after = sorted(
        r[0] for r in conn_src.execute("SELECT external_id FROM respondent").fetchall()
    )
    conn_src.close()

    assert original_ids == ids_after


def test_response_data_preserved(tmp_path):
    """Responses (including free-text) must not be modified."""
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)

    pseudonymize(src, out)

    conn = init_oltp(out)
    texts = [
        r[0] for r in conn.execute(
            "SELECT response_text FROM response WHERE response_text IS NOT NULL"
        ).fetchall()
    ]
    conn.close()

    assert len(texts) == 3
    assert all("John Doe" in t for t in texts)


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def test_warns_free_text_responses(tmp_path):
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)

    result = pseudonymize(src, out)

    free_text_warnings = [w for w in result.warnings if "notes" in w]
    assert len(free_text_warnings) == 1
    assert "text" in free_text_warnings[0]


def test_warns_institutional_fields(tmp_path):
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)

    result = pseudonymize(src, out)

    inst_warnings = [w for w in result.warnings if "principal_investigator" in w]
    assert len(inst_warnings) == 1
    assert "IRB" in inst_warnings[0] or "irb_number" in inst_warnings[0]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_error_output_exists(tmp_path):
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)
    out.write_text("existing")

    with pytest.raises(FileExistsError, match="already exists"):
        pseudonymize(src, out)


def test_overwrite_flag(tmp_path):
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)
    out.write_text("existing")

    result = pseudonymize(src, out, overwrite=True)
    assert result.respondents_pseudonymized == 3


def test_key_returned_in_result(tmp_path):
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    _make_db(src)

    result = pseudonymize(src, out)
    assert isinstance(result.key, bytes)
    assert len(result.key) == 32


def test_null_external_ids_untouched(tmp_path):
    """Respondents with no external_id should remain NULL after pseudonymization."""
    src = tmp_path / "study.db"
    out = tmp_path / "anon.db"
    conn = init_oltp(src)
    conn.execute("INSERT INTO respondent (external_id) VALUES (NULL)")
    conn.commit()
    conn.close()

    result = pseudonymize(src, out)

    conn = init_oltp(out)
    null_count = conn.execute(
        "SELECT COUNT(*) FROM respondent WHERE external_id IS NULL"
    ).fetchone()[0]
    conn.close()

    assert null_count == 1
    assert result.respondents_pseudonymized == 0
