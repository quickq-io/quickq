"""
End-to-end walkthrough validation.

Builds a wheel from this repo, installs it non-editable into a fresh venv,
and runs the CLI flow that ``docs/tutorials/end-to-end.md`` documents:
init → load (with iterative replace) → fhir export → seed → refresh →
report → SQL queries against the OLAP.

Why a non-editable install: this is the only path that validates the wheel
actually ships sql/*.sql and library/*.yaml (regression guard for the
packaging bug closed in quickq-io-yp8). An editable install would shadow
the wheel and silently mask packaging breakage.

This test reproduces the assertions made by hand during the
quickq-io-ie4 walkthrough re-run. Asserts on behaviour (return codes,
log substrings, row counts) rather than exact CLI output so that
cosmetic changes to the docs don't break it. When the walkthrough text
itself evolves, update both the doc and this test together.

Run with:
    uv run pytest tests/test_walkthrough_e2e.py -m e2e -v

Slow (~30s — builds a wheel and creates a fresh venv).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures: wheel build + fresh venv install (module-scoped so we pay the
# cost once per pytest invocation, not per test function).
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def quickq_wheel(tmp_path_factory):
    """Build a non-editable wheel from this repo. Yields the wheel path."""
    out = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out), str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"uv build failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected 1 wheel, found {wheels}"
    return wheels[0]


@pytest.fixture(scope="module")
def quickq_bin(tmp_path_factory, quickq_wheel):
    """Create a fresh venv and install the wheel non-editable. Yields the quickq CLI path."""
    venv = tmp_path_factory.mktemp("venv")
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv / "bin" / "python"), str(quickq_wheel)],
        check=True, capture_output=True,
    )
    quickq = venv / "bin" / "quickq"
    assert quickq.exists(), f"quickq binary not at {quickq}"
    return str(quickq)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, capture text I/O, fail loudly on non-zero exit."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _stage_yaml(stage: int) -> str:
    """Return the YAML for the given walkthrough stage (1..4)."""
    if stage == 1:
        return """
questionnaire:
  name: "Gout Symptoms Check-In"
  canonical_url: "http://example.com/instruments/gout-checkin"
  version: "1.0"
  questions:
    - { link_id: gout.last_attack, text: "When did you last have a gout attack?", type: date }
    - { link_id: gout.pain_now,    text: "Pain 0-10",                              type: numeric, range: [0, 10] }
"""
    if stage == 2:
        return """
questionnaire:
  name: "Gout Symptoms Check-In"
  canonical_url: "http://example.com/instruments/gout-checkin"
  version: "1.0"
  option_sets:
    frequency:
      - { text: "Never", value: "never" }
      - { text: "Often", value: "often" }
  questions:
    - { link_id: gout.last_attack, text: "When did you last have a gout attack?", type: date }
    - { link_id: gout.pain_now,    text: "Pain 0-10",                              type: numeric, range: [0, 10] }
    - { link_id: gout.alcohol,     text: "Alcohol freq?",                          type: single_choice, options: $frequency }
"""
    if stage == 3:
        return """
questionnaire:
  name: "Gout Symptoms Check-In"
  canonical_url: "http://example.com/instruments/gout-checkin"
  version: "1.0"
  option_sets:
    frequency:
      - { text: "Never", value: "never" }
      - { text: "Often", value: "often" }
    joints:
      - { text: "Big toe", value: "big_toe" }
      - { text: "Knee",    value: "knee" }
  questions:
    - { link_id: gout.last_attack, text: "When did you last have a gout attack?", type: date }
    - link_id: gout.joints_today
      text: "Which joints were affected? Select all that apply."
      type: multiple_choice
      options: $joints
      show_when: { question: gout.last_attack, operator: exists }
    - { link_id: gout.pain_now, text: "Pain 0-10", type: numeric, range: [0, 10] }
    - { link_id: gout.alcohol,  text: "Alcohol freq?", type: single_choice, options: $frequency }
    - { library: gout.notes }
"""
    if stage == 4:
        return _stage_yaml(3) + """    - { library: phq9.1 }
    - { library: phq9.2 }
"""
    raise ValueError(stage)


# ---------------------------------------------------------------------------
# The walkthrough as a single sequential test. Splitting into multiple
# tests would either rebuild the wheel/venv per test (slow) or leak state
# via shared fixtures (brittle). One linear test matches the walkthrough
# narrative.
# ---------------------------------------------------------------------------

def test_walkthrough_round_trip(quickq_bin, tmp_path):
    """Walk the documented end-to-end flow on a non-editable install."""
    db = tmp_path / "study.db"
    yaml_path = tmp_path / "gout.yaml"

    # Step 3 — init with library
    r = run([quickq_bin, "init", str(db), "--with-library"])
    assert r.returncode == 0, r.stderr
    assert "library questions" in r.stdout, "init should report library question count"

    # Step 4 Stage 1 — fresh load
    yaml_path.write_text(_stage_yaml(1))
    r = run([quickq_bin, "load", str(yaml_path), str(db)])
    assert r.returncode == 0, r.stderr
    assert "Loaded questionnaire id=1" in r.stdout

    # Step 4 Stages 2–4 — silent replace each time (validates ddw upsert)
    for stage in (2, 3, 4):
        yaml_path.write_text(_stage_yaml(stage))
        r = run([quickq_bin, "load", str(yaml_path), str(db)])
        assert r.returncode == 0, f"Stage {stage} failed: {r.stderr}"
        assert "Replaced questionnaire id=1" in r.stdout, (
            f"Stage {stage} should silent-replace; got: {r.stdout}"
        )

    # quickq list surveys — the walkthrough's smoke check
    r = run([quickq_bin, "list", "surveys", str(db)])
    assert r.returncode == 0
    assert "Gout Symptoms Check-In" in r.stdout

    # Step 5 — FHIR export with all 8 linkIds present
    fhir_path = tmp_path / "gout.json"
    r = run([quickq_bin, "fhir", "export", str(db), "1", "--output", str(fhir_path)])
    assert r.returncode == 0
    import json
    fhir = json.loads(fhir_path.read_text())
    link_ids = [item["linkId"] for item in fhir.get("item", [])]
    assert link_ids == [
        "gout.last_attack", "gout.joints_today", "gout.pain_now",
        "gout.alcohol", "gout.notes", "phq9.1", "phq9.2",
    ], f"unexpected linkIds: {link_ids}"

    # Step 9 — seed 50 synthetic responses
    r = run([quickq_bin, "seed", str(db), "1", "--n", "50", "--seed", "42"])
    assert r.returncode == 0
    assert "50 response session" in r.stdout

    # Now that responses exist, an attempt to reload the same version must
    # fail with the version-bump guidance (validates ddw error path).
    r = run([quickq_bin, "load", str(yaml_path), str(db)])
    assert r.returncode != 0, "load should fail when responses exist"
    assert "response session" in r.stderr.lower() or "response session" in r.stdout.lower()
    assert "version" in (r.stderr + r.stdout).lower(), (
        "error message should steer the user to bump version"
    )

    # Bumping the version succeeds and creates a new questionnaire id
    bumped_yaml = _stage_yaml(4).replace('version: "1.0"', 'version: "1.1"')
    yaml_path.write_text(bumped_yaml)
    r = run([quickq_bin, "load", str(yaml_path), str(db)])
    assert r.returncode == 0, r.stderr
    assert "Loaded questionnaire id=2" in r.stdout

    # Step 10 — refresh produces facts; Step 11 — report renders
    olap = tmp_path / "analytics.duckdb"
    r = run([quickq_bin, "refresh", str(db), str(olap)])
    assert r.returncode == 0
    assert "fact rows" in r.stdout

    report_path = tmp_path / "report.md"
    r = run([quickq_bin, "report", str(olap), str(db), "1", "--output", str(report_path)])
    assert r.returncode == 0
    report = report_path.read_text()
    assert "Gout Symptoms Check-In" in report
    assert "Respondents" in report

    # Step 12 — three SQL queries against the OLAP must return sensible data
    import duckdb
    conn = duckdb.connect(str(olap), read_only=True)

    # Q1: joint distribution non-empty
    rows = conn.execute("""
        SELECT ro.option_text AS joint, COUNT(*) AS n
        FROM fact_response f
        JOIN dim_question q USING (question_id)
        JOIN dim_response_option ro USING (option_id)
        WHERE q.link_id = 'gout.joints_today'
        GROUP BY ro.option_text
    """).fetchall()
    assert rows, "joint distribution query returned no rows"

    # Q2: alcohol vs pain — joins across two question types must produce data
    rows = conn.execute("""
        SELECT alcohol.option_text, COUNT(*) AS n,
               ROUND(AVG(pain.response_numeric), 1) AS avg_pain
        FROM (SELECT f.session_id, ro.option_text FROM fact_response f
              JOIN dim_question q USING (question_id)
              JOIN dim_response_option ro USING (option_id)
              WHERE q.link_id = 'gout.alcohol') alcohol
        JOIN (SELECT f.session_id, f.response_numeric FROM fact_response f
              JOIN dim_question q USING (question_id)
              WHERE q.link_id = 'gout.pain_now') pain USING (session_id)
        GROUP BY alcohol.option_text
    """).fetchall()
    assert rows, "alcohol-vs-pain query returned no rows"

    # Q3: skip logic was respected — every session that answered joints
    # must also have answered the date question.
    skip_respected, skip_violated = conn.execute("""
        SELECT
            SUM(CASE WHEN date_answered AND joints_answered THEN 1 ELSE 0 END),
            SUM(CASE WHEN NOT date_answered AND joints_answered THEN 1 ELSE 0 END)
        FROM (SELECT session_id,
                     BOOL_OR(q.link_id = 'gout.last_attack')   AS date_answered,
                     BOOL_OR(q.link_id = 'gout.joints_today')  AS joints_answered
              FROM fact_response f
              JOIN dim_question q USING (question_id)
              GROUP BY session_id);
    """).fetchone()
    assert skip_violated == 0, f"skip-logic violated: {skip_violated} sessions"
    assert skip_respected and skip_respected > 0
