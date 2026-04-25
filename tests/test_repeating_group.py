"""
Tests for repeating_group question type.

Covers: YAML loading, FHIR export, FHIR import round-trip, response
import with repeat_index, and OLAP ETL propagation.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.loader import load_yaml
from quickq.renderer_fhir import export_fhir, export_fhir_json
from quickq.parser_fhir import import_fhir
from quickq.parser_fhir_response import import_fhir_response
from quickq.olap_schema import refresh, init_olap

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------
# Shared fixture
# ------------------------------------------------------------------

@pytest.fixture()
def prenatal_db(tmp_path):
    conn = init_oltp(str(tmp_path / "prenatal.db"))
    load_yaml(conn, str(FIXTURES / "prenatal_visits.yaml"))
    return conn, str(tmp_path / "prenatal.db")


# ------------------------------------------------------------------
# YAML loading
# ------------------------------------------------------------------

def test_loads_without_error(prenatal_db):
    conn, _ = prenatal_db
    row = conn.execute("SELECT * FROM questionnaire").fetchone()
    assert row["name"] == "Prenatal Visit Log"


def test_repeating_group_question_exists(prenatal_db):
    conn, _ = prenatal_db
    row = conn.execute(
        "SELECT question_type FROM question WHERE link_id = 'visits'"
    ).fetchone()
    assert row is not None
    assert row["question_type"] == "repeating_group"


def test_child_questions_have_parent_qq_id(prenatal_db):
    conn, _ = prenatal_db
    parent_qq = conn.execute(
        """SELECT qq.qq_id FROM questionnaire_question qq
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'visits'"""
    ).fetchone()
    assert parent_qq is not None

    children = conn.execute(
        "SELECT count(*) FROM questionnaire_question WHERE parent_qq_id = ?",
        (parent_qq["qq_id"],),
    ).fetchone()[0]
    assert children == 3   # visits.week, visits.provider, visits.concern


def test_top_level_question_count(prenatal_db):
    conn, _ = prenatal_db
    top_level = conn.execute(
        """SELECT count(*) FROM questionnaire_question
           WHERE questionnaire_id = 1 AND parent_qq_id IS NULL"""
    ).fetchone()[0]
    assert top_level == 2   # visit_count + visits (group)


# ------------------------------------------------------------------
# FHIR export
# ------------------------------------------------------------------

@pytest.fixture()
def prenatal_fhir(prenatal_db):
    conn, _ = prenatal_db
    return export_fhir(conn, 1)


def test_fhir_export_group_type(prenatal_fhir):
    group = next(i for i in prenatal_fhir["item"] if i["linkId"] == "visits")
    assert group["type"] == "group"
    assert group["repeats"] is True


def test_fhir_export_has_nested_items(prenatal_fhir):
    group = next(i for i in prenatal_fhir["item"] if i["linkId"] == "visits")
    assert "item" in group
    child_ids = [c["linkId"] for c in group["item"]]
    assert "visits.week" in child_ids
    assert "visits.provider" in child_ids
    assert "visits.concern" in child_ids


def test_fhir_export_top_level_item_count(prenatal_fhir):
    # Only visit_count and visits at top level — children are nested
    assert len(prenatal_fhir["item"]) == 2


def test_fhir_export_child_types(prenatal_fhir):
    group = next(i for i in prenatal_fhir["item"] if i["linkId"] == "visits")
    types = {c["linkId"]: c["type"] for c in group["item"]}
    assert types["visits.week"] == "decimal"
    assert types["visits.provider"] == "choice"
    assert types["visits.concern"] == "boolean"


# ------------------------------------------------------------------
# FHIR import round-trip
# ------------------------------------------------------------------

def test_fhir_roundtrip(prenatal_db, tmp_path):
    conn, _ = prenatal_db
    fhir_json = export_fhir_json(conn, 1)
    conn.close()

    conn2 = init_oltp(str(tmp_path / "import.db"))
    qid2 = import_fhir(conn2, fhir_json)

    group = conn2.execute(
        "SELECT question_type FROM question WHERE link_id = 'visits'"
    ).fetchone()
    assert group["question_type"] == "repeating_group"

    parent_qq = conn2.execute(
        """SELECT qq.qq_id FROM questionnaire_question qq
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'visits' AND qq.questionnaire_id = ?""",
        (qid2,),
    ).fetchone()
    children = conn2.execute(
        "SELECT count(*) FROM questionnaire_question WHERE parent_qq_id = ?",
        (parent_qq["qq_id"],),
    ).fetchone()[0]
    assert children == 3


# ------------------------------------------------------------------
# Response import with repeat_index
# ------------------------------------------------------------------

_RESPONSE = {
    "resourceType": "QuestionnaireResponse",
    "questionnaire": "http://quickq.io/instruments/prenatal-visits",
    "status": "completed",
    "subject": {"reference": "Patient/prenatal-001"},
    "item": [
        {
            "linkId": "visit_count",
            "answer": [{"valueDecimal": 2}],
        },
        # First visit
        {
            "linkId": "visits",
            "item": [
                {"linkId": "visits.week", "answer": [{"valueDecimal": 12}]},
                {"linkId": "visits.provider", "answer": [{"valueCoding": {"code": "ob"}}]},
                {"linkId": "visits.concern", "answer": [{"valueBoolean": False}]},
            ],
        },
        # Second visit
        {
            "linkId": "visits",
            "item": [
                {"linkId": "visits.week", "answer": [{"valueDecimal": 20}]},
                {"linkId": "visits.provider", "answer": [{"valueCoding": {"code": "midwife"}}]},
                {"linkId": "visits.concern", "answer": [{"valueBoolean": True}]},
            ],
        },
    ],
}


@pytest.fixture()
def prenatal_with_response(prenatal_db):
    conn, db_path = prenatal_db
    import_fhir_response(conn, _RESPONSE)
    conn.commit()
    return conn, db_path


def test_response_row_count(prenatal_with_response):
    conn, _ = prenatal_with_response
    total = conn.execute("SELECT count(*) FROM response").fetchone()[0]
    # 1 (visit_count) + 3 (visit 1) + 3 (visit 2) = 7
    assert total == 7


def test_repeat_index_values(prenatal_with_response):
    conn, _ = prenatal_with_response
    rows = conn.execute(
        """SELECT r.repeat_index, r.response_numeric
           FROM response r
           JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'visits.week'
           ORDER BY r.repeat_index"""
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["repeat_index"] == 0
    assert rows[0]["response_numeric"] == 12.0
    assert rows[1]["repeat_index"] == 1
    assert rows[1]["response_numeric"] == 20.0


def test_non_repeating_has_null_repeat_index(prenatal_with_response):
    conn, _ = prenatal_with_response
    row = conn.execute(
        """SELECT r.repeat_index FROM response r
           JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'visit_count'"""
    ).fetchone()
    assert row["repeat_index"] is None


def test_concern_repeat_indices(prenatal_with_response):
    conn, _ = prenatal_with_response
    rows = conn.execute(
        """SELECT r.repeat_index, r.response_text
           FROM response r
           JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'visits.concern'
           ORDER BY r.repeat_index"""
    ).fetchall()
    assert rows[0]["response_text"] == "false"
    assert rows[1]["response_text"] == "true"


# ------------------------------------------------------------------
# OLAP ETL
# ------------------------------------------------------------------

def test_olap_repeat_index_propagated(prenatal_with_response, tmp_path):
    _, db_path = prenatal_with_response
    olap_path = str(tmp_path / "analytics.duckdb")
    refresh(olap_path, db_path)
    oconn = init_olap(olap_path, db_path)

    rows = oconn.execute(
        """SELECT repeat_index, response_numeric
           FROM fact_response fr
           JOIN dim_question dq USING (question_id)
           WHERE dq.link_id = 'visits.week'
           ORDER BY repeat_index"""
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == 0 and rows[0][1] == 12.0
    assert rows[1][0] == 1 and rows[1][1] == 20.0
