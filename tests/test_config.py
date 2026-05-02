"""
Tests for quickq/config.py.

Covers:
  - Defaults when no quickq.yml present
  - Parsing all supported keys
  - Upward directory search (finds config in parent)
  - Unknown keys are ignored (forward compatibility)
  - CLI/config priority via the loader strict_concepts flag
  - Concept code collision warning fires when strict_concepts=True
  - Concept code collision warning suppressed when strict_concepts=False
"""
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.config import load_config, QuickqConfig
from quickq.schema import init_oltp
from quickq.authoring import upsert_question, insert_questionnaire, place_question, upsert_vocabulary, upsert_concept
from quickq.models import QuestionDef, QuestionnaireDef


# ------------------------------------------------------------------
# Config loading
# ------------------------------------------------------------------

def test_defaults_when_no_config(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.authoring.strict_concepts is True
    assert cfg.authoring.auto_concept is False
    assert cfg.render.format == "md"
    assert cfg.data_dict.format == "markdown"


def test_parses_all_keys(tmp_path):
    (tmp_path / "quickq.yml").write_text(
        "authoring:\n  strict_concepts: false\n  auto_concept: true\n"
        "render:\n  format: pdf\n"
        "data_dict:\n  format: csv\n"
    )
    cfg = load_config(tmp_path)
    assert cfg.authoring.strict_concepts is False
    assert cfg.authoring.auto_concept is True
    assert cfg.render.format == "pdf"
    assert cfg.data_dict.format == "csv"


def test_partial_config_inherits_defaults(tmp_path):
    (tmp_path / "quickq.yml").write_text("authoring:\n  strict_concepts: false\n")
    cfg = load_config(tmp_path)
    assert cfg.authoring.strict_concepts is False
    assert cfg.render.format == "md"          # default
    assert cfg.data_dict.format == "markdown" # default


def test_unknown_keys_ignored(tmp_path):
    (tmp_path / "quickq.yml").write_text("future_feature:\n  foo: bar\n")
    cfg = load_config(tmp_path)
    assert isinstance(cfg, QuickqConfig)


def test_empty_config_file_returns_defaults(tmp_path):
    (tmp_path / "quickq.yml").write_text("")
    cfg = load_config(tmp_path)
    assert cfg.authoring.strict_concepts is True


def test_upward_search_finds_parent_config(tmp_path):
    (tmp_path / "quickq.yml").write_text("authoring:\n  strict_concepts: false\n")
    subdir = tmp_path / "study" / "instruments"
    subdir.mkdir(parents=True)
    cfg = load_config(subdir)
    assert cfg.authoring.strict_concepts is False


def test_child_config_takes_precedence_over_parent(tmp_path):
    (tmp_path / "quickq.yml").write_text("authoring:\n  strict_concepts: false\n")
    subdir = tmp_path / "study"
    subdir.mkdir()
    (subdir / "quickq.yml").write_text("authoring:\n  strict_concepts: true\n")
    cfg = load_config(subdir)
    assert cfg.authoring.strict_concepts is True


# ------------------------------------------------------------------
# Concept collision warning
# ------------------------------------------------------------------

def _db_with_concept(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    upsert_vocabulary(conn, "LOINC", "LOINC", "https://loinc.org", "2.77")
    upsert_concept(conn, "Little interest or pleasure", "Question", "LOINC", "Survey", "44250-9", "S")
    return conn


def test_collision_warning_fires_when_strict(tmp_path):
    conn = _db_with_concept(tmp_path)
    # First question claims LOINC:44250-9
    q1 = upsert_question(conn, QuestionDef(
        link_id="phq9.1", text="Little interest?", type="single_choice",
        concept="LOINC:44250-9",
    ), strict_concepts=True)
    conn.commit()

    # Second question tries to claim the same concept under a different link_id
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        upsert_question(conn, QuestionDef(
            link_id="study.interest", text="Little interest?", type="single_choice",
            concept="LOINC:44250-9",
        ), strict_concepts=True)

    messages = [str(w.message) for w in caught]
    assert any("44250-9" in m and "phq9.1" in m for m in messages)


def test_collision_warning_suppressed_when_not_strict(tmp_path):
    conn = _db_with_concept(tmp_path)
    upsert_question(conn, QuestionDef(
        link_id="phq9.1", text="Little interest?", type="single_choice",
        concept="LOINC:44250-9",
    ), strict_concepts=False)
    conn.commit()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        upsert_question(conn, QuestionDef(
            link_id="study.interest", text="Little interest?", type="single_choice",
            concept="LOINC:44250-9",
        ), strict_concepts=False)

    assert not any("44250-9" in str(w.message) for w in caught)


def test_no_warning_when_concept_not_mapped(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        upsert_question(conn, QuestionDef(
            link_id="q.novel", text="Novel question?", type="text",
            concept=None,
        ), strict_concepts=True)
    assert not caught


def test_unresolved_concept_warns_when_vocab_seeded(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    upsert_vocabulary(conn, "LOINC", "LOINC", "https://loinc.org", "2.77")
    # Vocab is seeded but this specific code is not — should warn

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        upsert_question(conn, QuestionDef(
            link_id="q.missing", text="Missing code?", type="boolean",
            concept="LOINC:99999-9",
        ))

    assert any("99999-9" in str(w.message) and "unmapped" in str(w.message) for w in caught)


def test_unresolved_concept_silent_when_vocab_not_seeded(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    # LOINC vocabulary not seeded at all — should stay quiet

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        upsert_question(conn, QuestionDef(
            link_id="q.loinc", text="LOINC question?", type="boolean",
            concept="LOINC:44250-9",
        ))

    assert not any("unmapped" in str(w.message) for w in caught)


def test_direct_concept_id_bypasses_string_lookup(tmp_path):
    conn = _db_with_concept(tmp_path)
    concept_id_row = conn.execute(
        "SELECT concept_id FROM concept WHERE concept_code = '44250-9'"
    ).fetchone()
    direct_id = concept_id_row["concept_id"]

    q_id = upsert_question(conn, QuestionDef(
        link_id="q.direct", text="Direct concept_id?", type="boolean",
        concept_id=direct_id,
    ))
    conn.commit()

    row = conn.execute(
        "SELECT concept_id FROM question WHERE question_id = ?", (q_id,)
    ).fetchone()
    assert row["concept_id"] == direct_id


def test_direct_concept_id_takes_precedence_over_string(tmp_path):
    conn = _db_with_concept(tmp_path)
    concept_id_row = conn.execute(
        "SELECT concept_id FROM concept WHERE concept_code = '44250-9'"
    ).fetchone()
    direct_id = concept_id_row["concept_id"]

    # concept string points to a non-existent code, but concept_id is valid
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        q_id = upsert_question(conn, QuestionDef(
            link_id="q.precedence", text="Precedence?", type="boolean",
            concept="LOINC:99999-9",   # would fail lookup
            concept_id=direct_id,       # takes precedence
        ))
    conn.commit()

    row = conn.execute(
        "SELECT concept_id FROM question WHERE question_id = ?", (q_id,)
    ).fetchone()
    assert row["concept_id"] == direct_id
    assert not any("unmapped" in str(w.message) for w in caught)


def test_no_warning_for_same_link_id(tmp_path):
    conn = _db_with_concept(tmp_path)
    upsert_question(conn, QuestionDef(
        link_id="phq9.1", text="Little interest?", type="single_choice",
        concept="LOINC:44250-9",
    ), strict_concepts=True)
    conn.commit()

    # Re-upserting same link_id should not warn (returns existing, never hits check)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        upsert_question(conn, QuestionDef(
            link_id="phq9.1", text="Little interest?", type="single_choice",
            concept="LOINC:44250-9",
        ), strict_concepts=True)
    assert not any("44250-9" in str(w.message) for w in caught)
