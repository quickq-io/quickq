"""Tests for quickq.fork — forking a study's structure into a new database."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from quickq.schema import init_oltp, open_oltp
from quickq.loader import load_yaml
from quickq.fork import fork_database, ForkError

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_one_response(conn: sqlite3.Connection, qid: int) -> None:
    """Insert one minimal respondent + session + response so we can verify
    the fork strips response-bearing rows."""
    conn.execute(
        "INSERT INTO respondent (study_id, external_id) VALUES (NULL, ?)",
        ("subj-001",),
    )
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO response_session (respondent_id, questionnaire_id, started_at, is_complete)
           VALUES (?, ?, '2026-05-01T00:00:00Z', 1)""",
        (rid, qid),
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    qq_id = conn.execute(
        "SELECT qq_id FROM questionnaire_question WHERE questionnaire_id = ? LIMIT 1",
        (qid,),
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO response (session_id, qq_id, response_text)
           VALUES (?, ?, 'sample answer')""",
        (sid, qq_id),
    )
    conn.commit()


def _populated_source_db(tmp_path: Path) -> tuple[Path, int]:
    """Make a source DB from the simple.yaml fixture with one stored response."""
    src_path = tmp_path / "source.db"
    conn = init_oltp(src_path)
    qid = load_yaml(conn, FIXTURES / "simple.yaml")
    _seed_one_response(conn, qid)
    conn.close()
    return src_path, qid


# ---------------------------------------------------------------------------
# Structure preservation
# ---------------------------------------------------------------------------

def test_fork_preserves_questionnaire_and_questions(tmp_path):
    src_path, qid = _populated_source_db(tmp_path)
    out_path = tmp_path / "fork.db"
    result = fork_database(src_path, qid, out_path)

    out = open_oltp(out_path)
    qrow = out.execute(
        "SELECT canonical_url, version FROM questionnaire WHERE questionnaire_id = ?",
        (result.new_questionnaire_id,),
    ).fetchone()
    assert qrow["canonical_url"] == "http://quickq.io/instruments/tobacco-use"

    link_ids = {r[0] for r in out.execute("SELECT link_id FROM question").fetchall()}
    assert {"tobacco.current", "tobacco.type", "tobacco.cpd", "tobacco.quit_attempts"} == link_ids

    # Options carry over (and are not duplicated)
    n_yn_opts = out.execute(
        """SELECT COUNT(*) FROM response_option ro
           JOIN question q USING (question_id)
           WHERE q.link_id = 'tobacco.current'"""
    ).fetchone()[0]
    assert n_yn_opts == 2


def test_fork_preserves_skip_rules(tmp_path):
    src_path, qid = _populated_source_db(tmp_path)
    out_path = tmp_path / "fork.db"
    fork_database(src_path, qid, out_path)

    out = open_oltp(out_path)
    n_skip = out.execute("SELECT COUNT(*) FROM skip_rule").fetchone()[0]
    assert n_skip >= 1, "simple.yaml has at least one show_when rule"


# ---------------------------------------------------------------------------
# Response exclusion
# ---------------------------------------------------------------------------

def test_fork_excludes_responses(tmp_path):
    src_path, qid = _populated_source_db(tmp_path)
    out_path = tmp_path / "fork.db"
    fork_database(src_path, qid, out_path)

    out = open_oltp(out_path)
    assert out.execute("SELECT COUNT(*) FROM respondent").fetchone()[0] == 0
    assert out.execute("SELECT COUNT(*) FROM response_session").fetchone()[0] == 0
    assert out.execute("SELECT COUNT(*) FROM response").fetchone()[0] == 0


def test_fork_excludes_data_quality_flags(tmp_path):
    src_path, qid = _populated_source_db(tmp_path)
    # Add a data_quality_flag tied to the seeded session
    conn = sqlite3.connect(src_path)
    conn.execute(
        """INSERT INTO data_quality_flag (session_id, qq_id, severity, rule_name, message)
           VALUES (1, 1, 'warning', 'test_rule', 'test message')"""
    )
    conn.commit()
    conn.close()

    out_path = tmp_path / "fork.db"
    fork_database(src_path, qid, out_path)

    out = open_oltp(out_path)
    assert out.execute("SELECT COUNT(*) FROM data_quality_flag").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_fork_records_provenance_in_audit_log(tmp_path):
    src_path, qid = _populated_source_db(tmp_path)
    out_path = tmp_path / "fork.db"
    result = fork_database(src_path, qid, out_path, note="testing fork")

    out = open_oltp(out_path)
    audit = out.execute(
        "SELECT operation, details FROM tool_audit_log WHERE operation = 'fork'"
    ).fetchone()
    assert audit is not None
    details = json.loads(audit["details"])
    assert details["source_questionnaire_id"] == qid
    assert details["new_questionnaire_id"] == result.new_questionnaire_id
    assert details["source_canonical_url"] == "http://quickq.io/instruments/tobacco-use"
    assert details["source_version"] == "1.0"
    assert details["note"] == "testing fork"


def test_fork_excludes_source_audit_log(tmp_path):
    src_path, qid = _populated_source_db(tmp_path)
    # Pre-populate the source's audit log with an unrelated entry
    conn = sqlite3.connect(src_path)
    conn.execute(
        "INSERT INTO tool_audit_log (study_id, operation, performed_by, details) VALUES (NULL, 'pseudonymize', 'tester', NULL)"
    )
    conn.commit()
    conn.close()

    out_path = tmp_path / "fork.db"
    fork_database(src_path, qid, out_path)

    out = open_oltp(out_path)
    ops = [r[0] for r in out.execute("SELECT operation FROM tool_audit_log").fetchall()]
    # Only the fork audit entry should be present; the source's pseudonymize entry must not bleed in.
    assert ops == ["fork"]


# ---------------------------------------------------------------------------
# Optional flags
# ---------------------------------------------------------------------------

def test_fork_with_version_bump(tmp_path):
    src_path, qid = _populated_source_db(tmp_path)
    out_path = tmp_path / "fork.db"
    result = fork_database(src_path, qid, out_path, new_version="2.0")

    assert result.source_version == "1.0"
    assert result.new_version == "2.0"

    out = open_oltp(out_path)
    version = out.execute(
        "SELECT version FROM questionnaire WHERE questionnaire_id = ?",
        (result.new_questionnaire_id,),
    ).fetchone()[0]
    assert version == "2.0"


def test_fork_reset_study_metadata(tmp_path):
    src_path = tmp_path / "source.db"
    conn = init_oltp(src_path)
    # Insert a study with rich metadata, then load a questionnaire under it
    conn.execute(
        """INSERT INTO study
           (name, description, principal_investigator, irb_number, start_date)
           VALUES ('Original Study', 'Original desc', 'Dr. Original',
                   'IRB-12345', '2026-01-01')"""
    )
    conn.commit()
    qid = load_yaml(conn, FIXTURES / "simple.yaml")
    # Link the loaded questionnaire to the study
    conn.execute(
        "UPDATE questionnaire SET study_id = (SELECT study_id FROM study WHERE name='Original Study') WHERE questionnaire_id = ?",
        (qid,),
    )
    conn.commit()
    conn.close()

    out_path = tmp_path / "fork.db"
    fork_database(src_path, qid, out_path, reset_study_metadata=True)

    out = open_oltp(out_path)
    s = out.execute(
        "SELECT name, description, principal_investigator, irb_number, start_date FROM study LIMIT 1"
    ).fetchone()
    # Name preserved; sensitive metadata blanked
    assert s["name"] == "Original Study"
    assert s["description"] is None
    assert s["principal_investigator"] is None
    assert s["irb_number"] is None
    assert s["start_date"] is None


def test_fork_overwrite_required_when_output_exists(tmp_path):
    src_path, qid = _populated_source_db(tmp_path)
    out_path = tmp_path / "fork.db"
    out_path.write_bytes(b"existing")

    with pytest.raises(ForkError, match="already exists"):
        fork_database(src_path, qid, out_path)

    # With overwrite=True it succeeds.
    result = fork_database(src_path, qid, out_path, overwrite=True)
    assert result.new_questionnaire_id is not None


def test_fork_unknown_questionnaire_id_errors(tmp_path):
    src_path, _qid = _populated_source_db(tmp_path)
    out_path = tmp_path / "fork.db"
    with pytest.raises(ForkError, match="not found"):
        fork_database(src_path, 9999, out_path)


# ---------------------------------------------------------------------------
# Reload + use the fork
# ---------------------------------------------------------------------------

def test_fork_supports_collecting_independently(tmp_path):
    """Smoke test: a forked DB is fully functional for collection. Insert a
    new respondent + response into the fork and verify it doesn't appear in
    the source."""
    src_path, qid = _populated_source_db(tmp_path)
    out_path = tmp_path / "fork.db"
    result = fork_database(src_path, qid, out_path)

    fork = sqlite3.connect(out_path)
    fork.execute(
        "INSERT INTO respondent (study_id, external_id) VALUES (NULL, 'fork-subj-1')"
    )
    rid = fork.execute("SELECT last_insert_rowid()").fetchone()[0]
    fork.execute(
        """INSERT INTO response_session (respondent_id, questionnaire_id, started_at, is_complete)
           VALUES (?, ?, '2026-05-04T00:00:00Z', 1)""",
        (rid, result.new_questionnaire_id),
    )
    fork.commit()
    fork.close()

    src = sqlite3.connect(src_path)
    n_src = src.execute(
        "SELECT COUNT(*) FROM respondent WHERE external_id = 'fork-subj-1'"
    ).fetchone()[0]
    src.close()
    assert n_src == 0, "fork's new respondent must not appear in the source"
