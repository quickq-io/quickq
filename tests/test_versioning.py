import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.authoring import upsert_question, insert_questionnaire, place_question
from quickq.models import QuestionDef, QuestionnaireDef
from quickq.versioning import (
    record_question_lineage, get_lineage_ancestors,
    declare_equivalence, get_equivalence_group, compute_equivalence_groups,
    record_questionnaire_diff, diff_questionnaire_versions,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_question(conn, link_id, text="Question text", qtype="single_choice"):
    return upsert_question(conn, QuestionDef(link_id=link_id, text=text, type=qtype))


def _make_questionnaire(conn, name, question_link_ids):
    qid = insert_questionnaire(conn, QuestionnaireDef(name=name))
    qq_ids = {}
    for i, link_id in enumerate(question_link_ids):
        q_id = upsert_question(conn, QuestionDef(link_id=link_id, text=f"Text for {link_id}", type="single_choice"))
        qq_ids[link_id] = place_question(conn, qid, q_id, display_order=i)
    conn.commit()
    return qid, qq_ids


# ------------------------------------------------------------------
# upsert_question immutability guard
# ------------------------------------------------------------------

def test_upsert_question_same_text_is_idempotent(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    id1 = _make_question(conn, "q1", "Do you smoke?")
    id2 = _make_question(conn, "q1", "Do you smoke?")
    assert id1 == id2


def test_upsert_question_different_text_raises(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    _make_question(conn, "q1", "Original text")
    with pytest.raises(ValueError, match="different text"):
        _make_question(conn, "q1", "Changed text")


def test_upsert_question_error_message_names_link_id(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    _make_question(conn, "phq9.1", "Original")
    with pytest.raises(ValueError, match="phq9.1"):
        _make_question(conn, "phq9.1", "Changed")


# ------------------------------------------------------------------
# Question lineage
# ------------------------------------------------------------------

def test_record_lineage(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q_old = _make_question(conn, "diabetes.v1", "Have you been diagnosed with diabetes?")
    q_new = _make_question(conn, "diabetes.v2", "Has a doctor ever told you you have diabetes?")
    conn.commit()

    lid = record_question_lineage(
        conn, q_new, q_old,
        change_type="reword",
        change_description="Rewording to match BRFSS 2022 phrasing",
        effective_date="2022-01-01",
    )
    conn.commit()
    assert lid > 0

    row = conn.execute("SELECT * FROM question_lineage WHERE lineage_id=?", (lid,)).fetchone()
    assert row["question_id"] == q_new
    assert row["parent_question_id"] == q_old
    assert row["change_type"] == "reword"


def test_lineage_invalid_change_type_raises(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "q1", "Q1")
    q2 = _make_question(conn, "q2", "Q2")
    conn.commit()
    with pytest.raises(ValueError, match="Invalid change_type"):
        record_question_lineage(conn, q2, q1, change_type="typo_fix")


def test_lineage_self_reference_raises(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "q1", "Q1")
    conn.commit()
    with pytest.raises(Exception):   # CHECK constraint
        record_question_lineage(conn, q1, q1, change_type="reword")
        conn.commit()


def test_get_lineage_ancestors_chain(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "q.v1", "Version 1")
    q2 = _make_question(conn, "q.v2", "Version 2")
    q3 = _make_question(conn, "q.v3", "Version 3")
    conn.commit()

    record_question_lineage(conn, q2, q1, "reword")
    record_question_lineage(conn, q3, q2, "option_added")
    conn.commit()

    ancestors = get_lineage_ancestors(conn, q3)
    assert len(ancestors) == 2
    assert ancestors[0]["question_id"] == q2   # immediate parent first
    assert ancestors[1]["question_id"] == q1


def test_get_lineage_ancestors_no_parents(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "q1", "Standalone")
    conn.commit()
    assert get_lineage_ancestors(conn, q1) == []


# ------------------------------------------------------------------
# Question equivalence
# ------------------------------------------------------------------

def test_declare_equivalence_stores_both_directions(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "wave1.diabetes", "Diagnosed with diabetes?")
    q2 = _make_question(conn, "wave2.diabetes", "Doctor told you about diabetes?")
    conn.commit()

    fwd, rev = declare_equivalence(
        conn, q1, q2,
        relationship="near_equivalent",
        confidence="high",
        harmonization_notes="Reword only; no evidence of response bias.",
        declared_by="Dr. Smith",
    )
    conn.commit()

    assert fwd != rev
    rows = conn.execute(
        "SELECT question_id_1, question_id_2 FROM question_equivalence WHERE relationship='near_equivalent'"
    ).fetchall()
    pairs = {(r[0], r[1]) for r in rows}
    assert (q1, q2) in pairs
    assert (q2, q1) in pairs    # reverse direction stored


def test_declare_equivalence_idempotent(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "q1", "Q1")
    q2 = _make_question(conn, "q2", "Q2")
    conn.commit()

    declare_equivalence(conn, q1, q2, "equivalent", "high")
    declare_equivalence(conn, q1, q2, "equivalent", "high")   # second call — no-op
    conn.commit()

    count = conn.execute(
        "SELECT COUNT(*) FROM question_equivalence WHERE question_id_1=? AND question_id_2=?",
        (q1, q2),
    ).fetchone()[0]
    assert count == 1


def test_declare_equivalence_invalid_relationship_raises(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "q1", "Q1")
    q2 = _make_question(conn, "q2", "Q2")
    conn.commit()
    with pytest.raises(ValueError, match="Invalid relationship"):
        declare_equivalence(conn, q1, q2, relationship="same")


def test_declare_equivalence_invalid_confidence_raises(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "q1", "Q1")
    q2 = _make_question(conn, "q2", "Q2")
    conn.commit()
    with pytest.raises(ValueError, match="Invalid confidence"):
        declare_equivalence(conn, q1, q2, confidence="very_high")


def test_get_equivalence_group(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "w1.smoke", "Do you smoke?")
    q2 = _make_question(conn, "w2.smoke", "Do you currently smoke?")
    q3 = _make_question(conn, "w3.smoke", "Are you a current smoker?")
    conn.commit()

    declare_equivalence(conn, q1, q2, "near_equivalent", "high")
    declare_equivalence(conn, q1, q3, "near_equivalent", "medium")
    conn.commit()

    group = get_equivalence_group(conn, q1)
    group_ids = {g["question_id"] for g in group}
    assert q2 in group_ids
    assert q3 in group_ids
    assert q1 not in group_ids   # self not included


def test_compute_equivalence_groups_connected_components(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    # Group A: q1—q2—q3
    q1 = _make_question(conn, "q1", "Q1")
    q2 = _make_question(conn, "q2", "Q2")
    q3 = _make_question(conn, "q3", "Q3")
    # Group B: q4—q5
    q4 = _make_question(conn, "q4", "Q4")
    q5 = _make_question(conn, "q5", "Q5")
    # Isolated: q6
    q6 = _make_question(conn, "q6", "Q6")
    conn.commit()

    declare_equivalence(conn, q1, q2, "equivalent", "high")
    declare_equivalence(conn, q2, q3, "near_equivalent", "medium")
    declare_equivalence(conn, q4, q5, "equivalent", "high")
    conn.commit()

    groups = compute_equivalence_groups(conn)

    # q1, q2, q3 should share a group
    assert groups[q1] == groups[q2] == groups[q3]
    # q4, q5 should share a group (different from q1's group)
    assert groups[q4] == groups[q5]
    assert groups[q4] != groups[q1]
    # q6 is in its own group
    assert groups[q6] != groups[q1]
    assert groups[q6] != groups[q4]


def test_compute_equivalence_groups_no_equivalences(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q1 = _make_question(conn, "q1", "Q1")
    q2 = _make_question(conn, "q2", "Q2")
    conn.commit()

    groups = compute_equivalence_groups(conn)
    # Each question in its own group
    assert groups[q1] != groups[q2]


# ------------------------------------------------------------------
# Questionnaire version diffing
# ------------------------------------------------------------------

def test_diff_item_added(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    v1_id, _ = _make_questionnaire(conn, "Survey v1", ["q.a", "q.b"])
    v2_id, _ = _make_questionnaire(conn, "Survey v2", ["q.a", "q.b", "q.c"])

    diffs = diff_questionnaire_versions(conn, v1_id, v2_id)
    added = [d for d in diffs if d["change_type"] == "item_added"]
    assert len(added) == 1
    assert added[0]["link_id"] == "q.c"


def test_diff_item_removed(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    v1_id, _ = _make_questionnaire(conn, "Survey v1", ["q.a", "q.b", "q.c"])
    v2_id, _ = _make_questionnaire(conn, "Survey v2", ["q.a", "q.c"])

    diffs = diff_questionnaire_versions(conn, v1_id, v2_id)
    removed = [d for d in diffs if d["change_type"] == "item_removed"]
    assert len(removed) == 1
    assert removed[0]["link_id"] == "q.b"


def test_diff_item_reworded(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    v1_id, _ = _make_questionnaire(conn, "Survey v1", ["q.smoke"])

    # Create a new question with same link_id but different text — not possible
    # with upsert_question. Instead create a new link_id and use it in v2.
    q_new = upsert_question(conn, QuestionDef(
        link_id="q.smoke.v2",
        text="Do you currently smoke cigarettes?",
        type="single_choice",
    ))
    v2_qid = insert_questionnaire(conn, QuestionnaireDef(name="Survey v2"))
    place_question(conn, v2_qid, q_new, display_order=0)
    conn.commit()

    # The diff won't detect this as a reword automatically (different link_ids)
    # — that's correct: the analyst declares it via declare_equivalence + lineage.
    diffs = diff_questionnaire_versions(conn, v1_id, v2_qid)
    assert any(d["change_type"] == "item_removed" for d in diffs)
    assert any(d["change_type"] == "item_added" for d in diffs)


def test_diff_no_changes(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    v1_id, _ = _make_questionnaire(conn, "Survey v1", ["q.a", "q.b"])
    v2_id, _ = _make_questionnaire(conn, "Survey v2", ["q.a", "q.b"])

    diffs = diff_questionnaire_versions(conn, v1_id, v2_id)
    assert diffs == []


def test_diff_auto_record(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    v1_id, _ = _make_questionnaire(conn, "Survey v1", ["q.a", "q.b"])
    v2_id, _ = _make_questionnaire(conn, "Survey v2", ["q.a", "q.b", "q.c"])

    diffs = diff_questionnaire_versions(conn, v1_id, v2_id, auto_record=True)
    assert len(diffs) == 1

    stored = conn.execute(
        "SELECT * FROM questionnaire_version_diff WHERE from_questionnaire_id=?", (v1_id,)
    ).fetchall()
    assert len(stored) == 1
    assert stored[0]["change_type"] == "item_added"


def test_record_questionnaire_diff_invalid_type_raises(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    v1_id, _ = _make_questionnaire(conn, "v1", ["q.a"])
    v2_id, _ = _make_questionnaire(conn, "v2", ["q.a"])
    with pytest.raises(ValueError, match="Invalid change_type"):
        record_questionnaire_diff(conn, v1_id, v2_id, change_type="typo_fixed")
