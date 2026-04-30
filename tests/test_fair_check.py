"""
Tests for quickq.fair_check: FAIR self-audit.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from quickq.schema import init_oltp
from quickq.authoring import insert_study, insert_questionnaire, upsert_question, place_question
from quickq.models import QuestionnaireDef, QuestionDef
from quickq.compliance import set_study_metadata
from quickq.fair_check import fair_check, format_fair_check, FAIRCheckResult

from click.testing import CliRunner
from quickq.cli import main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_study(tmp_path: Path) -> tuple:
    """Study with no metadata fields set — should fail most checks."""
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    study_id = insert_study(conn, name="Minimal Study")
    conn.commit()
    return db, conn, study_id


def _full_study(tmp_path: Path) -> tuple:
    """Study with all metadata fields populated — should pass all checks."""
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    study_id = insert_study(
        conn,
        name="Full Study",
        principal_investigator="Dr. Jane Smith",
        irb_number="IRB-2025-001",
    )
    set_study_metadata(
        conn, study_id,
        description="A well-documented study.",
        population="Adults 18+ in the United States",
        license="CC-BY-4.0",
        protocol_url="https://clinicaltrials.gov/ct2/show/NCT00000001",
        doi="10.5281/zenodo.0000001",
        geographic_scope="United States",
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
    conn.commit()
    return db, conn, study_id


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------

def test_returns_fair_check_result(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    assert isinstance(result, FAIRCheckResult)
    assert result.study_id == study_id
    assert result.study_name == "Minimal Study"


def test_unknown_study_raises(tmp_path):
    db, conn, _ = _minimal_study(tmp_path)
    with pytest.raises(ValueError, match="No study found"):
        fair_check(conn, 9999)


def test_all_principles_covered(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    principles = {i.principle for i in result.items}
    assert {"F1", "F2", "A1", "A2", "I1", "I2", "R1.1", "R1.2", "R1.3"}.issubset(principles)


# ---------------------------------------------------------------------------
# Minimal study — expected failures
# ---------------------------------------------------------------------------

def test_minimal_study_fails_f1(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    f1 = next(i for i in result.items if i.principle == "F1")
    assert f1.status == "fail"


def test_minimal_study_fails_r1_1_license(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    r1_1 = next(i for i in result.items if i.principle == "R1.1")
    assert r1_1.status == "fail"


def test_minimal_study_fails_r1_2_protocol(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    r1_2 = next(i for i in result.items if i.principle == "R1.2")
    assert r1_2.status == "fail"


def test_minimal_study_not_ready_to_share(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    assert not result.is_ready_to_share


# ---------------------------------------------------------------------------
# A1 always passes
# ---------------------------------------------------------------------------

def test_a1_always_passes(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    a1 = next(i for i in result.items if i.principle == "A1")
    assert a1.status == "pass"


# ---------------------------------------------------------------------------
# Full study — expected passes
# ---------------------------------------------------------------------------

def test_full_study_passes_f1(tmp_path):
    db, conn, study_id = _full_study(tmp_path)
    result = fair_check(conn, study_id)
    f1 = next(i for i in result.items if i.principle == "F1")
    assert f1.status == "pass"


def test_full_study_passes_r1_1(tmp_path):
    db, conn, study_id = _full_study(tmp_path)
    result = fair_check(conn, study_id)
    r1_1 = next(i for i in result.items if i.principle == "R1.1")
    assert r1_1.status == "pass"


def test_full_study_is_ready_to_share(tmp_path):
    db, conn, study_id = _full_study(tmp_path)
    result = fair_check(conn, study_id)
    assert result.is_ready_to_share


def test_full_study_no_failures(tmp_path):
    db, conn, study_id = _full_study(tmp_path)
    result = fair_check(conn, study_id)
    assert result.failures == []


# ---------------------------------------------------------------------------
# Protocol URL without DOI → warn not fail on F1/A2
# ---------------------------------------------------------------------------

def test_protocol_url_without_doi_warns_f1(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    set_study_metadata(conn, study_id, protocol_url="https://osf.io/abc")
    result = fair_check(conn, study_id)
    f1 = next(i for i in result.items if i.principle == "F1")
    assert f1.status == "warn"


# ---------------------------------------------------------------------------
# Concept code coverage
# ---------------------------------------------------------------------------

def test_unmapped_questions_warn_i2(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    study_id = insert_study(conn, name="Study")
    q_id = insert_questionnaire(
        conn,
        QuestionnaireDef(name="Q", canonical_url="http://example.com/q", version="1.0"),
        study_id=study_id,
    )
    q = upsert_question(conn, QuestionDef(link_id="q.1", text="Question?", type="text"))
    place_question(conn, q_id, q, display_order=1)
    conn.commit()

    result = fair_check(conn, study_id)
    i2 = next(i for i in result.items if i.principle == "I2")
    assert i2.status in ("warn", "fail")


# ---------------------------------------------------------------------------
# format_fair_check
# ---------------------------------------------------------------------------

def test_format_includes_all_principles(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    text = format_fair_check(result)
    for principle in ("F1", "F2", "A1", "A2", "I1", "I2", "R1.1", "R1.2", "R1.3"):
        assert principle in text


def test_format_includes_summary_line(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    result = fair_check(conn, study_id)
    text = format_fair_check(result)
    assert "Summary:" in text
    assert "Status:" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_fair_check_exits_nonzero_on_failures(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["compliance", "fair-check", str(db), "--study-id", str(study_id)])
    assert result.exit_code != 0


def test_cli_fair_check_exits_zero_on_full_study(tmp_path):
    db, conn, study_id = _full_study(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["compliance", "fair-check", str(db), "--study-id", str(study_id)])
    assert result.exit_code == 0


def test_cli_fair_check_json_output(tmp_path):
    db, conn, study_id = _minimal_study(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["compliance", "fair-check", str(db), "--study-id", str(study_id), "--json"])
    data = json.loads(result.output)
    assert "items" in data
    assert "is_ready_to_share" in data
    assert isinstance(data["items"], list)
