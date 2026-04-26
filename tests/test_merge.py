"""
Tests for quickq.merge — merging multiple site databases.
"""
import pytest
from pathlib import Path

from quickq.schema import init_oltp
from quickq.authoring import (
    insert_study, insert_questionnaire, upsert_question, place_question,
)
from quickq.models import QuestionnaireDef, QuestionDef
from quickq.merge import merge_databases, MergeError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_study_db(
    path: Path,
    site_name: str,
    n_respondents: int = 2,
    *,
    external_id_prefix: str | None = None,
    session_id_prefix: str | None = None,
) -> None:
    """Create a minimal quickq database with one questionnaire and n respondents."""
    external_id_prefix = external_id_prefix or site_name
    session_id_prefix  = session_id_prefix  or site_name

    conn = init_oltp(path)

    study_id = insert_study(conn, name="Multi-Site Study")

    q_id = insert_questionnaire(
        conn,
        QuestionnaireDef(
            name="PHQ-2",
            canonical_url="http://quickq.io/instruments/phq2",
            version="1.0",
        ),
        study_id=study_id,
    )

    q1 = upsert_question(conn, QuestionDef(
        link_id="phq2.1",
        text="Little interest or pleasure in doing things",
        type="likert",
    ))
    q2 = upsert_question(conn, QuestionDef(
        link_id="phq2.2",
        text="Feeling down, depressed, or hopeless",
        type="likert",
    ))
    place_question(conn, q_id, q1, display_order=1)
    place_question(conn, q_id, q2, display_order=2)

    for i in range(n_respondents):
        external_id = f"{external_id_prefix}::P{i:03d}"
        conn.execute(
            "INSERT INTO respondent (study_id, external_id) VALUES (?, ?)",
            (study_id, external_id),
        )
        respondent_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO response_session
               (questionnaire_id, respondent_id, started_at, is_complete,
                fhir_response_id)
               VALUES (?, ?, '2025-01-01T10:00:00Z', 1, ?)""",
            (q_id, respondent_id, f"fhir-{session_id_prefix}-{i}"),
        )

    conn.commit()
    conn.close()


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Basic merge
# ---------------------------------------------------------------------------

def test_merge_two_sites(tmp_path):
    site_a = tmp_path / "site_a.db"
    site_b = tmp_path / "site_b.db"
    out    = tmp_path / "combined.db"

    _make_study_db(site_a, "SITE_A", n_respondents=3)
    _make_study_db(site_b, "SITE_B", n_respondents=2)

    result = merge_databases([site_a, site_b], out)

    assert result.respondents_merged == 5
    assert result.sessions_merged == 5
    assert result.sessions_skipped_duplicate == 0
    assert result.warnings == []

    conn = init_oltp(out)
    assert _count(conn, "respondent") == 5
    assert _count(conn, "response_session") == 5
    # Instrument definitions are deduplicated — one questionnaire, two questions
    assert _count(conn, "questionnaire") == 1
    assert _count(conn, "question") == 2
    assert _count(conn, "questionnaire_question") == 2
    conn.close()


def test_merge_three_sites(tmp_path):
    dbs = []
    for name, n in [("A", 4), ("B", 3), ("C", 2)]:
        p = tmp_path / f"site_{name}.db"
        _make_study_db(p, name, n_respondents=n)
        dbs.append(p)

    result = merge_databases(dbs, tmp_path / "combined.db")

    assert result.respondents_merged == 9
    assert result.sessions_merged == 9


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_duplicate_sessions_skipped(tmp_path):
    """Merging the same database twice should skip all sessions from the second copy."""
    site_a = tmp_path / "site_a.db"
    out    = tmp_path / "combined.db"
    _make_study_db(site_a, "SITE_A", n_respondents=3)

    result = merge_databases([site_a, site_a], out)

    assert result.sessions_merged == 3
    assert result.sessions_skipped_duplicate == 3

    conn = init_oltp(out)
    assert _count(conn, "response_session") == 3  # not 6
    assert _count(conn, "respondent") == 3         # not 6
    conn.close()


def test_instrument_deduplication(tmp_path):
    """Same questionnaire definition should appear exactly once in the merged output."""
    site_a = tmp_path / "site_a.db"
    site_b = tmp_path / "site_b.db"
    out    = tmp_path / "combined.db"
    _make_study_db(site_a, "A", n_respondents=1)
    _make_study_db(site_b, "B", n_respondents=1)

    merge_databases([site_a, site_b], out)

    conn = init_oltp(out)
    assert _count(conn, "questionnaire") == 1
    assert _count(conn, "question") == 2
    assert _count(conn, "questionnaire_question") == 2
    conn.close()


def test_respondent_deduplication_same_external_id(tmp_path):
    """Same external_id maps to one respondent; their sessions from each site are both kept."""
    site_a = tmp_path / "site_a.db"
    site_b = tmp_path / "site_b.db"
    out    = tmp_path / "combined.db"

    # Both sites share the same participant pool (same external_ids) but each site
    # collected an independent session (distinct fhir_response_ids).
    _make_study_db(site_a, "SHARED", n_respondents=2,
                   external_id_prefix="SHARED", session_id_prefix="sess-A")
    _make_study_db(site_b, "SHARED", n_respondents=2,
                   external_id_prefix="SHARED", session_id_prefix="sess-B")

    result = merge_databases([site_a, site_b], out)

    conn = init_oltp(out)
    assert _count(conn, "respondent") == 2   # deduplicated by (study_id, external_id)
    assert _count(conn, "response_session") == 4  # 2 sessions per respondent across sites
    conn.close()
    assert result.respondents_merged == 2
    assert result.sessions_merged == 4
    assert result.sessions_skipped_duplicate == 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_merge_error_output_exists(tmp_path):
    site_a = tmp_path / "site_a.db"
    out    = tmp_path / "combined.db"
    _make_study_db(site_a, "A", n_respondents=1)
    out.write_text("existing content")

    with pytest.raises(MergeError, match="already exists"):
        merge_databases([site_a], out)


def test_merge_overwrite_flag(tmp_path):
    site_a = tmp_path / "site_a.db"
    out    = tmp_path / "combined.db"
    _make_study_db(site_a, "A", n_respondents=2)
    out.write_text("existing content")

    result = merge_databases([site_a], out, overwrite=True)
    assert result.sessions_merged == 2


def test_merge_error_question_text_divergence(tmp_path):
    """Two sites with the same link_id but different question text must raise MergeError."""
    site_a = tmp_path / "site_a.db"
    site_b = tmp_path / "site_b.db"
    out    = tmp_path / "combined.db"

    _make_study_db(site_a, "A", n_respondents=1)

    # Site B has a different text for phq2.1
    conn_b = init_oltp(site_b)
    study_id = insert_study(conn_b, name="Multi-Site Study")
    q_id = insert_questionnaire(
        conn_b,
        QuestionnaireDef(
            name="PHQ-2",
            canonical_url="http://quickq.io/instruments/phq2",
            version="1.0",
        ),
        study_id=study_id,
    )
    q1 = upsert_question(conn_b, QuestionDef(
        link_id="phq2.1",
        text="DIFFERENT TEXT for same link_id",
        type="likert",
    ))
    place_question(conn_b, q_id, q1, display_order=1)
    conn_b.commit()
    conn_b.close()

    with pytest.raises(MergeError, match="Question text divergence"):
        merge_databases([site_a, site_b], out)

    assert not out.exists()  # output cleaned up on failure


def test_merge_error_cleans_up_output(tmp_path):
    """On MergeError the partial output file must be removed."""
    site_a = tmp_path / "site_a.db"
    _make_study_db(site_a, "A")

    bad_path = tmp_path / "nonexistent_dir" / "out.db"

    with pytest.raises(Exception):
        merge_databases([site_a], bad_path)

    assert not bad_path.exists()


# ---------------------------------------------------------------------------
# Stats accuracy
# ---------------------------------------------------------------------------

def test_merge_stats(tmp_path):
    site_a = tmp_path / "site_a.db"
    site_b = tmp_path / "site_b.db"
    out    = tmp_path / "combined.db"
    _make_study_db(site_a, "A", n_respondents=4)
    _make_study_db(site_b, "B", n_respondents=6)

    result = merge_databases([site_a, site_b], out)

    assert result.respondents_merged == 10
    assert result.sessions_merged == 10
    assert result.sessions_skipped_duplicate == 0
    assert result.sources == [str(site_a), str(site_b)]
    assert result.output == str(out)


# ---------------------------------------------------------------------------
# Versioning metadata is preserved
# ---------------------------------------------------------------------------

def test_merge_preserves_errata(tmp_path):
    site_a = tmp_path / "site_a.db"
    out    = tmp_path / "combined.db"
    _make_study_db(site_a, "A", n_respondents=1)

    conn = init_oltp(site_a)
    conn.execute(
        """INSERT INTO study_errata_log
           (event_type, severity, title, description, reported_at)
           VALUES ('note', 'informational', 'Test errata', 'A note.', '2025-01-01T00:00:00Z')"""
    )
    conn.commit()
    conn.close()

    merge_databases([site_a], out)

    conn_out = init_oltp(out)
    assert _count(conn_out, "study_errata_log") == 1
    conn_out.close()
