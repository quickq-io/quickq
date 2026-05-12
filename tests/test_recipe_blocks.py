"""CI gate for SQL recipes embedded in documentation pages.

Every fenced ```sql block in the reference and tutorial pages below is
extracted and executed against a freshly built demo database. The assertion
is "no error" — recipes are allowed to return zero rows (placeholders like
'your_question_link_id' are intentional in some patterns), but they must
parse and run.

Closes quickq-io-i3j. R and Python recipe blocks are out of scope per the
SQL-canonical framing — see quickq-io-ejk for the reframe plan that splits
the canonical SQL (gated here) from the wrapper pages (manual verification).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
EXAMPLES = REPO / "examples"
LIBRARY = REPO / "quickq" / "library"

# ---------------------------------------------------------------------------
# Page -> demo mapping
# ---------------------------------------------------------------------------
#
# Two demos are needed because the recipe pages target different ones:
#
# - phq9_visits: produced by scripts/generate_demo.py — PHQ-9 + prenatal
#   visits, with the v_phq9_scores / v_prenatal_visits demo views. Used by
#   the tutorials and skip-logic-qc.md.
#
# - health_intake: built per the docs/reference/analysis-recipes.md "Option
#   A" path — examples/health_intake_demo.yaml seeded with n=500. Used by
#   query-patterns.md (which references demo.* link_ids across all 11
#   question types).
#
# Each test parametrizes over the SQL blocks in a single doc file.

PAGES = {
    # path                                     demo fixture name
    "docs/reference/query-patterns.md":        "health_intake",
    "docs/reference/skip-logic-qc.md":         "phq9_visits",
    "docs/tutorials/analytics.md":             "phq9_visits",
    "docs/tutorials/data-quality.md":          "phq9_visits",
    "docs/tutorials/end-to-end.md":            "phq9_visits",
}


# ---------------------------------------------------------------------------
# Block extraction
# ---------------------------------------------------------------------------

SQL_BLOCK_RE = re.compile(r"```sql\n(.*?)\n```", re.DOTALL)


def extract_sql_blocks(path: Path) -> list[tuple[int, str]]:
    """Return [(approximate_line_number, sql_text), ...] for fenced ```sql blocks."""
    text = path.read_text()
    blocks = []
    for match in SQL_BLOCK_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        blocks.append((line, match.group(1)))
    return blocks


# ---------------------------------------------------------------------------
# Demo database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def phq9_visits_demo(tmp_path_factory) -> tuple[Path, Path]:
    """Provide the PHQ-9 + prenatal-visits demo from scripts/generate_demo.py.

    The script writes to REPO/demo/ (its hardcoded output path). If that
    directory is missing or its analytics.duckdb is stale, rebuild via
    subprocess. Returns the canonical paths; per-test logic in
    test_sql_block_runs copies them into the test's tmp dir so cwd-relative
    ATTACH statements resolve.
    """
    canonical_dir = REPO / "demo"
    study_db = canonical_dir / "study.db"
    analytics_db = canonical_dir / "analytics.duckdb"

    if not study_db.exists() or not analytics_db.exists():
        env_python = sys.executable
        result = subprocess.run(
            [env_python, str(REPO / "scripts" / "generate_demo.py")],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.fail(
                f"generate_demo.py failed:\n--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}"
            )

    assert study_db.exists(), "demo/study.db not produced"
    assert analytics_db.exists(), "demo/analytics.duckdb not produced"
    return study_db, analytics_db


@pytest.fixture(scope="session")
def health_intake_demo(tmp_path_factory) -> tuple[Path, Path]:
    """Build the health-intake demo per docs/reference/analysis-recipes.md Option A.

    Equivalent to:
        quickq init study.db
        quickq load examples/health_intake_demo.yaml study.db
        quickq seed study.db 1 --n 500 --seed 20260503
        quickq refresh study.db analytics.duckdb

    Done in-process to avoid CLI invocation overhead and dependency.
    """
    from quickq.schema import init_oltp, open_oltp
    from quickq.loader import load_yaml
    from quickq.seed import seed_responses
    from quickq.olap_schema import refresh

    dest = tmp_path_factory.mktemp("health_intake")
    study_db = dest / "study.db"
    analytics_db = dest / "analytics.duckdb"

    init_oltp(study_db)
    conn = open_oltp(study_db)
    load_yaml(conn, EXAMPLES / "health_intake_demo.yaml")
    seed_responses(conn, questionnaire_id=1, n=500, rng_seed=20260503)
    conn.commit()
    conn.close()

    refresh(str(analytics_db), str(study_db))
    return study_db, analytics_db


@pytest.fixture(scope="session")
def demos(phq9_visits_demo, health_intake_demo) -> dict[str, tuple[Path, Path]]:
    """Single-shot demo lookup by name."""
    return {
        "phq9_visits": phq9_visits_demo,
        "health_intake": health_intake_demo,
    }


# ---------------------------------------------------------------------------
# Per-page parametrized tests
# ---------------------------------------------------------------------------


def _id(path: str, line: int) -> str:
    return f"{Path(path).name}:L{line}"


PARAMS = [
    pytest.param(page, demo, line, sql, id=_id(page, line))
    for page, demo in PAGES.items()
    for line, sql in extract_sql_blocks(REPO / page)
]


@pytest.mark.parametrize("page,demo_name,line,sql", PARAMS)
def test_sql_block_runs(page, demo_name, line, sql, demos, tmp_path):
    """Each fenced ```sql block must parse and execute without raising.

    The recipes are allowed to return zero rows (some are templates with
    placeholder link_ids). They must not raise a Catalog / Binder / Parse
    error.
    """
    study_db, analytics_db = demos[demo_name]

    # ATTACH statements in the doc refer to 'study.db' as a relative path
    # — copy/symlink study.db into the cwd we'll execute from so the path
    # resolves the same way a reader's local copy would.
    cwd_study = tmp_path / "study.db"
    if not cwd_study.exists():
        shutil.copy2(study_db, cwd_study)
    cwd_analytics = tmp_path / "analytics.duckdb"
    if not cwd_analytics.exists():
        shutil.copy2(analytics_db, cwd_analytics)

    import os
    prev_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with duckdb.connect(str(cwd_analytics), read_only=False) as con:
            # Some blocks ATTACH the OLTP themselves; others assume it's
            # already attached. Pre-attach so blocks of either shape work.
            try:
                con.execute(
                    f"ATTACH '{cwd_study.name}' AS oltp (TYPE sqlite, READ_ONLY)"
                )
            except duckdb.CatalogException:
                # Already attached from a previous statement in this conn.
                pass

            # Some blocks contain multiple statements separated by `;` —
            # run them sequentially. Strip comments only after the split
            # so '--' inside SQL strings is left alone.
            for stmt in _split_sql(sql):
                stmt = stmt.strip()
                if not stmt:
                    continue
                # Skip the block-internal ATTACH; we did it above.
                if re.match(r"\s*ATTACH\s+", stmt, re.IGNORECASE):
                    continue
                con.execute(stmt)
    finally:
        os.chdir(prev_cwd)


def _split_sql(sql: str) -> list[str]:
    """Split on top-level semicolons. Naive but sufficient for the docs."""
    out = []
    depth = 0
    buf = []
    in_str = False
    for ch in sql:
        if ch == "'" and not in_str:
            in_str = True
        elif ch == "'" and in_str:
            in_str = False
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == ";" and depth == 0:
                out.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    tail = "".join(buf)
    if tail.strip():
        out.append(tail)
    return out


# ---------------------------------------------------------------------------
# Sanity: at least one block per page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", list(PAGES.keys()))
def test_page_has_at_least_one_sql_block(page):
    """Catch the case where someone deletes all SQL blocks from a page."""
    blocks = extract_sql_blocks(REPO / page)
    assert len(blocks) > 0, f"{page} has no fenced ```sql blocks"
