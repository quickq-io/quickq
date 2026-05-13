"""End-to-end seed tests for the ap8 Class-A and Class-B skip-rule
extensions shipped in commit 14ab5ba.

The unit tests in tests/test_skip_rule_extensions.py exercise the
loader and the rule evaluator in isolation. This file runs a full
seed cycle on YAML that uses both features and asserts the generated
synthetic responses honor them.
"""
from __future__ import annotations

import pytest

from quickq.loader import load_yaml
from quickq.schema import init_oltp
from quickq.seed import seed_responses


# - condition: single_choice with 4 options (3 "trigger" the followup, 1 doesn't)
# - followup_condition: gated on condition `in [cancer, heart_disease, diabetes]`
#
# - skip_age: boolean controlling whether the age question is asked
# - age: numeric, gated on skip_age="false"
# - senior_followup: gated on age >= 65 with on_missing: 70
#
# The on_missing default (70) is *above* the threshold (65), so:
#   - when age is answered: real age decides eligibility
#   - when age is absent (skipped): default 70 → rule TRUE → followup answered
# This makes on_missing observably different from the absent-trigger-FALSE
# fallback.
YAML_FIXTURE = """
name: Seed extensions test
version: "1.0"
canonical_url: http://quickq.io/instruments/seed-extensions-test

questions:
  - link_id: condition
    text: Have you ever been diagnosed with...?
    type: single_choice
    options:
      - { text: "Cancer",        value: "cancer" }
      - { text: "Heart disease", value: "heart_disease" }
      - { text: "Diabetes",      value: "diabetes" }
      - { text: "None",          value: "none" }

  - link_id: followup_condition
    text: When were you diagnosed?
    type: date
    show_when:
      question: condition
      operator: in
      values: ["cancer", "heart_disease", "diabetes"]

  - link_id: skip_age
    text: Skip the age question?
    type: boolean

  - link_id: age
    text: How old are you?
    type: numeric
    range: [18, 95]
    show_when:
      question: skip_age
      operator: "="
      value: "false"

  - link_id: senior_followup
    text: Senior-specific follow-up
    type: text
    show_when:
      question: age
      operator: ">="
      value: 65
      on_missing: 70   # absent age → use 70 → 70 >= 65 TRUE → followup IS answered
"""


@pytest.fixture()
def seeded_db(tmp_path):
    db_path = tmp_path / "seed_ext.db"
    conn = init_oltp(db_path)
    yaml_path = tmp_path / "seed_ext.yaml"
    yaml_path.write_text(YAML_FIXTURE)
    load_yaml(conn, yaml_path)
    conn.commit()

    # Seed enough sessions that the 25% / 75% split lands within tolerance.
    seed_responses(conn, questionnaire_id=1, n=200, rng_seed=42)
    conn.commit()
    return conn


def test_seed_in_operator_gates_followup_question(seeded_db):
    """Followup answered iff the condition was one of the three triggers."""
    rows = seeded_db.execute(
        """SELECT
               r_cond.response_text   AS condition_choice,
               COUNT(DISTINCT r_cond.session_id) AS n_sessions,
               COUNT(DISTINCT r_fu.session_id)   AS n_with_followup
           FROM response r_cond
           JOIN questionnaire_question qq_cond ON r_cond.qq_id = qq_cond.qq_id
           JOIN question q_cond ON qq_cond.question_id = q_cond.question_id
           LEFT JOIN response_option opt ON r_cond.option_id = opt.option_id
           LEFT JOIN response r_fu
             ON r_fu.session_id = r_cond.session_id
            AND r_fu.qq_id IN (SELECT qq.qq_id FROM questionnaire_question qq
                                JOIN question q ON qq.question_id = q.question_id
                                WHERE q.link_id = 'followup_condition')
           WHERE q_cond.link_id = 'condition'
           GROUP BY opt.option_value
           ORDER BY opt.option_value"""
    ).fetchall()

    # Easier path: directly enumerate via option_value.
    by_choice = {}
    for r in seeded_db.execute(
        """SELECT opt.option_value, COUNT(DISTINCT r.session_id) AS n
           FROM response r
           JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           JOIN response_option opt ON r.option_id = opt.option_id
           WHERE q.link_id = 'condition'
           GROUP BY opt.option_value"""
    ):
        by_choice[r[0]] = r[1]

    # Followup count per condition_choice
    followup_by_choice = {}
    for r in seeded_db.execute(
        """SELECT opt.option_value, COUNT(DISTINCT r_fu.session_id) AS n
           FROM response r_cond
           JOIN questionnaire_question qq_cond ON r_cond.qq_id = qq_cond.qq_id
           JOIN question q_cond ON qq_cond.question_id = q_cond.question_id
           JOIN response_option opt ON r_cond.option_id = opt.option_id
           LEFT JOIN response r_fu
             ON r_fu.session_id = r_cond.session_id
            AND r_fu.qq_id IN (SELECT qq.qq_id FROM questionnaire_question qq
                                JOIN question q ON qq.question_id = q.question_id
                                WHERE q.link_id = 'followup_condition')
           WHERE q_cond.link_id = 'condition'
             AND r_fu.session_id IS NOT NULL
           GROUP BY opt.option_value"""
    ):
        followup_by_choice[r[0]] = r[1]

    # Triggering choices should have followup ≈ 100% of their sessions;
    # 'none' should have 0%.
    for trigger in ("cancer", "heart_disease", "diabetes"):
        n_total = by_choice.get(trigger, 0)
        n_followup = followup_by_choice.get(trigger, 0)
        # Skip if no sessions happened to pick this option (rng-dependent)
        if n_total == 0:
            continue
        assert n_followup == n_total, (
            f"choice={trigger!r}: {n_followup}/{n_total} sessions got the followup; "
            f"expected all sessions to get it"
        )

    # 'none' must have zero followup answers
    assert followup_by_choice.get("none", 0) == 0, (
        f"choice='none' produced {followup_by_choice.get('none')} followup answers; "
        f"expected 0"
    )


def test_seed_on_missing_default_makes_senior_followup_appear(seeded_db):
    """When skip_age=true, age is absent — but on_missing: 70 means
    senior_followup's gate evaluates 70 >= 65 → TRUE → followup IS answered.

    Without on_missing, an absent trigger would make the rule FALSE and the
    followup would never appear for skip_age=true sessions.
    """
    # Find sessions where skip_age = true (age was deliberately skipped)
    skip_age_sessions = {
        row[0]
        for row in seeded_db.execute(
            """SELECT DISTINCT r.session_id
               FROM response r
               JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
               JOIN question q ON qq.question_id = q.question_id
               WHERE q.link_id = 'skip_age' AND r.response_text = 'true'"""
        )
    }
    assert len(skip_age_sessions) > 0, "Expected some sessions with skip_age=true"

    # Of those, how many have an answer to senior_followup?
    senior_fu_sessions_among_skipped = seeded_db.execute(
        f"""SELECT COUNT(DISTINCT r.session_id)
            FROM response r
            JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
            JOIN question q ON qq.question_id = q.question_id
            WHERE q.link_id = 'senior_followup'
              AND r.session_id IN ({','.join(map(str, skip_age_sessions))})"""
    ).fetchone()[0]

    # With on_missing=70 the rule evaluates TRUE for every skip_age=true
    # session, so all of them should have answered senior_followup.
    assert senior_fu_sessions_among_skipped == len(skip_age_sessions), (
        f"Expected on_missing=70 to make senior_followup appear in all "
        f"{len(skip_age_sessions)} skip_age=true sessions; got "
        f"{senior_fu_sessions_among_skipped}. on_missing default is not "
        f"being applied during seed."
    )


def test_seed_normal_age_path_still_respects_threshold(seeded_db):
    """Sanity: sessions where age was answered should follow the >=65 rule."""
    rows = seeded_db.execute(
        """SELECT r_age.response_numeric, r_fu.session_id IS NOT NULL AS has_followup
           FROM response r_age
           JOIN questionnaire_question qq_age ON r_age.qq_id = qq_age.qq_id
           JOIN question q_age ON qq_age.question_id = q_age.question_id
           LEFT JOIN response r_fu
             ON r_fu.session_id = r_age.session_id
            AND r_fu.qq_id IN (SELECT qq.qq_id FROM questionnaire_question qq
                                JOIN question q ON qq.question_id = q.question_id
                                WHERE q.link_id = 'senior_followup')
           WHERE q_age.link_id = 'age'"""
    ).fetchall()

    for age, has_followup in rows:
        if age >= 65:
            assert has_followup, f"age={age} should have senior_followup answered"
        else:
            assert not has_followup, f"age={age} should NOT have senior_followup answered"
