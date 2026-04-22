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


def upsert_question(conn: sqlite3.Connection, defn: QuestionDef) -> int:
    """
    Insert a question if its link_id does not exist; otherwise return the existing id.

    Raises ValueError if the existing question text differs from defn.text — questions
    are immutable once created.  To intentionally revise a question, create a new
    question with a new link_id and record the change via record_question_lineage().
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

    concept_id = resolve_concept(conn, defn.concept)
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
) -> dict[str, int]:
    """Insert response options. Returns {option_value: option_id}."""
    result: dict[str, int] = {}
    for i, opt in enumerate(options):
        concept_id = resolve_concept(conn, opt.concept)
        concept_code: str | None = None
        concept_system: str | None = None
        if opt.concept:
            vocab, code = _parse_concept_ref(opt.concept)
            concept_code = code
            concept_system = _vocab_system(vocab)

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
