import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.authoring import upsert_question, insert_questionnaire, place_question
from quickq.models import QuestionDef, QuestionnaireDef
from quickq.parser_fhir import import_fhir
from quickq.renderer_fhir import export_fhir


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _db(tmp_path):
    return init_oltp(tmp_path / "test.db")


_MINIMAL = {
    "resourceType": "Questionnaire",
    "status": "active",
    "title": "Minimal Survey",
    "version": "1.0",
    "item": [
        {"linkId": "q.smoke", "text": "Do you smoke?", "type": "boolean"},
    ],
}


# ------------------------------------------------------------------
# Basic import
# ------------------------------------------------------------------

def test_import_returns_questionnaire_id(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, _MINIMAL)
    assert isinstance(qid, int)
    assert qid > 0


def test_import_from_json_string(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, json.dumps(_MINIMAL))
    assert qid > 0


def test_import_creates_questionnaire_row(tmp_path):
    conn = _db(tmp_path)
    res = {
        "resourceType": "Questionnaire",
        "url": "https://example.org/fhir/Questionnaire/test",
        "version": "2.1",
        "title": "Test Survey",
        "status": "draft",
        "description": "A test survey.",
        "item": [],
    }
    qid = import_fhir(conn, res)
    conn.commit()
    row = conn.execute("SELECT * FROM questionnaire WHERE questionnaire_id=?", (qid,)).fetchone()
    assert row["name"] == "Test Survey"
    assert row["canonical_url"] == "https://example.org/fhir/Questionnaire/test"
    assert row["version"] == "2.1"
    assert row["fhir_status"] == "draft"
    assert row["description"] == "A test survey."


def test_import_invalid_resource_type_raises(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="resourceType"):
        import_fhir(conn, {"resourceType": "Patient"})


def test_import_missing_resource_type_raises(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="resourceType"):
        import_fhir(conn, {"title": "Oops"})


# ------------------------------------------------------------------
# Idempotency
# ------------------------------------------------------------------

def test_import_idempotent_same_url_version(tmp_path):
    conn = _db(tmp_path)
    res = {
        "resourceType": "Questionnaire",
        "url": "https://example.org/q/idem",
        "version": "1.0",
        "status": "active",
        "item": [{"linkId": "q.a", "text": "A?", "type": "boolean"}],
    }
    qid1 = import_fhir(conn, res)
    qid2 = import_fhir(conn, res)
    assert qid1 == qid2
    # Only one questionnaire row
    count = conn.execute(
        "SELECT COUNT(*) FROM questionnaire WHERE canonical_url='https://example.org/q/idem'"
    ).fetchone()[0]
    assert count == 1


def test_import_different_versions_create_separate_rows(tmp_path):
    conn = _db(tmp_path)
    base = {
        "resourceType": "Questionnaire",
        "url": "https://example.org/q/ver",
        "status": "active",
        "item": [],
    }
    qid1 = import_fhir(conn, {**base, "version": "1.0"})
    qid2 = import_fhir(conn, {**base, "version": "2.0"})
    assert qid1 != qid2


# ------------------------------------------------------------------
# Item / question creation
# ------------------------------------------------------------------

def test_import_creates_questions(tmp_path):
    conn = _db(tmp_path)
    res = {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [
            {"linkId": "q.a", "text": "Question A?", "type": "boolean"},
            {"linkId": "q.b", "text": "Question B?", "type": "text"},
        ],
    }
    import_fhir(conn, res)
    conn.commit()
    q = conn.execute("SELECT link_id FROM question WHERE link_id IN ('q.a','q.b') ORDER BY link_id").fetchall()
    assert [r["link_id"] for r in q] == ["q.a", "q.b"]


def test_import_creates_questionnaire_question_placements(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [
            {"linkId": "q.1", "text": "First?", "type": "boolean"},
            {"linkId": "q.2", "text": "Second?", "type": "text"},
        ],
    })
    conn.commit()
    rows = conn.execute(
        "SELECT display_order FROM questionnaire_question WHERE questionnaire_id=? ORDER BY display_order",
        (qid,),
    ).fetchall()
    assert [r["display_order"] for r in rows] == [0, 1]


def test_import_reuses_existing_question_by_link_id(tmp_path):
    conn = _db(tmp_path)
    # Pre-create the question
    existing_q_id = upsert_question(conn, QuestionDef(link_id="q.existing", text="Existing?", type="boolean"))
    conn.commit()

    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{"linkId": "q.existing", "text": "Existing?", "type": "boolean"}],
    })
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM question WHERE link_id='q.existing'").fetchone()[0]
    assert count == 1  # not duplicated


def test_import_conflicting_text_raises(tmp_path):
    conn = _db(tmp_path)
    upsert_question(conn, QuestionDef(link_id="q.x", text="Original text", type="boolean"))
    conn.commit()
    with pytest.raises(ValueError, match="different text"):
        import_fhir(conn, {
            "resourceType": "Questionnaire",
            "status": "active",
            "item": [{"linkId": "q.x", "text": "Changed text", "type": "boolean"}],
        })


def test_import_required_flag(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{"linkId": "q.req", "text": "Required?", "type": "boolean", "required": True}],
    })
    conn.commit()
    row = conn.execute(
        "SELECT is_required FROM questionnaire_question WHERE questionnaire_id=?", (qid,)
    ).fetchone()
    assert row["is_required"] == 1


# ------------------------------------------------------------------
# Type mapping
# ------------------------------------------------------------------

@pytest.mark.parametrize("fhir_type,expected", [
    ("choice",     "single_choice"),
    ("open-choice", "sata_other"),
    ("boolean",    "boolean"),
    ("text",       "text"),
    ("string",     "text"),
    ("decimal",    "numeric"),
    ("integer",    "numeric"),
    ("date",       "date"),
    ("dateTime",   "datetime"),
])
def test_type_mapping(tmp_path, fhir_type, expected):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{"linkId": f"q.{fhir_type}", "text": "Q?", "type": fhir_type}],
    })
    conn.commit()
    row = conn.execute(
        "SELECT question_type FROM question WHERE link_id=?", (f"q.{fhir_type}",)
    ).fetchone()
    assert row["question_type"] == expected


def test_choice_with_repeats_maps_to_multiple_choice(tmp_path):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{"linkId": "q.mc", "text": "Select all?", "type": "choice", "repeats": True}],
    })
    conn.commit()
    row = conn.execute("SELECT question_type FROM question WHERE link_id='q.mc'").fetchone()
    assert row["question_type"] == "multiple_choice"


def test_group_items_are_flattened(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "group.1",
            "type": "group",
            "text": "Section",
            "item": [
                {"linkId": "q.inside", "text": "Inside group?", "type": "boolean"},
            ],
        }],
    })
    conn.commit()
    row = conn.execute("SELECT link_id FROM question WHERE link_id='q.inside'").fetchone()
    assert row is not None
    count = conn.execute(
        "SELECT COUNT(*) FROM questionnaire_question WHERE questionnaire_id=?", (qid,)
    ).fetchone()[0]
    assert count == 1  # group itself not placed


def test_display_items_are_skipped(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [
            {"linkId": "q.info", "text": "Instructions", "type": "display"},
            {"linkId": "q.real", "text": "Real question?", "type": "boolean"},
        ],
    })
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM questionnaire_question WHERE questionnaire_id=?", (qid,)
    ).fetchone()[0]
    assert count == 1
    row = conn.execute("SELECT link_id FROM question WHERE link_id='q.real'").fetchone()
    assert row is not None


# ------------------------------------------------------------------
# Answer options
# ------------------------------------------------------------------

def test_import_answer_options_value_coding(tmp_path):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.smoke",
            "text": "Do you smoke?",
            "type": "choice",
            "answerOption": [
                {"valueCoding": {"code": "yes", "display": "Yes"}},
                {"valueCoding": {"code": "no",  "display": "No"}},
            ],
        }],
    })
    conn.commit()
    opts = conn.execute(
        "SELECT option_value, option_text FROM response_option"
        " WHERE question_id=(SELECT question_id FROM question WHERE link_id='q.smoke')"
        " ORDER BY display_order"
    ).fetchall()
    assert [(r["option_value"], r["option_text"]) for r in opts] == [("yes", "Yes"), ("no", "No")]


def test_import_answer_options_with_system(tmp_path):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.coded",
            "text": "Coded?",
            "type": "choice",
            "answerOption": [{
                "valueCoding": {
                    "code": "373066001",
                    "display": "Yes",
                    "system": "http://snomed.info/sct",
                }
            }],
        }],
    })
    conn.commit()
    opt = conn.execute(
        "SELECT concept_code, concept_system FROM response_option"
        " WHERE question_id=(SELECT question_id FROM question WHERE link_id='q.coded')"
    ).fetchone()
    assert opt["concept_code"] == "373066001"
    assert opt["concept_system"] == "http://snomed.info/sct"


def test_import_answer_value_set(tmp_path):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.vs",
            "text": "VS question?",
            "type": "choice",
            "answerValueSet": "https://example.org/fhir/ValueSet/yn",
        }],
    })
    conn.commit()
    row = conn.execute(
        "SELECT ros.canonical_url FROM question q"
        " JOIN response_option_set ros ON q.option_set_id = ros.option_set_id"
        " WHERE q.link_id='q.vs'"
    ).fetchone()
    assert row["canonical_url"] == "https://example.org/fhir/ValueSet/yn"


def test_import_answer_options_idempotent(tmp_path):
    conn = _db(tmp_path)
    res = {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.idem",
            "text": "Idem?",
            "type": "choice",
            "answerOption": [{"valueCoding": {"code": "a", "display": "A"}}],
        }],
    }
    import_fhir(conn, res)
    import_fhir(conn, {**res, "version": "2.0"})  # different version, same question
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM response_option"
        " WHERE question_id=(SELECT question_id FROM question WHERE link_id='q.idem')"
    ).fetchone()[0]
    assert count == 1  # not doubled


# ------------------------------------------------------------------
# Skip logic (enableWhen)
# ------------------------------------------------------------------

def test_import_enable_when_equals(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [
            {
                "linkId": "q.smoke",
                "text": "Do you smoke?",
                "type": "choice",
                "answerOption": [{"valueCoding": {"code": "yes", "display": "Yes"}}],
            },
            {
                "linkId": "q.packs",
                "text": "Packs per day?",
                "type": "decimal",
                "enableWhen": [{"question": "q.smoke", "operator": "=", "answerString": "yes"}],
            },
        ],
    })
    conn.commit()
    qq2 = conn.execute(
        "SELECT qq.qq_id FROM questionnaire_question qq"
        " JOIN question q ON qq.question_id=q.question_id"
        " WHERE qq.questionnaire_id=? AND q.link_id='q.packs'",
        (qid,),
    ).fetchone()
    rule = conn.execute("SELECT * FROM skip_rule WHERE qq_id=?", (qq2["qq_id"],)).fetchone()
    assert rule is not None
    assert rule["operator"] == "="
    assert rule["trigger_value"] == "yes"


def test_import_enable_when_exists(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [
            {"linkId": "q.a", "text": "A?", "type": "text"},
            {
                "linkId": "q.b",
                "text": "B?",
                "type": "text",
                "enableWhen": [{"question": "q.a", "operator": "exists", "answerBoolean": True}],
            },
        ],
    })
    conn.commit()
    qq_b = conn.execute(
        "SELECT qq.qq_id FROM questionnaire_question qq"
        " JOIN question q ON qq.question_id=q.question_id"
        " WHERE qq.questionnaire_id=? AND q.link_id='q.b'",
        (qid,),
    ).fetchone()
    rule = conn.execute("SELECT operator FROM skip_rule WHERE qq_id=?", (qq_b["qq_id"],)).fetchone()
    assert rule["operator"] == "exists"


def test_import_enable_when_not_exists(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [
            {"linkId": "q.a", "text": "A?", "type": "text"},
            {
                "linkId": "q.b",
                "text": "B?",
                "type": "text",
                "enableWhen": [{"question": "q.a", "operator": "exists", "answerBoolean": False}],
            },
        ],
    })
    conn.commit()
    qq_b = conn.execute(
        "SELECT qq.qq_id FROM questionnaire_question qq"
        " JOIN question q ON qq.question_id=q.question_id"
        " WHERE qq.questionnaire_id=? AND q.link_id='q.b'",
        (qid,),
    ).fetchone()
    rule = conn.execute("SELECT operator FROM skip_rule WHERE qq_id=?", (qq_b["qq_id"],)).fetchone()
    assert rule["operator"] == "not_exists"


def test_import_enable_when_numeric(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [
            {"linkId": "q.age", "text": "Age?", "type": "decimal"},
            {
                "linkId": "q.senior",
                "text": "Senior?",
                "type": "boolean",
                "enableWhen": [{"question": "q.age", "operator": ">=", "answerDecimal": 65}],
            },
        ],
    })
    conn.commit()
    qq = conn.execute(
        "SELECT qq.qq_id FROM questionnaire_question qq"
        " JOIN question q ON qq.question_id=q.question_id"
        " WHERE qq.questionnaire_id=? AND q.link_id='q.senior'",
        (qid,),
    ).fetchone()
    rule = conn.execute("SELECT operator, trigger_value FROM skip_rule WHERE qq_id=?", (qq["qq_id"],)).fetchone()
    assert rule["operator"] == ">="
    assert rule["trigger_value"] == "65.0"


def test_import_enable_behavior(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [
            {"linkId": "q.a", "text": "A?", "type": "boolean"},
            {"linkId": "q.b", "text": "B?", "type": "boolean"},
            {
                "linkId": "q.c",
                "text": "C?",
                "type": "text",
                "enableWhen": [
                    {"question": "q.a", "operator": "=", "answerBoolean": True},
                    {"question": "q.b", "operator": "=", "answerBoolean": True},
                ],
                "enableBehavior": "any",
            },
        ],
    })
    conn.commit()
    qq_c = conn.execute(
        "SELECT qq.qq_id FROM questionnaire_question qq"
        " JOIN question q ON qq.question_id=q.question_id"
        " WHERE qq.questionnaire_id=? AND q.link_id='q.c'",
        (qid,),
    ).fetchone()
    rules = conn.execute(
        "SELECT enable_behavior FROM skip_rule WHERE qq_id=?", (qq_c["qq_id"],)
    ).fetchall()
    assert len(rules) == 2
    assert all(r["enable_behavior"] == "any" for r in rules)


def test_import_unresolvable_enable_when_skipped(tmp_path):
    conn = _db(tmp_path)
    # enableWhen references a linkId that doesn't exist in the questionnaire
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.orphan",
            "text": "Orphan?",
            "type": "text",
            "enableWhen": [{"question": "q.ghost", "operator": "exists", "answerBoolean": True}],
        }],
    })
    conn.commit()
    # Should not crash; skip rule is just not inserted
    count = conn.execute("SELECT COUNT(*) FROM skip_rule WHERE qq_id IN ("
                         "SELECT qq_id FROM questionnaire_question WHERE questionnaire_id=?)",
                         (qid,)).fetchone()[0]
    assert count == 0


# ------------------------------------------------------------------
# Extensions
# ------------------------------------------------------------------

def test_import_help_text_extension(tmp_path):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.help",
            "text": "Q?",
            "type": "boolean",
            "extension": [{"url": "https://quickq.io/fhir/StructureDefinition/help-text",
                           "valueString": "This helps you answer."}],
        }],
    })
    conn.commit()
    row = conn.execute("SELECT help_text FROM question WHERE link_id='q.help'").fetchone()
    assert row["help_text"] == "This helps you answer."


def test_import_internal_note_extension(tmp_path):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.note",
            "text": "Q?",
            "type": "boolean",
            "extension": [{"url": "https://quickq.io/fhir/StructureDefinition/internal-note",
                           "valueString": "Analyst only."}],
        }],
    })
    conn.commit()
    row = conn.execute("SELECT internal_note FROM question WHERE link_id='q.note'").fetchone()
    assert row["internal_note"] == "Analyst only."


def test_import_source_instrument_extension(tmp_path):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.src",
            "text": "Q?",
            "type": "boolean",
            "extension": [
                {"url": "https://quickq.io/fhir/StructureDefinition/source-instrument",
                 "valueString": "PHQ-9"},
                {"url": "https://quickq.io/fhir/StructureDefinition/source-item-id",
                 "valueString": "PHQ-9-1"},
            ],
        }],
    })
    conn.commit()
    row = conn.execute(
        "SELECT source_instrument, source_item_id FROM question WHERE link_id='q.src'"
    ).fetchone()
    assert row["source_instrument"] == "PHQ-9"
    assert row["source_item_id"] == "PHQ-9-1"


# ------------------------------------------------------------------
# Scoring rules
# ------------------------------------------------------------------

def test_import_scoring_rule_extension(tmp_path):
    conn = _db(tmp_path)
    qid = import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "extension": [{
            "url": "https://quickq.io/fhir/StructureDefinition/scoring-rule",
            "extension": [
                {"url": "name",    "valueString": "PHQ-9 Total"},
                {"url": "formula", "valueString": "sum"},
                {"url": "description", "valueString": "Sum of 9 items"},
            ],
        }],
        "item": [{"linkId": "q.i1", "text": "Item 1?", "type": "choice"}],
    })
    conn.commit()
    rule = conn.execute(
        "SELECT name, formula, description FROM scoring_rule WHERE questionnaire_id=?", (qid,)
    ).fetchone()
    assert rule["name"] == "PHQ-9 Total"
    assert rule["formula"] == "sum"
    assert rule["description"] == "Sum of 9 items"


# ------------------------------------------------------------------
# Numeric constraints
# ------------------------------------------------------------------

def test_import_numeric_min_max(tmp_path):
    conn = _db(tmp_path)
    import_fhir(conn, {
        "resourceType": "Questionnaire",
        "status": "active",
        "item": [{
            "linkId": "q.bmi",
            "text": "BMI?",
            "type": "decimal",
            "extension": [
                {"url": "http://hl7.org/fhir/StructureDefinition/minValue", "valueDecimal": 10.0},
                {"url": "http://hl7.org/fhir/StructureDefinition/maxValue", "valueDecimal": 80.0},
            ],
        }],
    })
    conn.commit()
    row = conn.execute(
        "SELECT numeric_min, numeric_max FROM question WHERE link_id='q.bmi'"
    ).fetchone()
    assert row["numeric_min"] == 10.0
    assert row["numeric_max"] == 80.0


# ------------------------------------------------------------------
# Round-trip: export → import produces equivalent structure
# ------------------------------------------------------------------

def _build_roundtrip_source(tmp_path):
    """Create a questionnaire with diverse features for round-trip testing."""
    conn = init_oltp(tmp_path / "source.db")

    q1 = upsert_question(conn, QuestionDef(
        link_id="rt.smoke",
        text="Do you currently smoke?",
        type="single_choice",
        help_text="Answer for the past 30 days.",
        source_instrument="BRFSS",
        source_item_id="BRFSS-SMOKER",
    ))
    conn.execute("INSERT INTO response_option (question_id,option_text,option_value,display_order) VALUES (?,?,?,?)",
                 (q1, "Yes", "yes", 0))
    conn.execute("INSERT INTO response_option (question_id,option_text,option_value,display_order) VALUES (?,?,?,?)",
                 (q1, "No", "no", 1))

    q2 = upsert_question(conn, QuestionDef(
        link_id="rt.packs",
        text="How many packs per day?",
        type="numeric",
        numeric_min=0.0,
        numeric_max=10.0,
    ))

    q3 = upsert_question(conn, QuestionDef(
        link_id="rt.age",
        text="What is your age?",
        type="numeric",
    ))

    qnaire_id = insert_questionnaire(conn, QuestionnaireDef(
        name="Round-trip Survey",
        canonical_url="https://example.org/fhir/Questionnaire/roundtrip",
        version="3.0",
        fhir_status="active",
        description="Tests round-trip fidelity.",
    ))

    qq1 = place_question(conn, qnaire_id, q1, display_order=0)
    qq2 = place_question(conn, qnaire_id, q2, display_order=1, required=True)
    qq3 = place_question(conn, qnaire_id, q3, display_order=2)

    conn.execute(
        "INSERT INTO skip_rule (qq_id, trigger_qq_id, operator, trigger_value) VALUES (?,?,?,?)",
        (qq2, qq1, "=", "yes"),
    )
    conn.execute(
        "INSERT INTO scoring_rule (questionnaire_id, name, formula) VALUES (?,?,?)",
        (qnaire_id, "Pack-years", "sum"),
    )
    conn.commit()
    return conn, qnaire_id


def test_roundtrip_questionnaire_metadata(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    dst_qid = import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    row = dst_conn.execute(
        "SELECT name, canonical_url, version, fhir_status, description FROM questionnaire WHERE questionnaire_id=?",
        (dst_qid,),
    ).fetchone()
    assert row["name"] == "Round-trip Survey"
    assert row["canonical_url"] == "https://example.org/fhir/Questionnaire/roundtrip"
    assert row["version"] == "3.0"
    assert row["fhir_status"] == "active"
    assert row["description"] == "Tests round-trip fidelity."


def test_roundtrip_item_count(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    dst_qid = import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    count = dst_conn.execute(
        "SELECT COUNT(*) FROM questionnaire_question WHERE questionnaire_id=?", (dst_qid,)
    ).fetchone()[0]
    assert count == 3


def test_roundtrip_question_types(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    rows = dst_conn.execute(
        "SELECT q.link_id, q.question_type FROM question q"
        " WHERE q.link_id IN ('rt.smoke','rt.packs','rt.age')"
        " ORDER BY q.link_id"
    ).fetchall()
    types = {r["link_id"]: r["question_type"] for r in rows}
    assert types["rt.smoke"] == "single_choice"
    assert types["rt.packs"] == "numeric"
    assert types["rt.age"]   == "numeric"


def test_roundtrip_answer_options(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    opts = dst_conn.execute(
        "SELECT option_value FROM response_option"
        " WHERE question_id=(SELECT question_id FROM question WHERE link_id='rt.smoke')"
        " ORDER BY display_order"
    ).fetchall()
    assert [r["option_value"] for r in opts] == ["yes", "no"]


def test_roundtrip_skip_rule(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    dst_qid = import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    # rt.packs should have an enableWhen rule pointing to rt.smoke
    rule = dst_conn.execute("""
        SELECT sr.operator, sr.trigger_value, tq.link_id AS trigger_link
        FROM skip_rule sr
        JOIN questionnaire_question tqq ON sr.trigger_qq_id = tqq.qq_id
        JOIN question tq ON tqq.question_id = tq.question_id
        JOIN questionnaire_question qq ON sr.qq_id = qq.qq_id
        JOIN question q ON qq.question_id = q.question_id
        WHERE qq.questionnaire_id=? AND q.link_id='rt.packs'
    """, (dst_qid,)).fetchone()
    assert rule is not None
    assert rule["operator"] == "="
    assert rule["trigger_value"] == "yes"
    assert rule["trigger_link"] == "rt.smoke"


def test_roundtrip_numeric_constraints(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    row = dst_conn.execute(
        "SELECT numeric_min, numeric_max FROM question WHERE link_id='rt.packs'"
    ).fetchone()
    assert row["numeric_min"] == 0.0
    assert row["numeric_max"] == 10.0


def test_roundtrip_help_text(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    row = dst_conn.execute(
        "SELECT help_text FROM question WHERE link_id='rt.smoke'"
    ).fetchone()
    assert row["help_text"] == "Answer for the past 30 days."


def test_roundtrip_scoring_rule(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    dst_qid = import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    rule = dst_conn.execute(
        "SELECT name, formula FROM scoring_rule WHERE questionnaire_id=?", (dst_qid,)
    ).fetchone()
    assert rule["name"] == "Pack-years"
    assert rule["formula"] == "sum"


def test_roundtrip_required_flag(tmp_path):
    src_conn, src_qid = _build_roundtrip_source(tmp_path)
    fhir_dict = export_fhir(src_conn, src_qid)

    dst_conn = init_oltp(tmp_path / "dst.db")
    dst_qid = import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    rows = dst_conn.execute("""
        SELECT q.link_id, qq.is_required
        FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        WHERE qq.questionnaire_id=?
    """, (dst_qid,)).fetchall()
    req = {r["link_id"]: r["is_required"] for r in rows}
    assert req["rt.packs"] == 1
    assert req["rt.smoke"] == 0


# ------------------------------------------------------------------
# Slider round-trip
# ------------------------------------------------------------------

def test_slider_roundtrip_preserves_type(tmp_path):
    src_conn = init_oltp(tmp_path / "src.db")
    q_id = upsert_question(src_conn, QuestionDef(
        link_id="q.vas", text="Pain level?", type="slider",
        numeric_min=0, numeric_max=100,
        slider_min_label="No pain", slider_max_label="Worst imaginable",
    ))
    qid = insert_questionnaire(src_conn, QuestionnaireDef(name="VAS"))
    place_question(src_conn, qid, q_id, display_order=0)
    src_conn.commit()

    fhir_dict = export_fhir(src_conn, qid)
    dst_conn = init_oltp(tmp_path / "dst.db")
    dst_qid = import_fhir(dst_conn, fhir_dict)
    dst_conn.commit()

    row = dst_conn.execute(
        """
        SELECT q.question_type, q.slider_min_label, q.slider_max_label,
               q.numeric_min, q.numeric_max
        FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        WHERE qq.questionnaire_id = ?
        """,
        (dst_qid,),
    ).fetchone()
    assert row["question_type"] == "slider"
    assert row["slider_min_label"] == "No pain"
    assert row["slider_max_label"] == "Worst imaginable"
    assert row["numeric_min"] == 0.0
    assert row["numeric_max"] == 100.0
