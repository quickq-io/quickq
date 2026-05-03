"""
End-to-end ETL test: OLTP → OLAP refresh.

Pipeline:
  1. Load PHQ-9 questionnaire into a fresh SQLite OLTP
  2. Import 5 synthetic FHIR QuestionnaireResponse records
  3. Run quickq refresh → DuckDB OLAP
  4. Assert fact rows, dimension rows, aggregates, and PHQ-9 scores

PHQ-9 expected scores (derived from fixture option values):
  synthetic-001: 1   → Minimal depression
  synthetic-002: 7   → Mild depression
  synthetic-003: 14  → Moderate depression
  synthetic-004: 20  → Severe depression
  synthetic-005: 27  → Severe depression
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.loader import load_yaml
from quickq.parser_fhir_response import import_fhir_response
from quickq.olap_schema import init_olap, refresh

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------
# Shared fixture
# ------------------------------------------------------------------

@pytest.fixture()
def phq9_olap(tmp_path):
    """Load PHQ-9 + 5 responses, run refresh, return open OLAP connection."""
    oltp_path = str(tmp_path / "study.db")
    olap_path = str(tmp_path / "analytics.duckdb")

    conn = init_oltp(oltp_path)
    load_yaml(conn, str(FIXTURES / "phq9.yaml"))
    responses = json.loads((FIXTURES / "phq9_fhir_responses.json").read_text())
    for r in responses:
        import_fhir_response(conn, r)
    conn.close()

    stats = refresh(olap_path, oltp_path)
    oconn = init_olap(olap_path, oltp_path)
    return oconn, stats


# ------------------------------------------------------------------
# Refresh stats
# ------------------------------------------------------------------

def test_refresh_stats(phq9_olap):
    _, stats = phq9_olap
    assert stats["sessions_loaded"] == 5
    assert stats["rows_loaded"] > 0      # at least one fact row
    assert stats["scores_computed"] == 5


def test_refresh_log_complete(phq9_olap):
    oconn, _ = phq9_olap
    row = oconn.execute(
        "SELECT status, rows_loaded FROM refresh_log ORDER BY refresh_id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "complete"
    assert row[1] > 0


def test_second_refresh_is_incremental(tmp_path):
    """A second refresh with no new OLTP rows loads 0 new fact rows."""
    oltp_path = str(tmp_path / "study.db")
    olap_path = str(tmp_path / "analytics.duckdb")

    conn = init_oltp(oltp_path)
    load_yaml(conn, str(FIXTURES / "phq9.yaml"))
    responses = json.loads((FIXTURES / "phq9_fhir_responses.json").read_text())
    for r in responses:
        import_fhir_response(conn, r)
    conn.close()

    refresh(olap_path, oltp_path)
    stats2 = refresh(olap_path, oltp_path)

    assert stats2["rows_loaded"] == 0
    assert stats2["sessions_loaded"] == 0


# ------------------------------------------------------------------
# Dimension tables
# ------------------------------------------------------------------

def test_dim_questionnaire_populated(phq9_olap):
    oconn, _ = phq9_olap
    row = oconn.execute(
        "SELECT name FROM dim_questionnaire WHERE questionnaire_id = 1"
    ).fetchone()
    assert row is not None
    assert "PHQ" in row[0]


def test_dim_question_populated(phq9_olap):
    oconn, _ = phq9_olap
    n = oconn.execute("SELECT count(*) FROM dim_question").fetchone()[0]
    assert n == 10  # 9 core + difficulty


def test_dim_respondent_populated(phq9_olap):
    oconn, _ = phq9_olap
    ids = {r[0] for r in oconn.execute("SELECT external_id FROM dim_respondent").fetchall()}
    assert ids == {
        "synthetic-001", "synthetic-002", "synthetic-003",
        "synthetic-004", "synthetic-005",
    }


def test_dim_session_populated(phq9_olap):
    oconn, _ = phq9_olap
    n = oconn.execute("SELECT count(*) FROM dim_session").fetchone()[0]
    assert n == 5
    complete = oconn.execute(
        "SELECT count(*) FROM dim_session WHERE is_complete = true"
    ).fetchone()[0]
    assert complete == 5


def test_dim_date_populated(phq9_olap):
    oconn, _ = phq9_olap
    n = oconn.execute("SELECT count(*) FROM dim_date").fetchone()[0]
    assert n >= 1


# ------------------------------------------------------------------
# Fact table
# ------------------------------------------------------------------

def test_fact_response_row_count(phq9_olap):
    oconn, stats = phq9_olap
    n = oconn.execute("SELECT count(*) FROM fact_response").fetchone()[0]
    assert n == stats["rows_loaded"]
    # PHQ-9: 5 sessions × (9 or 10 answers) = 49 total
    assert n == 49


def test_fact_response_option_value_set(phq9_olap):
    """Choice answers should have option_value populated for scoring."""
    oconn, _ = phq9_olap
    n_missing = oconn.execute("""
        SELECT count(*) FROM fact_response f
        JOIN dim_question dq USING (question_id)
        WHERE dq.question_type IN ('single_choice', 'likert')
          AND f.option_value IS NULL
    """).fetchone()[0]
    assert n_missing == 0


def test_fact_response_numeric_from_option_value(phq9_olap):
    """response_numeric should be set for choice items (used for scoring)."""
    oconn, _ = phq9_olap
    n_missing = oconn.execute("""
        SELECT count(*) FROM fact_response f
        JOIN dim_question dq USING (question_id)
        WHERE dq.question_type = 'single_choice'
          AND f.response_numeric IS NULL
    """).fetchone()[0]
    assert n_missing == 0


# ------------------------------------------------------------------
# Scores
# ------------------------------------------------------------------

_EXPECTED_SCORES = {
    "synthetic-001": (1.0,  "Minimal depression"),
    "synthetic-002": (7.0,  "Mild depression"),
    "synthetic-003": (14.0, "Moderate depression"),
    "synthetic-004": (20.0, "Severe depression"),
    "synthetic-005": (27.0, "Severe depression"),
}


def test_phq9_scores_correct(phq9_olap):
    oconn, _ = phq9_olap
    rows = oconn.execute("""
        SELECT r.external_id, ars.score_raw, ars.score_category
        FROM   agg_respondent_scores ars
        JOIN   dim_session s  ON ars.session_id   = s.session_id
        JOIN   dim_respondent r ON s.respondent_id = r.respondent_id
        ORDER  BY r.external_id
    """).fetchall()
    assert len(rows) == 5
    for ext_id, score_raw, category in rows:
        expected_score, expected_cat = _EXPECTED_SCORES[ext_id]
        assert score_raw == expected_score,  f"{ext_id}: score {score_raw} != {expected_score}"
        assert category  == expected_cat,    f"{ext_id}: cat {category!r} != {expected_cat!r}"


def test_phq9_scores_items_answered(phq9_olap):
    oconn, _ = phq9_olap
    rows = oconn.execute(
        "SELECT items_answered, items_total FROM agg_respondent_scores"
    ).fetchall()
    for answered, total in rows:
        assert total == 9   # PHQ-9 scoring rule covers 9 items
        assert answered == 9


# ------------------------------------------------------------------
# Aggregates
# ------------------------------------------------------------------

def test_agg_session_completion(phq9_olap):
    oconn, _ = phq9_olap
    row = oconn.execute("""
        SELECT sum(n_started), sum(n_completed)
        FROM agg_session_completion
    """).fetchone()
    assert row[0] == 5
    assert row[1] == 5


def test_agg_question_distribution_pct_sums(phq9_olap):
    """pct values for a given question should sum to ~100% (within rounding)."""
    oconn, _ = phq9_olap
    rows = oconn.execute("""
        SELECT question_id, sum(pct) AS total_pct
        FROM agg_question_distribution
        GROUP BY question_id
    """).fetchall()
    assert len(rows) > 0
    for qid, total_pct in rows:
        assert abs(total_pct - 100.0) < 1.0, f"q{qid}: pct sum {total_pct} != 100"


# ------------------------------------------------------------------
# Cast-failure surfacing (closes t2m)
# ------------------------------------------------------------------

def test_refresh_flags_session_timestamp_cast_failures(tmp_path):
    """When a response_session has a non-ISO timestamp that DuckDB cannot
    cast, the refresh writes a data_quality_flag with rule_name='cast_failure'
    so analysts see the loss instead of a silent NULL in dim_session."""
    import sqlite3
    from quickq.schema import init_oltp
    from quickq.olap_schema import refresh

    db_path = tmp_path / "study.db"
    init_oltp(db_path)
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        INSERT INTO study (study_id, name) VALUES (1, 'S');
        INSERT INTO questionnaire (questionnaire_id, study_id, name, version,
            canonical_url, fhir_status)
            VALUES (1, 1, 'Q', '1.0', 'http://ex/q', 'active');
        INSERT INTO respondent (respondent_id, study_id, external_id)
            VALUES (1, 1, 'r1');
        INSERT INTO response_session (session_id, questionnaire_id, respondent_id,
            started_at, completed_at, is_complete, admin_mode)
            VALUES (1, 1, 1, 'tomorrow', '2024-99-99', 1, 'api');
    """)
    raw.commit()
    raw.close()

    olap_path = str(tmp_path / "analytics.duckdb")
    stats = refresh(olap_path, str(db_path))
    assert stats.get("cast_failures_flagged", 0) >= 2

    raw = sqlite3.connect(str(db_path))
    flags = raw.execute(
        "SELECT message FROM data_quality_flag WHERE rule_name = 'cast_failure' "
        "ORDER BY flag_id"
    ).fetchall()
    raw.close()
    assert len(flags) >= 2
    messages = " | ".join(m[0] for m in flags)
    assert "started_at" in messages and "tomorrow" in messages
    assert "completed_at" in messages and "2024-99-99" in messages


def test_refresh_cast_failure_dedup(tmp_path):
    """Re-running refresh against the same bad data should not accumulate
    duplicate cast_failure flags."""
    import sqlite3
    from quickq.schema import init_oltp
    from quickq.olap_schema import refresh

    db_path = tmp_path / "study.db"
    init_oltp(db_path)
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        INSERT INTO study (study_id, name) VALUES (1, 'S');
        INSERT INTO questionnaire (questionnaire_id, study_id, name, version,
            canonical_url, fhir_status)
            VALUES (1, 1, 'Q', '1.0', 'http://ex/q', 'active');
        INSERT INTO respondent (respondent_id, study_id, external_id)
            VALUES (1, 1, 'r1');
        INSERT INTO response_session (session_id, questionnaire_id, respondent_id,
            started_at, is_complete, admin_mode)
            VALUES (1, 1, 1, 'not-a-date', 1, 'api');
    """)
    raw.commit()
    raw.close()

    olap_path = str(tmp_path / "analytics.duckdb")
    refresh(olap_path, str(db_path))
    refresh(olap_path, str(db_path))  # second run should be a no-op for flagging

    raw = sqlite3.connect(str(db_path))
    n_flags = raw.execute(
        "SELECT COUNT(*) FROM data_quality_flag WHERE rule_name = 'cast_failure'"
    ).fetchone()[0]
    raw.close()
    assert n_flags == 1, f"expected dedup; got {n_flags} flags after two refreshes"


def test_refresh_no_cast_failures_on_clean_data(tmp_path):
    """A study with all-valid timestamps produces zero cast_failure flags."""
    import sqlite3
    from quickq.schema import init_oltp
    from quickq.olap_schema import refresh

    db_path = tmp_path / "study.db"
    init_oltp(db_path)
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        INSERT INTO study (study_id, name, start_date, end_date)
            VALUES (1, 'S', '2024-01-01', '2024-12-31');
        INSERT INTO questionnaire (questionnaire_id, study_id, name, version,
            canonical_url, fhir_status)
            VALUES (1, 1, 'Q', '1.0', 'http://ex/q', 'active');
        INSERT INTO respondent (respondent_id, study_id, external_id)
            VALUES (1, 1, 'r1');
        INSERT INTO response_session (session_id, questionnaire_id, respondent_id,
            started_at, completed_at, is_complete, admin_mode)
            VALUES (1, 1, 1, '2024-06-01T10:00:00', '2024-06-01T10:15:00', 1, 'api');
    """)
    raw.commit()
    raw.close()

    olap_path = str(tmp_path / "analytics.duckdb")
    stats = refresh(olap_path, str(db_path))
    assert stats.get("cast_failures_flagged", 0) == 0


# ------------------------------------------------------------------
# Typed BOOLEAN column in fact_response (closes 104)
# ------------------------------------------------------------------

def test_fact_response_boolean_column_populated_for_boolean_questions(tmp_path):
    """For a boolean-typed question, fact_response.response_boolean is
    populated (BOOLEAN type) while response_text retains the original
    'true'/'false' string for round-trip."""
    import sqlite3
    from quickq.schema import init_oltp
    from quickq.olap_schema import refresh, init_olap

    db_path = tmp_path / "study.db"
    init_oltp(db_path)
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        INSERT INTO study (study_id, name) VALUES (1, 'S');
        INSERT INTO questionnaire (questionnaire_id, study_id, name, version,
            canonical_url, fhir_status)
            VALUES (1, 1, 'Q', '1.0', 'http://ex/q', 'active');
        INSERT INTO question (question_id, link_id, question_text, question_type)
            VALUES (1, 'b1', 'Yes/No', 'boolean');
        INSERT INTO questionnaire_question (qq_id, questionnaire_id, question_id, display_order)
            VALUES (1, 1, 1, 0);
        INSERT INTO respondent (respondent_id, study_id, external_id) VALUES (1, 1, 'r1');
        INSERT INTO respondent (respondent_id, study_id, external_id) VALUES (2, 1, 'r2');
        INSERT INTO response_session (session_id, questionnaire_id, respondent_id,
            started_at, completed_at, is_complete, admin_mode)
            VALUES (1, 1, 1, '2024-06-01T10:00:00', '2024-06-01T10:01:00', 1, 'api');
        INSERT INTO response_session (session_id, questionnaire_id, respondent_id,
            started_at, completed_at, is_complete, admin_mode)
            VALUES (2, 1, 2, '2024-06-02T10:00:00', '2024-06-02T10:01:00', 1, 'api');
        INSERT INTO response (session_id, qq_id, response_text)
            VALUES (1, 1, 'true');
        INSERT INTO response (session_id, qq_id, response_text)
            VALUES (2, 1, 'false');
    """)
    raw.commit()
    raw.close()

    olap_path = str(tmp_path / "analytics.duckdb")
    refresh(olap_path, str(db_path))
    oconn = init_olap(olap_path, str(db_path))

    rows = oconn.execute(
        "SELECT session_id, response_text, response_boolean "
        "FROM fact_response ORDER BY session_id"
    ).fetchall()
    assert len(rows) == 2

    # Column type is real BOOLEAN; values match the strings
    sid1, txt1, bool1 = rows[0]
    sid2, txt2, bool2 = rows[1]
    assert (sid1, txt1, bool1) == (1, "true", True)
    assert (sid2, txt2, bool2) == (2, "false", False)
    # Specifically: bool1 is Python True (not the string 'true')
    assert bool1 is True
    assert bool2 is False


def test_fact_response_boolean_null_for_non_boolean_questions(tmp_path):
    """response_boolean is NULL for non-boolean question types, even when
    response_text happens to be 'true' / 'false' (that case is only meaningful
    for boolean questions)."""
    import sqlite3
    from quickq.schema import init_oltp
    from quickq.olap_schema import refresh, init_olap

    db_path = tmp_path / "study.db"
    init_oltp(db_path)
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        INSERT INTO study (study_id, name) VALUES (1, 'S');
        INSERT INTO questionnaire (questionnaire_id, study_id, name, version,
            canonical_url, fhir_status)
            VALUES (1, 1, 'Q', '1.0', 'http://ex/q', 'active');
        INSERT INTO question (question_id, link_id, question_text, question_type)
            VALUES (1, 't1', 'Notes', 'text');
        INSERT INTO questionnaire_question (qq_id, questionnaire_id, question_id, display_order)
            VALUES (1, 1, 1, 0);
        INSERT INTO respondent (respondent_id, study_id, external_id) VALUES (1, 1, 'r1');
        INSERT INTO response_session (session_id, questionnaire_id, respondent_id,
            started_at, completed_at, is_complete, admin_mode)
            VALUES (1, 1, 1, '2024-06-01T10:00:00', '2024-06-01T10:01:00', 1, 'api');
        INSERT INTO response (session_id, qq_id, response_text)
            VALUES (1, 1, 'true');   -- string 'true' but the question is text-type
    """)
    raw.commit()
    raw.close()

    olap_path = str(tmp_path / "analytics.duckdb")
    refresh(olap_path, str(db_path))
    oconn = init_olap(olap_path, str(db_path))

    bool_val = oconn.execute(
        "SELECT response_boolean FROM fact_response"
    ).fetchone()[0]
    assert bool_val is None


def test_existing_olap_gets_boolean_column_via_migration(tmp_path):
    """An OLAP DB that pre-dates response_boolean should have the column
    added on next refresh via the ALTER TABLE IF NOT EXISTS guard."""
    import duckdb
    from quickq.schema import init_oltp
    from quickq.olap_schema import refresh, init_olap

    db_path = tmp_path / "study.db"
    init_oltp(db_path)

    # Initialize OLAP at current schema, then drop response_boolean to
    # simulate a database created before the column existed.
    olap_path = str(tmp_path / "analytics.duckdb")
    oconn = init_olap(olap_path, str(db_path))
    oconn.execute("ALTER TABLE fact_response DROP COLUMN response_boolean")
    oconn.close()

    pre = duckdb.connect(olap_path, read_only=True)
    pre_cols = {r[0] for r in pre.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'fact_response'"
    ).fetchall()}
    pre.close()
    assert "response_boolean" not in pre_cols, "test setup did not drop the column"

    # Refresh should re-add it via ALTER TABLE ... ADD COLUMN IF NOT EXISTS.
    refresh(olap_path, str(db_path))

    post = duckdb.connect(olap_path, read_only=True)
    post_cols = {r[0] for r in post.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'fact_response'"
    ).fetchall()}
    post.close()
    assert "response_boolean" in post_cols
