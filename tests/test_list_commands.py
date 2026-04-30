"""Tests for quickq list / ls commands."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from quickq.schema import init_oltp
from quickq.authoring import insert_study, insert_questionnaire
from quickq.models import QuestionnaireDef
from quickq.compliance import set_study_metadata
from quickq.cli import main


def _seed(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    s1 = insert_study(conn, name="Depression Study", principal_investigator="Smith, Jane",
                      irb_number="IRB-001")
    set_study_metadata(conn, s1, doi="10.5281/zenodo.1", start_date="2024-01-01")
    s2 = insert_study(conn, name="Anxiety Study")
    insert_questionnaire(conn, QuestionnaireDef(
        name="PHQ-9", canonical_url="http://quickq.io/phq9", version="1.0",
    ), study_id=s1)
    insert_questionnaire(conn, QuestionnaireDef(
        name="GAD-7", canonical_url="http://quickq.io/gad7", version="2.0",
    ), study_id=s2)
    conn.commit()
    return db, s1, s2


# ---------------------------------------------------------------------------
# list studies
# ---------------------------------------------------------------------------

def test_list_studies_exits_zero(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "studies", str(db)])
    assert result.exit_code == 0


def test_list_studies_shows_names(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "studies", str(db)])
    assert "Depression Study" in result.output
    assert "Anxiety Study" in result.output


def test_list_studies_shows_pi(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "studies", str(db)])
    assert "Smith, Jane" in result.output


def test_list_studies_shows_doi(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "studies", str(db)])
    assert "10.5281/zenodo.1" in result.output


def test_list_studies_empty_db(tmp_path):
    db = tmp_path / "empty.db"
    init_oltp(db)
    result = CliRunner().invoke(main, ["list", "studies", str(db)])
    assert result.exit_code == 0
    assert "No studies found" in result.output


def test_ls_studies_alias(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["ls", "studies", str(db)])
    assert result.exit_code == 0
    assert "Depression Study" in result.output


# ---------------------------------------------------------------------------
# list surveys
# ---------------------------------------------------------------------------

def test_list_surveys_exits_zero(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "surveys", str(db)])
    assert result.exit_code == 0


def test_list_surveys_shows_all(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "surveys", str(db)])
    assert "PHQ-9" in result.output
    assert "GAD-7" in result.output


def test_list_surveys_shows_version(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "surveys", str(db)])
    assert "2.0" in result.output


def test_list_surveys_shows_canonical_url(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "surveys", str(db)])
    assert "http://quickq.io/phq9" in result.output


def test_list_surveys_filter_by_study(tmp_path):
    db, s1, s2 = _seed(tmp_path)
    result = CliRunner().invoke(main, ["list", "surveys", str(db), "--study-id", str(s1)])
    assert "PHQ-9" in result.output
    assert "GAD-7" not in result.output


def test_list_surveys_empty(tmp_path):
    db = tmp_path / "empty.db"
    init_oltp(db)
    result = CliRunner().invoke(main, ["list", "surveys", str(db)])
    assert result.exit_code == 0
    assert "No surveys found" in result.output


def test_ls_surveys_alias(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["ls", "surveys", str(db)])
    assert result.exit_code == 0
    assert "PHQ-9" in result.output


# ---------------------------------------------------------------------------
# list library
# ---------------------------------------------------------------------------

def test_list_library_exits_zero(tmp_path):
    db = tmp_path / "study.db"
    from quickq.library_loader import load_all_libraries
    conn = init_oltp(db)
    load_all_libraries(conn)
    conn.commit()
    result = CliRunner().invoke(main, ["list", "library", str(db)])
    assert result.exit_code == 0


def test_library_not_flat_command(tmp_path):
    db, _, _ = _seed(tmp_path)
    result = CliRunner().invoke(main, ["library", str(db)])
    assert result.exit_code != 0
