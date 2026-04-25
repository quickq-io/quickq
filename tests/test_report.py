"""
Tests for the Markdown report generator (renderer_md.py).
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
from quickq.renderer_md import generate_report

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def phq9_report(tmp_path):
    oltp_path = str(tmp_path / "study.db")
    olap_path = str(tmp_path / "analytics.duckdb")

    conn = init_oltp(oltp_path)
    load_yaml(conn, str(FIXTURES / "phq9.yaml"))
    responses = json.loads((FIXTURES / "phq9_fhir_responses.json").read_text())
    for r in responses:
        import_fhir_response(conn, r)
    conn.close()

    refresh(olap_path, oltp_path)
    oconn = init_olap(olap_path, oltp_path)
    return generate_report(oconn, 1)


def test_report_is_string(phq9_report):
    assert isinstance(phq9_report, str)
    assert len(phq9_report) > 100


def test_report_has_title(phq9_report):
    assert "# PHQ-9" in phq9_report


def test_report_has_overview(phq9_report):
    assert "## Overview" in phq9_report
    assert "Respondents" in phq9_report
    assert "5" in phq9_report           # 5 respondents
    assert "100.0%" in phq9_report      # completion rate


def test_report_has_scores(phq9_report):
    assert "## Scores" in phq9_report
    assert "PHQ-9 Total Score" in phq9_report
    assert "Minimal depression" in phq9_report
    assert "Severe depression" in phq9_report


def test_report_score_stats(phq9_report):
    # mean of [1, 7, 14, 20, 27] = 13.8, median = 14
    assert "mean 13.8" in phq9_report
    assert "median 14.0" in phq9_report


def test_report_has_questions(phq9_report):
    assert "## Questions" in phq9_report
    assert "Little interest or pleasure" in phq9_report


def test_report_has_option_labels(phq9_report):
    assert "Not at all" in phq9_report
    assert "Nearly every day" in phq9_report


def test_report_has_percentages(phq9_report):
    assert "%" in phq9_report


def test_unknown_questionnaire_raises(tmp_path):
    oltp_path = str(tmp_path / "study.db")
    olap_path = str(tmp_path / "analytics.duckdb")

    conn = init_oltp(oltp_path)
    load_yaml(conn, str(FIXTURES / "phq9.yaml"))
    conn.close()
    refresh(olap_path, oltp_path)
    oconn = init_olap(olap_path, oltp_path)

    with pytest.raises(ValueError, match="not found"):
        generate_report(oconn, 999)


def test_report_markdown_structure(phq9_report):
    """Verify the report has valid Markdown table rows."""
    table_rows = [l for l in phq9_report.splitlines() if l.startswith("|")]
    assert len(table_rows) > 5
    # Every table row should have at least two pipe chars
    for row in table_rows:
        assert row.count("|") >= 2
