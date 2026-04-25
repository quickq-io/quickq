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
