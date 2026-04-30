"""
Tests for quickq.compliance: delete_respondent and set_study_metadata.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from quickq.schema import init_oltp
from quickq.authoring import insert_study, insert_questionnaire, upsert_question, place_question
from quickq.models import QuestionnaireDef, QuestionDef
from quickq.compliance import delete_respondent, set_study_metadata, DeleteResult

from click.testing import CliRunner
from quickq.cli import main


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _seed_db(path: Path) -> dict:
    """Create a study with two respondents and one session + response each."""
    conn = init_oltp(path)

    study_id = insert_study(conn, name="Compliance Test Study", irb_number="IRB-000")
    q_id = insert_questionnaire(
        conn,
        QuestionnaireDef(
            name="Demo",
            canonical_url="http://quickq.io/test/compliance",
            version="1.0",
        ),
        study_id=study_id,
    )
    q1 = upsert_question(conn, QuestionDef(link_id="demo.q1", text="How are you?", type="text"))
    qq1 = place_question(conn, q_id, q1, display_order=1)

    session_ids = []
    respondent_ids = []
    for i in range(2):
        conn.execute(
            "INSERT INTO respondent (study_id, external_id) VALUES (?, ?)",
            (study_id, f"PART-{i:04d}"),
        )
        r_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        respondent_ids.append(r_id)

        conn.execute(
            "INSERT INTO response_session (questionnaire_id, respondent_id, is_complete) VALUES (?, ?, 1)",
            (q_id, r_id),
        )
        s_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        session_ids.append(s_id)

        conn.execute(
            "INSERT INTO response (session_id, qq_id, response_text) VALUES (?, ?, ?)",
            (s_id, qq1, f"Answer from participant {i}"),
        )

        conn.execute(
            "INSERT INTO data_quality_flag (session_id, rule_name, message) VALUES (?, ?, ?)",
            (s_id, "test_rule", "test flag"),
        )

    conn.execute(
        "INSERT INTO person_map (respondent_id, omop_person_id) VALUES (?, ?)",
        (respondent_ids[0], 42),
    )

    conn.commit()
    conn.close()
    return {
        "study_id": study_id,
        "respondent_ids": respondent_ids,
        "session_ids": session_ids,
        "qq_id": qq1,
    }


# ---------------------------------------------------------------------------
# delete_respondent — happy path
# ---------------------------------------------------------------------------

def test_delete_removes_respondent_row(tmp_path):
    db = tmp_path / "study.db"
    _seed_db(db)
    conn = init_oltp(db)

    delete_respondent(conn, "PART-0000")

    remaining = [r[0] for r in conn.execute("SELECT external_id FROM respondent").fetchall()]
    assert "PART-0000" not in remaining
    assert "PART-0001" in remaining


def test_delete_removes_sessions(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    delete_respondent(conn, "PART-0000")

    count = conn.execute(
        "SELECT COUNT(*) FROM response_session WHERE respondent_id = ?",
        (info["respondent_ids"][0],),
    ).fetchone()[0]
    assert count == 0


def test_delete_removes_responses(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    delete_respondent(conn, "PART-0000")

    count = conn.execute(
        "SELECT COUNT(*) FROM response WHERE session_id = ?",
        (info["session_ids"][0],),
    ).fetchone()[0]
    assert count == 0


def test_delete_removes_data_quality_flags(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    delete_respondent(conn, "PART-0000")

    count = conn.execute(
        "SELECT COUNT(*) FROM data_quality_flag WHERE session_id = ?",
        (info["session_ids"][0],),
    ).fetchone()[0]
    assert count == 0


def test_delete_removes_person_map(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    delete_respondent(conn, "PART-0000")

    count = conn.execute(
        "SELECT COUNT(*) FROM person_map WHERE respondent_id = ?",
        (info["respondent_ids"][0],),
    ).fetchone()[0]
    assert count == 0


def test_delete_returns_correct_counts(tmp_path):
    db = tmp_path / "study.db"
    _seed_db(db)
    conn = init_oltp(db)

    result = delete_respondent(conn, "PART-0000")

    assert isinstance(result, DeleteResult)
    assert result.external_id == "PART-0000"
    assert result.sessions_deleted == 1
    assert result.responses_deleted == 1
    assert result.flags_deleted == 1


def test_delete_does_not_touch_other_respondent(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    delete_respondent(conn, "PART-0000")

    # Other respondent's data must be intact
    n_sessions = conn.execute(
        "SELECT COUNT(*) FROM response_session WHERE respondent_id = ?",
        (info["respondent_ids"][1],),
    ).fetchone()[0]
    n_responses = conn.execute(
        "SELECT COUNT(*) FROM response WHERE session_id = ?",
        (info["session_ids"][1],),
    ).fetchone()[0]
    assert n_sessions == 1
    assert n_responses == 1


def test_delete_with_study_id_disambiguates(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    result = delete_respondent(conn, "PART-0000", study_id=info["study_id"])
    assert result.respondent_id == info["respondent_ids"][0]


# ---------------------------------------------------------------------------
# delete_respondent — error cases
# ---------------------------------------------------------------------------

def test_delete_unknown_external_id_raises(tmp_path):
    db = tmp_path / "study.db"
    _seed_db(db)
    conn = init_oltp(db)

    with pytest.raises(ValueError, match="No respondent found"):
        delete_respondent(conn, "DOES-NOT-EXIST")


def test_delete_ambiguous_external_id_raises(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)

    # Two studies, same external_id
    sid1 = insert_study(conn, name="Study A")
    sid2 = insert_study(conn, name="Study B")
    conn.execute("INSERT INTO respondent (study_id, external_id) VALUES (?, 'P001')", (sid1,))
    conn.execute("INSERT INTO respondent (study_id, external_id) VALUES (?, 'P001')", (sid2,))
    conn.commit()

    with pytest.raises(ValueError, match="multiple studies"):
        delete_respondent(conn, "P001")


def test_delete_ambiguous_resolved_with_study_id(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)

    sid1 = insert_study(conn, name="Study A")
    sid2 = insert_study(conn, name="Study B")
    conn.execute("INSERT INTO respondent (study_id, external_id) VALUES (?, 'P001')", (sid1,))
    conn.execute("INSERT INTO respondent (study_id, external_id) VALUES (?, 'P001')", (sid2,))
    conn.commit()

    result = delete_respondent(conn, "P001", study_id=sid1)
    assert result.external_id == "P001"

    remaining = conn.execute("SELECT study_id FROM respondent WHERE external_id='P001'").fetchall()
    assert len(remaining) == 1
    assert remaining[0][0] == sid2


# ---------------------------------------------------------------------------
# delete_respondent — CLI
# ---------------------------------------------------------------------------

def test_cli_delete_respondent(tmp_path):
    db = tmp_path / "study.db"
    _seed_db(db)
    runner = CliRunner()

    result = runner.invoke(main, ["delete-respondent", str(db), "PART-0000", "--yes"])
    assert result.exit_code == 0
    assert "PART-0000" in result.output
    assert "1 session" in result.output


def test_cli_delete_respondent_unknown_exits_nonzero(tmp_path):
    db = tmp_path / "study.db"
    _seed_db(db)
    runner = CliRunner()

    result = runner.invoke(main, ["delete-respondent", str(db), "NOBODY", "--yes"])
    assert result.exit_code != 0


def test_cli_delete_respondent_prompts_without_yes(tmp_path):
    db = tmp_path / "study.db"
    _seed_db(db)
    runner = CliRunner()

    # Respond 'n' to the confirmation prompt — should abort
    result = runner.invoke(main, ["delete-respondent", str(db), "PART-0000"], input="n\n")
    assert result.exit_code != 0

    # Respondent must still exist
    from quickq.schema import open_oltp as _open
    conn = _open(db)
    row = conn.execute("SELECT 1 FROM respondent WHERE external_id='PART-0000'").fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# set_study_metadata — happy path
# ---------------------------------------------------------------------------

def test_set_metadata_updates_fields(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    updated = set_study_metadata(
        conn,
        info["study_id"],
        license="CC-BY-4.0",
        protocol_url="https://clinicaltrials.gov/ct2/show/NCT00000001",
        doi="10.5281/zenodo.0000001",
        population="Adults 18+ in the US",
        geographic_scope="United States",
    )

    assert updated["license"] == "CC-BY-4.0"
    assert updated["doi"] == "10.5281/zenodo.0000001"

    row = conn.execute(
        "SELECT license, protocol_url, doi, population, geographic_scope "
        "FROM study WHERE study_id = ?",
        (info["study_id"],),
    ).fetchone()
    assert row["license"] == "CC-BY-4.0"
    assert row["protocol_url"] == "https://clinicaltrials.gov/ct2/show/NCT00000001"
    assert row["doi"] == "10.5281/zenodo.0000001"
    assert row["population"] == "Adults 18+ in the US"
    assert row["geographic_scope"] == "United States"


def test_set_metadata_none_fields_skipped(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    # Set license first
    set_study_metadata(conn, info["study_id"], license="CC-BY-4.0")

    # Update doi only — license must be unchanged
    set_study_metadata(conn, info["study_id"], doi="10.5281/zenodo.1", license=None)

    row = conn.execute(
        "SELECT license, doi FROM study WHERE study_id = ?", (info["study_id"],)
    ).fetchone()
    assert row["license"] == "CC-BY-4.0"
    assert row["doi"] == "10.5281/zenodo.1"


def test_set_metadata_returns_only_updated_fields(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    updated = set_study_metadata(
        conn, info["study_id"], license="MIT", doi=None, population=None
    )
    assert set(updated.keys()) == {"license"}


def test_set_metadata_no_fields_returns_empty(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    updated = set_study_metadata(conn, info["study_id"])
    assert updated == {}


# ---------------------------------------------------------------------------
# set_study_metadata — error cases
# ---------------------------------------------------------------------------

def test_set_metadata_unknown_field_raises(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    with pytest.raises(ValueError, match="Unknown study metadata field"):
        set_study_metadata(conn, info["study_id"], bogus_field="value")


def test_set_metadata_unknown_study_raises(tmp_path):
    db = tmp_path / "study.db"
    _seed_db(db)
    conn = init_oltp(db)

    with pytest.raises(ValueError, match="No study found"):
        set_study_metadata(conn, 9999, license="CC-BY-4.0")


# ---------------------------------------------------------------------------
# set_study_metadata — CLI
# ---------------------------------------------------------------------------

def test_cli_set_metadata(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    runner = CliRunner()

    result = runner.invoke(main, [
        "set-metadata", str(db),
        "--study-id", str(info["study_id"]),
        "--license", "CC-BY-4.0",
        "--doi", "10.5281/zenodo.0000001",
    ])
    assert result.exit_code == 0
    assert "license" in result.output
    assert "CC-BY-4.0" in result.output


def test_cli_set_metadata_no_args_says_nothing_updated(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    runner = CliRunner()

    result = runner.invoke(main, ["set-metadata", str(db), "--study-id", str(info["study_id"])])
    assert result.exit_code == 0
    assert "nothing updated" in result.output.lower()


# ---------------------------------------------------------------------------
# Consent tracking on response_session
# ---------------------------------------------------------------------------

def test_consent_columns_exist(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(response_session)").fetchall()]
    assert "consent_version" in cols
    assert "consented_at" in cols


def test_consent_version_stored_and_retrieved(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    conn.execute(
        "UPDATE response_session SET consent_version = ?, consented_at = ? WHERE respondent_id = ?",
        ("v2.1", "2026-01-15T09:00:00Z", info["respondent_ids"][0]),
    )
    conn.commit()

    row = conn.execute(
        "SELECT consent_version, consented_at FROM response_session WHERE respondent_id = ?",
        (info["respondent_ids"][0],),
    ).fetchone()
    assert row["consent_version"] == "v2.1"
    assert row["consented_at"] == "2026-01-15T09:00:00Z"


def test_consent_version_nullable_for_anonymous(tmp_path):
    db = tmp_path / "study.db"
    info = _seed_db(db)
    conn = init_oltp(db)

    row = conn.execute(
        "SELECT consent_version FROM response_session WHERE respondent_id = ?",
        (info["respondent_ids"][0],),
    ).fetchone()
    assert row["consent_version"] is None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_audit_log_table_exists(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "tool_audit_log" in tables


def test_record_audit_event_inserts_row(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)

    from quickq.compliance import record_audit_event
    record_audit_event(conn, "test_operation", details={"key": "value"})

    rows = conn.execute(
        "SELECT operation, details FROM tool_audit_log WHERE operation = 'test_operation'"
    ).fetchall()
    assert len(rows) == 1
    import json
    assert json.loads(rows[0]["details"])["key"] == "value"


def test_audit_event_recorded_after_delete(tmp_path):
    db = tmp_path / "study.db"
    _seed_db(db)
    runner = CliRunner()

    runner.invoke(main, ["delete-respondent", str(db), "PART-0000", "--yes"])

    conn = init_oltp(db)
    row = conn.execute(
        "SELECT details FROM tool_audit_log WHERE operation = 'delete_respondent'"
    ).fetchone()
    assert row is not None
    import json
    details = json.loads(row["details"])
    assert details["external_id"] == "PART-0000"


def test_audit_log_silent_on_missing_table(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    conn.execute("DROP TABLE tool_audit_log")
    conn.commit()

    from quickq.compliance import record_audit_event
    record_audit_event(conn, "test_op")  # must not raise
