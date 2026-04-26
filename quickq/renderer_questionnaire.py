"""
Render a questionnaire definition as a human-readable Markdown document.

Reads from the OLTP (SQLite). Does not require the OLAP.

Output structure:
  - Header: name, version, canonical URL
  - Sections with questions in display order
    - Each question: text, link_id, type, concept, options, skip conditions, scoring membership
    - Repeating group children are indented beneath their parent
  - Scoring rules appendix: formula, items, category thresholds
"""
from __future__ import annotations

import sqlite3


def render_questionnaire_md(
    conn: sqlite3.Connection,
    questionnaire_id: int,
) -> str:
    """Return a Markdown string describing the questionnaire definition."""
    meta = conn.execute(
        "SELECT name, version, description, canonical_url FROM questionnaire WHERE questionnaire_id = ?",
        (questionnaire_id,),
    ).fetchone()
    if meta is None:
        raise ValueError(f"Questionnaire {questionnaire_id} not found")

    lines: list[str] = []

    _render_header(lines, meta)
    _render_body(conn, lines, questionnaire_id)
    _render_scoring(conn, lines, questionnaire_id)

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _render_header(lines: list[str], meta: sqlite3.Row) -> None:
    lines.append(f"# {meta['name']}\n")
    if meta["version"]:
        lines.append(f"**Version:** {meta['version']}  ")
    if meta["canonical_url"]:
        lines.append(f"**URL:** {meta['canonical_url']}  ")
    lines.append("")
    if meta["description"]:
        lines.append(f"{meta['description']}\n")
    lines.append("---\n")


# ---------------------------------------------------------------------------
# Body: sections → questions
# ---------------------------------------------------------------------------

def _render_body(
    conn: sqlite3.Connection,
    lines: list[str],
    questionnaire_id: int,
) -> None:
    sections = conn.execute(
        """
        SELECT section_id, title, description
        FROM section
        WHERE questionnaire_id = ?
        ORDER BY display_order
        """,
        (questionnaire_id,),
    ).fetchall()

    top_questions = conn.execute(
        """
        SELECT qq.qq_id, qq.display_order, qq.is_required, qq.section_id,
               q.question_id, q.link_id, q.question_text, q.question_type,
               c.vocabulary_id, c.concept_code
        FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        LEFT JOIN concept c ON q.concept_id = c.concept_id
        WHERE qq.questionnaire_id = ?
          AND qq.status = 'active'
          AND qq.parent_qq_id IS NULL
        ORDER BY qq.display_order
        """,
        (questionnaire_id,),
    ).fetchall()

    if sections:
        by_section: dict[int, list] = {s["section_id"]: [] for s in sections}
        unsectioned: list = []
        for q in top_questions:
            if q["section_id"] and q["section_id"] in by_section:
                by_section[q["section_id"]].append(q)
            else:
                unsectioned.append(q)

        counter = 1
        for sec in sections:
            lines.append(f"## {sec['title']}\n")
            if sec["description"]:
                lines.append(f"{sec['description']}\n")
            for q in by_section[sec["section_id"]]:
                _render_question(conn, lines, q, counter, indent=0)
                counter += 1

        for q in unsectioned:
            _render_question(conn, lines, q, counter, indent=0)
            counter += 1
    else:
        for i, q in enumerate(top_questions, 1):
            _render_question(conn, lines, q, i, indent=0)


# ---------------------------------------------------------------------------
# Single question
# ---------------------------------------------------------------------------

def _render_question(
    conn: sqlite3.Connection,
    lines: list[str],
    q: sqlite3.Row,
    number: int,
    indent: int,
) -> None:
    pad = "  " * indent

    concept = (
        f" · {q['vocabulary_id']}:{q['concept_code']}"
        if q["concept_code"] else ""
    )
    required = " · *required*" if q["is_required"] else ""

    lines.append(f"{pad}**{number}. {q['question_text']}**")
    lines.append(f"{pad}`{q['link_id']}` · `{q['question_type']}`{concept}{required}")

    _render_skip_conditions(conn, lines, q["qq_id"], pad)
    _render_scoring_membership(conn, lines, q["qq_id"], pad)
    lines.append("")

    qtype = q["question_type"]
    if qtype in ("single_choice", "multiple_choice", "sata_other", "likert", "ranked"):
        _render_options(conn, lines, q["question_id"], pad)
    elif qtype == "boolean":
        lines += [f"{pad}- Yes / No", ""]
    elif qtype == "numeric":
        lines += [f"{pad}*Numeric response*", ""]
    elif qtype == "slider":
        lines += [f"{pad}*Slider (visual analog scale)*", ""]
    elif qtype == "text":
        lines += [f"{pad}*Free-text response*", ""]
    elif qtype == "date":
        lines += [f"{pad}*Date*", ""]
    elif qtype == "repeating_group":
        lines += [f"{pad}*Repeating group — one set of sub-questions per instance*", ""]
        _render_children(conn, lines, q["qq_id"], indent)


def _render_options(
    conn: sqlite3.Connection,
    lines: list[str],
    question_id: int,
    pad: str,
) -> None:
    options = conn.execute(
        """
        SELECT option_value, option_text, concept_system, concept_code, is_other
        FROM response_option
        WHERE question_id = ?
        ORDER BY display_order
        """,
        (question_id,),
    ).fetchall()
    for opt in options:
        concept = (
            f" · {_system_label(opt['concept_system'])}:{opt['concept_code']}"
            if opt["concept_code"] else ""
        )
        other = " *(free text)*" if opt["is_other"] else ""
        lines.append(f"{pad}- `{opt['option_value']}` {opt['option_text']}{concept}{other}")
    lines.append("")


def _render_skip_conditions(
    conn: sqlite3.Connection,
    lines: list[str],
    qq_id: int,
    pad: str,
) -> None:
    rules = conn.execute(
        """
        SELECT sr.action, tq.link_id AS trigger_link,
               sr.operator, sr.trigger_value, sr.enable_behavior
        FROM skip_rule sr
        JOIN questionnaire_question tqq ON sr.trigger_qq_id = tqq.qq_id
        JOIN question tq ON tqq.question_id = tq.question_id
        WHERE sr.qq_id = ?
        """,
        (qq_id,),
    ).fetchall()
    if not rules:
        return

    behavior = (rules[0]["enable_behavior"] or "any").lower()
    joiner = " and " if behavior == "all" else " or "
    action = rules[0]["action"].capitalize()

    conditions = []
    for r in rules:
        val = r["trigger_value"] if r["trigger_value"] is not None else "(any value)"
        conditions.append(f"`{r['trigger_link']}` {_op(r['operator'])} {val}")

    lines.append(f"{pad}*{action} when: {joiner.join(conditions)}*")


def _render_scoring_membership(
    conn: sqlite3.Connection,
    lines: list[str],
    qq_id: int,
    pad: str,
) -> None:
    rows = conn.execute(
        """
        SELECT scr.name
        FROM scoring_rule_item sri
        JOIN scoring_rule scr ON sri.scoring_rule_id = scr.scoring_rule_id
        WHERE sri.qq_id = ?
        """,
        (qq_id,),
    ).fetchall()
    if rows:
        names = ", ".join(r["name"] for r in rows)
        lines.append(f"{pad}*Scored in: {names}*")


def _render_children(
    conn: sqlite3.Connection,
    lines: list[str],
    parent_qq_id: int,
    indent: int,
) -> None:
    children = conn.execute(
        """
        SELECT qq.qq_id, qq.display_order, qq.is_required, qq.section_id,
               q.question_id, q.link_id, q.question_text, q.question_type,
               c.vocabulary_id, c.concept_code
        FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        LEFT JOIN concept c ON q.concept_id = c.concept_id
        WHERE qq.parent_qq_id = ? AND qq.status = 'active'
        ORDER BY qq.display_order
        """,
        (parent_qq_id,),
    ).fetchall()
    for i, child in enumerate(children, 1):
        _render_question(conn, lines, child, i, indent=indent + 1)


# ---------------------------------------------------------------------------
# Scoring appendix
# ---------------------------------------------------------------------------

def _render_scoring(
    conn: sqlite3.Connection,
    lines: list[str],
    questionnaire_id: int,
) -> None:
    rules = conn.execute(
        """
        SELECT scoring_rule_id, name, formula, description
        FROM scoring_rule
        WHERE questionnaire_id = ?
        ORDER BY scoring_rule_id
        """,
        (questionnaire_id,),
    ).fetchall()
    if not rules:
        return

    lines += ["---\n", "## Scoring\n"]

    for rule in rules:
        lines.append(f"### {rule['name']}\n")
        if rule["description"]:
            lines.append(f"{rule['description']}\n")

        items = conn.execute(
            """
            SELECT q.link_id
            FROM scoring_rule_item sri
            JOIN questionnaire_question qq ON sri.qq_id = qq.qq_id
            JOIN question q ON qq.question_id = q.question_id
            WHERE sri.scoring_rule_id = ?
            ORDER BY qq.display_order
            """,
            (rule["scoring_rule_id"],),
        ).fetchall()
        item_list = ", ".join(f"`{r['link_id']}`" for r in items)
        lines.append(f"**Formula:** {rule['formula']} of {item_list}\n")

        cats = conn.execute(
            """
            SELECT label, min_score, max_score
            FROM scoring_category
            WHERE scoring_rule_id = ?
            ORDER BY display_order
            """,
            (rule["scoring_rule_id"],),
        ).fetchall()
        if cats:
            lines += ["| Score | Category |", "|---|---|"]
            for cat in cats:
                lines.append(f"| {_score_fmt(cat['min_score'])}–{_score_fmt(cat['max_score'])} | {cat['label']} |")
            lines.append("")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _op(op: str) -> str:
    return {"=": "=", "!=": "≠", ">": ">", "<": "<",
            ">=": "≥", "<=": "≤", "exists": "is answered"}.get(op, op)


_SYSTEM_ALIASES: dict[str, str] = {
    "http://loinc.org":            "LOINC",
    "http://snomed.info/sct":      "SNOMED",
    "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm",
    "http://hl7.org/fhir/sid/icd-10": "ICD-10",
    "http://ncimeta.nci.nih.gov":  "NCI",
}


def _system_label(system: str | None) -> str:
    if not system:
        return ""
    return _SYSTEM_ALIASES.get(system.rstrip("/"), system)


def _score_fmt(val: float | int | None) -> str:
    if val is None:
        return "?"
    return str(int(val)) if float(val) == int(val) else str(val)
