"""
Tests for the synthetic response generator (quickq seed).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.loader import load_yaml
from quickq.seed import seed_responses

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def phq9_db(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(str(db))
    load_yaml(conn, FIXTURES / "phq9.yaml")
    return conn


def test_seed_creates_correct_session_count(phq9_db):
    ids = seed_responses(phq9_db, questionnaire_id=1, n=10, rng_seed=42)
    assert len(ids) == 10


def test_seed_sessions_marked_complete(phq9_db):
    seed_responses(phq9_db, questionnaire_id=1, n=5, rng_seed=1)
    count = phq9_db.execute(
        "SELECT COUNT(*) FROM response_session WHERE is_complete = 1"
    ).fetchone()[0]
    assert count == 5


def test_seed_responses_written(phq9_db):
    seed_responses(phq9_db, questionnaire_id=1, n=5, rng_seed=1)
    count = phq9_db.execute("SELECT COUNT(*) FROM response").fetchone()[0]
    assert count > 0


def test_seed_reproducible_with_same_seed(phq9_db):
    ids1 = seed_responses(phq9_db, questionnaire_id=1, n=3, rng_seed=99)
    # Re-seed — respondents already exist, but new sessions are created
    ids2 = seed_responses(phq9_db, questionnaire_id=1, n=3, rng_seed=99)
    # Both runs should produce the same number of sessions
    assert len(ids1) == len(ids2) == 3
    # Sessions should be distinct (different session_ids)
    assert set(ids1).isdisjoint(set(ids2))


def test_seed_admin_mode(phq9_db):
    seed_responses(phq9_db, questionnaire_id=1, n=3, admin_mode="api")
    modes = phq9_db.execute(
        "SELECT DISTINCT admin_mode FROM response_session"
    ).fetchall()
    assert [r[0] for r in modes] == ["api"]


def test_seed_creates_respondents(phq9_db):
    seed_responses(phq9_db, questionnaire_id=1, n=5, rng_seed=7)
    count = phq9_db.execute("SELECT COUNT(*) FROM respondent").fetchone()[0]
    assert count == 5


def test_seed_with_study_id(phq9_db):
    phq9_db.execute("INSERT INTO study (name) VALUES ('Test Study')")
    phq9_db.commit()
    study_id = phq9_db.execute("SELECT study_id FROM study").fetchone()[0]
    seed_responses(phq9_db, questionnaire_id=1, n=3, study_id=study_id, rng_seed=5)
    count = phq9_db.execute(
        "SELECT COUNT(*) FROM respondent WHERE study_id = ?", (study_id,)
    ).fetchone()[0]
    assert count == 3


def test_seed_cli(tmp_path):
    from click.testing import CliRunner
    from quickq.cli import main

    db = tmp_path / "study.db"
    conn = init_oltp(str(db))
    load_yaml(conn, FIXTURES / "phq9.yaml")
    conn.close()

    runner = CliRunner()
    result = runner.invoke(main, ["seed", str(db), "1", "--n", "20", "--seed", "42"])
    assert result.exit_code == 0, result.output
    assert "Seeded 20 response session(s)" in result.output
