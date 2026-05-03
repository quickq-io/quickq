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


# ------------------------------------------------------------------
# count_from: linkage between a repeating_group and a count question
# (closes 7m6)
# ------------------------------------------------------------------

def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "q.yaml"
    p.write_text(body)
    return p


def test_count_from_populates_count_qq_id(tmp_path):
    """When YAML declares count_from, the loader writes count_qq_id on the
    parent group qq_id pointing at the named numeric question's qq_id."""
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Family History
  canonical_url: http://example.com/fh
  version: "1.0"
  questions:
    - { link_id: family.n_siblings, text: "How many siblings?", type: numeric, range: [0, 20] }
    - link_id: family.siblings
      text: "About each sibling:"
      type: repeating_group
      count_from: family.n_siblings
      items:
        - { link_id: sibling.age, text: "Age (years)", type: numeric, range: [0, 120] }
""")
    load_yaml(conn, str(yaml_path))

    # Look up qq_ids by link_id and verify the linkage.
    n_qq_id = conn.execute(
        "SELECT qq.qq_id FROM questionnaire_question qq JOIN question q USING (question_id) "
        "WHERE q.link_id = 'family.n_siblings'"
    ).fetchone()[0]
    siblings_qq_id, count_qq_id = conn.execute(
        "SELECT qq.qq_id, qq.count_qq_id FROM questionnaire_question qq JOIN question q USING (question_id) "
        "WHERE q.link_id = 'family.siblings'"
    ).fetchone()
    assert count_qq_id == n_qq_id, (
        f"expected count_qq_id={n_qq_id} (family.n_siblings), got {count_qq_id}"
    )


def test_count_from_unknown_link_id_errors(tmp_path):
    """Referencing an undefined link_id in count_from raises a clear error."""
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Bad
  canonical_url: http://example.com/bad
  version: "1.0"
  questions:
    - link_id: g
      text: G
      type: repeating_group
      count_from: does_not_exist
      items:
        - { link_id: g.x, text: "X", type: text }
""")
    with pytest.raises(ValueError, match="count_from"):
        load_yaml(conn, str(yaml_path))


def test_repeating_group_without_count_from_keeps_null(tmp_path):
    """Free-add pattern: no count_from set; count_qq_id stays NULL."""
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Medications
  canonical_url: http://example.com/meds
  version: "1.0"
  questions:
    - link_id: meds
      text: "Add medications you currently take:"
      type: repeating_group
      items:
        - { link_id: meds.name, text: "Name", type: text }
        - { link_id: meds.dose, text: "Dose (mg)", type: numeric, range: [0, 5000] }
""")
    load_yaml(conn, str(yaml_path))

    count_qq_id = conn.execute(
        "SELECT qq.count_qq_id FROM questionnaire_question qq JOIN question q USING (question_id) "
        "WHERE q.link_id = 'meds'"
    ).fetchone()[0]
    assert count_qq_id is None


def test_count_from_must_appear_before_group(tmp_path):
    """If count_from references a link_id defined LATER in the YAML, the
    loader cannot resolve it (single forward pass) and raises."""
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Order
  canonical_url: http://example.com/order
  version: "1.0"
  questions:
    - link_id: g
      text: G
      type: repeating_group
      count_from: n_things        # not yet defined at this point in the file
      items:
        - { link_id: g.x, text: "X", type: text }
    - { link_id: n_things, text: "How many?", type: numeric, range: [0, 10] }
""")
    with pytest.raises(ValueError, match="count_from"):
        load_yaml(conn, str(yaml_path))
