"""
Real-world repeating_group fixture: US Surgeon General Family Health Portrait
(USSG-FHT, LOINC 54127-6), extracted from the HL7 FHIR R4 example bundle.

Validates that nested repeating groups import and round-trip correctly.
The fixture exercises:
  - Single-level repeating group  (1.2 — disease history)
  - Two-level nested repeating group  (2.1 — family members containing 2.1.2 — member disease history)
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.parser_fhir import import_fhir
from quickq.renderer_fhir import export_fhir

FIXTURE = Path(__file__).parent / "fixtures" / "ussg_fht.json"


@pytest.fixture(scope="module")
def ussg_conn(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("ussg") / "ussg.db")
    conn = init_oltp(db)
    import_fhir(conn, FIXTURE.read_text())
    return conn


def test_import_succeeds(ussg_conn):
    row = ussg_conn.execute("SELECT name FROM questionnaire").fetchone()
    assert "Surgeon General" in row["name"] or "Family Health" in row["name"]


def test_single_level_repeating_group(ussg_conn):
    """1.2 — Your diseases history — repeating_group with 2 leaf children."""
    row = ussg_conn.execute(
        "SELECT question_type FROM question WHERE link_id = '1.2'"
    ).fetchone()
    assert row is not None
    assert row["question_type"] == "repeating_group"

    qq = ussg_conn.execute(
        """SELECT qq.qq_id FROM questionnaire_question qq
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = '1.2'"""
    ).fetchone()
    children = ussg_conn.execute(
        "SELECT count(*) FROM questionnaire_question WHERE parent_qq_id = ?",
        (qq["qq_id"],),
    ).fetchone()[0]
    assert children == 2   # 1.2.1 + 1.2.2


def test_top_level_repeating_group(ussg_conn):
    """2.1 — family member loop — repeating_group with 10 children (9 leaf + 1 nested group)."""
    row = ussg_conn.execute(
        "SELECT question_type FROM question WHERE link_id = '2.1'"
    ).fetchone()
    assert row is not None
    assert row["question_type"] == "repeating_group"

    qq = ussg_conn.execute(
        """SELECT qq.qq_id FROM questionnaire_question qq
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = '2.1'"""
    ).fetchone()
    children = ussg_conn.execute(
        "SELECT count(*) FROM questionnaire_question WHERE parent_qq_id = ?",
        (qq["qq_id"],),
    ).fetchone()[0]
    assert children == 10


def test_nested_repeating_group(ussg_conn):
    """2.1.2 — disease history per family member — is itself a repeating_group."""
    row = ussg_conn.execute(
        "SELECT question_type FROM question WHERE link_id = '2.1.2'"
    ).fetchone()
    assert row is not None
    assert row["question_type"] == "repeating_group"

    qq = ussg_conn.execute(
        """SELECT qq.qq_id FROM questionnaire_question qq
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = '2.1.2'"""
    ).fetchone()
    children = ussg_conn.execute(
        "SELECT count(*) FROM questionnaire_question WHERE parent_qq_id = ?",
        (qq["qq_id"],),
    ).fetchone()[0]
    assert children == 5   # 2.1.2.1 – 2.1.2.5


def test_fhir_roundtrip_preserves_repeating_groups(ussg_conn):
    """Export and re-examine FHIR JSON — repeating group linkIds must appear as group+repeats."""
    qid = ussg_conn.execute("SELECT questionnaire_id FROM questionnaire").fetchone()[0]
    exported = export_fhir(ussg_conn, qid)

    def find(items, target_link_id):
        for item in items:
            if item["linkId"] == target_link_id:
                return item
            found = find(item.get("item", []), target_link_id)
            if found:
                return found
        return None

    g12 = find(exported["item"], "1.2")
    assert g12 is not None
    assert g12["type"] == "group"
    assert g12.get("repeats") is True
    child_ids = [c["linkId"] for c in g12.get("item", [])]
    assert "1.2.1" in child_ids and "1.2.2" in child_ids

    g21 = find(exported["item"], "2.1")
    assert g21 is not None
    assert g21["type"] == "group"
    assert g21.get("repeats") is True

    g212 = find(g21.get("item", []), "2.1.2")
    assert g212 is not None
    assert g212["type"] == "group"
    assert g212.get("repeats") is True


def test_top_level_items_excludes_nested(ussg_conn):
    """Children of repeating groups must not appear as top-level questionnaire_question rows."""
    qid = ussg_conn.execute("SELECT questionnaire_id FROM questionnaire").fetchone()[0]
    top_level_count = ussg_conn.execute(
        "SELECT count(*) FROM questionnaire_question WHERE questionnaire_id = ? AND parent_qq_id IS NULL",
        (qid,),
    ).fetchone()[0]
    # 4 (group 0 leaves) + 11 (group 1.1 leaves) + 1 (1.2 repeating) + 1 (2.1 repeating) = 17
    assert top_level_count == 17
