import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from quickq.schema import init_oltp


EXPECTED_OLTP_TABLES = {
    "vocabulary",
    "concept",
    "concept_relationship",
    "study",
    "questionnaire",
    "section",
    "response_option_set",
    "question",
    "response_option",
    "grid_row",
    "grid_column",
    "questionnaire_question",
    "skip_rule",
    "scoring_rule",
    "scoring_rule_item",
    "scoring_category",
    "respondent",
    "response_session",
    "response",
    "admin_event",
    "data_quality_flag",
    # versioning
    "question_lineage",
    "question_equivalence",
    "questionnaire_version_diff",
    "person_map",
    # administration
    "study_errata_log",
}


def test_oltp_schema_creates_all_tables(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    actual = {r["name"] for r in rows}
    assert actual == EXPECTED_OLTP_TABLES


def test_oltp_foreign_keys_enabled(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    result = conn.execute("PRAGMA foreign_keys").fetchone()
    assert result[0] == 1


def test_oltp_wal_mode(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    result = conn.execute("PRAGMA journal_mode").fetchone()
    assert result[0] == "wal"


def test_concept_plane_round_trip(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    conn.execute("INSERT INTO vocabulary (vocabulary_id, vocabulary_name) VALUES ('Local', 'Local')")
    conn.execute("""
        INSERT INTO concept (concept_name, domain_id, vocabulary_id, concept_class_id, concept_code)
        VALUES ('Test concept', 'Question', 'Local', 'Survey', 'LOCAL:001')
    """)
    conn.commit()
    row = conn.execute("SELECT concept_name FROM concept WHERE concept_code = 'LOCAL:001'").fetchone()
    assert row["concept_name"] == "Test concept"


def test_instrument_plane_round_trip(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    conn.execute("INSERT INTO study (name) VALUES ('Test Study')")
    conn.execute("""
        INSERT INTO questionnaire (study_id, name, canonical_url)
        VALUES (1, 'Intake Survey', 'http://quickq.io/test/intake')
    """)
    conn.execute("""
        INSERT INTO question (link_id, question_text, question_type)
        VALUES ('q1', 'Do you smoke?', 'single_choice')
    """)
    conn.execute("""
        INSERT INTO questionnaire_question (questionnaire_id, question_id, display_order)
        VALUES (1, 1, 0)
    """)
    conn.execute("""
        INSERT INTO response_option (question_id, option_text, option_value, display_order)
        VALUES (1, 'Yes', 'yes', 0), (1, 'No', 'no', 1)
    """)
    conn.commit()

    options = conn.execute(
        "SELECT option_value FROM response_option WHERE question_id = 1 ORDER BY display_order"
    ).fetchall()
    assert [r["option_value"] for r in options] == ["yes", "no"]


def test_response_plane_round_trip(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    conn.executescript("""
        INSERT INTO study (name) VALUES ('Test Study');
        INSERT INTO questionnaire (study_id, name) VALUES (1, 'Intake');
        INSERT INTO question (link_id, question_text, question_type) VALUES ('q1', 'Smoke?', 'single_choice');
        INSERT INTO questionnaire_question (questionnaire_id, question_id, display_order) VALUES (1, 1, 0);
        INSERT INTO response_option (question_id, option_text, option_value, display_order) VALUES (1, 'Yes', 'yes', 0);
        INSERT INTO respondent (study_id, external_id) VALUES (1, 'P001');
        INSERT INTO response_session (questionnaire_id, respondent_id, is_complete) VALUES (1, 1, 1);
        INSERT INTO response (session_id, qq_id, option_id) VALUES (1, 1, 1);
    """)
    conn.commit()

    row = conn.execute("""
        SELECT ro.option_value
        FROM response r
        JOIN response_option ro ON r.option_id = ro.option_id
        WHERE r.session_id = 1
    """).fetchone()
    assert row["option_value"] == "yes"


def test_skip_rule_fk_integrity(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    conn.executescript("""
        INSERT INTO study (name) VALUES ('S');
        INSERT INTO questionnaire (study_id, name) VALUES (1, 'Q');
        INSERT INTO question (link_id, question_text, question_type) VALUES ('q1', 'A?', 'single_choice');
        INSERT INTO question (link_id, question_text, question_type) VALUES ('q2', 'B?', 'text');
        INSERT INTO questionnaire_question (questionnaire_id, question_id, display_order) VALUES (1, 1, 0);
        INSERT INTO questionnaire_question (questionnaire_id, question_id, display_order) VALUES (1, 2, 1);
        INSERT INTO skip_rule (qq_id, trigger_qq_id, operator, trigger_value) VALUES (2, 1, '=', 'yes');
    """)
    conn.commit()

    rule = conn.execute("SELECT operator, trigger_value FROM skip_rule WHERE qq_id = 2").fetchone()
    assert rule["operator"] == "="
    assert rule["trigger_value"] == "yes"
