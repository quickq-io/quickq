"""
Question library loader.

Reads YAML files from quickq/library/, seeds vocabularies and concepts,
and populates the question bank.  All operations are idempotent: loading
the same library file twice leaves the database unchanged.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from .authoring import (
    upsert_vocabulary, upsert_concept,
    upsert_option_set, upsert_question, insert_options,
    insert_grid_rows_columns,
)
from .models import OptionDef, QuestionDef, GridRowDef, GridColumnDef

LIBRARY_DIR = Path(__file__).parent / "library"


def _parse_options(raw_list: list[dict]) -> list[OptionDef]:
    return [
        OptionDef(
            text=o["text"],
            value=str(o["value"]),
            concept=o.get("concept"),
            is_other=bool(o.get("is_other", False)),
            is_exclusive=bool(o.get("is_exclusive", False)),
        )
        for o in raw_list
    ]


def load_library_file(conn: sqlite3.Connection, path: Path) -> list[int]:
    """
    Load one library YAML file into the question bank.
    Returns the list of question_ids that were inserted or already existed.
    """
    raw = yaml.safe_load(path.read_text())
    body = raw.get("library", raw)
    instrument = body.get("instrument", "")

    with conn:
        # Seed vocabularies referenced by this library
        for v in body.get("vocabularies", []):
            upsert_vocabulary(
                conn,
                v["vocabulary_id"],
                v["vocabulary_name"],
                v.get("vocabulary_reference"),
            )

        # Seed concepts so resolve_concept() works for all refs in this file
        for c in body.get("concepts", []):
            ref: str = c["ref"]
            vocab, code = (ref.split(":", 1) if ":" in ref else ("Local", ref))
            upsert_concept(
                conn,
                concept_name=c["name"],
                domain_id=c["domain"],
                vocabulary_id=vocab,
                concept_class_id=c["class"],
                concept_code=code,
                standard_concept=c.get("standard"),
            )

        # Register named option sets
        option_sets: dict[str, tuple[int, list[OptionDef]]] = {}
        for name, opts in body.get("option_sets", {}).items():
            set_id = upsert_option_set(conn, name)
            option_sets[name] = (set_id, _parse_options(opts))

        question_ids: list[int] = []
        for q_raw in body.get("questions", []):
            q_def = QuestionDef(
                link_id=q_raw["link_id"],
                text=q_raw["text"],
                type=q_raw["type"],
                concept=q_raw.get("concept"),
                source_instrument=instrument,
                source_item_id=q_raw.get("source_item_id"),
                citation=body.get("citation"),
                numeric_min=q_raw.get("numeric_min"),
                numeric_max=q_raw.get("numeric_max"),
                numeric_step=q_raw.get("numeric_step"),
            )
            question_id = upsert_question(conn, q_def, strict_concepts=False)

            # Only insert options if this question has none yet (idempotent)
            has_options = conn.execute(
                "SELECT COUNT(*) FROM response_option WHERE question_id = ?",
                (question_id,),
            ).fetchone()[0]

            if not has_options:
                raw_opts = q_raw.get("options")
                if isinstance(raw_opts, str) and raw_opts.startswith("$"):
                    set_name = raw_opts[1:]
                    if set_name not in option_sets:
                        raise ValueError(
                            f"Library question '{q_raw['link_id']}' references "
                            f"unknown option_set '{set_name}'"
                        )
                    set_id, opts = option_sets[set_name]
                    insert_options(conn, question_id, opts, set_id)
                elif isinstance(raw_opts, list):
                    insert_options(conn, question_id, _parse_options(raw_opts))

            if q_raw.get("rows") and q_raw.get("columns"):
                rows = [GridRowDef(text=r["text"]) for r in q_raw["rows"]]
                cols = [
                    GridColumnDef(
                        text=c["text"],
                        value=str(c["value"]) if "value" in c else None,
                        column_type=c.get("column_type", "single_choice"),
                    )
                    for c in q_raw["columns"]
                ]
                insert_grid_rows_columns(conn, question_id, rows, cols)

            question_ids.append(question_id)

    return question_ids


def load_all_libraries(conn: sqlite3.Connection) -> dict[str, int]:
    """
    Load all *.yaml files from the library directory.
    Returns {filename_stem: questions_loaded} counts.
    """
    counts: dict[str, int] = {}
    for path in sorted(LIBRARY_DIR.glob("*.yaml")):
        ids = load_library_file(conn, path)
        counts[path.stem] = len(ids)
    return counts


def list_library_questions(conn: sqlite3.Connection) -> list[dict]:
    """Return all questions in the bank that came from a named instrument."""
    rows = conn.execute(
        """
        SELECT q.link_id, q.question_text, q.question_type,
               q.source_instrument, q.source_item_id,
               c.concept_code, c.vocabulary_id
        FROM question q
        LEFT JOIN concept c ON q.concept_id = c.concept_id
        WHERE q.source_instrument IS NOT NULL
        ORDER BY q.source_instrument, q.source_item_id
        """
    ).fetchall()
    return [dict(r) for r in rows]
