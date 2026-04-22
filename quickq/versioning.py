"""
Versioning and equivalence helpers.

Questions and questionnaire versions are immutable once created.  This
module provides the tools to:

  - Record question lineage when a question is intentionally revised
  - Declare equivalences between questions across waves or instruments
  - Diff two questionnaire versions and record what changed
  - Compute equivalence groups (connected components) for the OLAP layer
"""
from __future__ import annotations

import sqlite3


# ------------------------------------------------------------------
# Question lineage
# ------------------------------------------------------------------

LINEAGE_CHANGE_TYPES = frozenset({
    "reword", "option_added", "option_removed",
    "option_reworded", "split", "merge", "other",
})


def record_question_lineage(
    conn: sqlite3.Connection,
    question_id: int,
    parent_question_id: int,
    change_type: str,
    change_description: str | None = None,
    effective_date: str | None = None,
) -> int:
    """
    Record that question_id is a revised version of parent_question_id.

    change_type must be one of: reword, option_added, option_removed,
    option_reworded, split, merge, other.
    """
    if change_type not in LINEAGE_CHANGE_TYPES:
        raise ValueError(
            f"Invalid change_type '{change_type}'. "
            f"Must be one of: {sorted(LINEAGE_CHANGE_TYPES)}"
        )
    conn.execute(
        """
        INSERT INTO question_lineage
            (question_id, parent_question_id, change_type, change_description, effective_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (question_id, parent_question_id, change_type, change_description, effective_date),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_lineage_ancestors(
    conn: sqlite3.Connection, question_id: int
) -> list[dict]:
    """
    Return all ancestor questions in lineage order (immediate parent first).
    Uses a recursive traversal of question_lineage.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE ancestors(question_id, parent_question_id, change_type,
                                  change_description, effective_date, depth) AS (
            SELECT question_id, parent_question_id, change_type,
                   change_description, effective_date, 1
            FROM question_lineage
            WHERE question_id = ?
            UNION ALL
            SELECT l.question_id, l.parent_question_id, l.change_type,
                   l.change_description, l.effective_date, a.depth + 1
            FROM question_lineage l
            JOIN ancestors a ON l.question_id = a.parent_question_id
        )
        SELECT a.parent_question_id AS question_id,
               q.link_id, q.question_text, a.change_type,
               a.change_description, a.effective_date, a.depth
        FROM ancestors a
        JOIN question q ON a.parent_question_id = q.question_id
        ORDER BY a.depth
        """,
        (question_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Question equivalence
# ------------------------------------------------------------------

EQUIVALENCE_RELATIONSHIPS = frozenset({
    "equivalent", "near_equivalent", "related", "supersedes",
})

EQUIVALENCE_CONFIDENCE = frozenset({"high", "medium", "low"})


def declare_equivalence(
    conn: sqlite3.Connection,
    question_id_1: int,
    question_id_2: int,
    relationship: str = "near_equivalent",
    confidence: str = "medium",
    harmonization_notes: str | None = None,
    declared_by: str | None = None,
) -> tuple[int, int]:
    """
    Declare that two questions are equivalent (or related) for analysis purposes.

    Both directions are stored so queries only need WHERE question_id_1 = X.
    Returns (equivalence_id_forward, equivalence_id_reverse).

    relationship:  equivalent | near_equivalent | related | supersedes
    confidence:    high | medium | low
    """
    if relationship not in EQUIVALENCE_RELATIONSHIPS:
        raise ValueError(
            f"Invalid relationship '{relationship}'. "
            f"Must be one of: {sorted(EQUIVALENCE_RELATIONSHIPS)}"
        )
    if confidence not in EQUIVALENCE_CONFIDENCE:
        raise ValueError(
            f"Invalid confidence '{confidence}'. Must be one of: high, medium, low"
        )

    def _insert(q1: int, q2: int) -> int:
        conn.execute(
            """
            INSERT INTO question_equivalence
                (question_id_1, question_id_2, relationship, confidence,
                 harmonization_notes, declared_by)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (question_id_1, question_id_2, relationship) DO NOTHING
            """,
            (q1, q2, relationship, confidence, harmonization_notes, declared_by),
        )
        return conn.execute(
            """SELECT equivalence_id FROM question_equivalence
               WHERE question_id_1=? AND question_id_2=? AND relationship=?""",
            (q1, q2, relationship),
        ).fetchone()[0]

    fwd = _insert(question_id_1, question_id_2)
    rev = _insert(question_id_2, question_id_1)
    return fwd, rev


def get_equivalence_group(
    conn: sqlite3.Connection, question_id: int
) -> list[dict]:
    """
    Return all questions declared equivalent or near_equivalent to question_id,
    along with relationship metadata.
    """
    rows = conn.execute(
        """
        SELECT e.question_id_2 AS question_id,
               q.link_id, q.question_text, q.source_instrument,
               e.relationship, e.confidence, e.harmonization_notes
        FROM question_equivalence e
        JOIN question q ON e.question_id_2 = q.question_id
        WHERE e.question_id_1 = ?
          AND e.relationship IN ('equivalent', 'near_equivalent')
        ORDER BY e.confidence DESC, q.link_id
        """,
        (question_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def compute_equivalence_groups(conn: sqlite3.Connection) -> dict[int, int]:
    """
    Compute connected components of the equivalence graph using union-find.
    Only 'equivalent' and 'near_equivalent' edges are included.

    Returns {question_id: group_id} where group_id is the smallest question_id
    in the component (stable across refreshes as long as question_ids don't change).
    All questions are included; isolated questions map to their own id.
    """
    pairs = conn.execute(
        """
        SELECT DISTINCT question_id_1, question_id_2
        FROM question_equivalence
        WHERE relationship IN ('equivalent', 'near_equivalent')
        """,
    ).fetchall()

    all_ids = [r[0] for r in conn.execute("SELECT question_id FROM question").fetchall()]

    parent: dict[int, int] = {qid: qid for qid in all_ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            # Use min id as root for stability
            if px < py:
                parent[py] = px
            else:
                parent[px] = py

    for q1, q2 in pairs:
        if q1 in parent and q2 in parent:
            union(q1, q2)

    return {qid: find(qid) for qid in all_ids}


# ------------------------------------------------------------------
# Questionnaire version diffing
# ------------------------------------------------------------------

DIFF_CHANGE_TYPES = frozenset({
    "item_added", "item_removed", "item_reworded",
    "item_reordered", "skip_rule_changed", "scoring_changed", "option_changed",
})


def record_questionnaire_diff(
    conn: sqlite3.Connection,
    from_questionnaire_id: int,
    to_questionnaire_id: int,
    change_type: str,
    qq_id_from: int | None = None,
    qq_id_to: int | None = None,
    notes: str | None = None,
) -> int:
    if change_type not in DIFF_CHANGE_TYPES:
        raise ValueError(
            f"Invalid change_type '{change_type}'. "
            f"Must be one of: {sorted(DIFF_CHANGE_TYPES)}"
        )
    conn.execute(
        """
        INSERT INTO questionnaire_version_diff
            (from_questionnaire_id, to_questionnaire_id, change_type,
             qq_id_from, qq_id_to, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (from_questionnaire_id, to_questionnaire_id, change_type,
         qq_id_from, qq_id_to, notes),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def diff_questionnaire_versions(
    conn: sqlite3.Connection,
    from_questionnaire_id: int,
    to_questionnaire_id: int,
    auto_record: bool = False,
) -> list[dict]:
    """
    Compare two questionnaire versions and return a list of detected changes.

    Detects: item_added, item_removed, item_reworded, item_reordered.
    If auto_record=True, writes the diffs to questionnaire_version_diff.

    Returns a list of dicts, one per detected change.
    """
    def _placements(qid: int) -> list[dict]:
        rows = conn.execute(
            """
            SELECT qq.qq_id, qq.display_order, q.question_id,
                   q.link_id, q.question_text
            FROM questionnaire_question qq
            JOIN question q ON qq.question_id = q.question_id
            WHERE qq.questionnaire_id = ?
            ORDER BY qq.display_order
            """,
            (qid,),
        ).fetchall()
        return [dict(r) for r in rows]

    from_items = {p["link_id"]: p for p in _placements(from_questionnaire_id)}
    to_items   = {p["link_id"]: p for p in _placements(to_questionnaire_id)}

    diffs: list[dict] = []

    for link_id, from_p in from_items.items():
        if link_id not in to_items:
            diffs.append({
                "change_type": "item_removed",
                "link_id": link_id,
                "qq_id_from": from_p["qq_id"],
                "qq_id_to": None,
                "notes": f"Item '{link_id}' present in v_from, absent in v_to",
            })
        else:
            to_p = to_items[link_id]
            if from_p["question_text"] != to_p["question_text"]:
                diffs.append({
                    "change_type": "item_reworded",
                    "link_id": link_id,
                    "qq_id_from": from_p["qq_id"],
                    "qq_id_to": to_p["qq_id"],
                    "notes": (
                        f"Text changed:\n"
                        f"  from: {from_p['question_text']!r}\n"
                        f"  to  : {to_p['question_text']!r}"
                    ),
                })
            elif from_p["display_order"] != to_p["display_order"]:
                diffs.append({
                    "change_type": "item_reordered",
                    "link_id": link_id,
                    "qq_id_from": from_p["qq_id"],
                    "qq_id_to": to_p["qq_id"],
                    "notes": (
                        f"Order changed: {from_p['display_order']} → {to_p['display_order']}"
                    ),
                })

    for link_id, to_p in to_items.items():
        if link_id not in from_items:
            diffs.append({
                "change_type": "item_added",
                "link_id": link_id,
                "qq_id_from": None,
                "qq_id_to": to_p["qq_id"],
                "notes": f"Item '{link_id}' absent in v_from, added in v_to",
            })

    if auto_record:
        with conn:
            for d in diffs:
                record_questionnaire_diff(
                    conn,
                    from_questionnaire_id=from_questionnaire_id,
                    to_questionnaire_id=to_questionnaire_id,
                    change_type=d["change_type"],
                    qq_id_from=d.get("qq_id_from"),
                    qq_id_to=d.get("qq_id_to"),
                    notes=d.get("notes"),
                )

    return diffs
