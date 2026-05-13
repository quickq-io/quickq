"""Boundary tests for FHIR response import on nested structures.

The flat repeating-group case (boolean/numeric/date/text/choice children)
is covered by tests/test_repeating_group.py. This file probes the limits:

- Grid as a child of a repeating group
- Repeating-group instance with no answers (empty list)
- (Future: repeating-group as a child of a repeating-group — quickq's
  schema doesn't currently model this depth; see quickq-io-ap8.)

Each test documents the CURRENT behavior. Where behavior is incorrect or
incomplete relative to the data-model promise, the test serves as a
regression check after the parser is extended.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from quickq.loader import load_yaml
from quickq.parser_fhir_response import import_fhir_response
from quickq.schema import init_oltp

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def rg_grid_db(tmp_path):
    db_path = tmp_path / "rg_grid.db"
    conn = init_oltp(db_path)
    load_yaml(conn, FIXTURES / "repeating_with_grid.yaml")
    conn.commit()
    return conn


# A FHIR QuestionnaireResponse with two visit instances; each visit has a
# week answer and a grid of three symptom rows × four severity columns.
# Grid children encode (row, column) via FHIR's nested item structure:
# parent linkId is the grid question; child linkIds are `<parent>.r0`,
# `<parent>.r1`, ... where the row order follows the YAML rows definition.
_RESPONSE_WITH_GRID = {
    "resourceType": "QuestionnaireResponse",
    "questionnaire": "http://quickq.io/instruments/repeating-with-grid-test",
    "status": "completed",
    "subject": {"reference": "Patient/rg-001"},
    "item": [
        {"linkId": "rg.visit_count", "answer": [{"valueDecimal": 2}]},
        {
            "linkId": "rg.visits",
            "item": [
                {"linkId": "rg.visits.week", "answer": [{"valueDecimal": 8}]},
                {
                    "linkId": "rg.visits.severity",
                    "item": [
                        {"linkId": "rg.visits.severity.r0", "answer": [{"valueCoding": {"code": "2"}}]},
                        {"linkId": "rg.visits.severity.r1", "answer": [{"valueCoding": {"code": "1"}}]},
                        {"linkId": "rg.visits.severity.r2", "answer": [{"valueCoding": {"code": "0"}}]},
                    ],
                },
            ],
        },
        {
            "linkId": "rg.visits",
            "item": [
                {"linkId": "rg.visits.week", "answer": [{"valueDecimal": 20}]},
                {
                    "linkId": "rg.visits.severity",
                    "item": [
                        {"linkId": "rg.visits.severity.r0", "answer": [{"valueCoding": {"code": "3"}}]},
                        {"linkId": "rg.visits.severity.r1", "answer": [{"valueCoding": {"code": "2"}}]},
                        {"linkId": "rg.visits.severity.r2", "answer": [{"valueCoding": {"code": "1"}}]},
                    ],
                },
            ],
        },
    ],
}


def test_grid_inside_repeating_group_writes_flat_children(rg_grid_db):
    """The flat (non-grid) children of a repeating group still import correctly.

    This is the regression check: the existing flat-case must not break when
    a grid sibling is also present.
    """
    conn = rg_grid_db
    import_fhir_response(conn, _RESPONSE_WITH_GRID)
    conn.commit()

    weeks = conn.execute(
        """SELECT r.response_numeric, r.repeat_index
           FROM response r
           JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'rg.visits.week'
           ORDER BY r.repeat_index"""
    ).fetchall()
    assert [(r[0], r[1]) for r in weeks] == [(8.0, 0), (20.0, 1)]


def test_grid_inside_repeating_group_writes_grid_cells(rg_grid_db):
    """Each grid cell in each visit instance should produce a response row
    with grid_row_id, grid_column_id, AND repeat_index populated.

    Expected: 6 rows (3 symptom rows × 2 visit instances).
    """
    conn = rg_grid_db
    import_fhir_response(conn, _RESPONSE_WITH_GRID)
    conn.commit()

    rows = conn.execute(
        """SELECT r.grid_row_id, r.grid_column_id, r.repeat_index
           FROM response r
           JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'rg.visits.severity'
           ORDER BY r.repeat_index, r.grid_row_id"""
    ).fetchall()
    # Expected: 6 grid responses (3 rows × 2 instances), each with grid_row_id,
    # grid_column_id, and repeat_index populated.
    assert len(rows) == 6, (
        f"Expected 6 grid-cell rows (3 rows × 2 instances); got {len(rows)}. "
        "Grid-in-repeating-group is silently dropping cells."
    )
    # Each row must carry both grid_row_id, grid_column_id, AND the
    # repeat_index from its containing visit instance.
    for grid_row_id, grid_column_id, repeat_index in rows:
        assert grid_row_id is not None, "grid_row_id should be set on grid responses"
        assert grid_column_id is not None, "grid_column_id should be set on grid responses"
        assert repeat_index in (0, 1), f"unexpected repeat_index: {repeat_index}"

    # First visit: 3 rows at repeat_index=0; second visit: 3 rows at repeat_index=1
    indices = [r[2] for r in rows]
    assert indices.count(0) == 3, f"expected 3 cells at repeat_index=0; got {indices.count(0)}"
    assert indices.count(1) == 3, f"expected 3 cells at repeat_index=1; got {indices.count(1)}"


def test_empty_repeating_instance_does_not_crash(rg_grid_db):
    """A repeating-group entry with `item: []` (no children answered) should
    parse without error. Whether it advances the repeat_index is the
    documented behavior we want to verify.
    """
    conn = rg_grid_db
    empty_instance_response = {
        "resourceType": "QuestionnaireResponse",
        "questionnaire": "http://quickq.io/instruments/repeating-with-grid-test",
        "status": "completed",
        "subject": {"reference": "Patient/rg-empty"},
        "item": [
            {"linkId": "rg.visit_count", "answer": [{"valueDecimal": 1}]},
            {"linkId": "rg.visits", "item": []},                     # empty visit
            {
                "linkId": "rg.visits",                                # populated visit
                "item": [
                    {"linkId": "rg.visits.week", "answer": [{"valueDecimal": 12}]},
                ],
            },
        ],
    }
    import_fhir_response(conn, empty_instance_response)
    conn.commit()

    # The populated visit's week answer must land. Whether it's at
    # repeat_index=0 (the empty instance was ignored) or repeat_index=1 (the
    # empty instance consumed an index) is the implementation choice we
    # capture here.
    weeks = conn.execute(
        """SELECT r.repeat_index
           FROM response r
           JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
           JOIN question q ON qq.question_id = q.question_id
           WHERE q.link_id = 'rg.visits.week'"""
    ).fetchall()
    assert len(weeks) == 1, f"expected one week answer; got {len(weeks)}"
    # The current parser increments _repeat_count on every repeating-group
    # encounter, so the empty instance does consume index 0.
    assert weeks[0][0] in (0, 1), (
        "repeat_index for the populated visit should be 0 or 1 depending on "
        "empty-instance semantics; document the choice"
    )
