"""
Tests for the federated query executor.

Covers:
  - SQL validation: blocked columns, missing aggregation, invalid SQL
  - Cell suppression: count column detection, row removal, threshold
  - Full pipeline: query against a real OLAP database
  - CLI: federated-query command
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from quickq.federated import (
    FederatedQueryResult,
    _find_count_columns,
    _suppress_small_cells,
    _validate_query,
    run_federated_query,
    result_to_dict,
)
from quickq.schema import init_oltp
from quickq.parser_fhir import import_fhir
from quickq.parser_fhir_response import import_fhir_response
from quickq.olap_schema import refresh, init_olap

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------
# Validation: blocked columns
# ------------------------------------------------------------------

def test_blocked_respondent_id():
    with pytest.raises(ValueError, match="respondent_id"):
        _validate_query("SELECT respondent_id, COUNT(*) AS n FROM fact_response GROUP BY respondent_id")


def test_blocked_session_id():
    with pytest.raises(ValueError, match="session_id"):
        _validate_query("SELECT session_id, COUNT(*) AS n FROM fact_response GROUP BY session_id")


def test_blocked_external_id():
    with pytest.raises(ValueError, match="external_id"):
        _validate_query("SELECT external_id, COUNT(*) AS n FROM dim_respondent GROUP BY external_id")


def test_non_blocked_columns_pass():
    # Should not raise
    _validate_query("SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id")


# ------------------------------------------------------------------
# Validation: aggregate requirement
# ------------------------------------------------------------------

def test_no_aggregate_rejected():
    with pytest.raises(ValueError, match="aggregate"):
        _validate_query("SELECT question_id, response_text FROM fact_response")


def test_select_star_without_aggregate_rejected():
    with pytest.raises(ValueError, match="aggregate"):
        _validate_query("SELECT * FROM fact_response")


def test_count_satisfies_aggregate():
    _validate_query("SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id")


def test_avg_satisfies_aggregate():
    _validate_query("SELECT question_id, AVG(response_numeric) AS mean FROM fact_response GROUP BY question_id")


def test_sum_satisfies_aggregate():
    _validate_query("SELECT study_id, SUM(response_numeric) AS total FROM fact_response GROUP BY study_id")


def test_stddev_satisfies_aggregate():
    _validate_query("SELECT question_id, STDDEV(response_numeric) AS sd FROM fact_response GROUP BY question_id")


# ------------------------------------------------------------------
# Validation: invalid SQL
# ------------------------------------------------------------------

def test_invalid_sql_raises():
    with pytest.raises(ValueError, match="Invalid SQL"):
        _validate_query("THIS IS NOT SQL $$$$")


def test_non_select_rejected():
    with pytest.raises(ValueError, match="SELECT"):
        _validate_query("INSERT INTO fact_response VALUES (1)")


# ------------------------------------------------------------------
# Validation: window functions
# ------------------------------------------------------------------

def test_window_function_rejected():
    with pytest.raises(ValueError, match="Window"):
        _validate_query(
            "SELECT link_id, COUNT(*) OVER (PARTITION BY link_id) AS n FROM fact_response"
        )


def test_window_function_with_group_by_rejected():
    with pytest.raises(ValueError, match="Window"):
        _validate_query(
            "SELECT link_id, ROW_NUMBER() OVER (ORDER BY link_id) AS rn, "
            "COUNT(*) AS n FROM fact_response GROUP BY link_id"
        )


# ------------------------------------------------------------------
# Validation: blocked file-reading functions
# ------------------------------------------------------------------

def test_read_csv_blocked():
    with pytest.raises(ValueError, match="read_csv"):
        _validate_query("SELECT COUNT(*) AS n FROM read_csv('/data/file.csv')")


def test_sqlite_scan_blocked():
    with pytest.raises(ValueError, match="sqlite_scan"):
        _validate_query("SELECT COUNT(*) AS n FROM sqlite_scan('study.db', 'respondent')")


def test_read_parquet_blocked():
    with pytest.raises(ValueError, match="read_parquet"):
        _validate_query("SELECT COUNT(*) AS n FROM read_parquet('/data/file.parquet')")


# ------------------------------------------------------------------
# Validation: aggregate alias collection
# ------------------------------------------------------------------

def test_validate_returns_count_alias():
    aliases = _validate_query(
        "SELECT question_id, COUNT(*) AS responses FROM fact_response GROUP BY question_id"
    )
    assert "responses" in aliases


def test_validate_returns_sum_alias():
    aliases = _validate_query(
        "SELECT study_id, SUM(response_numeric) AS total_score FROM fact_response GROUP BY study_id"
    )
    assert "total_score" in aliases


def test_validate_does_not_return_avg_alias():
    # AVG is not a count — should not be in forced suppression set
    aliases = _validate_query(
        "SELECT question_id, AVG(response_numeric) AS mean_val, COUNT(*) AS n "
        "FROM fact_response GROUP BY question_id"
    )
    assert "mean_val" not in aliases
    assert "n" in aliases


def test_validate_returns_empty_for_standard_count_name():
    # "n" will be caught by name pattern anyway; alias set may or may not include it
    aliases = _validate_query(
        "SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id"
    )
    # n is in the alias set (it IS a COUNT alias)
    assert "n" in aliases


# ------------------------------------------------------------------
# Cell suppression: column detection
# ------------------------------------------------------------------

@pytest.mark.parametrize("col_name", ["n", "count", "cnt", "freq", "total",
                                       "n_completed", "session_count", "response_n",
                                       "frequency"])
def test_count_column_detected(col_name: str):
    rows = [{col_name: 10}, {col_name: 20}]
    assert col_name in _find_count_columns([col_name], rows)


@pytest.mark.parametrize("col_name", ["question_id", "link_id", "pct", "mean",
                                       "option_text", "response_date"])
def test_non_count_column_not_detected(col_name: str):
    rows = [{col_name: 42}]
    assert col_name not in _find_count_columns([col_name], rows)


def test_count_column_float_value_not_detected():
    # 7.5 is not a whole number — not a count
    rows = [{"n": 7.5}]
    assert "n" not in _find_count_columns(["n"], rows)


def test_count_column_returns_empty_for_no_rows():
    assert _find_count_columns(["n"], []) == []


def test_forced_alias_detected_regardless_of_name():
    # "responses" doesn't match the name pattern but is forced by the validator
    rows = [{"category": "A", "responses": 3}, {"category": "B", "responses": 10}]
    found = _find_count_columns(["category", "responses"], rows, forced={"responses"})
    assert "responses" in found


def test_forced_alias_not_added_for_non_integer_values():
    # AVG output with a forced alias should not be suppressed (float, not integer)
    rows = [{"category": "A", "mean_val": 4.7}]
    found = _find_count_columns(["mean_val"], rows, forced={"mean_val"})
    assert "mean_val" not in found


# ------------------------------------------------------------------
# Cell suppression: row removal
# ------------------------------------------------------------------

def test_suppress_removes_rows_below_threshold():
    rows = [
        {"category": "A", "n": 10},
        {"category": "B", "n": 3},   # below min_cell=5
        {"category": "C", "n": 7},
    ]
    kept, suppressed = _suppress_small_cells(rows, ["n"], min_cell=5)
    assert suppressed == 1
    assert len(kept) == 2
    assert all(r["n"] >= 5 for r in kept)


def test_suppress_keeps_all_rows_above_threshold():
    rows = [{"n": 5}, {"n": 10}, {"n": 100}]
    kept, suppressed = _suppress_small_cells(rows, ["n"], min_cell=5)
    assert suppressed == 0
    assert len(kept) == 3


def test_suppress_removes_row_if_any_count_col_below():
    rows = [{"n": 10, "n_male": 2, "n_female": 8}]  # n_male below threshold
    kept, suppressed = _suppress_small_cells(rows, ["n", "n_male", "n_female"], min_cell=5)
    assert suppressed == 1
    assert kept == []


def test_suppress_no_count_cols_returns_all_rows():
    rows = [{"question_id": 1}, {"question_id": 2}]
    kept, suppressed = _suppress_small_cells(rows, [], min_cell=5)
    assert suppressed == 0
    assert len(kept) == 2


def test_suppress_threshold_is_exclusive():
    # Exactly min_cell should be kept (threshold is strict <, not <=)
    rows = [{"n": 5}]
    kept, suppressed = _suppress_small_cells(rows, ["n"], min_cell=5)
    assert suppressed == 0
    assert len(kept) == 1


# ------------------------------------------------------------------
# Full pipeline: real OLAP database
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def phq9_olap(tmp_path_factory):
    """PHQ-9 questionnaire + 5 responses → refreshed OLAP."""
    tmp = tmp_path_factory.mktemp("federated")
    oltp_path = str(tmp / "study.db")
    olap_path = str(tmp / "analytics.duckdb")

    conn = init_oltp(oltp_path)
    import_fhir(conn, (FIXTURES / "phq9_fhir_questionnaire.json").read_text())
    conn.commit()

    responses = json.loads((FIXTURES / "phq9_fhir_responses.json").read_text())
    for r in responses:
        import_fhir_response(conn, r)
    conn.close()

    refresh(olap_path, oltp_path)
    return olap_path


def test_valid_aggregate_query_runs(phq9_olap):
    result = run_federated_query(
        phq9_olap,
        "SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id",
    )
    assert isinstance(result, FederatedQueryResult)
    assert result.rows_total > 0
    assert "n" in result.count_columns


def test_result_contains_expected_columns(phq9_olap):
    result = run_federated_query(
        phq9_olap,
        "SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id",
    )
    assert "question_id" in result.columns
    assert "n" in result.columns


def test_suppression_applied(phq9_olap):
    # min_cell=1000 ensures all real rows (n=5 max) are suppressed
    result = run_federated_query(
        phq9_olap,
        "SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id",
        min_cell=1000,
    )
    assert result.rows_suppressed == result.rows_total
    assert result.rows == []


def test_no_suppression_when_all_cells_large_enough(phq9_olap):
    result = run_federated_query(
        phq9_olap,
        "SELECT COUNT(*) AS n FROM fact_response",
        min_cell=1,
    )
    assert result.rows_suppressed == 0
    assert len(result.rows) == 1


def test_blocked_column_raises_at_runtime(phq9_olap):
    with pytest.raises(ValueError, match="respondent_id"):
        run_federated_query(
            phq9_olap,
            "SELECT respondent_id, COUNT(*) AS n FROM fact_response GROUP BY respondent_id",
        )


def test_result_to_dict_structure(phq9_olap):
    result = run_federated_query(
        phq9_olap,
        "SELECT COUNT(*) AS n FROM fact_response",
        min_cell=1,
    )
    d = result_to_dict(result)
    assert "query_hash" in d
    assert "rows" in d
    assert "columns" in d
    assert "disclosure_control" in d
    dc = d["disclosure_control"]
    assert "suppression_threshold" in dc
    assert "rows_suppressed" in dc
    assert "rows_total" in dc
    assert "count_columns" in dc


def test_non_standard_count_alias_suppressed(phq9_olap):
    # "responses" doesn't match the name pattern but IS a COUNT alias —
    # suppression must still apply (this was the gap being closed).
    result = run_federated_query(
        phq9_olap,
        "SELECT question_id, COUNT(*) AS responses FROM fact_response GROUP BY question_id",
        min_cell=1000,
    )
    assert "responses" in result.count_columns
    assert result.rows_suppressed == result.rows_total


def test_avg_alias_not_suppressed_independently(phq9_olap):
    # AVG output should not trigger suppression on its own value
    # (suppression would only come from a companion COUNT column if present)
    result = run_federated_query(
        phq9_olap,
        "SELECT question_id, AVG(response_numeric) AS mean_val "
        "FROM fact_response GROUP BY question_id",
        min_cell=5,
    )
    assert "mean_val" not in result.count_columns


def test_query_hash_is_deterministic(phq9_olap):
    sql = "SELECT COUNT(*) AS n FROM fact_response"
    r1 = run_federated_query(phq9_olap, sql, min_cell=1)
    r2 = run_federated_query(phq9_olap, sql, min_cell=1)
    assert r1.query_hash == r2.query_hash


# ------------------------------------------------------------------
# Re-identification risk
# ------------------------------------------------------------------

from quickq.federated import _reidentification_risk


def test_risk_high_for_zero_rows():
    assert _reidentification_risk(0) == "high"


def test_risk_high_for_one_row():
    assert _reidentification_risk(1) == "high"


def test_risk_high_for_two_rows():
    assert _reidentification_risk(2) == "high"


def test_risk_medium_for_three_rows():
    assert _reidentification_risk(3) == "medium"


def test_risk_medium_for_four_rows():
    assert _reidentification_risk(4) == "medium"


def test_risk_low_for_five_rows():
    assert _reidentification_risk(5) == "low"


def test_risk_low_for_many_rows():
    assert _reidentification_risk(100) == "low"


def test_result_includes_risk_field(phq9_olap):
    result = run_federated_query(
        phq9_olap,
        "SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id",
        min_cell=1,
    )
    assert result.reidentification_risk in ("low", "medium", "high")


def test_result_to_dict_includes_risk(phq9_olap):
    result = run_federated_query(
        phq9_olap,
        "SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id",
        min_cell=1,
    )
    d = result_to_dict(result)
    assert "reidentification_risk" in d["disclosure_control"]


def test_high_risk_includes_note_in_dict(phq9_olap):
    # min_cell=1000 suppresses all but possibly 0 rows → high risk
    result = run_federated_query(
        phq9_olap,
        "SELECT COUNT(*) AS n FROM fact_response",
        min_cell=1000,
    )
    d = result_to_dict(result)
    dc = d["disclosure_control"]
    if dc["reidentification_risk"] != "low":
        assert "reidentification_risk_note" in dc


def test_low_risk_omits_note_in_dict(phq9_olap):
    result = run_federated_query(
        phq9_olap,
        "SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id",
        min_cell=1,
    )
    if result.reidentification_risk == "low":
        d = result_to_dict(result)
        assert "reidentification_risk_note" not in d["disclosure_control"]


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

from click.testing import CliRunner
from quickq.cli import main


def test_cli_run_query(phq9_olap, tmp_path):
    sql_file = tmp_path / "query.sql"
    sql_file.write_text("SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id")
    runner = CliRunner()
    result = runner.invoke(main, ["federated", "query", str(sql_file), phq9_olap])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "rows" in data
    assert "disclosure_control" in data


def test_cli_run_query_blocked_column_error(phq9_olap, tmp_path):
    sql_file = tmp_path / "bad.sql"
    sql_file.write_text(
        "SELECT respondent_id, COUNT(*) AS n FROM fact_response GROUP BY respondent_id"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["federated", "query", str(sql_file), phq9_olap])
    assert result.exit_code != 0
    assert "respondent_id" in result.output


def test_cli_run_query_output_file(phq9_olap, tmp_path):
    sql_file = tmp_path / "query.sql"
    out_file = tmp_path / "results.json"
    sql_file.write_text("SELECT COUNT(*) AS n FROM fact_response")
    runner = CliRunner()
    result = runner.invoke(main, ["federated", "query", str(sql_file), phq9_olap, "--output", str(out_file)])
    assert result.exit_code == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text())
    assert "rows" in data


def test_cli_run_query_min_cell_flag(phq9_olap, tmp_path):
    sql_file = tmp_path / "query.sql"
    sql_file.write_text("SELECT question_id, COUNT(*) AS n FROM fact_response GROUP BY question_id")
    runner = CliRunner()
    result = runner.invoke(main, ["federated", "query", str(sql_file), phq9_olap, "--min-cell", "1000"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["rows"] == []
    assert data["disclosure_control"]["rows_suppressed"] > 0
