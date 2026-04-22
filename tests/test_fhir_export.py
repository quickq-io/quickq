import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.authoring import (
    upsert_question, insert_questionnaire, place_question,
    insert_skip_rule, insert_scoring_rule,
)
from quickq.models import QuestionDef, QuestionnaireDef, OptionDef
from quickq.renderer_fhir import export_fhir, export_fhir_json


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _db(tmp_path):
    return init_oltp(tmp_path / "test.db")


def _smoke_questionnaire(conn):
    """Single-choice smoke question, one questionnaire."""
    q_id = upsert_question(conn, QuestionDef(
        link_id="q.smoke",
        text="Do you currently smoke?",
        type="single_choice",
    ))
    conn.execute(
        "INSERT INTO response_option (question_id, option_text, option_value, display_order)"
        " VALUES (?,?,?,?)", (q_id, "Yes", "yes", 0),
    )
    conn.execute(
        "INSERT INTO response_option (question_id, option_text, option_value, display_order)"
        " VALUES (?,?,?,?)", (q_id, "No", "no", 1),
    )
    qnaire_id = insert_questionnaire(conn, QuestionnaireDef(
        name="Tobacco Survey",
        version="2.0",
        canonical_url="https://example.org/fhir/Questionnaire/tobacco",
        description="Brief tobacco screen",
        fhir_status="active",
    ))
    place_question(conn, qnaire_id, q_id, display_order=0)
    conn.commit()
    return qnaire_id, q_id


# ------------------------------------------------------------------
# Resource-level fields
# ------------------------------------------------------------------

def test_resource_type(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    r = export_fhir(conn, qid)
    assert r["resourceType"] == "Questionnaire"


def test_status_and_title(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    r = export_fhir(conn, qid)
    assert r["status"] == "active"
    assert r["title"] == "Tobacco Survey"


def test_url_and_version(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    r = export_fhir(conn, qid)
    assert r["url"] == "https://example.org/fhir/Questionnaire/tobacco"
    assert r["version"] == "2.0"


def test_description(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    r = export_fhir(conn, qid)
    assert r["description"] == "Brief tobacco screen"


def test_machine_name_strips_spaces(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    r = export_fhir(conn, qid)
    assert " " not in r.get("name", "")
    assert r.get("name") == "TobaccoSurvey"


def test_no_url_when_not_set(tmp_path):
    conn = _db(tmp_path)
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Unnamed"))
    conn.commit()
    r = export_fhir(conn, qid)
    assert "url" not in r


def test_unknown_questionnaire_raises(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        export_fhir(conn, 99999)


# ------------------------------------------------------------------
# Item structure
# ------------------------------------------------------------------

def test_item_link_id_and_text(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    items = export_fhir(conn, qid)["item"]
    assert items[0]["linkId"] == "q.smoke"
    assert items[0]["text"] == "Do you currently smoke?"


def test_items_ordered_by_display_order(tmp_path):
    conn = _db(tmp_path)
    q1 = upsert_question(conn, QuestionDef(link_id="q.a", text="A?", type="boolean"))
    q2 = upsert_question(conn, QuestionDef(link_id="q.b", text="B?", type="boolean"))
    q3 = upsert_question(conn, QuestionDef(link_id="q.c", text="C?", type="boolean"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Order Test"))
    place_question(conn, qid, q1, display_order=0)
    place_question(conn, qid, q3, display_order=2)
    place_question(conn, qid, q2, display_order=1)
    conn.commit()
    link_ids = [i["linkId"] for i in export_fhir(conn, qid)["item"]]
    assert link_ids == ["q.a", "q.b", "q.c"]


def test_empty_questionnaire_has_no_item_key(tmp_path):
    conn = _db(tmp_path)
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Empty"))
    conn.commit()
    r = export_fhir(conn, qid)
    assert "item" not in r


# ------------------------------------------------------------------
# Type mapping
# ------------------------------------------------------------------

@pytest.mark.parametrize("qtype,expected_fhir", [
    ("single_choice",   "choice"),
    ("multiple_choice", "choice"),
    ("sata_other",      "open-choice"),
    ("boolean",         "boolean"),
    ("text",            "text"),
    ("numeric",         "decimal"),
    ("date",            "date"),
    ("datetime",        "dateTime"),
    ("likert",          "choice"),
    ("slider",          "decimal"),
])
def test_type_mapping(tmp_path, qtype, expected_fhir):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id=f"q.{qtype}", text="Q?", type=qtype))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Types"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    item = export_fhir(conn, qid)["item"][0]
    assert item["type"] == expected_fhir


def test_multiple_choice_has_repeats(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.mc", text="Select all?", type="multiple_choice"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="MC"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    item = export_fhir(conn, qid)["item"][0]
    assert item.get("repeats") is True


def test_single_choice_no_repeats(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    item = export_fhir(conn, qid)["item"][0]
    assert "repeats" not in item


# ------------------------------------------------------------------
# required flag
# ------------------------------------------------------------------

def test_required_flag(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.req", text="Required?", type="boolean"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Req"))
    place_question(conn, qid, q_id, display_order=0, required=True)
    conn.commit()
    item = export_fhir(conn, qid)["item"][0]
    assert item.get("required") is True


def test_not_required_omits_field(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    item = export_fhir(conn, qid)["item"][0]
    assert "required" not in item


# ------------------------------------------------------------------
# Answer options
# ------------------------------------------------------------------

def test_answer_options_present(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    item = export_fhir(conn, qid)["item"][0]
    assert "answerOption" in item
    assert len(item["answerOption"]) == 2


def test_answer_option_value_coding(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    item = export_fhir(conn, qid)["item"][0]
    codes = [o["valueCoding"]["code"] for o in item["answerOption"]]
    assert "yes" in codes
    assert "no" in codes


def test_answer_option_with_concept_code(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.coded", text="Coded?", type="single_choice"))
    conn.execute(
        "INSERT INTO response_option (question_id, option_text, option_value, display_order,"
        " concept_code, concept_system) VALUES (?,?,?,?,?,?)",
        (q_id, "Yes", "yes", 0, "373066001", "http://snomed.info/sct"),
    )
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Coded"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    item = export_fhir(conn, qid)["item"][0]
    opt = item["answerOption"][0]["valueCoding"]
    assert opt["code"] == "373066001"
    assert opt["system"] == "http://snomed.info/sct"
    assert opt["display"] == "Yes"


def test_answer_value_set_when_option_set_has_url(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO response_option_set (name, canonical_url) VALUES (?,?)",
        ("yn", "https://example.org/fhir/ValueSet/yn"),
    )
    os_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    q_id = upsert_question(conn, QuestionDef(link_id="q.vs", text="VS?", type="single_choice"))
    conn.execute("UPDATE question SET option_set_id=? WHERE question_id=?", (os_id, q_id))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="VS"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    item = export_fhir(conn, qid)["item"][0]
    assert item["answerValueSet"] == "https://example.org/fhir/ValueSet/yn"
    assert "answerOption" not in item


# ------------------------------------------------------------------
# Skip logic → enableWhen
# ------------------------------------------------------------------

def test_enable_when_equals(tmp_path):
    conn = _db(tmp_path)
    q1 = upsert_question(conn, QuestionDef(link_id="q.smoke", text="Smoke?", type="single_choice"))
    conn.execute("INSERT INTO response_option (question_id,option_text,option_value,display_order) VALUES (?,?,?,?)",
                 (q1, "Yes", "yes", 0))
    q2 = upsert_question(conn, QuestionDef(link_id="q.packs", text="Packs/day?", type="numeric"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Skip"))
    qq1 = place_question(conn, qid, q1, display_order=0)
    qq2 = place_question(conn, qid, q2, display_order=1)
    conn.execute(
        "INSERT INTO skip_rule (qq_id, trigger_qq_id, operator, trigger_value) VALUES (?,?,?,?)",
        (qq2, qq1, "=", "yes"),
    )
    conn.commit()
    item = export_fhir(conn, qid)["item"][1]
    assert "enableWhen" in item
    ew = item["enableWhen"][0]
    assert ew["question"] == "q.smoke"
    assert ew["operator"] == "="
    # choice-type trigger → answerCoding (FHIR R4 §10.6.5 type alignment)
    assert ew["answerCoding"]["code"] == "yes"
    assert "answerString" not in ew


def test_enable_when_exists(tmp_path):
    conn = _db(tmp_path)
    q1 = upsert_question(conn, QuestionDef(link_id="q.a", text="A?", type="text"))
    q2 = upsert_question(conn, QuestionDef(link_id="q.b", text="B?", type="text"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Exists"))
    qq1 = place_question(conn, qid, q1, display_order=0)
    qq2 = place_question(conn, qid, q2, display_order=1)
    conn.execute(
        "INSERT INTO skip_rule (qq_id, trigger_qq_id, operator) VALUES (?,?,?)",
        (qq2, qq1, "exists"),
    )
    conn.commit()
    ew = export_fhir(conn, qid)["item"][1]["enableWhen"][0]
    assert ew["operator"] == "exists"
    assert ew["answerBoolean"] is True


def test_enable_when_not_exists(tmp_path):
    conn = _db(tmp_path)
    q1 = upsert_question(conn, QuestionDef(link_id="q.a", text="A?", type="text"))
    q2 = upsert_question(conn, QuestionDef(link_id="q.b", text="B?", type="text"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="NotExists"))
    qq1 = place_question(conn, qid, q1, display_order=0)
    qq2 = place_question(conn, qid, q2, display_order=1)
    conn.execute(
        "INSERT INTO skip_rule (qq_id, trigger_qq_id, operator) VALUES (?,?,?)",
        (qq2, qq1, "not_exists"),
    )
    conn.commit()
    ew = export_fhir(conn, qid)["item"][1]["enableWhen"][0]
    assert ew["operator"] == "exists"
    assert ew["answerBoolean"] is False


def test_enable_when_numeric_value(tmp_path):
    conn = _db(tmp_path)
    q1 = upsert_question(conn, QuestionDef(link_id="q.age", text="Age?", type="numeric"))
    q2 = upsert_question(conn, QuestionDef(link_id="q.senior", text="Senior?", type="boolean"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Num"))
    qq1 = place_question(conn, qid, q1, display_order=0)
    qq2 = place_question(conn, qid, q2, display_order=1)
    conn.execute(
        "INSERT INTO skip_rule (qq_id, trigger_qq_id, operator, trigger_value) VALUES (?,?,?,?)",
        (qq2, qq1, ">=", "65"),
    )
    conn.commit()
    ew = export_fhir(conn, qid)["item"][1]["enableWhen"][0]
    assert ew["operator"] == ">="
    assert ew["answerDecimal"] == 65.0
    assert "answerString" not in ew


def test_enable_behavior_set_for_multiple_rules(tmp_path):
    conn = _db(tmp_path)
    q1 = upsert_question(conn, QuestionDef(link_id="q.a", text="A?", type="single_choice"))
    conn.execute("INSERT INTO response_option (question_id,option_text,option_value,display_order) VALUES (?,?,?,?)",
                 (q1, "Yes", "yes", 0))
    q2 = upsert_question(conn, QuestionDef(link_id="q.b", text="B?", type="single_choice"))
    conn.execute("INSERT INTO response_option (question_id,option_text,option_value,display_order) VALUES (?,?,?,?)",
                 (q2, "Yes", "yes", 0))
    q3 = upsert_question(conn, QuestionDef(link_id="q.c", text="C?", type="text"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Multi"))
    qq1 = place_question(conn, qid, q1, display_order=0)
    qq2 = place_question(conn, qid, q2, display_order=1)
    qq3 = place_question(conn, qid, q3, display_order=2)
    conn.execute(
        "INSERT INTO skip_rule (qq_id, trigger_qq_id, operator, trigger_value, enable_behavior) VALUES (?,?,?,?,?)",
        (qq3, qq1, "=", "yes", "any"),
    )
    conn.execute(
        "INSERT INTO skip_rule (qq_id, trigger_qq_id, operator, trigger_value, enable_behavior) VALUES (?,?,?,?,?)",
        (qq3, qq2, "=", "yes", "any"),
    )
    conn.commit()
    item = export_fhir(conn, qid)["item"][2]
    assert len(item["enableWhen"]) == 2
    assert item["enableBehavior"] == "any"


def test_single_rule_no_enable_behavior(tmp_path):
    conn = _db(tmp_path)
    q1 = upsert_question(conn, QuestionDef(link_id="q.a", text="A?", type="text"))
    q2 = upsert_question(conn, QuestionDef(link_id="q.b", text="B?", type="text"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Single"))
    qq1 = place_question(conn, qid, q1, display_order=0)
    qq2 = place_question(conn, qid, q2, display_order=1)
    conn.execute(
        "INSERT INTO skip_rule (qq_id, trigger_qq_id, operator) VALUES (?,?,?)",
        (qq2, qq1, "exists"),
    )
    conn.commit()
    item = export_fhir(conn, qid)["item"][1]
    assert "enableBehavior" not in item


# ------------------------------------------------------------------
# Extensions for non-FHIR fields
# ------------------------------------------------------------------

def test_help_text_extension(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(
        link_id="q.help", text="Q?", type="boolean", help_text="This helps respondents.",
    ))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Help"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    exts = export_fhir(conn, qid)["item"][0]["extension"]
    urls = [e["url"] for e in exts]
    assert "https://quickq.io/fhir/StructureDefinition/help-text" in urls


def test_internal_note_extension(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.note", text="Q?", type="boolean"))
    conn.execute("UPDATE question SET internal_note='Analyst note here' WHERE question_id=?", (q_id,))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Note"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    exts = export_fhir(conn, qid)["item"][0]["extension"]
    note_ext = next(e for e in exts if "internal-note" in e["url"])
    assert note_ext["valueString"] == "Analyst note here"


def test_no_extension_when_no_extra_fields(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.plain", text="Plain?", type="boolean"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Plain"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    item = export_fhir(conn, qid)["item"][0]
    assert "extension" not in item


# ------------------------------------------------------------------
# Numeric constraints
# ------------------------------------------------------------------

def test_numeric_min_max_extensions(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(
        link_id="q.bmi", text="BMI?", type="numeric",
        numeric_min=10.0, numeric_max=80.0,
    ))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="Num"))
    place_question(conn, qid, q_id, display_order=0)
    conn.commit()
    exts = export_fhir(conn, qid)["item"][0]["extension"]
    ext_urls = {e["url"]: e for e in exts}
    assert "http://hl7.org/fhir/StructureDefinition/minValue" in ext_urls
    assert "http://hl7.org/fhir/StructureDefinition/maxValue" in ext_urls
    assert ext_urls["http://hl7.org/fhir/StructureDefinition/minValue"]["valueDecimal"] == 10.0
    assert ext_urls["http://hl7.org/fhir/StructureDefinition/maxValue"]["valueDecimal"] == 80.0


# ------------------------------------------------------------------
# Scoring rules as top-level extensions
# ------------------------------------------------------------------

def test_scoring_rule_extension(tmp_path):
    conn = _db(tmp_path)
    q_id = upsert_question(conn, QuestionDef(link_id="q.phq1", text="Little interest?", type="single_choice"))
    qid = insert_questionnaire(conn, QuestionnaireDef(name="PHQ-2"))
    place_question(conn, qid, q_id, display_order=0)
    conn.execute(
        "INSERT INTO scoring_rule (questionnaire_id, name, formula, description)"
        " VALUES (?,?,?,?)",
        (qid, "PHQ-2 Total", "sum", "Sum of items 1-2"),
    )
    conn.commit()
    r = export_fhir(conn, qid)
    exts = r.get("extension", [])
    scoring = [e for e in exts if "scoring-rule" in e["url"]]
    assert len(scoring) == 1
    inner = {e["url"].split("/")[-1]: e for e in scoring[0]["extension"]}
    assert inner["name"]["valueString"] == "PHQ-2 Total"
    assert inner["formula"]["valueString"] == "sum"


def test_no_scoring_extension_when_no_rules(tmp_path):
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    r = export_fhir(conn, qid)
    assert "extension" not in r


# ------------------------------------------------------------------
# JSON serialization
# ------------------------------------------------------------------

def test_export_fhir_json_is_valid_json(tmp_path):
    import json
    conn = _db(tmp_path)
    qid, _ = _smoke_questionnaire(conn)
    text = export_fhir_json(conn, qid)
    parsed = json.loads(text)
    assert parsed["resourceType"] == "Questionnaire"
