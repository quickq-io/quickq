import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.library_loader import load_library_file, load_all_libraries, list_library_questions
from quickq.loader import load_yaml, load_def
from quickq.models import QuestionnaireDef, SectionDef, QuestionDef

LIBRARY_DIR = Path(__file__).parent.parent / "quickq" / "library"
FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------
# load_library_file
# ------------------------------------------------------------------

def test_load_phq9_library(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    ids = load_library_file(conn, LIBRARY_DIR / "phq9.yaml")
    assert len(ids) == 10   # 9 items + difficulty


def test_load_gad7_library(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    ids = load_library_file(conn, LIBRARY_DIR / "gad7.yaml")
    assert len(ids) == 8    # 7 items + difficulty


def test_load_phq2_library(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    ids = load_library_file(conn, LIBRARY_DIR / "phq2.yaml")
    assert len(ids) == 2


def test_load_cdc_demographics_library(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    ids = load_library_file(conn, LIBRARY_DIR / "cdc_demographics.yaml")
    assert len(ids) == 7    # age, sex, gender, race/ethnicity, education, income, marital


def test_load_brfss_tobacco_library(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    ids = load_library_file(conn, LIBRARY_DIR / "brfss_tobacco.yaml")
    assert len(ids) == 7


def test_library_questions_have_concepts(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_library_file(conn, LIBRARY_DIR / "phq9.yaml")

    rows = conn.execute("""
        SELECT q.link_id, c.vocabulary_id, c.concept_code
        FROM question q
        JOIN concept c ON q.concept_id = c.concept_id
        WHERE q.source_instrument = 'PHQ-9'
    """).fetchall()
    assert len(rows) == 10
    assert all(r["vocabulary_id"] == "LOINC" for r in rows)


def test_library_options_have_concept_system(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_library_file(conn, LIBRARY_DIR / "phq9.yaml")

    opts = conn.execute("""
        SELECT ro.option_value, ro.concept_system
        FROM response_option ro
        JOIN question q ON ro.question_id = q.question_id
        WHERE q.link_id = 'phq9.1'
        ORDER BY ro.display_order
    """).fetchall()
    assert len(opts) == 4
    assert all(o["concept_system"] == "http://loinc.org" for o in opts)
    assert [o["option_value"] for o in opts] == ["0", "1", "2", "3"]


def test_library_is_idempotent(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_library_file(conn, LIBRARY_DIR / "phq9.yaml")
    load_library_file(conn, LIBRARY_DIR / "phq9.yaml")   # second load — no-op

    count = conn.execute("SELECT COUNT(*) FROM question WHERE source_instrument='PHQ-9'").fetchone()[0]
    assert count == 10

    opt_count = conn.execute("""
        SELECT COUNT(*) FROM response_option ro
        JOIN question q ON ro.question_id = q.question_id
        WHERE q.link_id = 'phq9.1'
    """).fetchone()[0]
    assert opt_count == 4


def test_phq2_reuses_phq9_questions_when_both_loaded(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_library_file(conn, LIBRARY_DIR / "phq9.yaml")
    load_library_file(conn, LIBRARY_DIR / "phq2.yaml")   # phq9.1 and phq9.2 already exist

    count = conn.execute("SELECT COUNT(*) FROM question WHERE link_id IN ('phq9.1','phq9.2')").fetchone()[0]
    assert count == 2   # no duplicates


# ------------------------------------------------------------------
# load_all_libraries
# ------------------------------------------------------------------

def test_load_all_libraries(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    counts = load_all_libraries(conn)
    assert "phq9" in counts
    assert "gad7" in counts
    assert "cdc_demographics" in counts
    assert "brfss_tobacco" in counts
    assert all(v > 0 for v in counts.values())


def test_load_all_libraries_idempotent(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_all_libraries(conn)
    load_all_libraries(conn)

    total = conn.execute("SELECT COUNT(*) FROM question").fetchone()[0]
    load_all_libraries(conn)
    total2 = conn.execute("SELECT COUNT(*) FROM question").fetchone()[0]
    assert total == total2


def test_list_library_questions(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_all_libraries(conn)
    questions = list_library_questions(conn)
    assert len(questions) > 0
    instruments = {q["source_instrument"] for q in questions}
    assert "PHQ-9" in instruments
    assert "GAD-7" in instruments
    assert "CDC-DEMO" in instruments


# ------------------------------------------------------------------
# library_ref in questionnaire YAML
# ------------------------------------------------------------------

def test_questionnaire_references_library_question(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_all_libraries(conn)

    # A questionnaire that pulls two PHQ-9 items from the library
    defn = QuestionnaireDef(
        name="Depression Screen",
        sections=[
            SectionDef(questions=[
                QuestionDef(library_ref="phq9.1"),
                QuestionDef(library_ref="phq9.2"),
            ])
        ],
    )
    qid = load_def(conn, defn)

    placements = conn.execute("""
        SELECT q.link_id FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        WHERE qq.questionnaire_id = ?
        ORDER BY qq.display_order
    """, (qid,)).fetchall()
    assert [r["link_id"] for r in placements] == ["phq9.1", "phq9.2"]


def test_questionnaire_mixes_library_and_inline(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_all_libraries(conn)

    defn = QuestionnaireDef(
        name="Mixed Survey",
        sections=[
            SectionDef(questions=[
                QuestionDef(library_ref="demo.age"),
                QuestionDef(library_ref="demo.sex"),
                QuestionDef(
                    link_id="custom.q1",
                    text="How are you feeling today?",
                    type="text",
                ),
            ])
        ],
    )
    qid = load_def(conn, defn)

    link_ids = [r[0] for r in conn.execute("""
        SELECT q.link_id FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        WHERE qq.questionnaire_id = ?
        ORDER BY qq.display_order
    """, (qid,)).fetchall()]
    assert link_ids == ["demo.age", "demo.sex", "custom.q1"]


def test_library_ref_missing_raises_clear_error(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    # library not loaded — phq9.1 doesn't exist

    defn = QuestionnaireDef(
        name="Survey",
        sections=[SectionDef(questions=[QuestionDef(library_ref="phq9.1")])],
    )
    with pytest.raises(ValueError, match="not found"):
        load_def(conn, defn)


def test_yaml_with_library_key(tmp_path):
    """A YAML file using the 'library:' shorthand."""
    conn = init_oltp(tmp_path / "test.db")
    load_all_libraries(conn)

    yaml_content = """
questionnaire:
  name: "PHQ-2 Screen"
  version: "1.0"
  sections:
    - title: "Depression Screen"
      questions:
        - library: phq9.1
        - library: phq9.2
"""
    yaml_path = tmp_path / "phq2_screen.yaml"
    yaml_path.write_text(yaml_content)
    qid = load_yaml(conn, yaml_path)

    placements = conn.execute("""
        SELECT q.link_id FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        WHERE qq.questionnaire_id = ?
        ORDER BY qq.display_order
    """, (qid,)).fetchall()
    assert [r["link_id"] for r in placements] == ["phq9.1", "phq9.2"]
