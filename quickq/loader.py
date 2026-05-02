"""
YAML questionnaire compiler.

Parses a .yaml definition file into a QuestionnaireDef, then writes it
to the OLTP database in a single transaction.  Two-pass approach:

  Pass 1 — insert all questions and placements; build link_id → qq_id map.
  Pass 2 — insert skip_rule rows using the resolved qq_ids.
  Pass 3 — insert scoring rules and categories.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

from .models import (
    OptionDef, QuestionDef, SectionDef,
    SkipCondition, ShowWhen,
    ScoringRuleDef, ScoringCategoryDef,
    QuestionnaireDef, GridRowDef, GridColumnDef,
)
from .authoring import (
    upsert_option_set, upsert_question, insert_options,
    insert_grid_rows_columns,
    insert_questionnaire, insert_section, place_question,
    insert_skip_rule, insert_scoring_rule,
    insert_scoring_rule_item, insert_scoring_category,
)


# ------------------------------------------------------------------
# YAML → dataclass parsers
# ------------------------------------------------------------------

def _parse_option(raw: dict) -> OptionDef:
    return OptionDef(
        text=raw["text"],
        value=str(raw["value"]),
        concept=raw.get("concept"),
        concept_id=int(raw["concept_id"]) if raw.get("concept_id") is not None else None,
        is_other=bool(raw.get("is_other", False)),
        is_exclusive=bool(raw.get("is_exclusive", False)),
    )


def _parse_show_when(raw: Any) -> ShowWhen:
    """
    Accepts two shapes:

      Single condition (shorthand):
        show_when:
          question: q1
          operator: "="
          value: "yes"

      Multi-condition:
        show_when:
          behavior: all
          conditions:
            - { question: q1, operator: "=", value: "yes" }
    """
    if "conditions" in raw:
        conditions = [
            SkipCondition(
                question=c["question"],
                operator=c.get("operator", "="),
                value=str(c["value"]) if "value" in c else None,
            )
            for c in raw["conditions"]
        ]
        return ShowWhen(conditions=conditions, behavior=raw.get("behavior", "all"))

    return ShowWhen(
        conditions=[
            SkipCondition(
                question=raw["question"],
                operator=raw.get("operator", "="),
                value=str(raw["value"]) if "value" in raw else None,
            )
        ],
        behavior="all",
    )


def _parse_question(raw: dict) -> QuestionDef:
    # Shorthand: { library: phq9.1 } — resolved against DB at load time
    if "library" in raw and len(raw) == 1:
        return QuestionDef(library_ref=raw["library"])

    options: list[OptionDef] | None = None
    option_set: str | None = raw.get("option_set") or raw.get("options_from")

    raw_options = raw.get("options")
    if isinstance(raw_options, str) and raw_options.startswith("$"):
        option_set = raw_options[1:]
    elif isinstance(raw_options, list):
        options = [_parse_option(o) for o in raw_options]

    show_when: ShowWhen | None = None
    if "show_when" in raw:
        show_when = _parse_show_when(raw["show_when"])

    range_ = raw.get("range")

    return QuestionDef(
        link_id=raw["link_id"],
        text=raw["text"],
        type=raw["type"],
        help_text=raw.get("help_text"),
        concept=raw.get("concept"),
        concept_id=int(raw["concept_id"]) if raw.get("concept_id") is not None else None,
        options=options,
        option_set=option_set,
        show_when=show_when,
        required=bool(raw.get("required", False)),
        source_instrument=raw.get("source_instrument"),
        source_item_id=raw.get("source_item_id"),
        citation=raw.get("citation"),
        numeric_min=float(range_[0]) if range_ else raw.get("numeric_min"),
        numeric_max=float(range_[1]) if range_ else raw.get("numeric_max"),
        numeric_step=raw.get("numeric_step"),
        slider_min_label=raw.get("slider_min_label"),
        slider_max_label=raw.get("slider_max_label"),
        rows=[
            GridRowDef(
                text=r["text"],
                concept=r.get("concept"),
                concept_id=int(r["concept_id"]) if r.get("concept_id") is not None else None,
            )
            for r in raw["rows"]
        ] if raw.get("rows") else None,
        columns=[
            GridColumnDef(
                text=c["text"],
                value=str(c["value"]) if "value" in c else None,
                column_type=c.get("column_type", "single_choice"),
                concept=c.get("concept"),
                concept_id=int(c["concept_id"]) if c.get("concept_id") is not None else None,
            )
            for c in raw["columns"]
        ] if raw.get("columns") else None,
        items=[_parse_question(q) for q in raw["items"]] if raw.get("items") else None,
    )


def _parse_section(raw: dict) -> SectionDef:
    return SectionDef(
        title=raw.get("title"),
        description=raw.get("description"),
        questions=[_parse_question(q) for q in raw.get("questions", [])],
    )


def _parse_scoring(raw: dict) -> ScoringRuleDef:
    categories = [
        ScoringCategoryDef(
            label=c["label"],
            min_score=c.get("min"),
            max_score=c.get("max"),
        )
        for c in raw.get("categories", [])
    ]
    return ScoringRuleDef(
        name=raw["name"],
        formula=str(raw.get("formula", "sum")),
        items=list(raw.get("items", [])),
        categories=categories,
        description=raw.get("description"),
    )


def parse_questionnaire_def(raw: dict) -> QuestionnaireDef:
    """Parse the top-level YAML dict into a QuestionnaireDef."""
    body = raw.get("questionnaire", raw)   # allow with or without top-level key

    option_sets: dict[str, list[OptionDef]] = {}
    for name, opts in body.get("option_sets", {}).items():
        option_sets[name] = [_parse_option(o) for o in opts]

    sections = [_parse_section(s) for s in body.get("sections", [])]

    # flat questions list (no sections) is also valid
    if not sections and "questions" in body:
        sections = [SectionDef(questions=[_parse_question(q) for q in body["questions"]])]

    scoring = [_parse_scoring(s) for s in body.get("scoring", [])]

    return QuestionnaireDef(
        name=body["name"],
        version=str(body.get("version", "1.0")),
        canonical_url=body.get("canonical_url"),
        description=body.get("description"),
        fhir_status=body.get("fhir_status", "draft"),
        option_sets=option_sets,
        sections=sections,
        scoring=scoring,
    )


# ------------------------------------------------------------------
# Compiler
# ------------------------------------------------------------------

def load_yaml(
    conn: sqlite3.Connection,
    path: str | Path,
    study_id: int | None = None,
    strict_concepts: bool = True,
    auto_concept: bool = False,
) -> int:
    """
    Compile a YAML questionnaire definition and write it to the database.
    Returns the new questionnaire_id.  Runs in a single transaction.
    """
    raw = yaml.safe_load(Path(path).read_text())
    defn = parse_questionnaire_def(raw)
    return load_def(conn, defn, study_id=study_id, strict_concepts=strict_concepts,
                    auto_concept=auto_concept)


def load_def(
    conn: sqlite3.Connection,
    defn: QuestionnaireDef,
    study_id: int | None = None,
    strict_concepts: bool = True,
    auto_concept: bool = False,
) -> int:
    """
    Write a QuestionnaireDef to the database.  Returns questionnaire_id.
    """
    with conn:
        questionnaire_id = insert_questionnaire(conn, defn, study_id)

        # Register option_sets so they can be referenced by name in questions
        set_ids: dict[str, int] = {}
        for name in defn.option_sets:
            set_ids[name] = upsert_option_set(conn, name)

        link_id_to_qq_id: dict[str, int] = {}
        # (qq_id, ShowWhen) pairs deferred until all questions are placed
        pending_skip: list[tuple[int, ShowWhen]] = []

        global_order = 0
        for sec_order, section_def in enumerate(defn.sections):
            section_id: int | None = None
            if section_def.title or section_def.description:
                section_id = insert_section(conn, questionnaire_id, section_def, sec_order)

            for q_def in section_def.questions:
                q_order = global_order
                global_order += 1
                # Library reference: look up an existing question by link_id
                if q_def.library_ref:
                    row = conn.execute(
                        "SELECT question_id, link_id FROM question WHERE link_id = ?",
                        (q_def.library_ref,),
                    ).fetchone()
                    if row is None:
                        raise ValueError(
                            f"Library question '{q_def.library_ref}' not found. "
                            f"Run: quickq init --with-library"
                        )
                    question_id = row["question_id"]
                    effective_link_id = row["link_id"]
                    # skip option insertion — library question already has options
                else:
                    question_id = upsert_question(conn, q_def, strict_concepts=strict_concepts,
                                                  auto_concept=auto_concept)
                    effective_link_id = q_def.link_id

                    # Resolve options: inline list or shared option_set reference
                    effective_options: list[OptionDef] | None = q_def.options
                    effective_set_id: int | None = None

                    if q_def.option_set:
                        set_name = q_def.option_set
                        if set_name not in defn.option_sets:
                            raise ValueError(
                                f"Question '{q_def.link_id}' references unknown option_set '{set_name}'"
                            )
                        effective_options = defn.option_sets[set_name]
                        effective_set_id = set_ids[set_name]

                    if effective_options:
                        insert_options(conn, question_id, effective_options, effective_set_id,
                                       auto_concept=auto_concept)

                    if q_def.rows and q_def.columns:
                        insert_grid_rows_columns(conn, question_id, q_def.rows, q_def.columns,
                                                 auto_concept=auto_concept)

                qq_id = place_question(
                    conn,
                    questionnaire_id=questionnaire_id,
                    question_id=question_id,
                    display_order=q_order,
                    section_id=section_id,
                    required=q_def.required,
                )
                link_id_to_qq_id[effective_link_id] = qq_id

                if q_def.show_when:
                    pending_skip.append((qq_id, q_def.show_when))

                # Repeating group: place child questions with parent_qq_id
                if q_def.type == "repeating_group" and q_def.items:
                    for child_order, child_def in enumerate(q_def.items):
                        child_q_id = upsert_question(conn, child_def, auto_concept=auto_concept)
                        if child_def.options:
                            insert_options(conn, child_q_id, child_def.options, None,
                                           auto_concept=auto_concept)
                        child_qq_id = place_question(
                            conn,
                            questionnaire_id=questionnaire_id,
                            question_id=child_q_id,
                            display_order=child_order,
                            section_id=section_id,
                            required=child_def.required,
                            parent_qq_id=qq_id,
                        )
                        link_id_to_qq_id[child_def.link_id] = child_qq_id
                        if child_def.show_when:
                            pending_skip.append((child_qq_id, child_def.show_when))

        # Pass 2: resolve skip rules now that all link_ids are mapped
        for qq_id, show_when in pending_skip:
            for condition in show_when.conditions:
                trigger_qq_id = link_id_to_qq_id.get(condition.question)
                if trigger_qq_id is None:
                    raise ValueError(
                        f"skip rule references unknown link_id '{condition.question}'"
                    )
                insert_skip_rule(
                    conn,
                    qq_id=qq_id,
                    trigger_qq_id=trigger_qq_id,
                    operator=condition.operator,
                    trigger_value=condition.value,
                    enable_behavior=show_when.behavior,
                )

        # Pass 3: scoring rules
        for rule_def in defn.scoring:
            scoring_rule_id = insert_scoring_rule(conn, questionnaire_id, rule_def)
            for cat_order, cat in enumerate(rule_def.categories):
                insert_scoring_category(conn, scoring_rule_id, cat, cat_order)
            for item_link_id in rule_def.items:
                qq_id = link_id_to_qq_id.get(item_link_id)
                if qq_id is None:
                    raise ValueError(
                        f"scoring rule '{rule_def.name}' references unknown link_id '{item_link_id}'"
                    )
                insert_scoring_rule_item(conn, scoring_rule_id, qq_id)

    return questionnaire_id
