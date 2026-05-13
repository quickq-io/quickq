"""Tests for the two skip-rule extensions filed in quickq-io-ap8:

  A. `operator: in` with `values: [...]` in YAML — authoring shorthand
     that expands to N `=` rules combined via `enable_behavior=any`.
  B. `on_missing: <value>` per-condition — when the trigger response is
     absent, substitute this value for the comparison instead of
     defaulting to FALSE.

The schema change is the addition of a `trigger_default_value` column
to `skip_rule`. The `is_one_of` operator is implemented entirely at
the YAML layer; storage stays as N flat rules.
"""
from __future__ import annotations

import sqlite3

import pytest

from quickq.loader import _parse_show_when, load_yaml
from quickq.models import ShowWhen
from quickq.schema import init_oltp
from quickq.seed import _eval_condition


# ---------------------------------------------------------------------------
# A. is_one_of expansion at the loader level
# ---------------------------------------------------------------------------

def test_in_operator_expands_to_n_equality_rules():
    """operator: in becomes one `=` SkipCondition per value, combined OR."""
    sw = _parse_show_when({
        "question": "condition",
        "operator": "in",
        "values": ["cancer", "heart_disease", "diabetes"],
    })
    assert isinstance(sw, ShowWhen)
    assert sw.behavior == "any"
    assert len(sw.conditions) == 3
    for cond in sw.conditions:
        assert cond.question == "condition"
        assert cond.operator == "=", f"expanded condition should use '=', got {cond.operator!r}"
    values = [c.value for c in sw.conditions]
    assert values == ["cancer", "heart_disease", "diabetes"]


def test_in_operator_alias_is_one_of_also_works():
    """is_one_of is accepted as an alias for in (Connect's vocabulary)."""
    sw = _parse_show_when({
        "question": "smoker",
        "operator": "is_one_of",
        "values": [1, 2],
    })
    assert sw.behavior == "any"
    assert len(sw.conditions) == 2
    assert sw.conditions[0].operator == "="


def test_in_operator_without_values_raises():
    """operator: in must have a values list."""
    with pytest.raises(ValueError, match="values"):
        _parse_show_when({"question": "q1", "operator": "in"})


def test_in_inside_conditions_list_also_expands():
    """The expansion works in multi-condition (conditions: [...]) form too."""
    sw = _parse_show_when({
        "behavior": "all",
        "conditions": [
            {"question": "q1", "operator": "in", "values": ["a", "b"]},
            {"question": "q2", "operator": "=", "value": "yes"},
        ],
    })
    # 2 from the `in` expansion + 1 plain = 3 conditions
    assert len(sw.conditions) == 3
    assert sw.behavior == "all"  # behavior preserved from outer


# ---------------------------------------------------------------------------
# B. on_missing default-value semantic
# ---------------------------------------------------------------------------

def test_on_missing_parses_into_skipcondition():
    """on_missing in YAML becomes SkipCondition.on_missing."""
    sw = _parse_show_when({
        "question": "age",
        "operator": ">=",
        "value": 65,
        "on_missing": 0,
    })
    assert sw.conditions[0].on_missing == "0"


def test_eval_condition_absent_trigger_with_no_default_is_false():
    """Regression: prior behavior preserved when no default is set."""
    rule = {
        "operator": ">=",
        "trigger_qq_id": 42,
        "trigger_value": "65",
        "trigger_default_value": None,
    }
    assert _eval_condition(rule, answers={}) is False


def test_eval_condition_absent_trigger_with_default_uses_default():
    """When the trigger isn't answered, the rule evaluates against the default."""
    rule = {
        "operator": ">=",
        "trigger_qq_id": 42,
        "trigger_value": "65",
        "trigger_default_value": "70",
    }
    # Default 70 >= 65 → True
    assert _eval_condition(rule, answers={}) is True

    rule_below = dict(rule, trigger_default_value="40")
    # Default 40 >= 65 → False
    assert _eval_condition(rule_below, answers={}) is False


def test_eval_condition_present_trigger_ignores_default():
    """A real answer wins over the default."""
    rule = {
        "operator": "=",
        "trigger_qq_id": 1,
        "trigger_value": "yes",
        "trigger_default_value": "no",
    }
    # Real answer "yes" matches → True; default would have said False
    assert _eval_condition(rule, answers={1: "yes"}) is True
    # Real answer "no" doesn't match → False; default isn't consulted
    assert _eval_condition(rule, answers={1: "no"}) is False


# ---------------------------------------------------------------------------
# End-to-end: load a YAML using both features, verify the database state
# ---------------------------------------------------------------------------

YAML_FIXTURE = """
name: Skip Rule Extensions Test
version: "1.0"
canonical_url: http://quickq.io/instruments/skip-rule-extensions-test

questions:
  - link_id: condition
    text: Have you ever been diagnosed with one of these?
    type: single_choice
    options:
      - { text: "Cancer",        value: "cancer" }
      - { text: "Heart disease", value: "heart_disease" }
      - { text: "Diabetes",      value: "diabetes" }
      - { text: "None",          value: "none" }

  - link_id: age
    text: How old are you?
    type: numeric

  - link_id: followup_cancer
    text: When were you diagnosed?
    type: date
    show_when:
      question: condition
      operator: in
      values: ["cancer", "heart_disease", "diabetes"]

  - link_id: senior_followup
    text: Senior-specific follow-up
    type: text
    show_when:
      question: age
      operator: ">="
      value: 65
      on_missing: 0
"""


@pytest.fixture()
def loaded_db(tmp_path):
    db_path = tmp_path / "ext.db"
    conn = init_oltp(db_path)
    yaml_path = tmp_path / "ext.yaml"
    yaml_path.write_text(YAML_FIXTURE)
    load_yaml(conn, yaml_path)
    conn.commit()
    return conn


def test_in_lands_as_three_equality_rules_in_db(loaded_db):
    """Verify the YAML-level `in` produced three '=' rows in skip_rule."""
    rows = loaded_db.execute(
        """SELECT sr.operator, sr.trigger_value, sr.enable_behavior
           FROM skip_rule sr
           JOIN questionnaire_question qq ON sr.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'followup_cancer'
           ORDER BY sr.trigger_value"""
    ).fetchall()
    assert len(rows) == 3
    operators = {r[0] for r in rows}
    behaviors = {r[2] for r in rows}
    values = sorted(r[1] for r in rows)
    assert operators == {"="}, f"expected all '=' operators, got {operators}"
    assert behaviors == {"any"}, f"expected enable_behavior=any, got {behaviors}"
    assert values == ["cancer", "diabetes", "heart_disease"]


def test_on_missing_lands_in_trigger_default_value(loaded_db):
    """The on_missing YAML field stores as trigger_default_value in skip_rule."""
    row = loaded_db.execute(
        """SELECT sr.operator, sr.trigger_value, sr.trigger_default_value
           FROM skip_rule sr
           JOIN questionnaire_question qq ON sr.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'senior_followup'"""
    ).fetchone()
    assert row is not None
    assert row[0] == ">="
    assert row[1] == "65"
    assert row[2] == "0", f"on_missing=0 should store as trigger_default_value='0', got {row[2]!r}"


def test_rules_without_on_missing_have_null_default(loaded_db):
    """Backwards compatible: rules that don't declare on_missing get NULL."""
    rows = loaded_db.execute(
        """SELECT sr.trigger_default_value
           FROM skip_rule sr
           JOIN questionnaire_question qq ON sr.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'followup_cancer'"""
    ).fetchall()
    for r in rows:
        assert r[0] is None, "rules without on_missing should have NULL trigger_default_value"
