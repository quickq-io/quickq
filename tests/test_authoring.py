import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import warnings
from quickq.schema import init_oltp
from quickq.models import OptionDef, QuestionDef, SectionDef, QuestionnaireDef, GridRowDef, GridColumnDef
from quickq.authoring import (
    upsert_vocabulary, upsert_concept, resolve_concept,
    insert_study, insert_questionnaire, insert_section,
    upsert_option_set, upsert_question, insert_options,
    insert_grid_rows_columns,
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


# ------------------------------------------------------------------
# concept_id integer path for options and grid items
# ------------------------------------------------------------------

def _db_with_snomed(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    upsert_vocabulary(conn, "SNOMED", "SNOMED CT", "https://snomed.info/sct")
    upsert_concept(conn, "Yes", "Answer", "SNOMED", "Answer", "373066001")
    upsert_concept(conn, "No",  "Answer", "SNOMED", "Answer", "373067005")
    return conn


def test_option_direct_concept_id_bypasses_string(tmp_path):
    conn = _db_with_snomed(tmp_path)
    cid = conn.execute(
        "SELECT concept_id FROM concept WHERE concept_code = '373066001'"
    ).fetchone()["concept_id"]

    q_id = upsert_question(conn, QuestionDef(link_id="q1", text="Smoke?", type="single_choice"))
    insert_options(conn, q_id, [OptionDef(text="Yes", value="yes", concept_id=cid)])
    conn.commit()

    row = conn.execute(
        "SELECT concept_id, concept_code FROM response_option WHERE question_id = ?", (q_id,)
    ).fetchone()
    assert row["concept_id"] == cid
    assert row["concept_code"] == "373066001"


def test_option_direct_concept_id_takes_precedence_over_string(tmp_path):
    conn = _db_with_snomed(tmp_path)
    cid = conn.execute(
        "SELECT concept_id FROM concept WHERE concept_code = '373066001'"
    ).fetchone()["concept_id"]

    q_id = upsert_question(conn, QuestionDef(link_id="q1", text="Smoke?", type="single_choice"))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        insert_options(conn, q_id, [
            OptionDef(text="Yes", value="yes", concept="SNOMED:99999-X", concept_id=cid),
        ])
    conn.commit()

    row = conn.execute(
        "SELECT concept_id FROM response_option WHERE question_id = ?", (q_id,)
    ).fetchone()
    assert row["concept_id"] == cid
    assert not any("unmapped" in str(w.message) for w in caught)


def test_option_unresolved_concept_warns_when_vocab_seeded(tmp_path):
    conn = _db_with_snomed(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q1", text="Smoke?", type="single_choice"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        insert_options(conn, q_id, [OptionDef(text="Maybe", value="maybe", concept="SNOMED:99999-X")])

    assert any("99999-X" in str(w.message) and "unmapped" in str(w.message) for w in caught)


def test_option_unresolved_concept_silent_when_vocab_not_seeded(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q_id = upsert_question(conn, QuestionDef(link_id="q1", text="Smoke?", type="single_choice"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        insert_options(conn, q_id, [OptionDef(text="Maybe", value="maybe", concept="SNOMED:99999-X")])

    assert not any("unmapped" in str(w.message) for w in caught)


def test_grid_row_direct_concept_id(tmp_path):
    conn = _db_with_snomed(tmp_path)
    cid = conn.execute(
        "SELECT concept_id FROM concept WHERE concept_code = '373066001'"
    ).fetchone()["concept_id"]

    q_id = upsert_question(conn, QuestionDef(link_id="g1", text="Grid?", type="grid"))
    insert_grid_rows_columns(
        conn, q_id,
        rows=[GridRowDef(text="Row A", concept_id=cid)],
        columns=[GridColumnDef(text="Col 1", value="c1")],
    )
    conn.commit()

    row = conn.execute(
        "SELECT concept_id FROM grid_row WHERE question_id = ?", (q_id,)
    ).fetchone()
    assert row["concept_id"] == cid


def test_grid_column_direct_concept_id(tmp_path):
    conn = _db_with_snomed(tmp_path)
    cid = conn.execute(
        "SELECT concept_id FROM concept WHERE concept_code = '373066001'"
    ).fetchone()["concept_id"]

    q_id = upsert_question(conn, QuestionDef(link_id="g1", text="Grid?", type="grid"))
    insert_grid_rows_columns(
        conn, q_id,
        rows=[GridRowDef(text="Row A")],
        columns=[GridColumnDef(text="Col 1", value="c1", concept_id=cid)],
    )
    conn.commit()

    col = conn.execute(
        "SELECT concept_id FROM grid_column WHERE question_id = ?", (q_id,)
    ).fetchone()
    assert col["concept_id"] == cid


def test_grid_row_unresolved_concept_warns_when_vocab_seeded(tmp_path):
    conn = _db_with_snomed(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="g1", text="Grid?", type="grid"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        insert_grid_rows_columns(
            conn, q_id,
            rows=[GridRowDef(text="Row A", concept="SNOMED:99999-X")],
            columns=[GridColumnDef(text="Col 1", value="c1")],
        )

    assert any("99999-X" in str(w.message) and "unmapped" in str(w.message) for w in caught)


def test_grid_column_unresolved_concept_warns_when_vocab_seeded(tmp_path):
    conn = _db_with_snomed(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="g1", text="Grid?", type="grid"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        insert_grid_rows_columns(
            conn, q_id,
            rows=[GridRowDef(text="Row A")],
            columns=[GridColumnDef(text="Col 1", value="c1", concept="SNOMED:99999-X")],
        )

    assert any("99999-X" in str(w.message) and "unmapped" in str(w.message) for w in caught)


def test_grid_concept_silent_when_vocab_not_seeded(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q_id = upsert_question(conn, QuestionDef(link_id="g1", text="Grid?", type="grid"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        insert_grid_rows_columns(
            conn, q_id,
            rows=[GridRowDef(text="Row A", concept="SNOMED:373066001")],
            columns=[GridColumnDef(text="Col 1", value="c1", concept="SNOMED:373067005")],
        )

    assert not any("unmapped" in str(w.message) for w in caught)


# ------------------------------------------------------------------
# auto_upsert_local_concept and upsert_concept_relationship
# ------------------------------------------------------------------

def test_auto_upsert_local_concept_generates_omop_range_code(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    from quickq.authoring import auto_upsert_local_concept
    cid = auto_upsert_local_concept(conn, "Novel symptom", "Survey", "Question")
    conn.commit()
    row = conn.execute(
        "SELECT concept_code, vocabulary_id FROM concept WHERE concept_id = ?", (cid,)
    ).fetchone()
    assert row["vocabulary_id"] == "Local"
    assert int(row["concept_code"]) >= 2000000001


def test_auto_upsert_local_concept_increments(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    from quickq.authoring import auto_upsert_local_concept
    cid1 = auto_upsert_local_concept(conn, "Concept A", "Survey", "Question")
    cid2 = auto_upsert_local_concept(conn, "Concept B", "Survey", "Question")
    conn.commit()
    code1 = int(conn.execute(
        "SELECT concept_code FROM concept WHERE concept_id = ?", (cid1,)
    ).fetchone()["concept_code"])
    code2 = int(conn.execute(
        "SELECT concept_code FROM concept WHERE concept_id = ?", (cid2,)
    ).fetchone()["concept_code"])
    assert code2 == code1 + 1


def test_auto_upsert_local_concept_idempotent_by_name(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    from quickq.authoring import auto_upsert_local_concept
    cid1 = auto_upsert_local_concept(conn, "Stable concept", "Survey", "Question")
    cid2 = auto_upsert_local_concept(conn, "Stable concept", "Survey", "Question")
    conn.commit()
    assert cid1 == cid2
    count = conn.execute(
        "SELECT COUNT(*) FROM concept WHERE vocabulary_id = 'Local' AND concept_name = 'Stable concept'"
    ).fetchone()[0]
    assert count == 1


def test_auto_upsert_seeds_local_vocabulary(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    from quickq.authoring import auto_upsert_local_concept
    auto_upsert_local_concept(conn, "Any concept", "Survey", "Question")
    conn.commit()
    row = conn.execute("SELECT vocabulary_id FROM vocabulary WHERE vocabulary_id = 'Local'").fetchone()
    assert row is not None


def test_upsert_concept_relationship_idempotent(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    from quickq.authoring import auto_upsert_local_concept, upsert_concept_relationship
    upsert_vocabulary(conn, "LOINC", "LOINC")
    local_id = auto_upsert_local_concept(conn, "Depressed mood", "Survey", "Question")
    loinc_id = upsert_concept(conn, "Feeling depressed", "Survey", "LOINC", "Survey", "44255-8")
    upsert_concept_relationship(conn, local_id, loinc_id, "Maps to")
    upsert_concept_relationship(conn, local_id, loinc_id, "Maps to")  # idempotent
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM concept_relationship WHERE concept_id_1 = ? AND concept_id_2 = ?",
        (local_id, loinc_id),
    ).fetchone()[0]
    assert count == 1


# ------------------------------------------------------------------
# auto_concept flag in upsert_question / insert_options / insert_grid_rows_columns
# ------------------------------------------------------------------

def test_auto_concept_question_assigns_local_code(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q_id = upsert_question(
        conn,
        QuestionDef(link_id="q.novel", text="Novel question?", type="boolean"),
        auto_concept=True,
    )
    conn.commit()
    row = conn.execute(
        "SELECT c.vocabulary_id, c.concept_code FROM question q "
        "JOIN concept c ON q.concept_id = c.concept_id WHERE q.question_id = ?",
        (q_id,),
    ).fetchone()
    assert row["vocabulary_id"] == "Local"
    assert int(row["concept_code"]) >= 2000000001


def test_auto_concept_false_leaves_concept_null(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q_id = upsert_question(
        conn,
        QuestionDef(link_id="q.unmapped", text="Unmapped?", type="boolean"),
        auto_concept=False,
    )
    conn.commit()
    row = conn.execute(
        "SELECT concept_id FROM question WHERE question_id = ?", (q_id,)
    ).fetchone()
    assert row["concept_id"] is None


def test_auto_concept_does_not_override_explicit_concept(tmp_path):
    conn = _db_with_snomed(tmp_path)
    q_id = upsert_question(
        conn,
        QuestionDef(link_id="q.explicit", text="Explicit?", type="boolean",
                    concept="SNOMED:373066001"),
        auto_concept=True,
    )
    conn.commit()
    row = conn.execute(
        "SELECT c.vocabulary_id FROM question q "
        "JOIN concept c ON q.concept_id = c.concept_id WHERE q.question_id = ?",
        (q_id,),
    ).fetchone()
    assert row["vocabulary_id"] == "SNOMED"


def test_auto_concept_option_assigns_local_code(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q_id = upsert_question(conn, QuestionDef(link_id="q1", text="Q?", type="single_choice"))
    insert_options(conn, q_id, [OptionDef(text="Never", value="never")], auto_concept=True)
    conn.commit()
    row = conn.execute(
        "SELECT c.vocabulary_id, c.concept_code, ro.concept_code AS opt_code "
        "FROM response_option ro JOIN concept c ON ro.concept_id = c.concept_id "
        "WHERE ro.question_id = ?",
        (q_id,),
    ).fetchone()
    assert row["vocabulary_id"] == "Local"
    assert int(row["concept_code"]) >= 2000000001
    assert row["opt_code"] == row["concept_code"]


def test_auto_concept_grid_assigns_local_codes(tmp_path):
    conn = init_oltp(tmp_path / "test.db")
    q_id = upsert_question(conn, QuestionDef(link_id="g1", text="Grid?", type="grid"))
    insert_grid_rows_columns(
        conn, q_id,
        rows=[GridRowDef(text="Never"), GridRowDef(text="Sometimes")],
        columns=[GridColumnDef(text="Agree", value="agree")],
        auto_concept=True,
    )
    conn.commit()
    rows = conn.execute(
        "SELECT concept_id FROM grid_row WHERE question_id = ?", (q_id,)
    ).fetchall()
    assert all(r["concept_id"] is not None for r in rows)
    col = conn.execute(
        "SELECT concept_id FROM grid_column WHERE question_id = ?", (q_id,)
    ).fetchone()
    assert col["concept_id"] is not None
