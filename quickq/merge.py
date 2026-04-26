"""
Merge multiple quickq SQLite databases into a single combined study database.

Natural-key deduplication ensures instrument definitions (questionnaires,
questions, options) are never duplicated. Responses are remapped to new
integer PKs. Duplicate sessions are skipped and counted.

Typical use case: each site in a multi-site study collects into its own
.db file; a nightly quickq merge assembles the combined study.

Usage:
    from quickq.merge import merge_databases
    result = merge_databases(["site_a.db", "site_b.db"], "combined.db")
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .schema import init_oltp, open_oltp


class MergeError(Exception):
    pass


@dataclass
class MergeResult:
    sources: list[str]
    output: str
    respondents_merged: int = 0
    sessions_merged: int = 0
    responses_merged: int = 0
    sessions_skipped_duplicate: int = 0
    warnings: list[str] = field(default_factory=list)


def merge_databases(
    source_paths: list[str | Path],
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> MergeResult:
    """
    Merge multiple quickq SQLite databases into output_path.

    Instrument definitions are deduplicated by natural key:
    - questions by link_id
    - questionnaires by (canonical_url, version)
    - respondents by (study_id, external_id)
    - sessions by fhir_response_id, or by (respondent_id, questionnaire_id, started_at)

    Raises MergeError if:
    - A question link_id has different question_text across sources
    - output_path already exists and overwrite=False
    """
    output_path = Path(output_path)
    source_paths = [Path(p) for p in source_paths]

    if output_path.exists():
        if not overwrite:
            raise MergeError(
                f"{output_path} already exists. Pass overwrite=True to replace it."
            )
        output_path.unlink()

    result = MergeResult(
        sources=[str(p) for p in source_paths],
        output=str(output_path),
    )

    out = init_oltp(output_path)
    try:
        for src_path in source_paths:
            src = open_oltp(src_path, read_only=True)
            try:
                _merge_one(src, out, result, source_label=str(src_path))
            finally:
                src.close()
        out.commit()
    except Exception:
        out.close()
        output_path.unlink(missing_ok=True)
        raise

    out.close()
    return result


# ---------------------------------------------------------------------------
# Per-source orchestration
# ---------------------------------------------------------------------------

def _merge_one(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    result: MergeResult,
    *,
    source_label: str,
) -> None:
    idmap: dict[str, dict] = {
        "vocabulary":    {},
        "concept":       {},
        "study":         {},
        "option_set":    {},
        "question":      {},
        "option":        {},
        "grid_row":      {},
        "grid_col":      {},
        "questionnaire": {},
        "section":       {},
        "qq":            {},
        "scoring_rule":  {},
        "respondent":    {},
        "session":       {},
    }

    _merge_vocabularies(src, out, idmap)
    _merge_concepts(src, out, idmap)
    _merge_studies(src, out, idmap, result, source_label)
    _merge_option_sets(src, out, idmap)
    _merge_questions(src, out, idmap, source_label)
    _merge_options(src, out, idmap)
    _merge_grid_rows(src, out, idmap)
    _merge_grid_cols(src, out, idmap)
    _merge_questionnaires(src, out, idmap)
    _merge_sections(src, out, idmap)
    new_qq_ids = _merge_questionnaire_questions(src, out, idmap)
    _merge_skip_rules(src, out, idmap, new_qq_ids)
    _merge_scoring_rules(src, out, idmap)
    _merge_respondents(src, out, idmap, result)
    new_session_ids = _merge_sessions(src, out, idmap, result)
    _merge_responses(src, out, idmap, new_session_ids, result)
    _merge_admin_events(src, out, idmap, new_session_ids)
    _merge_data_quality_flags(src, out, idmap, new_session_ids)
    _merge_errata(src, out, idmap)
    _merge_versioning(src, out, idmap)
    _merge_person_map(src, out, idmap)


# ---------------------------------------------------------------------------
# Concept plane
# ---------------------------------------------------------------------------

def _merge_vocabularies(src: sqlite3.Connection, out: sqlite3.Connection, idmap: dict) -> None:
    for row in src.execute("SELECT * FROM vocabulary").fetchall():
        row = dict(row)
        vid = row["vocabulary_id"]
        exists = out.execute(
            "SELECT 1 FROM vocabulary WHERE vocabulary_id = ?", (vid,)
        ).fetchone()
        if not exists:
            out.execute(
                """INSERT INTO vocabulary
                   (vocabulary_id, vocabulary_name, vocabulary_reference, version, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (vid, row["vocabulary_name"], row.get("vocabulary_reference"),
                 row.get("version"), row["created_at"]),
            )
        idmap["vocabulary"][vid] = vid


def _merge_concepts(src: sqlite3.Connection, out: sqlite3.Connection, idmap: dict) -> None:
    for row in src.execute("SELECT * FROM concept").fetchall():
        row = dict(row)
        existing = out.execute(
            "SELECT concept_id FROM concept WHERE vocabulary_id = ? AND concept_code = ?",
            (row["vocabulary_id"], row["concept_code"]),
        ).fetchone()
        if existing:
            idmap["concept"][row["concept_id"]] = existing[0]
        else:
            cur = out.execute(
                """INSERT INTO concept
                   (concept_name, domain_id, vocabulary_id, concept_class_id,
                    standard_concept, concept_code, valid_start_date, valid_end_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["concept_name"], row["domain_id"], row["vocabulary_id"],
                 row["concept_class_id"], row.get("standard_concept"),
                 row["concept_code"], row["valid_start_date"], row["valid_end_date"]),
            )
            idmap["concept"][row["concept_id"]] = cur.lastrowid

    for row in src.execute("SELECT * FROM concept_relationship").fetchall():
        row = dict(row)
        c1 = idmap["concept"].get(row["concept_id_1"])
        c2 = idmap["concept"].get(row["concept_id_2"])
        if c1 is None or c2 is None:
            continue
        out.execute(
            """INSERT OR IGNORE INTO concept_relationship
               (concept_id_1, concept_id_2, relationship_id, valid_start_date, valid_end_date)
               VALUES (?, ?, ?, ?, ?)""",
            (c1, c2, row["relationship_id"],
             row["valid_start_date"], row["valid_end_date"]),
        )


# ---------------------------------------------------------------------------
# Instrument plane
# ---------------------------------------------------------------------------

def _merge_studies(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
    result: MergeResult,
    source_label: str,
) -> None:
    for row in src.execute("SELECT * FROM study").fetchall():
        row = dict(row)
        existing = out.execute(
            "SELECT study_id FROM study WHERE name = ?", (row["name"],)
        ).fetchone()
        if existing:
            idmap["study"][row["study_id"]] = existing[0]
        else:
            cur = out.execute(
                """INSERT INTO study
                   (name, description, principal_investigator, irb_number,
                    start_date, end_date, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (row["name"], row.get("description"),
                 row.get("principal_investigator"), row.get("irb_number"),
                 row.get("start_date"), row.get("end_date"), row["created_at"]),
            )
            idmap["study"][row["study_id"]] = cur.lastrowid


def _merge_option_sets(src: sqlite3.Connection, out: sqlite3.Connection, idmap: dict) -> None:
    for row in src.execute("SELECT * FROM response_option_set").fetchall():
        row = dict(row)
        existing = out.execute(
            "SELECT option_set_id FROM response_option_set WHERE name = ?", (row["name"],)
        ).fetchone()
        if existing:
            idmap["option_set"][row["option_set_id"]] = existing[0]
        else:
            cur = out.execute(
                """INSERT INTO response_option_set
                   (name, canonical_url, description, created_at)
                   VALUES (?, ?, ?, ?)""",
                (row["name"], row.get("canonical_url"),
                 row.get("description"), row["created_at"]),
            )
            idmap["option_set"][row["option_set_id"]] = cur.lastrowid


def _merge_questions(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
    source_label: str,
) -> None:
    for row in src.execute("SELECT * FROM question").fetchall():
        row = dict(row)
        existing = out.execute(
            "SELECT question_id, question_text FROM question WHERE link_id = ?",
            (row["link_id"],),
        ).fetchone()
        if existing:
            if existing["question_text"] != row["question_text"]:
                raise MergeError(
                    f"Question text divergence for link_id={row['link_id']!r} "
                    f"in {source_label}.\n"
                    f"  existing: {existing['question_text']!r}\n"
                    f"  source:   {row['question_text']!r}\n"
                    "Use record_question_lineage to declare intentional revisions "
                    "before merging."
                )
            idmap["question"][row["question_id"]] = existing["question_id"]
        else:
            concept_id = idmap["concept"].get(row["concept_id"]) if row.get("concept_id") else None
            option_set_id = idmap["option_set"].get(row["option_set_id"]) if row.get("option_set_id") else None
            cur = out.execute(
                """INSERT INTO question
                   (link_id, question_text, question_type, help_text, concept_id,
                    source_instrument, source_item_id, citation, option_set_id,
                    numeric_min, numeric_max, numeric_step,
                    slider_min_label, slider_max_label,
                    created_at, is_active, internal_note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["link_id"], row["question_text"], row["question_type"],
                 row.get("help_text"), concept_id,
                 row.get("source_instrument"), row.get("source_item_id"),
                 row.get("citation"), option_set_id,
                 row.get("numeric_min"), row.get("numeric_max"), row.get("numeric_step"),
                 row.get("slider_min_label"), row.get("slider_max_label"),
                 row["created_at"], row.get("is_active", 1), row.get("internal_note")),
            )
            idmap["question"][row["question_id"]] = cur.lastrowid


def _merge_options(src: sqlite3.Connection, out: sqlite3.Connection, idmap: dict) -> None:
    for row in src.execute("SELECT * FROM response_option ORDER BY display_order").fetchall():
        row = dict(row)
        q_new = idmap["question"].get(row["question_id"])
        if q_new is None:
            continue
        existing = out.execute(
            "SELECT option_id FROM response_option WHERE question_id = ? AND option_value = ?",
            (q_new, row["option_value"]),
        ).fetchone()
        if existing:
            idmap["option"][row["option_id"]] = existing[0]
        else:
            concept_id = idmap["concept"].get(row["concept_id"]) if row.get("concept_id") else None
            option_set_id = idmap["option_set"].get(row["option_set_id"]) if row.get("option_set_id") else None
            cur = out.execute(
                """INSERT INTO response_option
                   (question_id, option_set_id, option_text, option_value,
                    display_order, concept_id, concept_code, concept_system,
                    is_other, is_exclusive, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (q_new, option_set_id, row["option_text"], row["option_value"],
                 row.get("display_order", 0), concept_id,
                 row.get("concept_code"), row.get("concept_system"),
                 row.get("is_other", 0), row.get("is_exclusive", 0), row["created_at"]),
            )
            idmap["option"][row["option_id"]] = cur.lastrowid


def _merge_grid_rows(src: sqlite3.Connection, out: sqlite3.Connection, idmap: dict) -> None:
    for row in src.execute("SELECT * FROM grid_row ORDER BY display_order").fetchall():
        row = dict(row)
        q_new = idmap["question"].get(row["question_id"])
        if q_new is None:
            continue
        existing = out.execute(
            "SELECT row_id FROM grid_row WHERE question_id = ? AND display_order = ?",
            (q_new, row.get("display_order", 0)),
        ).fetchone()
        if existing:
            idmap["grid_row"][row["row_id"]] = existing[0]
        else:
            concept_id = idmap["concept"].get(row["concept_id"]) if row.get("concept_id") else None
            cur = out.execute(
                "INSERT INTO grid_row (question_id, row_text, display_order, concept_id) VALUES (?, ?, ?, ?)",
                (q_new, row["row_text"], row.get("display_order", 0), concept_id),
            )
            idmap["grid_row"][row["row_id"]] = cur.lastrowid


def _merge_grid_cols(src: sqlite3.Connection, out: sqlite3.Connection, idmap: dict) -> None:
    for row in src.execute("SELECT * FROM grid_column ORDER BY display_order").fetchall():
        row = dict(row)
        q_new = idmap["question"].get(row["question_id"])
        if q_new is None:
            continue
        existing = out.execute(
            "SELECT column_id FROM grid_column WHERE question_id = ? AND display_order = ?",
            (q_new, row.get("display_order", 0)),
        ).fetchone()
        if existing:
            idmap["grid_col"][row["column_id"]] = existing[0]
        else:
            concept_id = idmap["concept"].get(row["concept_id"]) if row.get("concept_id") else None
            cur = out.execute(
                """INSERT INTO grid_column
                   (question_id, column_text, column_value, column_type,
                    display_order, concept_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (q_new, row["column_text"], row.get("column_value"),
                 row.get("column_type", "single_choice"),
                 row.get("display_order", 0), concept_id),
            )
            idmap["grid_col"][row["column_id"]] = cur.lastrowid


def _merge_questionnaires(src: sqlite3.Connection, out: sqlite3.Connection, idmap: dict) -> None:
    for row in src.execute("SELECT * FROM questionnaire").fetchall():
        row = dict(row)
        existing = out.execute(
            "SELECT questionnaire_id FROM questionnaire WHERE canonical_url IS ? AND version = ?",
            (row.get("canonical_url"), row["version"]),
        ).fetchone()
        if existing:
            idmap["questionnaire"][row["questionnaire_id"]] = existing[0]
        else:
            study_id = idmap["study"].get(row["study_id"]) if row.get("study_id") else None
            concept_id = idmap["concept"].get(row["concept_id"]) if row.get("concept_id") else None
            superseded_by = idmap["questionnaire"].get(row["superseded_by"]) if row.get("superseded_by") else None
            cur = out.execute(
                """INSERT INTO questionnaire
                   (study_id, name, description, canonical_url, version, fhir_status,
                    concept_id, superseded_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (study_id, row["name"], row.get("description"),
                 row.get("canonical_url"), row["version"],
                 row.get("fhir_status", "draft"),
                 concept_id, superseded_by, row["created_at"]),
            )
            idmap["questionnaire"][row["questionnaire_id"]] = cur.lastrowid


def _merge_sections(src: sqlite3.Connection, out: sqlite3.Connection, idmap: dict) -> None:
    for row in src.execute("SELECT * FROM section ORDER BY display_order").fetchall():
        row = dict(row)
        q_new = idmap["questionnaire"].get(row["questionnaire_id"])
        if q_new is None:
            continue
        existing = out.execute(
            "SELECT section_id FROM section WHERE questionnaire_id = ? AND display_order = ?",
            (q_new, row.get("display_order", 0)),
        ).fetchone()
        if existing:
            idmap["section"][row["section_id"]] = existing[0]
        else:
            cur = out.execute(
                """INSERT INTO section
                   (questionnaire_id, title, description, display_order,
                    display_condition, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (q_new, row.get("title"), row.get("description"),
                 row.get("display_order", 0),
                 row.get("display_condition"), row["created_at"]),
            )
            idmap["section"][row["section_id"]] = cur.lastrowid


def _merge_questionnaire_questions(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
) -> set[int]:
    """
    Returns the set of new qq_ids in the output (newly inserted, not pre-existing).
    Self-referential FKs (parent_qq_id, count_qq_id) are patched in a second pass.
    """
    new_qq_ids: set[int] = set()

    # Pass 1: insert with self-referential FKs set to NULL
    for row in src.execute(
        "SELECT * FROM questionnaire_question ORDER BY display_order"
    ).fetchall():
        row = dict(row)
        q_new = idmap["questionnaire"].get(row["questionnaire_id"])
        question_new = idmap["question"].get(row["question_id"])
        if q_new is None or question_new is None:
            continue
        section_new = idmap["section"].get(row["section_id"]) if row.get("section_id") else None

        existing = out.execute(
            """SELECT qq_id FROM questionnaire_question
               WHERE questionnaire_id = ? AND question_id = ?""",
            (q_new, question_new),
        ).fetchone()
        if existing:
            idmap["qq"][row["qq_id"]] = existing[0]
        else:
            cur = out.execute(
                """INSERT INTO questionnaire_question
                   (questionnaire_id, section_id, question_id, display_order,
                    is_required, parent_qq_id, count_qq_id, display_condition,
                    created_at, status, status_changed_at, status_notes)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)""",
                (q_new, section_new, question_new,
                 row.get("display_order", 0), row.get("is_required", 0),
                 row.get("display_condition"), row["created_at"],
                 row.get("status", "active"),
                 row.get("status_changed_at"), row.get("status_notes")),
            )
            new_id = cur.lastrowid
            idmap["qq"][row["qq_id"]] = new_id
            new_qq_ids.add(new_id)

    # Pass 2: patch self-referential FKs for newly inserted QQs
    for row in src.execute(
        "SELECT qq_id, parent_qq_id, count_qq_id FROM questionnaire_question"
    ).fetchall():
        row = dict(row)
        new_qq = idmap["qq"].get(row["qq_id"])
        if new_qq not in new_qq_ids:
            continue
        parent_new = idmap["qq"].get(row["parent_qq_id"]) if row.get("parent_qq_id") else None
        count_new  = idmap["qq"].get(row["count_qq_id"])  if row.get("count_qq_id")  else None
        if parent_new is not None or count_new is not None:
            out.execute(
                "UPDATE questionnaire_question SET parent_qq_id = ?, count_qq_id = ? WHERE qq_id = ?",
                (parent_new, count_new, new_qq),
            )

    return new_qq_ids


def _merge_skip_rules(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
    new_qq_ids: set[int],
) -> None:
    for row in src.execute("SELECT * FROM skip_rule").fetchall():
        row = dict(row)
        qq_new = idmap["qq"].get(row["qq_id"])
        trigger_new = idmap["qq"].get(row["trigger_qq_id"])
        if qq_new is None or trigger_new is None:
            continue
        if qq_new not in new_qq_ids:
            continue  # pre-existing QQ already has its skip rules
        out.execute(
            """INSERT INTO skip_rule
               (qq_id, enable_behavior, trigger_qq_id, operator,
                trigger_value, action, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (qq_new, row.get("enable_behavior", "all"), trigger_new,
             row["operator"], row.get("trigger_value"),
             row.get("action", "show"), row["created_at"]),
        )


def _merge_scoring_rules(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
) -> None:
    for row in src.execute("SELECT * FROM scoring_rule").fetchall():
        row = dict(row)
        q_new = idmap["questionnaire"].get(row["questionnaire_id"])
        if q_new is None:
            continue
        existing = out.execute(
            "SELECT scoring_rule_id FROM scoring_rule WHERE questionnaire_id = ? AND name = ?",
            (q_new, row["name"]),
        ).fetchone()
        if existing:
            idmap["scoring_rule"][row["scoring_rule_id"]] = existing[0]
            continue

        cur = out.execute(
            """INSERT INTO scoring_rule
               (questionnaire_id, name, description, formula, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (q_new, row["name"], row.get("description"),
             row["formula"], row["created_at"]),
        )
        new_rule_id = cur.lastrowid
        idmap["scoring_rule"][row["scoring_rule_id"]] = new_rule_id

        for item in src.execute(
            "SELECT * FROM scoring_rule_item WHERE scoring_rule_id = ?",
            (row["scoring_rule_id"],),
        ).fetchall():
            item = dict(item)
            qq_new = idmap["qq"].get(item["qq_id"])
            if qq_new is None:
                continue
            out.execute(
                """INSERT OR IGNORE INTO scoring_rule_item
                   (scoring_rule_id, qq_id, weight, reverse_score)
                   VALUES (?, ?, ?, ?)""",
                (new_rule_id, qq_new,
                 item.get("weight", 1.0), item.get("reverse_score", 0)),
            )

        for cat in src.execute(
            "SELECT * FROM scoring_category WHERE scoring_rule_id = ? ORDER BY display_order",
            (row["scoring_rule_id"],),
        ).fetchall():
            cat = dict(cat)
            out.execute(
                """INSERT OR IGNORE INTO scoring_category
                   (scoring_rule_id, label, min_score, max_score, display_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (new_rule_id, cat["label"], cat.get("min_score"),
                 cat.get("max_score"), cat.get("display_order", 0)),
            )


# ---------------------------------------------------------------------------
# Response plane
# ---------------------------------------------------------------------------

def _merge_respondents(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
    result: MergeResult,
) -> None:
    for row in src.execute("SELECT * FROM respondent").fetchall():
        row = dict(row)
        study_new = idmap["study"].get(row["study_id"]) if row.get("study_id") else None
        existing = out.execute(
            "SELECT respondent_id FROM respondent WHERE study_id IS ? AND external_id IS ?",
            (study_new, row.get("external_id")),
        ).fetchone()
        if existing:
            idmap["respondent"][row["respondent_id"]] = existing[0]
        else:
            cur = out.execute(
                "INSERT INTO respondent (study_id, external_id, created_at) VALUES (?, ?, ?)",
                (study_new, row.get("external_id"), row["created_at"]),
            )
            idmap["respondent"][row["respondent_id"]] = cur.lastrowid
            result.respondents_merged += 1


def _merge_sessions(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
    result: MergeResult,
) -> set[int]:
    """
    Returns the set of new session_ids in the output (newly inserted).
    Deduplication: prefer fhir_response_id; fall back to
    (respondent_id, questionnaire_id, started_at).
    """
    new_session_ids: set[int] = set()

    for row in src.execute("SELECT * FROM response_session").fetchall():
        row = dict(row)
        respondent_new = idmap["respondent"].get(row["respondent_id"])
        q_new = idmap["questionnaire"].get(row["questionnaire_id"])
        if respondent_new is None or q_new is None:
            continue

        if row.get("fhir_response_id"):
            existing = out.execute(
                "SELECT session_id FROM response_session WHERE fhir_response_id = ?",
                (row["fhir_response_id"],),
            ).fetchone()
        else:
            existing = out.execute(
                """SELECT session_id FROM response_session
                   WHERE respondent_id = ? AND questionnaire_id = ? AND started_at = ?""",
                (respondent_new, q_new, row["started_at"]),
            ).fetchone()

        if existing:
            idmap["session"][row["session_id"]] = existing[0]
            result.sessions_skipped_duplicate += 1
        else:
            cur = out.execute(
                """INSERT INTO response_session
                   (questionnaire_id, respondent_id, started_at, completed_at,
                    is_complete, admin_mode, is_proxy, interviewer_id,
                    fhir_response_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (q_new, respondent_new, row["started_at"], row.get("completed_at"),
                 row.get("is_complete", 0), row.get("admin_mode"),
                 row.get("is_proxy", 0), row.get("interviewer_id"),
                 row.get("fhir_response_id"), row["created_at"]),
            )
            new_id = cur.lastrowid
            idmap["session"][row["session_id"]] = new_id
            new_session_ids.add(new_id)
            result.sessions_merged += 1

    return new_session_ids


def _merge_responses(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
    new_session_ids: set[int],
    result: MergeResult,
) -> None:
    for row in src.execute("SELECT * FROM response").fetchall():
        row = dict(row)
        session_new = idmap["session"].get(row["session_id"])
        qq_new = idmap["qq"].get(row["qq_id"])
        if session_new is None or qq_new is None:
            continue
        if session_new not in new_session_ids:
            continue  # duplicate session — skip its responses
        option_new   = idmap["option"].get(row["option_id"])       if row.get("option_id")    else None
        grid_row_new = idmap["grid_row"].get(row["grid_row_id"])   if row.get("grid_row_id")  else None
        grid_col_new = idmap["grid_col"].get(row["grid_column_id"]) if row.get("grid_column_id") else None
        out.execute(
            """INSERT INTO response
               (session_id, qq_id, option_id, response_text, response_numeric,
                response_date, grid_row_id, grid_column_id, repeat_index,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_new, qq_new, option_new,
             row.get("response_text"), row.get("response_numeric"),
             row.get("response_date"), grid_row_new, grid_col_new,
             row.get("repeat_index"), row["created_at"], row["updated_at"]),
        )
        result.responses_merged += 1


def _merge_admin_events(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
    new_session_ids: set[int],
) -> None:
    for row in src.execute("SELECT * FROM admin_event").fetchall():
        row = dict(row)
        session_new = idmap["session"].get(row["session_id"]) if row.get("session_id") else None
        if row.get("session_id") and session_new not in new_session_ids:
            continue
        study_new      = idmap["study"].get(row["study_id"])             if row.get("study_id")        else None
        q_new          = idmap["questionnaire"].get(row["questionnaire_id"]) if row.get("questionnaire_id") else None
        respondent_new = idmap["respondent"].get(row["respondent_id"])   if row.get("respondent_id")   else None
        out.execute(
            """INSERT INTO admin_event
               (study_id, questionnaire_id, respondent_id, session_id,
                event_type, event_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (study_new, q_new, respondent_new, session_new,
             row["event_type"], row["event_at"], row.get("notes")),
        )


def _merge_data_quality_flags(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
    new_session_ids: set[int],
) -> None:
    for row in src.execute("SELECT * FROM data_quality_flag").fetchall():
        row = dict(row)
        session_new = idmap["session"].get(row["session_id"]) if row.get("session_id") else None
        if row.get("session_id") and session_new not in new_session_ids:
            continue
        qq_new = idmap["qq"].get(row["qq_id"]) if row.get("qq_id") else None
        out.execute(
            """INSERT INTO data_quality_flag
               (session_id, response_id, qq_id, rule_name, message,
                severity, is_resolved, created_at)
               VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
            (session_new, qq_new, row["rule_name"], row["message"],
             row.get("severity", "warning"), row.get("is_resolved", 0),
             row["created_at"]),
        )


def _merge_errata(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
) -> None:
    for row in src.execute("SELECT * FROM study_errata_log").fetchall():
        row = dict(row)
        study_new    = idmap["study"].get(row["study_id"])               if row.get("study_id")        else None
        q_new        = idmap["questionnaire"].get(row["questionnaire_id"]) if row.get("questionnaire_id") else None
        question_new = idmap["question"].get(row["question_id"])         if row.get("question_id")     else None
        # Session FKs: remap if available, else NULL (session may not have been imported)
        sess_from    = idmap["session"].get(row["affects_session_from"]) if row.get("affects_session_from") else None
        sess_to      = idmap["session"].get(row["affects_session_to"])   if row.get("affects_session_to")   else None
        out.execute(
            """INSERT INTO study_errata_log
               (study_id, questionnaire_id, question_id, event_type, severity,
                title, description, affects_session_from, affects_session_to,
                affects_date_from, affects_date_to, analyst_guidance, status,
                reported_by, reported_at, resolved_by, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (study_new, q_new, question_new,
             row["event_type"], row.get("severity", "minor"),
             row["title"], row["description"],
             sess_from, sess_to,
             row.get("affects_date_from"), row.get("affects_date_to"),
             row.get("analyst_guidance"), row.get("status", "open"),
             row.get("reported_by"), row["reported_at"],
             row.get("resolved_by"), row.get("resolved_at")),
        )


# ---------------------------------------------------------------------------
# Versioning and equivalence
# ---------------------------------------------------------------------------

def _merge_versioning(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
) -> None:
    for row in src.execute("SELECT * FROM question_lineage").fetchall():
        row = dict(row)
        q_new = idmap["question"].get(row["question_id"])
        parent_new = idmap["question"].get(row["parent_question_id"])
        if q_new is None or parent_new is None:
            continue
        exists = out.execute(
            """SELECT 1 FROM question_lineage
               WHERE question_id = ? AND parent_question_id = ? AND change_type = ?""",
            (q_new, parent_new, row["change_type"]),
        ).fetchone()
        if not exists:
            out.execute(
                """INSERT INTO question_lineage
                   (question_id, parent_question_id, change_type,
                    change_description, effective_date, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (q_new, parent_new, row["change_type"],
                 row.get("change_description"), row.get("effective_date"),
                 row["created_at"]),
            )

    for row in src.execute("SELECT * FROM question_equivalence").fetchall():
        row = dict(row)
        q1_new = idmap["question"].get(row["question_id_1"])
        q2_new = idmap["question"].get(row["question_id_2"])
        if q1_new is None or q2_new is None:
            continue
        out.execute(
            """INSERT OR IGNORE INTO question_equivalence
               (question_id_1, question_id_2, relationship, confidence,
                harmonization_notes, declared_by, declared_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (q1_new, q2_new, row["relationship"],
             row.get("confidence", "medium"),
             row.get("harmonization_notes"), row.get("declared_by"),
             row["declared_at"]),
        )

    for row in src.execute("SELECT * FROM questionnaire_version_diff").fetchall():
        row = dict(row)
        from_q = idmap["questionnaire"].get(row["from_questionnaire_id"])
        to_q   = idmap["questionnaire"].get(row["to_questionnaire_id"])
        if from_q is None or to_q is None:
            continue
        qq_from = idmap["qq"].get(row["qq_id_from"]) if row.get("qq_id_from") else None
        qq_to   = idmap["qq"].get(row["qq_id_to"])   if row.get("qq_id_to")   else None
        exists = out.execute(
            """SELECT 1 FROM questionnaire_version_diff
               WHERE from_questionnaire_id = ? AND to_questionnaire_id = ?
                 AND change_type = ? AND qq_id_from IS ? AND qq_id_to IS ?""",
            (from_q, to_q, row["change_type"], qq_from, qq_to),
        ).fetchone()
        if not exists:
            out.execute(
                """INSERT INTO questionnaire_version_diff
                   (from_questionnaire_id, to_questionnaire_id, change_type,
                    qq_id_from, qq_id_to, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (from_q, to_q, row["change_type"],
                 qq_from, qq_to, row.get("notes"), row["created_at"]),
            )


def _merge_person_map(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    idmap: dict,
) -> None:
    for row in src.execute("SELECT * FROM person_map").fetchall():
        row = dict(row)
        respondent_new = idmap["respondent"].get(row["respondent_id"])
        if respondent_new is None:
            continue
        out.execute(
            """INSERT OR IGNORE INTO person_map
               (respondent_id, omop_person_id, created_at)
               VALUES (?, ?, ?)""",
            (respondent_new, row["omop_person_id"], row["created_at"]),
        )
