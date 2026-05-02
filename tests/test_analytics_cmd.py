"""
Unit tests for the `quickq analytics` CLI shim (closes quickq-io-yn5).

The interactive path (duckdb -ui) and the --queries-file path against a real
DuckDB are exercised by tests/test_walkthrough_e2e.py. These tests focus on
the error paths that the e2e flow can't easily reach: missing OLAP file,
missing duckdb binary.
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from quickq.cli import main


def test_analytics_missing_olap(tmp_path: Path) -> None:
    """Missing OLAP file should error with a hint to run quickq refresh."""
    runner = CliRunner()
    olap = tmp_path / "nope.duckdb"
    result = runner.invoke(main, ["analytics", str(olap)])
    assert result.exit_code != 0
    assert "not found" in result.output
    assert "quickq refresh" in result.output


def test_analytics_default_path_missing(tmp_path: Path, monkeypatch) -> None:
    """No argument → defaults to ./analytics.duckdb; same not-found path."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["analytics"])
    assert result.exit_code != 0
    assert "analytics.duckdb" in result.output


def test_analytics_no_duckdb_binary(tmp_path: Path, monkeypatch) -> None:
    """If the duckdb binary is missing, error with platform install hints."""
    # Create a real OLAP file so we get past the existence check
    olap = tmp_path / "analytics.duckdb"
    olap.write_bytes(b"")  # contents don't matter; we never open it
    # Pretend duckdb isn't on PATH
    monkeypatch.setattr("shutil.which", lambda name: None if name == "duckdb" else name)

    runner = CliRunner()
    result = runner.invoke(main, ["analytics", str(olap)])
    assert result.exit_code != 0
    assert "duckdb binary not found" in result.output
    assert "brew install duckdb" in result.output
