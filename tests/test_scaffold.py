"""Tests for quickq.scaffold — the `quickq new` repo scaffolder."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quickq.scaffold import scaffold, ScaffoldError, _slug_to_name

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def test_scaffold_creates_expected_layout(tmp_path):
    target = tmp_path / "my-study"
    result = scaffold(target, init_git=False)

    assert result.target == target
    expected = {
        "README.md",
        ".gitignore",
        "instrument.yaml",
        "library/README.md",
        "docs/README.md",
    }
    assert set(result.files_created) == expected
    for rel in expected:
        assert (target / rel).is_file()


def test_scaffold_does_not_create_scripts_directory(tmp_path):
    """scripts/ wrappers were vestigial; the scaffold no longer ships them.
    quickq commands are invoked directly (see README quick-start)."""
    target = tmp_path / "no-scripts"
    scaffold(target, init_git=False)
    assert not (target / "scripts").exists()


def test_readme_contains_study_name(tmp_path):
    target = tmp_path / "gout-checkin"
    scaffold(target, init_git=False)
    readme = (target / "README.md").read_text()
    assert "Gout Checkin" in readme


def test_explicit_name_overrides_default(tmp_path):
    target = tmp_path / "weird-slug"
    scaffold(target, init_git=False, name="The Real Display Name")
    readme = (target / "README.md").read_text()
    assert "The Real Display Name" in readme
    assert "Weird Slug" not in readme


def test_slug_to_name_handles_dashes_and_underscores():
    assert _slug_to_name("gout-study") == "Gout Study"
    assert _slug_to_name("phq9_baseline") == "Phq9 Baseline"
    assert _slug_to_name("my-multi-word-slug") == "My Multi Word Slug"
    assert _slug_to_name("") == ""


# ---------------------------------------------------------------------------
# --from yaml
# ---------------------------------------------------------------------------

def test_scaffold_from_yaml_copies_content(tmp_path):
    src_yaml = tmp_path / "source.yaml"
    src_yaml.write_text("# my custom yaml\nquestionnaire:\n  name: Custom\n")

    target = tmp_path / "study"
    scaffold(target, from_yaml=src_yaml, init_git=False)

    assert (target / "instrument.yaml").read_text() == src_yaml.read_text()


def test_scaffold_from_yaml_missing_file_errors(tmp_path):
    target = tmp_path / "study"
    with pytest.raises(ScaffoldError, match="does not exist"):
        scaffold(target, from_yaml=tmp_path / "nonexistent.yaml", init_git=False)


# ---------------------------------------------------------------------------
# Existing target / safety
# ---------------------------------------------------------------------------

def test_scaffold_errors_on_existing_target(tmp_path):
    target = tmp_path / "exists"
    target.mkdir()
    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold(target, init_git=False)


# ---------------------------------------------------------------------------
# Git init
# ---------------------------------------------------------------------------

def test_scaffold_with_git_initializes_repo(tmp_path):
    target = tmp_path / "git-study"
    result = scaffold(target, init_git=True)

    if shutil.which("git") is None:
        # CI without git: scaffold still succeeds, just no git_initialized=True
        assert result.git_initialized is False
        return

    assert result.git_initialized is True
    assert (target / ".git").is_dir()


def test_scaffold_no_git_skips_init(tmp_path):
    target = tmp_path / "nogit-study"
    result = scaffold(target, init_git=False)
    assert result.git_initialized is False
    assert not (target / ".git").exists()


# ---------------------------------------------------------------------------
# Loadability of the scaffolded YAML
# ---------------------------------------------------------------------------

def test_scaffolded_yaml_loads_into_study_db(tmp_path):
    """The blank-template instrument.yaml should be loadable by quickq load
    without error, producing a working study.db with the example question."""
    target = tmp_path / "loadable-study"
    scaffold(target, init_git=False)

    db = target / "study.db"
    yaml = target / "instrument.yaml"

    # init + load using the public Python API rather than subprocessing the CLI
    from quickq.schema import init_oltp
    from quickq.loader import load_yaml

    conn = init_oltp(db)
    qid = load_yaml(conn, yaml)
    assert isinstance(qid, int)

    link_ids = {r[0] for r in conn.execute("SELECT link_id FROM question").fetchall()}
    assert "example.q1" in link_ids
    conn.close()


# ---------------------------------------------------------------------------
# End-to-end via direct quickq commands (smoke; requires quickq on PATH)
# ---------------------------------------------------------------------------

def test_scaffolded_yaml_loads_via_cli(tmp_path):
    """Verify the canonical quick-start in the README works: `quickq init` +
    `quickq load instrument.yaml` against a freshly-scaffolded repo produces
    a working study.db."""
    if shutil.which("quickq") is None:
        pytest.skip("quickq CLI not on PATH; skipping end-to-end CLI test")

    target = tmp_path / "e2e-study"
    scaffold(target, init_git=False)

    init_result = subprocess.run(
        ["quickq", "init", "study.db"], cwd=target, capture_output=True, text=True
    )
    assert init_result.returncode == 0, f"quickq init failed: {init_result.stderr}"

    load_result = subprocess.run(
        ["quickq", "load", "instrument.yaml", "study.db"],
        cwd=target, capture_output=True, text=True,
    )
    assert load_result.returncode == 0, f"quickq load failed: {load_result.stderr}"
    assert (target / "study.db").is_file()
