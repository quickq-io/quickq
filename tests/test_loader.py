import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.loader import load_yaml, load_def, parse_questionnaire_def
from quickq.models import (
    QuestionnaireDef, SectionDef, QuestionDef,
    OptionDef, ShowWhen, SkipCondition, ScoringRuleDef, ScoringCategoryDef,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------
# parse_questionnaire_def
# ------------------------------------------------------------------

def test_parse_simple_fixture():
    raw = __import__("yaml").safe_load((FIXTURES / "simple.yaml").read_text())
    defn = parse_questionnaire_def(raw)
    assert defn.name == "Tobacco Use Survey"
    assert defn.version == "1.0"
    assert defn.canonical_url == "http://quickq.io/instruments/tobacco-use"
    assert "yn" in defn.option_sets
    assert len(defn.option_sets["yn"]) == 2
    assert len(defn.sections) == 1
    assert len(defn.sections[0].questions) == 4


def test_parse_option_set_ref():
    raw = __import__("yaml").safe_load((FIXTURES / "simple.yaml").read_text())
    defn = parse_questionnaire_def(raw)
    q = defn.sections[0].questions[0]
    assert q.link_id == "tobacco.current"
    assert q.option_set == "yn"
    assert q.options is None   # set ref, not inline


def test_parse_inline_options():
    raw = __import__("yaml").safe_load((FIXTURES / "phq9.yaml").read_text())
    defn = parse_questionnaire_def(raw)
    # difficulty question has inline options, not a set ref
    difficulty = defn.sections[1].questions[0]
    assert difficulty.link_id == "phq9.difficulty"
    assert difficulty.options is not None
    assert len(difficulty.options) == 4


def test_parse_show_when_single(tmp_path):
    raw = __import__("yaml").safe_load((FIXTURES / "simple.yaml").read_text())
    defn = parse_questionnaire_def(raw)
    q_type = defn.sections[0].questions[1]
    assert q_type.show_when is not None
    assert len(q_type.show_when.conditions) == 1
    cond = q_type.show_when.conditions[0]
    assert cond.question == "tobacco.current"
    assert cond.operator == "="
    assert cond.value == "yes"


def test_parse_show_when_multi_condition():
    raw = __import__("yaml").safe_load((FIXTURES / "phq9.yaml").read_text())
    defn = parse_questionnaire_def(raw)
    difficulty = defn.sections[1].questions[0]
    sw = difficulty.show_when
    assert sw is not None
    assert sw.behavior == "any"
    assert len(sw.conditions) == 3
    assert all(c.operator == "!=" for c in sw.conditions)


def test_parse_phq9_scoring():
    raw = __import__("yaml").safe_load((FIXTURES / "phq9.yaml").read_text())
    defn = parse_questionnaire_def(raw)
    assert len(defn.scoring) == 1
    rule = defn.scoring[0]
    assert rule.name == "PHQ-9 Total Score"
    assert rule.formula == "sum"
    assert len(rule.items) == 9
    assert len(rule.categories) == 5
    assert rule.categories[0].label == "Minimal depression"
    assert rule.categories[0].min_score == 0
    assert rule.categories[0].max_score == 4


def test_parse_numeric_range():
    raw = __import__("yaml").safe_load((FIXTURES / "simple.yaml").read_text())
    defn = parse_questionnaire_def(raw)
    cpd = defn.sections[0].questions[2]
    assert cpd.link_id == "tobacco.cpd"
    assert cpd.numeric_min == 1.0
    assert cpd.numeric_max == 100.0


def test_parse_source_instrument():
    raw = __import__("yaml").safe_load((FIXTURES / "phq9.yaml").read_text())
    defn = parse_questionnaire_def(raw)
    q = defn.sections[0].questions[0]
    assert q.source_instrument == "PHQ-9"
    assert q.source_item_id == "PHQ-9-1"


# ------------------------------------------------------------------
# load_yaml / load_def — database integration
# ------------------------------------------------------------------

def test_load_simple_yaml(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    qid = load_yaml(conn, FIXTURES / "simple.yaml")
    assert isinstance(qid, int)

    row = conn.execute(
        "SELECT name, canonical_url FROM questionnaire WHERE questionnaire_id=?", (qid,)
    ).fetchone()
    assert row["name"] == "Tobacco Use Survey"
    assert row["canonical_url"] == "http://quickq.io/instruments/tobacco-use"


def test_load_creates_questions(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_yaml(conn, FIXTURES / "simple.yaml")

    link_ids = {r[0] for r in conn.execute("SELECT link_id FROM question").fetchall()}
    assert {"tobacco.current", "tobacco.type", "tobacco.cpd", "tobacco.quit_attempts"} == link_ids


def test_load_creates_options_with_set_provenance(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_yaml(conn, FIXTURES / "simple.yaml")

    yn_set = conn.execute("SELECT option_set_id FROM response_option_set WHERE name='yn'").fetchone()
    assert yn_set is not None
    set_id = yn_set[0]

    opts = conn.execute(
        """SELECT ro.option_value FROM response_option ro
           JOIN question q ON ro.question_id = q.question_id
           WHERE q.link_id = 'tobacco.current'
           ORDER BY ro.display_order""",
    ).fetchall()
    assert [r[0] for r in opts] == ["yes", "no"]

    # options from a shared set carry the option_set_id for FHIR export
    set_ids = conn.execute(
        """SELECT DISTINCT ro.option_set_id FROM response_option ro
           JOIN question q ON ro.question_id = q.question_id
           WHERE q.link_id = 'tobacco.current'""",
    ).fetchall()
    assert set_ids[0][0] == set_id


def test_reload_does_not_duplicate_options(tmp_path):
    """Re-loading the same YAML must not duplicate response_option rows.

    Regression: questions are matched by link_id and reused on reload, but
    insert_options used to unconditionally append. After two loads of a
    multi-choice question with three options, response_option had six rows.
    """
    conn = init_oltp(tmp_path / "test.db")
    load_yaml(conn, FIXTURES / "simple.yaml")
    load_yaml(conn, FIXTURES / "simple.yaml")
    load_yaml(conn, FIXTURES / "simple.yaml")

    opts = conn.execute(
        """SELECT ro.option_value FROM response_option ro
           JOIN question q ON ro.question_id = q.question_id
           WHERE q.link_id = 'tobacco.current'
           ORDER BY ro.display_order""",
    ).fetchall()
    assert [r[0] for r in opts] == ["yes", "no"], (
        "options should still be exactly the original two after three reloads"
    )


def test_load_creates_skip_rules(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_yaml(conn, FIXTURES / "simple.yaml")

    # tobacco.type should show when tobacco.current = 'yes'
    rule = conn.execute("""
        SELECT sr.operator, sr.trigger_value
        FROM skip_rule sr
        JOIN questionnaire_question qq_target ON sr.qq_id = qq_target.qq_id
        JOIN question q_target ON qq_target.question_id = q_target.question_id
        JOIN questionnaire_question qq_trigger ON sr.trigger_qq_id = qq_trigger.qq_id
        JOIN question q_trigger ON qq_trigger.question_id = q_trigger.question_id
        WHERE q_target.link_id = 'tobacco.type'
          AND q_trigger.link_id = 'tobacco.current'
    """).fetchone()
    assert rule is not None
    assert rule["operator"] == "="
    assert rule["trigger_value"] == "yes"


def test_load_phq9(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    qid = load_yaml(conn, FIXTURES / "phq9.yaml")

    questions = conn.execute(
        """SELECT q.link_id FROM questionnaire_question qq
           JOIN question q ON qq.question_id = q.question_id
           WHERE qq.questionnaire_id = ?
           ORDER BY qq.display_order""",
        (qid,),
    ).fetchall()
    link_ids = [r[0] for r in questions]
    assert "phq9.1" in link_ids
    assert "phq9.9" in link_ids
    assert "phq9.difficulty" in link_ids


def test_load_phq9_scoring(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    qid = load_yaml(conn, FIXTURES / "phq9.yaml")

    rule = conn.execute(
        "SELECT scoring_rule_id, name, formula FROM scoring_rule WHERE questionnaire_id=?", (qid,)
    ).fetchone()
    assert rule["name"] == "PHQ-9 Total Score"
    assert rule["formula"] == "sum"

    items = conn.execute(
        "SELECT COUNT(*) FROM scoring_rule_item WHERE scoring_rule_id=?", (rule["scoring_rule_id"],)
    ).fetchone()[0]
    assert items == 9

    categories = conn.execute(
        "SELECT label FROM scoring_category WHERE scoring_rule_id=? ORDER BY display_order",
        (rule["scoring_rule_id"],),
    ).fetchall()
    assert categories[0]["label"] == "Minimal depression"
    assert categories[4]["label"] == "Severe depression"


def test_load_phq9_multi_condition_skip(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_yaml(conn, FIXTURES / "phq9.yaml")

    rules = conn.execute("""
        SELECT sr.enable_behavior, sr.operator, sr.trigger_value
        FROM skip_rule sr
        JOIN questionnaire_question qq ON sr.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE q.link_id = 'phq9.difficulty'
        ORDER BY sr.rule_id
    """).fetchall()
    assert len(rules) == 3
    assert rules[0]["enable_behavior"] == "any"
    assert all(r["operator"] == "!=" for r in rules)


def test_load_question_reuse_across_questionnaires(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    load_yaml(conn, FIXTURES / "phq9.yaml")

    # Load a v2 of the same instrument (same questions, new questionnaire row)
    raw = __import__("yaml").safe_load((FIXTURES / "phq9.yaml").read_text())
    raw["questionnaire"]["version"] = "2.0"
    defn = parse_questionnaire_def(raw)
    load_def(conn, defn)

    # question rows are reused (idempotent), not duplicated
    count = conn.execute(
        "SELECT COUNT(*) FROM question WHERE link_id='phq9.1'"
    ).fetchone()[0]
    assert count == 1

    # but there are two separate questionnaire_question placements (one per version)
    placements = conn.execute(
        """SELECT COUNT(*) FROM questionnaire_question qq
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'phq9.1'"""
    ).fetchone()[0]
    assert placements == 2


def test_unknown_option_set_raises(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    defn = QuestionnaireDef(
        name="Bad Survey",
        sections=[
            SectionDef(questions=[
                QuestionDef(
                    link_id="q1", text="Q?", type="single_choice",
                    option_set="nonexistent_set",
                ),
            ])
        ],
    )
    with pytest.raises(ValueError, match="unknown option_set"):
        load_def(conn, defn)


def test_unknown_skip_rule_link_id_raises(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    defn = QuestionnaireDef(
        name="Bad Survey",
        sections=[
            SectionDef(questions=[
                QuestionDef(
                    link_id="q1", text="Q?", type="single_choice",
                    show_when=ShowWhen(
                        conditions=[SkipCondition(question="q.does_not_exist", operator="=", value="yes")]
                    ),
                )
            ])
        ],
    )
    with pytest.raises(ValueError, match="unknown link_id"):
        load_def(conn, defn)


def test_load_datetime_type(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    defn = QuestionnaireDef(
        name="Datetime Test",
        sections=[SectionDef(questions=[
            QuestionDef(link_id="q.ts", text="When exactly?", type="datetime"),
        ])],
    )
    load_def(conn, defn)
    row = conn.execute(
        "SELECT question_type FROM question WHERE link_id='q.ts'"
    ).fetchone()
    assert row["question_type"] == "datetime"
