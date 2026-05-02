"""
Tests for render_questionnaire_md.

Covers:
  - All question type display strings (including datetime and grid)
  - Skip condition rendering
  - Scoring appendix rendering
  - Repeating group children indented beneath parent
  - Unknown questionnaire raises ValueError
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.authoring import (
    upsert_question, insert_questionnaire, place_question,
    insert_skip_rule, insert_scoring_rule,
    insert_grid_rows_columns,
)
from quickq.models import QuestionDef, QuestionnaireDef, ScoringRuleDef
from quickq.renderer_questionnaire import render_questionnaire_md


def _db(tmp_path):
    return init_oltp(tmp_path / "test.db")


def _simple_questionnaire(conn, qtype, extra_kwargs=None):
    kwargs = {"link_id": f"q.{qtype}", "text": "Test question?", "type": qtype}
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    q_id = upsert_question(conn, QuestionDef(**kwargs))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Test"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    return qid


# ------------------------------------------------------------------
# Unknown questionnaire
# ------------------------------------------------------------------

def test_unknown_questionnaire_raises(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        render_questionnaire_md(conn, 999)


# ------------------------------------------------------------------
# Question type display strings
# ------------------------------------------------------------------

@pytest.mark.parametrize("qtype,expected_fragment", [
    ("boolean",   "Yes / No"),
    ("numeric",   "Numeric response"),
    ("slider",    "Slider"),
    ("text",      "Free-text response"),
    ("date",      "*Date*"),
    ("datetime",  "Date and time"),
])
def test_question_type_display(tmp_path, qtype, expected_fragment):
    conn = _db(tmp_path)
    qid = _simple_questionnaire(conn, qtype)
    output = render_questionnaire_md(conn, qid)
    assert expected_fragment in output


def test_single_choice_renders_options(tmp_path):
    from quickq.authoring import insert_options
    from quickq.models import OptionDef
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.sc", text="Pick one?", type="single_choice"))
    insert_options(conn, q_id, [OptionDef(text="Yes", value="1"), OptionDef(text="No", value="0")])
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Test"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    output = render_questionnaire_md(conn, qid)
    assert "`1` Yes" in output
    assert "`0` No" in output


def test_grid_renders_table(tmp_path):
    from quickq.models import GridRowDef, GridColumnDef
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.grid", text="Rate each:", type="grid"))
    insert_grid_rows_columns(
        conn, q_id,
        rows=[GridRowDef(text="Row A"), GridRowDef(text="Row B")],
        columns=[GridColumnDef(text="Low", value="1"), GridColumnDef(text="High", value="3")],
    )
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Test"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    output = render_questionnaire_md(conn, qid)
    assert "Row A" in output
    assert "Row B" in output
    assert "Low (`1`)" in output
    assert "High (`3`)" in output


# ------------------------------------------------------------------
# Skip condition rendering
# ------------------------------------------------------------------

def test_skip_condition_rendered(tmp_path):
    from quickq.authoring import insert_skip_rule
    conn = _db(tmp_path)
    trigger_id = upsert_question(conn, QuestionDef(link_id="q.trigger", text="Trigger?", type="boolean"))
    target_id  = upsert_question(conn, QuestionDef(link_id="q.target",  text="Target?",  type="text"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Test"))
    trigger_qq = place_question(conn, qid, trigger_id, display_order=0)
    target_qq  = place_question(conn, qid, target_id,  display_order=1)
    insert_skip_rule(conn, target_qq, trigger_qq, operator="=", trigger_value="true", action="show")
    conn.commit()
    output = render_questionnaire_md(conn, qid)
    assert "q.trigger" in output
    assert "Show" in output or "show" in output


# ------------------------------------------------------------------
# Scoring appendix
# ------------------------------------------------------------------

def test_scoring_appendix_rendered(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.item", text="Item?", type="numeric"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Test"))
    qq_id = place_question(conn, qid, q_id, display_order=0)
    rule_id = insert_scoring_rule(conn, qid, ScoringRuleDef(name="Total", formula="sum"))
    conn.execute(
        "INSERT INTO scoring_rule_item (scoring_rule_id, qq_id) VALUES (?, ?)",
        (rule_id, qq_id),
    )
    conn.execute(
        "INSERT INTO scoring_category (scoring_rule_id, label, min_score, max_score, display_order)"
        " VALUES (?, ?, ?, ?, ?)",
        (rule_id, "Low", 0, 5, 0),
    )
    conn.commit()
    output = render_questionnaire_md(conn, qid)
    assert "## Scoring" in output
    assert "Total" in output
    assert "Low" in output
    assert "0–5" in output


# ------------------------------------------------------------------
# Repeating group children
# ------------------------------------------------------------------

def test_repeating_group_children_indented(tmp_path):
    conn = _db(tmp_path)
    parent_id = upsert_question(conn, QuestionDef(link_id="q.group", text="Group:", type="repeating_group"))
    child_id  = upsert_question(conn, QuestionDef(link_id="q.child", text="Child?",  type="boolean"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Test"))
    parent_qq = place_question(conn, qid, parent_id, display_order=0)
    conn.execute(
        """
        INSERT INTO questionnaire_question
            (questionnaire_id, question_id, display_order, parent_qq_id, status)
        VALUES (?, ?, ?, ?, 'active')
        """,
        (qid, child_id, 0, parent_qq),
    )
    conn.commit()
    output = render_questionnaire_md(conn, qid)
    lines = output.splitlines()
    child_line = next(l for l in lines if "Child?" in l)
    assert child_line.startswith("  "), "child question should be indented"
