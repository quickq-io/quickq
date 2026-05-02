"""
DML helpers for writing questionnaire definitions into the OLTP database.

All public functions accept an open sqlite3.Connection and return the
integer primary key of the created or retrieved row. Operations on
entities identified by a natural key (link_id, canonical_url, vocab code)
are idempotent: a second call returns the existing row's id unchanged.
"""
from __future__ import annotations

import sqlite3
from .models import (
    OptionDef, QuestionDef, SectionDef,
    QuestionnaireDef, ShowWhen, ScoringRuleDef, ScoringCategoryDef,
    GridRowDef, GridColumnDef,
)

# Maps vocabulary_id to its canonical FHIR/HL7 system URI
_VOCAB_SYSTEMS: dict[str, str] = {
    "LOINC":    "http://loinc.org",
    "SNOMED":   "http://snomed.info/sct",
    "NCI":      "http://ncithesaurus.nci.nih.gov",
    "ICD10CM":  "http://hl7.org/fhir/sid/icd-10-cm",
    "BRFSS":    "http://www.cdc.gov/brfss",
    "Local":    "http://quickq.io/concepts/local",
}


def _vocab_system(vocabulary_id: str) -> str:
    return _VOCAB_SYSTEMS.get(vocabulary_id, f"urn:quickq:{vocabulary_id.lower()}")


def _parse_concept_ref(ref: str) -> tuple[str, str]:
    """'LOINC:72166-2' → ('LOINC', '72166-2').  Bare code → ('Local', code)."""
    if ":" in ref:
        vocab, code = ref.split(":", 1)
        return vocab, code
    return "Local", ref


# ------------------------------------------------------------------
# Concept plane
# ------------------------------------------------------------------

def resolve_concept(conn: sqlite3.Connection, concept_ref: str | None) -> int | None:
    """Look up a concept by 'VOCAB:code'. Returns concept_id or None if not found."""
    if not concept_ref:
        return None
    vocab, code = _parse_concept_ref(concept_ref)
    row = conn.execute(
        "SELECT concept_id FROM concept WHERE vocabulary_id = ? AND concept_code = ?",
        (vocab, code),
    ).fetchone()
    return row[0] if row else None


def upsert_vocabulary(
    conn: sqlite3.Connection,
    vocabulary_id: str,
    vocabulary_name: str,
    vocabulary_reference: str | None = None,
    version: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO vocabulary (vocabulary_id, vocabulary_name, vocabulary_reference, version)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (vocabulary_id) DO NOTHING
        """,
        (vocabulary_id, vocabulary_name, vocabulary_reference, version),
    )


def upsert_concept(
    conn: sqlite3.Connection,
    concept_name: str,
    domain_id: str,
    vocabulary_id: str,
    concept_class_id: str,
    concept_code: str,
    standard_concept: str | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO concept
            (concept_name, domain_id, vocabulary_id, concept_class_id, concept_code, standard_concept)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (vocabulary_id, concept_code) DO NOTHING
        """,
        (concept_name, domain_id, vocabulary_id, concept_class_id, concept_code, standard_concept),
    )
    row = conn.execute(
        "SELECT concept_id FROM concept WHERE vocabulary_id = ? AND concept_code = ?",
        (vocabulary_id, concept_code),
    ).fetchone()
    return row[0]


def auto_upsert_local_concept(
    conn: sqlite3.Connection,
    name: str,
    domain_id: str = "Survey",
    concept_class_id: str = "Question",
) -> int:
    """
    Create a new Local concept with an OMOP-range code (2000000001+), or return
    the existing concept_id if one with this name already exists in the Local vocab.

    Codes are assigned sequentially within the OMOP local-concept range so they
    are stable, portable, and recognisable to OMOP-adjacent teams.  The Local
    vocabulary row is seeded automatically if it is not already present.
    """
    upsert_vocabulary(conn, "Local", "Local", "http://quickq.io/concepts/local")

    # Re-use if an identical (name, domain, class) Local concept already exists
    existing = conn.execute(
        """
        SELECT concept_id FROM concept
        WHERE vocabulary_id = 'Local' AND concept_name = ?
          AND domain_id = ? AND concept_class_id = ?
        LIMIT 1
        """,
        (name, domain_id, concept_class_id),
    ).fetchone()
    if existing:
        return existing["concept_id"]

    row = conn.execute(
        """
        SELECT MAX(CAST(concept_code AS INTEGER))
        FROM concept
        WHERE vocabulary_id = 'Local'
          AND concept_code GLOB '[0-9]*'
          AND CAST(concept_code AS INTEGER) >= 2000000001
        """
    ).fetchone()
    next_code = str((row[0] or 2000000000) + 1)
    return upsert_concept(conn, name, domain_id, "Local", concept_class_id, next_code)


def upsert_concept_relationship(
    conn: sqlite3.Connection,
    concept_id_1: int,
    concept_id_2: int,
    relationship_id: str,
) -> None:
    """
    Record a directed relationship between two concepts (e.g. 'Maps to').
    Both directions should be inserted separately per the OMOP convention.
    Idempotent: a second call with the same triple is a no-op.
    """
    conn.execute(
        """
        INSERT INTO concept_relationship (concept_id_1, concept_id_2, relationship_id)
        VALUES (?, ?, ?)
        ON CONFLICT (concept_id_1, concept_id_2, relationship_id) DO NOTHING
        """,
        (concept_id_1, concept_id_2, relationship_id),
    )


# ------------------------------------------------------------------
# Instrument plane
# ------------------------------------------------------------------

def insert_study(
    conn: sqlite3.Connection,
    name: str,
    description: str | None = None,
    principal_investigator: str | None = None,
    irb_number: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO study (name, description, principal_investigator, irb_number, start_date, end_date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, description, principal_investigator, irb_number, start_date, end_date),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_questionnaire(
    conn: sqlite3.Connection,
    defn: QuestionnaireDef,
    study_id: int | None = None,
) -> int:
    concept_id = resolve_concept(conn, None)  # questionnaire-level concept optional
    conn.execute(
        """
        INSERT INTO questionnaire
            (study_id, name, description, canonical_url, version, fhir_status, concept_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            study_id,
            defn.name,
            defn.description,
            defn.canonical_url,
            defn.version,
            defn.fhir_status,
            concept_id,
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def find_existing_questionnaire(
    conn: sqlite3.Connection,
    canonical_url: str | None,
    version: str,
) -> int | None:
    """Return the questionnaire_id of an existing row matching (canonical_url, version), or None."""
    if not canonical_url:
        return None
    row = conn.execute(
        "SELECT questionnaire_id FROM questionnaire WHERE canonical_url = ? AND version = ?",
        (canonical_url, version),
    ).fetchone()
    return row["questionnaire_id"] if row else None


def count_responses(conn: sqlite3.Connection, questionnaire_id: int) -> int:
    """Count response_session rows associated with a questionnaire."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM response_session WHERE questionnaire_id = ?",
        (questionnaire_id,),
    ).fetchone()["n"]


def clear_questionnaire_definition(conn: sqlite3.Connection, questionnaire_id: int) -> None:
    """
    Delete the definition rows that belong to a questionnaire (sections, placements,
    skip rules, scoring rules) so the questionnaire can be re-loaded under the same
    questionnaire_id. Does NOT touch responses, admin events, or errata logs.

    Caller must verify no responses exist (count_responses) before invoking, or
    the FK from response.qq_id will block the questionnaire_question delete.
    """
    qq_ids_subq = (
        "SELECT qq_id FROM questionnaire_question WHERE questionnaire_id = ?"
    )
    rule_ids_subq = (
        "SELECT scoring_rule_id FROM scoring_rule WHERE questionnaire_id = ?"
    )
    # Delete deepest-first to satisfy non-cascading FKs
    conn.execute(f"DELETE FROM scoring_rule_item WHERE scoring_rule_id IN ({rule_ids_subq})", (questionnaire_id,))
    conn.execute(f"DELETE FROM scoring_category  WHERE scoring_rule_id IN ({rule_ids_subq})", (questionnaire_id,))
    conn.execute("DELETE FROM scoring_rule  WHERE questionnaire_id = ?", (questionnaire_id,))
    conn.execute(f"DELETE FROM skip_rule         WHERE qq_id IN ({qq_ids_subq})", (questionnaire_id,))
    conn.execute(f"DELETE FROM data_quality_flag WHERE qq_id IN ({qq_ids_subq})", (questionnaire_id,))
    conn.execute("DELETE FROM questionnaire_question WHERE questionnaire_id = ?", (questionnaire_id,))
    conn.execute("DELETE FROM section              WHERE questionnaire_id = ?", (questionnaire_id,))


def update_questionnaire(
    conn: sqlite3.Connection,
    questionnaire_id: int,
    defn: QuestionnaireDef,
    study_id: int | None = None,
) -> None:
    """Update the metadata fields on an existing questionnaire row in place."""
    fields = "name = ?, description = ?, fhir_status = ?"
    params: list = [defn.name, defn.description, defn.fhir_status]
    if study_id is not None:
        fields += ", study_id = ?"
        params.append(study_id)
    params.append(questionnaire_id)
    conn.execute(f"UPDATE questionnaire SET {fields} WHERE questionnaire_id = ?", params)


def insert_section(
    conn: sqlite3.Connection,
    questionnaire_id: int,
    defn: SectionDef,
    display_order: int = 0,
) -> int:
    conn.execute(
        """
        INSERT INTO section (questionnaire_id, title, description, display_order)
        VALUES (?, ?, ?, ?)
        """,
        (questionnaire_id, defn.title, defn.description, display_order),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def upsert_option_set(conn: sqlite3.Connection, name: str, canonical_url: str | None = None) -> int:
    conn.execute(
        """
        INSERT INTO response_option_set (name, canonical_url)
        VALUES (?, ?)
        ON CONFLICT (name) DO NOTHING
        """,
        (name, canonical_url),
    )
    return conn.execute(
        "SELECT option_set_id FROM response_option_set WHERE name = ?", (name,)
    ).fetchone()[0]


def _warn_unresolved_concept(
    conn: sqlite3.Connection,
    link_id: str,
    concept_ref: str,
) -> None:
    """
    Warn only when the vocabulary is seeded but the specific code is missing.
    Silent when the vocabulary hasn't been loaded at all — that's expected in
    lightweight setups and assign-first workflows.
    """
    import warnings
    try:
        vocab, code = _parse_concept_ref(concept_ref)
    except Exception:
        return
    vocab_exists = conn.execute(
        "SELECT 1 FROM vocabulary WHERE vocabulary_id = ?", (vocab,)
    ).fetchone()
    if vocab_exists:
        warnings.warn(
            f"concept '{concept_ref}' not found in {vocab} vocabulary — "
            f"'{link_id}' loaded as unmapped. "
            f"Add the missing concept or remove the concept field.",
            stacklevel=5,
        )


def _warn_concept_collision(
    conn: sqlite3.Connection,
    new_link_id: str,
    concept_ref: str,
) -> None:
    """Emit a warning if concept_ref is already mapped to a different link_id."""
    import warnings
    try:
        vocab, code = _parse_concept_ref(concept_ref)
    except Exception:
        return
    row = conn.execute(
        """
        SELECT q.link_id FROM question q
        JOIN concept c ON q.concept_id = c.concept_id
        WHERE c.vocabulary_id = ? AND c.concept_code = ?
          AND q.link_id != ?
        LIMIT 1
        """,
        (vocab, code, new_link_id),
    ).fetchone()
    if row:
        warnings.warn(
            f"{concept_ref} is already mapped to '{row['link_id']}'. "
            f"Consider using '{{{{ library: {row['link_id']} }}}}' instead of "
            f"authoring a new question.",
            stacklevel=4,
        )


def upsert_question(
    conn: sqlite3.Connection,
    defn: QuestionDef,
    strict_concepts: bool = True,
    auto_concept: bool = False,
) -> int:
    """
    Insert a question if its link_id does not exist; otherwise return the existing id.

    Raises ValueError if the existing question text differs from defn.text — questions
    are immutable once created.  To intentionally revise a question, create a new
    question with a new link_id and record the change via record_question_lineage().

    When strict_concepts=True (default), emits a warning if the question's concept
    code is already mapped to a different link_id in this database.

    When auto_concept=True and no concept is specified, creates a Local concept with
    an OMOP-range code (2000000001+) so every question carries a stable identifier.
    """
    existing = conn.execute(
        "SELECT question_id, question_text FROM question WHERE link_id = ?",
        (defn.link_id,),
    ).fetchone()
    if existing:
        if defn.text and existing["question_text"] != defn.text:
            raise ValueError(
                f"Question '{defn.link_id}' already exists with different text.\n"
                f"  Existing : {existing['question_text']!r}\n"
                f"  Provided : {defn.text!r}\n"
                f"Questions are immutable. Create a new link_id and call "
                f"record_question_lineage() to link it to this one."
            )
        return existing["question_id"]

    if defn.concept_id is not None:
        concept_id: int | None = defn.concept_id
    elif defn.concept:
        if strict_concepts:
            _warn_concept_collision(conn, defn.link_id, defn.concept)
        concept_id = resolve_concept(conn, defn.concept)
        if concept_id is None:
            _warn_unresolved_concept(conn, defn.link_id, defn.concept)
    elif auto_concept:
        concept_id = auto_upsert_local_concept(conn, defn.text, "Survey", "Question")
    else:
        concept_id = None
    conn.execute(
        """
        INSERT INTO question (
            link_id, question_text, question_type, help_text, concept_id,
            source_instrument, source_item_id, citation,
            numeric_min, numeric_max, numeric_step,
            slider_min_label, slider_max_label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            defn.link_id, defn.text, defn.type, defn.help_text, concept_id,
            defn.source_instrument, defn.source_item_id, defn.citation,
            defn.numeric_min, defn.numeric_max, defn.numeric_step,
            defn.slider_min_label, defn.slider_max_label,
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_options(
    conn: sqlite3.Connection,
    question_id: int,
    options: list[OptionDef],
    option_set_id: int | None = None,
    auto_concept: bool = False,
) -> dict[str, int]:
    """Insert response options. Returns {option_value: option_id}."""
    result: dict[str, int] = {}
    for i, opt in enumerate(options):
        concept_code: str | None = None
        concept_system: str | None = None

        if opt.concept_id is not None:
            concept_id: int | None = opt.concept_id
            row = conn.execute(
                "SELECT vocabulary_id, concept_code FROM concept WHERE concept_id = ?",
                (opt.concept_id,),
            ).fetchone()
            if row:
                concept_code = row["concept_code"]
                concept_system = _vocab_system(row["vocabulary_id"])
        elif opt.concept:
            concept_id = resolve_concept(conn, opt.concept)
            vocab, code = _parse_concept_ref(opt.concept)
            concept_code = code
            concept_system = _vocab_system(vocab)
            if concept_id is None:
                _warn_unresolved_concept(conn, f"option:{opt.value}", opt.concept)
        elif auto_concept:
            concept_id = auto_upsert_local_concept(conn, opt.text, "Meas Value", "Answer")
            concept_code = conn.execute(
                "SELECT concept_code FROM concept WHERE concept_id = ?", (concept_id,)
            ).fetchone()["concept_code"]
            concept_system = _vocab_system("Local")
        else:
            concept_id = None

        conn.execute(
            """
            INSERT INTO response_option (
                question_id, option_set_id, option_text, option_value, display_order,
                concept_id, concept_code, concept_system, is_other, is_exclusive
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id, option_set_id, opt.text, opt.value, i,
                concept_id, concept_code, concept_system,
                int(opt.is_other), int(opt.is_exclusive),
            ),
        )
        result[opt.value] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return result


def insert_grid_rows_columns(
    conn: sqlite3.Connection,
    question_id: int,
    rows: list[GridRowDef],
    columns: list[GridColumnDef],
    auto_concept: bool = False,
) -> None:
    """Insert grid row and column definitions. Idempotent — skips if already present."""
    if conn.execute(
        "SELECT COUNT(*) FROM grid_row WHERE question_id = ?", (question_id,)
    ).fetchone()[0]:
        return
    for i, row in enumerate(rows):
        if row.concept_id is not None:
            row_concept_id: int | None = row.concept_id
        elif row.concept:
            row_concept_id = resolve_concept(conn, row.concept)
            if row_concept_id is None:
                _warn_unresolved_concept(conn, f"grid_row:{row.text}", row.concept)
        elif auto_concept:
            row_concept_id = auto_upsert_local_concept(conn, row.text, "Survey", "Survey")
        else:
            row_concept_id = None
        conn.execute(
            "INSERT INTO grid_row (question_id, row_text, display_order, concept_id) VALUES (?, ?, ?, ?)",
            (question_id, row.text, i, row_concept_id),
        )
    for i, col in enumerate(columns):
        if col.concept_id is not None:
            col_concept_id: int | None = col.concept_id
        elif col.concept:
            col_concept_id = resolve_concept(conn, col.concept)
            if col_concept_id is None:
                _warn_unresolved_concept(conn, f"grid_col:{col.text}", col.concept)
        elif auto_concept:
            col_concept_id = auto_upsert_local_concept(conn, col.text, "Survey", "Survey")
        else:
            col_concept_id = None
        conn.execute(
            """
            INSERT INTO grid_column (question_id, column_text, column_value, column_type, display_order, concept_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (question_id, col.text, col.value, col.column_type, i, col_concept_id),
        )


def place_question(
    conn: sqlite3.Connection,
    questionnaire_id: int,
    question_id: int,
    display_order: int,
    section_id: int | None = None,
    required: bool = False,
    parent_qq_id: int | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO questionnaire_question
            (questionnaire_id, section_id, question_id, display_order, is_required, parent_qq_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (questionnaire_id, section_id, question_id, display_order, int(required), parent_qq_id),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_skip_rule(
    conn: sqlite3.Connection,
    qq_id: int,
    trigger_qq_id: int,
    operator: str,
    trigger_value: str | None,
    enable_behavior: str = "all",
    action: str = "show",
) -> int:
    conn.execute(
        """
        INSERT INTO skip_rule (qq_id, enable_behavior, trigger_qq_id, operator, trigger_value, action)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (qq_id, enable_behavior, trigger_qq_id, operator, trigger_value, action),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_scoring_rule(
    conn: sqlite3.Connection,
    questionnaire_id: int,
    defn: ScoringRuleDef,
) -> int:
    conn.execute(
        """
        INSERT INTO scoring_rule (questionnaire_id, name, description, formula)
        VALUES (?, ?, ?, ?)
        """,
        (questionnaire_id, defn.name, defn.description, defn.formula),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_scoring_rule_item(
    conn: sqlite3.Connection,
    scoring_rule_id: int,
    qq_id: int,
    weight: float = 1.0,
    reverse_score: bool = False,
) -> int:
    conn.execute(
        """
        INSERT INTO scoring_rule_item (scoring_rule_id, qq_id, weight, reverse_score)
        VALUES (?, ?, ?, ?)
        """,
        (scoring_rule_id, qq_id, weight, int(reverse_score)),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_scoring_category(
    conn: sqlite3.Connection,
    scoring_rule_id: int,
    defn: ScoringCategoryDef,
    display_order: int = 0,
) -> int:
    conn.execute(
        """
        INSERT INTO scoring_category (scoring_rule_id, label, min_score, max_score, display_order)
        VALUES (?, ?, ?, ?, ?)
        """,
        (scoring_rule_id, defn.label, defn.min_score, defn.max_score, display_order),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
