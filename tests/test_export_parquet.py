"""
Tests for quickq.export_parquet.
"""
import json
from pathlib import Path

import pytest

from quickq.schema import init_oltp
from quickq.loader import load_yaml
from quickq.parser_fhir_response import import_fhir_response
from quickq.olap_schema import refresh
from quickq.export_parquet import export_parquet, _DEFAULT_TABLES

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Shared fixture: populated OLAP
# ---------------------------------------------------------------------------

@pytest.fixture()
def populated_olap(tmp_path):
    oltp_path = str(tmp_path / "study.db")
    olap_path = str(tmp_path / "analytics.duckdb")

    conn = init_oltp(oltp_path)
    load_yaml(conn, str(FIXTURES / "phq9.yaml"))
    responses = json.loads((FIXTURES / "phq9_fhir_responses.json").read_text())
    for r in responses:
        import_fhir_response(conn, r)
    conn.close()

    refresh(olap_path, oltp_path)
    return olap_path, tmp_path


# ---------------------------------------------------------------------------
# Basic export
# ---------------------------------------------------------------------------

def test_exports_parquet_files(populated_olap):
    olap_path, tmp_path = populated_olap
    out_dir = tmp_path / "export"

    result = export_parquet(olap_path, out_dir)

    assert len(result.files) > 0
    for table, path in result.files.items():
        assert Path(path).exists()
        assert Path(path).suffix == ".parquet"
        assert Path(path).name == f"{table}.parquet"


def test_core_tables_exported(populated_olap):
    olap_path, tmp_path = populated_olap
    result = export_parquet(olap_path, tmp_path / "export")

    exported = set(result.files.keys())
    for table in ["fact_response", "dim_question", "dim_respondent",
                  "dim_session", "dim_questionnaire"]:
        assert table in exported, f"{table} not exported"


def test_fact_response_has_rows(populated_olap):
    olap_path, tmp_path = populated_olap
    result = export_parquet(olap_path, tmp_path / "export")

    assert result.rows["fact_response"] > 0


def test_row_counts_accurate(populated_olap):
    """Row counts in the result should match what DuckDB reports."""
    import duckdb
    olap_path, tmp_path = populated_olap
    result = export_parquet(olap_path, tmp_path / "export")

    conn = duckdb.connect(olap_path, read_only=True)
    for table, reported_count in result.rows.items():
        actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert reported_count == actual, f"{table}: reported {reported_count}, actual {actual}"
    conn.close()


def test_parquet_files_readable(populated_olap):
    """Each exported file should be a valid Parquet file readable by DuckDB."""
    import duckdb
    olap_path, tmp_path = populated_olap
    out_dir = tmp_path / "export"
    result = export_parquet(olap_path, out_dir)

    conn = duckdb.connect()
    for table, path in result.files.items():
        rows = conn.execute(f"SELECT COUNT(*) FROM '{path}'").fetchone()[0]
        assert rows == result.rows[table]
    conn.close()


def test_output_dir_created(populated_olap):
    olap_path, tmp_path = populated_olap
    out_dir = tmp_path / "deep" / "nested" / "export"
    assert not out_dir.exists()

    export_parquet(olap_path, out_dir)

    assert out_dir.exists()


# ---------------------------------------------------------------------------
# Selective export
# ---------------------------------------------------------------------------

def test_selective_table_export(populated_olap):
    olap_path, tmp_path = populated_olap
    result = export_parquet(
        olap_path, tmp_path / "export",
        tables=["fact_response", "dim_question"],
    )

    assert set(result.files.keys()) == {"fact_response", "dim_question"}


def test_unknown_table_goes_to_skipped(populated_olap):
    olap_path, tmp_path = populated_olap
    result = export_parquet(
        olap_path, tmp_path / "export",
        tables=["fact_response", "nonexistent_table"],
    )

    assert "fact_response" in result.files
    assert "nonexistent_table" in result.skipped
    assert "nonexistent_table" not in result.files


# ---------------------------------------------------------------------------
# Overwrite behaviour
# ---------------------------------------------------------------------------

def test_error_if_output_exists(populated_olap):
    olap_path, tmp_path = populated_olap
    out_dir = tmp_path / "export"
    export_parquet(olap_path, out_dir, tables=["fact_response"])

    with pytest.raises(FileExistsError, match="already exist"):
        export_parquet(olap_path, out_dir, tables=["fact_response"])


def test_overwrite_flag(populated_olap):
    olap_path, tmp_path = populated_olap
    out_dir = tmp_path / "export"
    export_parquet(olap_path, out_dir, tables=["fact_response"])

    result = export_parquet(olap_path, out_dir, tables=["fact_response"], overwrite=True)
    assert "fact_response" in result.files


# ---------------------------------------------------------------------------
# Result metadata
# ---------------------------------------------------------------------------

def test_result_metadata(populated_olap):
    olap_path, tmp_path = populated_olap
    out_dir = tmp_path / "export"
    result = export_parquet(olap_path, out_dir)

    assert result.olap_path == str(olap_path)
    assert result.output_dir == str(out_dir)
    assert isinstance(result.files, dict)
    assert isinstance(result.rows, dict)
    assert isinstance(result.skipped, list)
