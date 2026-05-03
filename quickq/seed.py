"""
Synthetic response generator.

Reads questionnaire structure from the OLTP and generates N plausible
responses, respecting question types, option sets, numeric ranges, and
skip logic. Useful for populating the analytics layer during development
and for demonstrating the full pipeline without collecting real data.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def seed_responses(
    conn: sqlite3.Connection,
    questionnaire_id: int,
    n: int,
    study_id: int | None = None,
    admin_mode: str = "api",
    rng_seed: int | None = None,
) -> list[int]:
    """
    Generate *n* synthetic responses for *questionnaire_id*.
    Returns the list of created session_ids.
    """
    rng = random.Random(rng_seed)
    questions = _load_questions(conn, questionnaire_id)

    session_ids: list[int] = []
    with conn:
        for i in range(n):
            respondent_id = _upsert_respondent(conn, study_id, f"synthetic-{i + 1:04d}")
            session_id = _insert_session(conn, questionnaire_id, respondent_id, admin_mode)
            _generate_session(conn, rng, session_id, questions)
            session_ids.append(session_id)

    return session_ids


# ---------------------------------------------------------------------------
# Questionnaire structure loader
# ---------------------------------------------------------------------------

def _load_questions(conn: sqlite3.Connection, questionnaire_id: int) -> list[dict]:
    """Load all top-level questions for the questionnaire with options and skip rules.

    For repeating_group questions, also loads child sub-questions under
    `q["children"]` and the optional count linkage under `q["count_qq_id"]`.
    """
    rows = conn.execute(
        """
        SELECT
            qq.qq_id,
            qq.parent_qq_id,
            qq.count_qq_id,
            q.question_id,
            q.link_id,
            q.question_type  AS type,
            q.numeric_min,
            q.numeric_max
        FROM questionnaire_question qq
        JOIN question q ON q.question_id = qq.question_id
        WHERE qq.questionnaire_id = ?
          AND qq.parent_qq_id IS NULL
          AND qq.status = 'active'
        ORDER BY qq.display_order
        """,
        (questionnaire_id,),
    ).fetchall()

    questions = []
    for row in rows:
        q = dict(row)
        q["options"] = _load_options(conn, row["question_id"])
        q["skip_rules"] = _load_skip_rules(conn, row["qq_id"])
        q["grid_rows"] = _load_grid_rows(conn, row["question_id"])
        q["grid_columns"] = _load_grid_columns(conn, row["question_id"])
        if q["type"] == "repeating_group":
            q["children"] = _load_children(conn, q["qq_id"])
        questions.append(q)

    return questions


def _load_children(conn: sqlite3.Connection, parent_qq_id: int) -> list[dict]:
    """Load child sub-questions for a repeating_group parent."""
    rows = conn.execute(
        """
        SELECT
            qq.qq_id,
            qq.parent_qq_id,
            q.question_id,
            q.link_id,
            q.question_type  AS type,
            q.numeric_min,
            q.numeric_max
        FROM questionnaire_question qq
        JOIN question q ON q.question_id = qq.question_id
        WHERE qq.parent_qq_id = ?
          AND qq.status = 'active'
        ORDER BY qq.display_order
        """,
        (parent_qq_id,),
    ).fetchall()
    children = []
    for row in rows:
        q = dict(row)
        q["options"] = _load_options(conn, row["question_id"])
        q["skip_rules"] = _load_skip_rules(conn, row["qq_id"])
        q["grid_rows"] = _load_grid_rows(conn, row["question_id"])
        q["grid_columns"] = _load_grid_columns(conn, row["question_id"])
        children.append(q)
    return children


def _load_options(conn: sqlite3.Connection, question_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT option_id, option_value, is_exclusive, is_other "
            "FROM response_option WHERE question_id = ? ORDER BY display_order",
            (question_id,),
        ).fetchall()
    ]


def _load_skip_rules(conn: sqlite3.Connection, qq_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT trigger_qq_id, operator, trigger_value, action, enable_behavior "
            "FROM skip_rule WHERE qq_id = ?",
            (qq_id,),
        ).fetchall()
    ]


def _load_grid_rows(conn: sqlite3.Connection, question_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT row_id FROM grid_row WHERE question_id = ? ORDER BY display_order",
            (question_id,),
        ).fetchall()
    ]


def _load_grid_columns(conn: sqlite3.Connection, question_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT column_id, column_type FROM grid_column "
            "WHERE question_id = ? ORDER BY display_order",
            (question_id,),
        ).fetchall()
    ]


# ---------------------------------------------------------------------------
# Skip logic evaluator
# ---------------------------------------------------------------------------

def _is_shown(q: dict, answers: dict[int, Any]) -> bool:
    """
    Return True if the question should be answered given current answers.
    A question with no skip rules is always shown.
    show rules: question is visible only when conditions are met.
    hide rules: question is hidden when conditions are met.
    """
    show_rules = [r for r in q["skip_rules"] if r["action"] == "show"]
    hide_rules = [r for r in q["skip_rules"] if r["action"] == "hide"]

    if show_rules:
        behavior = show_rules[0]["enable_behavior"]
        results = [_eval_condition(r, answers) for r in show_rules]
        shown = all(results) if behavior == "all" else any(results)
        if not shown:
            return False

    if hide_rules:
        behavior = hide_rules[0]["enable_behavior"]
        results = [_eval_condition(r, answers) for r in hide_rules]
        hidden = all(results) if behavior == "all" else any(results)
        if hidden:
            return False

    return True


def _eval_condition(rule: dict, answers: dict[int, Any]) -> bool:
    op = rule["operator"]
    actual = answers.get(rule["trigger_qq_id"])
    expected = rule["trigger_value"]

    if op == "exists":
        return actual is not None
    if op == "not_exists":
        return actual is None
    if actual is None:
        return False
    if op == "=":
        return str(actual) == str(expected)
    if op == "!=":
        return str(actual) != str(expected)
    try:
        a, t = float(actual), float(expected)
        return {">"  : a > t,  "<"  : a < t,
                ">=" : a >= t, "<=" : a <= t}.get(op, False)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Answer generator
# ---------------------------------------------------------------------------

def _generate_session(
    conn: sqlite3.Connection,
    rng: random.Random,
    session_id: int,
    questions: list[dict],
) -> None:
    answers: dict[int, Any] = {}

    for q in questions:
        if not _is_shown(q, answers):
            continue

        # Repeating group: determine N from count_qq_id (if linked) or randomly,
        # then generate child sub-question answers per instance with repeat_index.
        if q["type"] == "repeating_group":
            n_instances = _resolve_repeat_count(rng, q, answers)
            for i in range(n_instances):
                for child in q.get("children", []):
                    # Skip rules on children evaluate against the same top-level
                    # answers map; per-instance skip evaluation is rare and not
                    # supported here. Free-add and count-driven both produce the
                    # same row shape: one response per (child qq, instance i).
                    if not _is_shown(child, answers):
                        continue
                    child_answer = _generate_answer(rng, child)
                    if child_answer is None:
                        continue
                    _write_response(conn, session_id, child, child_answer, repeat_index=i)
            continue

        answer = _generate_answer(rng, q)
        if answer is None:
            continue

        # Store representative value for skip logic evaluation
        answers[q["qq_id"]] = _representative_value(answer, q["type"])
        _write_response(conn, session_id, q, answer)


def _resolve_repeat_count(
    rng: random.Random, q: dict, answers: dict[int, Any]
) -> int:
    """Pick N for a repeating_group instance.

    If count_qq_id is set and the linked count question has been answered,
    use that value (clipped to [0, 20] for safety). Otherwise pick a small
    random count. Always returns int >= 0.
    """
    count_qq_id = q.get("count_qq_id")
    if count_qq_id is not None:
        val = answers.get(count_qq_id)
        if val is not None:
            try:
                n = int(round(float(val)))
                return max(0, min(20, n))
            except (TypeError, ValueError):
                pass
    # Free-add or unresolved count: pick a small random count.
    return rng.randint(0, 5)


def _generate_answer(rng: random.Random, q: dict) -> Any:
    """Return a generated answer in the internal format used by _write_response."""
    qtype = q["type"]

    if qtype in ("single_choice", "likert") and q["options"]:
        eligible = [o for o in q["options"] if not o["is_other"]]
        return rng.choice(eligible) if eligible else None

    if qtype in ("multiple_choice", "sata_other") and q["options"]:
        non_exclusive = [o for o in q["options"] if not o["is_exclusive"] and not o["is_other"]]
        exclusive = [o for o in q["options"] if o["is_exclusive"]]
        if exclusive and rng.random() < 0.1:
            return [rng.choice(exclusive)]
        k = rng.randint(1, min(3, len(non_exclusive)))
        return rng.sample(non_exclusive, k)

    if qtype == "boolean":
        return rng.choice(["true", "false"])

    if qtype in ("numeric", "slider", "likert"):
        lo = q["numeric_min"] if q["numeric_min"] is not None else 0.0
        hi = q["numeric_max"] if q["numeric_max"] is not None else 10.0
        # normal distribution centred in the middle, clipped to range
        mid, spread = (lo + hi) / 2, (hi - lo) / 4
        val = rng.gauss(mid, spread)
        val = max(lo, min(hi, val))
        # return int if both bounds are whole numbers
        if lo == int(lo) and hi == int(hi):
            return int(round(val))
        return round(val, 2)

    if qtype in ("date", "datetime"):
        delta = rng.randint(0, 730)
        return (date.today() - timedelta(days=delta)).isoformat()

    if qtype == "text":
        return rng.choice([
            "No additional comments.",
            "Mild symptoms, manageable.",
            "Significant impact on daily activities.",
            "Improving compared to last month.",
            "No change since last visit.",
        ])

    if qtype == "ranked" and q["options"]:
        shuffled = rng.sample(q["options"], len(q["options"]))
        return [{"opt": opt, "rank": i + 1} for i, opt in enumerate(shuffled)]

    if qtype == "grid" and q["grid_rows"] and q["grid_columns"]:
        # For each row pick one random column (covers single_choice grids).
        return [
            {"row_id": row["row_id"], "col_id": rng.choice(q["grid_columns"])["column_id"]}
            for row in q["grid_rows"]
        ]

    return None


def _representative_value(answer: Any, qtype: str) -> Any:
    """Extract the scalar value used for skip logic evaluation."""
    if qtype == "single_choice" and isinstance(answer, dict):
        return answer.get("option_value")
    if qtype in ("multiple_choice", "sata_other") and isinstance(answer, list):
        return answer[0].get("option_value") if answer else None
    if qtype == "ranked" and isinstance(answer, list):
        return answer[0]["opt"].get("option_value") if answer else None
    return answer


# ---------------------------------------------------------------------------
# Response writer
# ---------------------------------------------------------------------------

def _write_response(
    conn: sqlite3.Connection,
    session_id: int,
    q: dict,
    answer: Any,
    repeat_index: int | None = None,
) -> None:
    qtype = q["type"]
    qq_id = q["qq_id"]
    ri = repeat_index  # NULL when not in a repeating_group instance

    if qtype in ("single_choice", "likert") and isinstance(answer, dict):
        conn.execute(
            "INSERT INTO response (session_id, qq_id, option_id, repeat_index) VALUES (?, ?, ?, ?)",
            (session_id, qq_id, answer["option_id"], ri),
        )

    elif qtype in ("multiple_choice", "sata_other") and isinstance(answer, list):
        for opt in answer:
            conn.execute(
                "INSERT INTO response (session_id, qq_id, option_id, repeat_index) VALUES (?, ?, ?, ?)",
                (session_id, qq_id, opt["option_id"], ri),
            )

    elif qtype == "ranked" and isinstance(answer, list):
        for item in answer:
            conn.execute(
                "INSERT INTO response (session_id, qq_id, option_id, response_numeric, repeat_index) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, qq_id, item["opt"]["option_id"], item["rank"], ri),
            )

    elif qtype == "boolean":
        conn.execute(
            "INSERT INTO response (session_id, qq_id, response_text, repeat_index) VALUES (?, ?, ?, ?)",
            (session_id, qq_id, answer, ri),
        )

    elif qtype in ("numeric", "slider"):
        conn.execute(
            "INSERT INTO response (session_id, qq_id, response_numeric, repeat_index) VALUES (?, ?, ?, ?)",
            (session_id, qq_id, answer, ri),
        )

    elif qtype in ("date", "datetime"):
        conn.execute(
            "INSERT INTO response (session_id, qq_id, response_date, repeat_index) VALUES (?, ?, ?, ?)",
            (session_id, qq_id, answer, ri),
        )

    elif qtype == "text":
        conn.execute(
            "INSERT INTO response (session_id, qq_id, response_text, repeat_index) VALUES (?, ?, ?, ?)",
            (session_id, qq_id, answer, ri),
        )

    elif qtype == "grid" and isinstance(answer, list):
        for cell in answer:
            conn.execute(
                "INSERT INTO response "
                "(session_id, qq_id, grid_row_id, grid_column_id, repeat_index) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, qq_id, cell["row_id"], cell["col_id"], ri),
            )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _upsert_respondent(
    conn: sqlite3.Connection, study_id: int | None, external_id: str
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO respondent (study_id, external_id) VALUES (?, ?)",
        (study_id, external_id),
    )
    row = conn.execute(
        """SELECT respondent_id FROM respondent
           WHERE external_id = ?
             AND (study_id IS ? OR (study_id IS NULL AND ? IS NULL))""",
        (external_id, study_id, study_id),
    ).fetchone()
    return row["respondent_id"]


def _insert_session(
    conn: sqlite3.Connection,
    questionnaire_id: int,
    respondent_id: int,
    admin_mode: str,
) -> int:
    now = date.today().isoformat()
    cursor = conn.execute(
        """INSERT INTO response_session
           (questionnaire_id, respondent_id, started_at, completed_at, is_complete, admin_mode)
           VALUES (?, ?, ?, ?, 1, ?)""",
        (questionnaire_id, respondent_id, now, now, admin_mode),
    )
    return cursor.lastrowid
