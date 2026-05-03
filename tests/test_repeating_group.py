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


# ------------------------------------------------------------------
# count_from FHIR roundtrip (closes o1b)
# ------------------------------------------------------------------

def test_count_from_emits_fhir_extension(tmp_path):
    """FHIR export of a count-driven group emits the quickq count-from
    extension and the SDC questionnaire-maxOccurs cap when the count
    question has numeric_max."""
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
        - { link_id: sibling.age, text: "Age", type: numeric, range: [0, 120] }
""")
    qid = load_yaml(conn, str(yaml_path))

    fhir = export_fhir(conn, qid)
    # Find the family.siblings item.
    group_item = next(it for it in fhir["item"] if it["linkId"] == "family.siblings")
    exts = group_item.get("extension", [])
    urls = {e["url"]: e for e in exts}

    count_from_url = "https://quickq.io/fhir/StructureDefinition/count-from"
    max_occurs_url = "http://hl7.org/fhir/StructureDefinition/questionnaire-maxOccurs"

    assert count_from_url in urls, f"missing count-from extension; got: {list(urls)}"
    assert urls[count_from_url]["valueString"] == "family.n_siblings"
    assert max_occurs_url in urls, f"missing maxOccurs extension; got: {list(urls)}"
    assert urls[max_occurs_url]["valueInteger"] == 20


def test_count_from_omitted_when_count_qq_id_null(tmp_path):
    """Free-add groups (no count_from) should not emit either extension."""
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Medications
  canonical_url: http://example.com/meds
  version: "1.0"
  questions:
    - link_id: meds
      text: "Add medications:"
      type: repeating_group
      items:
        - { link_id: meds.name, text: "Name", type: text }
""")
    qid = load_yaml(conn, str(yaml_path))

    fhir = export_fhir(conn, qid)
    group_item = next(it for it in fhir["item"] if it["linkId"] == "meds")
    exts = group_item.get("extension", [])
    urls = {e["url"] for e in exts}

    count_from_url = "https://quickq.io/fhir/StructureDefinition/count-from"
    max_occurs_url = "http://hl7.org/fhir/StructureDefinition/questionnaire-maxOccurs"
    assert count_from_url not in urls
    assert max_occurs_url not in urls


def test_count_from_fhir_roundtrip_preserves_linkage(tmp_path):
    """Export to FHIR, re-import into a fresh DB, count_qq_id is restored."""
    src_db = tmp_path / "src.db"
    src = init_oltp(src_db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Family History
  canonical_url: http://example.com/fh-rt
  version: "1.0"
  questions:
    - { link_id: family.n_siblings, text: "How many siblings?", type: numeric, range: [0, 20] }
    - link_id: family.siblings
      text: "About each sibling:"
      type: repeating_group
      count_from: family.n_siblings
      items:
        - { link_id: sibling.age, text: "Age", type: numeric, range: [0, 120] }
""")
    qid_src = load_yaml(src, str(yaml_path))

    # Export
    fhir_json = export_fhir_json(src, qid_src)

    # Import into a fresh DB
    dst_db = tmp_path / "dst.db"
    dst = init_oltp(dst_db)
    qid_dst = import_fhir(dst, fhir_json)

    # In the imported DB, the family.siblings group should have count_qq_id
    # pointing at the family.n_siblings qq_id.
    n_qq_id = dst.execute(
        "SELECT qq.qq_id FROM questionnaire_question qq "
        "JOIN question q USING (question_id) "
        "WHERE qq.questionnaire_id = ? AND q.link_id = 'family.n_siblings'",
        (qid_dst,),
    ).fetchone()[0]
    siblings_qq_id, count_qq_id = dst.execute(
        "SELECT qq.qq_id, qq.count_qq_id FROM questionnaire_question qq "
        "JOIN question q USING (question_id) "
        "WHERE qq.questionnaire_id = ? AND q.link_id = 'family.siblings'",
        (qid_dst,),
    ).fetchone()
    assert count_qq_id == n_qq_id, (
        f"round-trip lost count linkage; expected {n_qq_id}, got {count_qq_id}"
    )


# ------------------------------------------------------------------
# seed support for repeating_group (closes fyj)
# ------------------------------------------------------------------

def test_seed_count_driven_repeating_group(tmp_path):
    """When count_from is set, seed uses the seeded count question's answer
    to drive the number of repeat instances written."""
    from quickq.seed import seed_responses

    db = tmp_path / "study.db"
    conn = init_oltp(db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Family History
  canonical_url: http://example.com/seed-fh
  version: "1.0"
  questions:
    - { link_id: family.n_siblings, text: "How many siblings?", type: numeric, range: [2, 4] }
    - link_id: family.siblings
      text: "About each sibling:"
      type: repeating_group
      count_from: family.n_siblings
      items:
        - { link_id: sibling.age, text: "Age", type: numeric, range: [0, 120] }
""")
    qid = load_yaml(conn, str(yaml_path))

    # Seed a deterministic batch
    sids = seed_responses(conn, qid, n=5, rng_seed=42)
    assert len(sids) == 5

    # For each session, the number of sibling.age responses should equal the
    # numeric answer to family.n_siblings (range [2, 4] so always >= 2).
    for sid in sids:
        n_siblings = conn.execute(
            "SELECT response_numeric FROM response r "
            "JOIN questionnaire_question qq USING (qq_id) "
            "JOIN question q USING (question_id) "
            "WHERE r.session_id = ? AND q.link_id = 'family.n_siblings'",
            (sid,),
        ).fetchone()[0]
        n_age_rows = conn.execute(
            "SELECT COUNT(*) FROM response r "
            "JOIN questionnaire_question qq USING (qq_id) "
            "JOIN question q USING (question_id) "
            "WHERE r.session_id = ? AND q.link_id = 'sibling.age'",
            (sid,),
        ).fetchone()[0]
        assert n_age_rows == int(round(n_siblings)), (
            f"expected {int(round(n_siblings))} sibling.age rows; got {n_age_rows}"
        )

        # repeat_index values should be 0..N-1 distinct integers
        indices = [r[0] for r in conn.execute(
            "SELECT r.repeat_index FROM response r "
            "JOIN questionnaire_question qq USING (qq_id) "
            "JOIN question q USING (question_id) "
            "WHERE r.session_id = ? AND q.link_id = 'sibling.age' "
            "ORDER BY r.repeat_index",
            (sid,),
        ).fetchall()]
        assert indices == list(range(n_age_rows))


def test_seed_free_add_repeating_group(tmp_path):
    """Free-add (no count_from): seed picks a small random N; rows still get
    sequential repeat_index values and are confined to a sane range."""
    from quickq.seed import seed_responses

    db = tmp_path / "study.db"
    conn = init_oltp(db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Medications
  canonical_url: http://example.com/seed-meds
  version: "1.0"
  questions:
    - link_id: meds
      text: "Add medications:"
      type: repeating_group
      items:
        - { link_id: meds.name, text: "Name", type: text }
""")
    qid = load_yaml(conn, str(yaml_path))

    sids = seed_responses(conn, qid, n=8, rng_seed=7)
    assert len(sids) == 8

    # Confirm at least one session has >0 instances and indices are 0-based
    # contiguous within each session.
    saw_nonempty = False
    for sid in sids:
        rows = conn.execute(
            "SELECT r.repeat_index FROM response r "
            "JOIN questionnaire_question qq USING (qq_id) "
            "JOIN question q USING (question_id) "
            "WHERE r.session_id = ? AND q.link_id = 'meds.name' "
            "ORDER BY r.repeat_index",
            (sid,),
        ).fetchall()
        indices = [r[0] for r in rows]
        if indices:
            saw_nonempty = True
            assert indices == list(range(len(indices)))
            assert all(i is not None for i in indices)
            assert max(indices) <= 5  # seed clips free-add to [0, 5]
    assert saw_nonempty, "expected at least one session with >0 free-add instances"


def test_seed_repeating_propagates_repeat_index_to_olap(tmp_path):
    """Seeded repeating_group rows show up in fact_response with the same
    repeat_index after refresh."""
    from quickq.seed import seed_responses

    db = tmp_path / "study.db"
    conn = init_oltp(db)
    yaml_path = _write_yaml(tmp_path, """
questionnaire:
  name: Pregnancies
  canonical_url: http://example.com/seed-pg
  version: "1.0"
  questions:
    - { link_id: n_preg, text: "How many pregnancies?", type: numeric, range: [1, 3] }
    - link_id: preg
      text: "About each pregnancy:"
      type: repeating_group
      count_from: n_preg
      items:
        - { link_id: preg.year, text: "Year", type: numeric, range: [1980, 2024] }
""")
    qid = load_yaml(conn, str(yaml_path))
    seed_responses(conn, qid, n=4, rng_seed=1)
    conn.commit()

    olap = str(tmp_path / "analytics.duckdb")
    refresh(olap, str(db))
    oconn = init_olap(olap, str(db))

    rows = oconn.execute("""
        SELECT fr.session_id, fr.repeat_index
        FROM fact_response fr
        JOIN dim_question dq USING (question_id)
        WHERE dq.link_id = 'preg.year'
        ORDER BY fr.session_id, fr.repeat_index
    """).fetchall()
    assert rows, "no preg.year rows landed in fact_response"
    # Each row must have a non-NULL repeat_index, and indices within a
    # session must be a contiguous 0..k sequence.
    from itertools import groupby
    for sid, group in groupby(rows, key=lambda r: r[0]):
        indices = [r[1] for r in group]
        assert all(i is not None for i in indices)
        assert indices == list(range(len(indices)))
