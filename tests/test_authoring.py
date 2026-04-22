import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.models import OptionDef, QuestionDef, SectionDef, QuestionnaireDef
from quickq.authoring import (
    upsert_vocabulary, upsert_concept, resolve_concept,
    insert_study, insert_questionnaire, insert_section,
    upsert_option_set, upsert_question, insert_options,
    place_question, insert_skip_rule,
    insert_scoring_rule, insert_scoring_rule_item, insert_scoring_category,
)
from quickq.models import ScoringRuleDef, ScoringCategoryDef


# ------------------------------------------------------------------
# Concept plane
# ------------------------------------------------------------------

def test_upsert_vocabulary_idempotent(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    upsert_vocabulary(conn, "LOINC", "Logical Observation Identifiers Names and Codes")
    upsert_vocabulary(conn, "LOINC", "Logical Observation Identifiers Names and Codes")
    count = conn.execute("SELECT COUNT(*) FROM vocabulary WHERE vocabulary_id='LOINC'").fetchone()[0]
    assert count == 1


def test_upsert_concept_idempotent(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    upsert_vocabulary(conn, "LOINC", "LOINC")
    id1 = upsert_concept(conn, "Tobacco use", "Question", "LOINC", "Survey", "72166-2", "S")
    id2 = upsert_concept(conn, "Tobacco use", "Question", "LOINC", "Survey", "72166-2", "S")
    assert id1 == id2


def test_resolve_concept_found(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    upsert_vocabulary(conn, "SNOMED", "SNOMED CT")
    upsert_concept(conn, "Yes", "Answer", "SNOMED", "Answer", "373066001")
    concept_id = resolve_concept(conn, "SNOMED:373066001")
    assert concept_id is not None


def test_resolve_concept_missing_returns_none(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    assert resolve_concept(conn, "LOINC:99999-9") is None


def test_resolve_concept_none_input(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    assert resolve_concept(conn, None) is None


# ------------------------------------------------------------------
# Instrument plane
# ------------------------------------------------------------------

def test_insert_study(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    study_id = insert_study(conn, "BRFSS 2024", principal_investigator="CDC")
    conn.commit()
    row = conn.execute("SELECT name, principal_investigator FROM study WHERE study_id=?", (study_id,)).fetchone()
    assert row["name"] == "BRFSS 2024"
    assert row["principal_investigator"] == "CDC"


def test_insert_questionnaire(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    study_id = insert_study(conn, "Study A")
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Intake", canonical_url="http://example.org/intake"), study_id)
    conn.commit()
    row = conn.execute("SELECT name, canonical_url FROM questionnaire WHERE questionnaire_id=?", (qid,)).fetchone()
    assert row["name"] == "Intake"
    assert row["canonical_url"] == "http://example.org/intake"


def test_upsert_question_idempotent(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q = QuestionDef(link_id="q.smoke", text="Do you smoke?", type="single_choice")
    id1 = upsert_question(conn, q)
    id2 = upsert_question(conn, q)
    conn.commit()
    assert id1 == id2
    count = conn.execute("SELECT COUNT(*) FROM question WHERE link_id='q.smoke'").fetchone()[0]
    assert count == 1


def test_insert_options_with_concept(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    upsert_vocabulary(conn, "SNOMED", "SNOMED CT")
    upsert_concept(conn, "Yes", "Answer", "SNOMED", "Answer", "373066001")
    upsert_concept(conn, "No",  "Answer", "SNOMED", "Answer", "373067005")

    q = QuestionDef(link_id="q1", text="Smoke?", type="single_choice")
    question_id = upsert_question(conn, q)
    opts = [
        OptionDef(text="Yes", value="yes", concept="SNOMED:373066001"),
        OptionDef(text="No",  value="no",  concept="SNOMED:373067005"),
    ]
    result = insert_options(conn, question_id, opts)
    conn.commit()

    assert set(result.keys()) == {"yes", "no"}
    rows = conn.execute(
        "SELECT option_value, concept_system FROM response_option WHERE question_id=? ORDER BY display_order",
        (question_id,),
    ).fetchall()
    assert rows[0]["option_value"] == "yes"
    assert rows[0]["concept_system"] == "http://snomed.info/sct"
    assert rows[1]["option_value"] == "no"


def test_insert_options_is_other_flag(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q = QuestionDef(link_id="q1", text="Products?", type="multiple_choice")
    question_id = upsert_question(conn, q)
    opts = [
        OptionDef(text="Cigarettes", value="cig"),
        OptionDef(text="Other",      value="other", is_other=True),
    ]
    insert_options(conn, question_id, opts)
    conn.commit()

    other = conn.execute(
        "SELECT is_other FROM response_option WHERE question_id=? AND option_value='other'",
        (question_id,),
    ).fetchone()
    assert other["is_other"] == 1


def test_option_set_provenance(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    set_id = upsert_option_set(conn, "yn")
    q = QuestionDef(link_id="q1", text="Smoke?", type="single_choice")
    question_id = upsert_question(conn, q)
    opts = [OptionDef(text="Yes", value="yes"), OptionDef(text="No", value="no")]
    insert_options(conn, question_id, opts, option_set_id=set_id)
    conn.commit()

    rows = conn.execute(
        "SELECT option_set_id FROM response_option WHERE question_id=?", (question_id,)
    ).fetchall()
    assert all(r["option_set_id"] == set_id for r in rows)


def test_place_question_and_skip_rule(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Survey"), study_id=None)
    q1_id = upsert_question(conn, QuestionDef(link_id="q1", text="Smoke?", type="single_choice"))
    q2_id = upsert_question(conn, QuestionDef(link_id="q2", text="How many?", type="numeric"))
    qq1 = place_question(conn, qid, q1_id, display_order=0)
    qq2 = place_question(conn, qid, q2_id, display_order=1)
    insert_skip_rule(conn, qq_id=qq2, trigger_qq_id=qq1, operator="=", trigger_value="yes")
    conn.commit()

    rule = conn.execute(
        "SELECT operator, trigger_value, trigger_qq_id FROM skip_rule WHERE qq_id=?", (qq2,)
    ).fetchone()
    assert rule["operator"] == "="
    assert rule["trigger_value"] == "yes"
    assert rule["trigger_qq_id"] == qq1


def test_scoring_rule_round_trip(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    qid = insert_questionnaire(conn, QuestionnaireDef(name="PHQ-9"), study_id=None)
    q_id = upsert_question(conn, QuestionDef(link_id="phq9.1", text="Item 1", type="single_choice"))
    qq_id = place_question(conn, qid, q_id, display_order=0)

    rule = ScoringRuleDef(name="PHQ-9 Total", formula="sum")
    rule_id = insert_scoring_rule(conn, qid, rule)
    insert_scoring_rule_item(conn, rule_id, qq_id)
    insert_scoring_category(conn, rule_id, ScoringCategoryDef(label="Minimal", min_score=0, max_score=4))
    conn.commit()

    categories = conn.execute(
        "SELECT label, min_score, max_score FROM scoring_category WHERE scoring_rule_id=?", (rule_id,)
    ).fetchall()
    assert categories[0]["label"] == "Minimal"
    assert categories[0]["min_score"] == 0
    assert categories[0]["max_score"] == 4

    items = conn.execute(
        "SELECT qq_id FROM scoring_rule_item WHERE scoring_rule_id=?", (rule_id,)
    ).fetchall()
    assert items[0]["qq_id"] == qq_id
