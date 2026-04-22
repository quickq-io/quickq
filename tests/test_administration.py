import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.authoring import upsert_question, insert_questionnaire, place_question
from quickq.models import QuestionDef, QuestionnaireDef
from quickq.administration import (
    deprecate_questionnaire_question,
    suspend_questionnaire_question,
    reactivate_questionnaire_question,
    log_errata,
    acknowledge_errata,
    resolve_errata,
    get_errata,
    data_dictionary,
    format_data_dict_markdown,
    format_data_dict_csv,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _setup(tmp_path):
    """Create a DB with one questionnaire containing two questions."""
    conn = init_oltp(tmp_path / "test.db")
    conn.execute("INSERT INTO study (name) VALUES ('Test Study')")
    study_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    q1_id = upsert_question(conn, QuestionDef(link_id="q.smoke", text="Do you smoke?", type="single_choice"))
    q2_id = upsert_question(conn, QuestionDef(link_id="q.age", text="What is your age?", type="numeric"))
    conn.execute(
        "INSERT INTO response_option (question_id, option_text, option_value, display_order) VALUES (?,?,?,?)",
        (q1_id, "Yes", "yes", 0),
    )
    conn.execute(
        "INSERT INTO response_option (question_id, option_text, option_value, display_order) VALUES (?,?,?,?)",
        (q1_id, "No", "no", 1),
    )

    qnaire_id = insert_questionnaire(conn, QuestionnaireDef(name="Intake Survey"))
    qq1_id = place_question(conn, qnaire_id, q1_id, display_order=0)
    qq2_id = place_question(conn, qnaire_id, q2_id, display_order=1)
    conn.commit()
    return conn, study_id, qnaire_id, qq1_id, qq2_id


# ------------------------------------------------------------------
# Question status lifecycle
# ------------------------------------------------------------------

def test_deprecate_sets_status(tmp_path):
    conn, _, _, qq1_id, _ = _setup(tmp_path)
    deprecate_questionnaire_question(conn, qq1_id, reason="Removed from protocol v2")
    conn.commit()
    row = conn.execute("SELECT status, status_notes FROM questionnaire_question WHERE qq_id=?", (qq1_id,)).fetchone()
    assert row["status"] == "deprecated"
    assert row["status_notes"] == "Removed from protocol v2"


def test_suspend_sets_status(tmp_path):
    conn, _, _, qq1_id, _ = _setup(tmp_path)
    suspend_questionnaire_question(conn, qq1_id, reason="Pending IRB review")
    conn.commit()
    row = conn.execute("SELECT status FROM questionnaire_question WHERE qq_id=?", (qq1_id,)).fetchone()
    assert row["status"] == "suspended"


def test_reactivate_restores_active(tmp_path):
    conn, _, _, qq1_id, _ = _setup(tmp_path)
    suspend_questionnaire_question(conn, qq1_id, reason="Temp hold")
    conn.commit()
    reactivate_questionnaire_question(conn, qq1_id, reason="Review complete")
    conn.commit()
    row = conn.execute("SELECT status FROM questionnaire_question WHERE qq_id=?", (qq1_id,)).fetchone()
    assert row["status"] == "active"


def test_status_changed_at_recorded(tmp_path):
    conn, _, _, qq1_id, _ = _setup(tmp_path)
    deprecate_questionnaire_question(conn, qq1_id, reason="Old", changed_at="2024-06-01T00:00:00Z")
    conn.commit()
    row = conn.execute("SELECT status_changed_at FROM questionnaire_question WHERE qq_id=?", (qq1_id,)).fetchone()
    assert row["status_changed_at"] == "2024-06-01T00:00:00Z"


def test_status_invalid_qq_id_raises(tmp_path):
    conn, _, _, _, _ = _setup(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        deprecate_questionnaire_question(conn, 99999, reason="Ghost")


# ------------------------------------------------------------------
# Errata log
# ------------------------------------------------------------------

def test_log_errata_returns_id(tmp_path):
    conn, study_id, _, _, _ = _setup(tmp_path)
    errata_id = log_errata(
        conn, title="Display bug", description="Question skipped incorrectly",
        event_type="delivery_bug", severity="major", study_id=study_id,
    )
    conn.commit()
    assert errata_id > 0


def test_log_errata_stored_correctly(tmp_path):
    conn, study_id, qnaire_id, _, _ = _setup(tmp_path)
    errata_id = log_errata(
        conn,
        title="IRB halt",
        description="Smoking question suspended per IRB#2024-001",
        event_type="irb_action",
        severity="critical",
        study_id=study_id,
        questionnaire_id=qnaire_id,
        analyst_guidance="Exclude sessions after 2024-03-01",
        reported_by="Dr. Smith",
    )
    conn.commit()
    row = conn.execute("SELECT * FROM study_errata_log WHERE errata_id=?", (errata_id,)).fetchone()
    assert row["event_type"] == "irb_action"
    assert row["severity"] == "critical"
    assert row["status"] == "open"
    assert row["reported_by"] == "Dr. Smith"


def test_acknowledge_errata(tmp_path):
    conn, study_id, _, _, _ = _setup(tmp_path)
    eid = log_errata(conn, "Note", "Just a note", "note", study_id=study_id)
    conn.commit()
    acknowledge_errata(conn, eid, acknowledged_by="analyst@example.com")
    conn.commit()
    row = conn.execute("SELECT status, resolved_by FROM study_errata_log WHERE errata_id=?", (eid,)).fetchone()
    assert row["status"] == "acknowledged"
    assert row["resolved_by"] == "analyst@example.com"


def test_resolve_errata(tmp_path):
    conn, study_id, _, _, _ = _setup(tmp_path)
    eid = log_errata(conn, "Bug", "Bad skip logic", "delivery_bug", study_id=study_id)
    conn.commit()
    resolve_errata(conn, eid, resolved_by="dev@example.com", resolved_at="2024-07-01T12:00:00Z")
    conn.commit()
    row = conn.execute("SELECT status, resolved_at FROM study_errata_log WHERE errata_id=?", (eid,)).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_at"] == "2024-07-01T12:00:00Z"


def test_get_errata_filter_by_study(tmp_path):
    conn, study_id, _, _, _ = _setup(tmp_path)
    log_errata(conn, "E1", "desc", "note", study_id=study_id)
    log_errata(conn, "E2", "desc", "note", study_id=study_id)
    log_errata(conn, "E3", "desc", "note")  # no study
    conn.commit()
    results = get_errata(conn, study_id=study_id)
    assert len(results) == 2


def test_get_errata_filter_by_status(tmp_path):
    conn, study_id, _, _, _ = _setup(tmp_path)
    eid1 = log_errata(conn, "Open", "desc", "note", study_id=study_id)
    eid2 = log_errata(conn, "Resolved", "desc", "note", study_id=study_id)
    conn.commit()
    resolve_errata(conn, eid2, resolved_by="me")
    conn.commit()
    open_items = get_errata(conn, status="open")
    resolved_items = get_errata(conn, status="resolved")
    assert any(r["errata_id"] == eid1 for r in open_items)
    assert any(r["errata_id"] == eid2 for r in resolved_items)


def test_get_errata_filter_by_severity(tmp_path):
    conn, study_id, _, _, _ = _setup(tmp_path)
    log_errata(conn, "Critical", "desc", "delivery_bug", severity="critical", study_id=study_id)
    log_errata(conn, "Minor", "desc", "note", severity="minor", study_id=study_id)
    conn.commit()
    critical = get_errata(conn, severity="critical")
    assert len(critical) == 1
    assert critical[0]["title"] == "Critical"


def test_get_errata_returns_dicts(tmp_path):
    conn, study_id, _, _, _ = _setup(tmp_path)
    log_errata(conn, "Test", "desc", "note", study_id=study_id)
    conn.commit()
    results = get_errata(conn)
    assert isinstance(results[0], dict)
    assert "errata_id" in results[0]


# ------------------------------------------------------------------
# Data dictionary
# ------------------------------------------------------------------

def test_data_dictionary_returns_active_questions(tmp_path):
    conn, _, qnaire_id, _, _ = _setup(tmp_path)
    rows = data_dictionary(conn, qnaire_id)
    assert len(rows) == 2
    variables = [r["variable"] for r in rows]
    assert "q.smoke" in variables
    assert "q.age" in variables


def test_data_dictionary_excludes_deprecated_by_default(tmp_path):
    conn, _, qnaire_id, qq1_id, _ = _setup(tmp_path)
    deprecate_questionnaire_question(conn, qq1_id, reason="Test")
    conn.commit()
    rows = data_dictionary(conn, qnaire_id)
    assert len(rows) == 1
    assert rows[0]["variable"] == "q.age"


def test_data_dictionary_include_deprecated(tmp_path):
    conn, _, qnaire_id, qq1_id, _ = _setup(tmp_path)
    deprecate_questionnaire_question(conn, qq1_id, reason="Test")
    conn.commit()
    rows = data_dictionary(conn, qnaire_id, include_deprecated=True)
    assert len(rows) == 2


def test_data_dictionary_ordered_by_display_order(tmp_path):
    conn, _, qnaire_id, _, _ = _setup(tmp_path)
    rows = data_dictionary(conn, qnaire_id)
    orders = [r["order"] for r in rows]
    assert orders == sorted(orders)


def test_data_dictionary_includes_valid_values(tmp_path):
    conn, _, qnaire_id, _, _ = _setup(tmp_path)
    rows = data_dictionary(conn, qnaire_id)
    smoke_row = next(r for r in rows if r["variable"] == "q.smoke")
    assert smoke_row["valid_values"] is not None
    assert "yes" in smoke_row["valid_values"]
    assert "no" in smoke_row["valid_values"]


def test_data_dictionary_empty_questionnaire(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    qnaire_id = insert_questionnaire(conn, QuestionnaireDef(name="Empty"))
    conn.commit()
    rows = data_dictionary(conn, qnaire_id)
    assert rows == []


# ------------------------------------------------------------------
# Format helpers
# ------------------------------------------------------------------

def test_format_markdown_produces_table(tmp_path):
    conn, _, qnaire_id, _, _ = _setup(tmp_path)
    rows = data_dictionary(conn, qnaire_id)
    md = format_data_dict_markdown(rows, title="Test Dict")
    assert "# Test Dict" in md
    assert "| # |" in md
    assert "q.smoke" in md


def test_format_markdown_empty(tmp_path):
    md = format_data_dict_markdown([], title="Empty")
    assert "No active questions" in md


def test_format_csv_produces_header(tmp_path):
    conn, _, qnaire_id, _, _ = _setup(tmp_path)
    rows = data_dictionary(conn, qnaire_id)
    csv_text = format_data_dict_csv(rows)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("order,")
    assert len(lines) == 3  # header + 2 data rows


def test_format_csv_empty(tmp_path):
    assert format_data_dict_csv([]) == ""
