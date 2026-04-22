"""
FHIR R4 Questionnaire export.

Produces a dict that conforms to the HL7 FHIR R4 Questionnaire resource.
Non-FHIR fields (internal_note, source provenance, scoring rules) are
serialized as extensions under https://quickq.io/fhir/StructureDefinition.

Reference: https://hl7.org/fhir/R4/questionnaire.html
"""
from __future__ import annotations

import json
import re
import sqlite3


_FHIR_TYPE: dict[str, str] = {
    "single_choice":   "choice",
    "multiple_choice": "choice",
    "sata_other":      "open-choice",
    "boolean":         "boolean",
    "text":            "text",
    "numeric":         "decimal",
    "date":            "date",
    "datetime":        "dateTime",
    "likert":          "choice",
    "grid":            "group",
    "ranked":          "choice",
    "slider":          "integer",
}

_REPEATS_TYPES = frozenset({"multiple_choice", "sata_other"})
_CHOICE_TYPES  = frozenset({"single_choice", "multiple_choice", "sata_other", "likert", "ranked"})

_EXT = "https://quickq.io/fhir/StructureDefinition"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def export_fhir(conn: sqlite3.Connection, questionnaire_id: int) -> dict:
    """
    Return a FHIR R4 Questionnaire resource as a Python dict.

    Raises ValueError if questionnaire_id does not exist.
    """
    row = conn.execute(
        "SELECT * FROM questionnaire WHERE questionnaire_id = ?",
        (questionnaire_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Questionnaire {questionnaire_id} not found")

    resource: dict = {
        "resourceType": "Questionnaire",
        "status": row["fhir_status"],
        "title": row["name"],
        "version": row["version"],
    }

    if row["canonical_url"]:
        resource["url"] = row["canonical_url"]
    if row["description"]:
        resource["description"] = row["description"]
    if row["created_at"]:
        resource["date"] = row["created_at"]

    # FHIR `name` must be a valid identifier (no spaces)
    machine_name = re.sub(r"[^A-Za-z0-9]", "", row["name"])
    if machine_name:
        resource["name"] = machine_name

    scoring_exts = _scoring_extensions(conn, questionnaire_id)
    if scoring_exts:
        resource["extension"] = scoring_exts

    items = _build_items(conn, questionnaire_id)
    if items:
        resource["item"] = items

    return resource


def export_fhir_json(conn: sqlite3.Connection, questionnaire_id: int, *, indent: int = 2) -> str:
    """Return the FHIR Questionnaire resource as a JSON string."""
    return json.dumps(export_fhir(conn, questionnaire_id), indent=indent)


# ------------------------------------------------------------------
# Item assembly
# ------------------------------------------------------------------

def _build_items(conn: sqlite3.Connection, questionnaire_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT qq.qq_id, qq.display_order, qq.is_required,
               q.question_id, q.link_id, q.question_text, q.question_type,
               q.help_text, q.internal_note, q.option_set_id,
               q.source_instrument, q.source_item_id,
               q.numeric_min, q.numeric_max, q.numeric_step,
               q.slider_min_label, q.slider_max_label,
               ros.canonical_url AS option_set_url
        FROM questionnaire_question qq
        JOIN question q ON qq.question_id = q.question_id
        LEFT JOIN response_option_set ros ON q.option_set_id = ros.option_set_id
        WHERE qq.questionnaire_id = ?
        ORDER BY qq.display_order
        """,
        (questionnaire_id,),
    ).fetchall()
    return [_build_item(conn, r) for r in rows]


def _build_item(conn: sqlite3.Connection, row) -> dict:
    qtype = row["question_type"]
    item: dict = {
        "linkId": row["link_id"],
        "text":   row["question_text"],
        "type":   _FHIR_TYPE.get(qtype, "string"),
    }

    if row["is_required"]:
        item["required"] = True
    if qtype in _REPEATS_TYPES:
        item["repeats"] = True

    # Answer options / value set
    if qtype in _CHOICE_TYPES:
        if row["option_set_url"]:
            item["answerValueSet"] = row["option_set_url"]
        else:
            opts = _answer_options(conn, row["question_id"])
            if opts:
                item["answerOption"] = opts

    # Numeric / slider constraints (SDC minValue / maxValue)
    if qtype in ("numeric", "slider"):
        ext_buf: list[dict] = item.setdefault("extension", [])
        value_key = "valueInteger" if qtype == "slider" else "valueDecimal"
        if row["numeric_min"] is not None:
            ext_buf.append({
                "url": "http://hl7.org/fhir/StructureDefinition/minValue",
                value_key: int(row["numeric_min"]) if qtype == "slider" else row["numeric_min"],
            })
        if row["numeric_max"] is not None:
            ext_buf.append({
                "url": "http://hl7.org/fhir/StructureDefinition/maxValue",
                value_key: int(row["numeric_max"]) if qtype == "slider" else row["numeric_max"],
            })
        if row["numeric_step"] is not None:
            ext_buf.append({
                "url": f"{_EXT}/numeric-step",
                "valueDecimal": row["numeric_step"],
            })
    if qtype == "slider":
        ext_buf = item.setdefault("extension", [])
        # FHIR SDC rendering hint — SDC-compliant tools render this as a slider;
        # LHC-Forms falls back to a validated integer text input (slider not supported).
        ext_buf.append({
            "url": "http://hl7.org/fhir/StructureDefinition/questionnaire-itemControl",
            "valueCodeableConcept": {
                "coding": [{"system": "http://hl7.org/fhir/questionnaire-item-control",
                             "code": "slider"}]
            },
        })
        if row["slider_min_label"]:
            ext_buf.append({"url": f"{_EXT}/slider-min-label", "valueString": row["slider_min_label"]})
        if row["slider_max_label"]:
            ext_buf.append({"url": f"{_EXT}/slider-max-label", "valueString": row["slider_max_label"]})

    # Skip logic
    enable_when = _enable_when(conn, row["qq_id"])
    if enable_when:
        item["enableWhen"] = enable_when
        if len(enable_when) > 1:
            b = conn.execute(
                "SELECT enable_behavior FROM skip_rule WHERE qq_id = ? LIMIT 1",
                (row["qq_id"],),
            ).fetchone()
            if b:
                item["enableBehavior"] = b["enable_behavior"]

    # Non-FHIR fields as extensions
    ext_fields: list[dict] = []
    if row["help_text"]:
        ext_fields.append({"url": f"{_EXT}/help-text",         "valueString": row["help_text"]})
    if row["internal_note"]:
        ext_fields.append({"url": f"{_EXT}/internal-note",     "valueString": row["internal_note"]})
    if row["source_instrument"]:
        ext_fields.append({"url": f"{_EXT}/source-instrument", "valueString": row["source_instrument"]})
    if row["source_item_id"]:
        ext_fields.append({"url": f"{_EXT}/source-item-id",    "valueString": row["source_item_id"]})
    if ext_fields:
        item.setdefault("extension", []).extend(ext_fields)

    return item


def _answer_options(conn: sqlite3.Connection, question_id: int) -> list[dict]:
    opts = conn.execute(
        """
        SELECT option_text, option_value, concept_code, concept_system
        FROM response_option
        WHERE question_id = ?
        ORDER BY display_order
        """,
        (question_id,),
    ).fetchall()

    result = []
    for opt in opts:
        coding: dict = {"display": opt["option_text"]}
        coding["code"] = opt["concept_code"] if opt["concept_code"] else opt["option_value"]
        if opt["concept_system"]:
            coding["system"] = opt["concept_system"]
        result.append({"valueCoding": coding})
    return result


def _enable_when(conn: sqlite3.Connection, qq_id: int) -> list[dict]:
    # Fetch rules with trigger question type and matching option coding so we
    # can emit the correct FHIR answer[x] type (FHIR R4 §10.6.5).
    rules = conn.execute(
        """
        SELECT sr.operator, sr.trigger_value,
               tq.link_id        AS trigger_link_id,
               tq.question_type  AS trigger_type,
               ro.concept_code   AS option_code,
               ro.concept_system AS option_system
        FROM skip_rule sr
        JOIN questionnaire_question tqq ON sr.trigger_qq_id = tqq.qq_id
        JOIN question tq ON tqq.question_id = tq.question_id
        LEFT JOIN response_option ro
               ON ro.question_id = tq.question_id
              AND ro.option_value = sr.trigger_value
        WHERE sr.qq_id = ?
        """,
        (qq_id,),
    ).fetchall()

    result = []
    for r in rules:
        op  = r["operator"]
        ew: dict = {"question": r["trigger_link_id"]}

        if op == "not_exists":
            ew["operator"] = "exists"
            ew["answerBoolean"] = False
        elif op == "exists":
            ew["operator"] = "exists"
            ew["answerBoolean"] = True
        else:
            ew["operator"] = op
            val = r["trigger_value"]
            if val is not None:
                if r["trigger_type"] in _CHOICE_TYPES:
                    # Use answerCoding so LHC-Forms (and any FHIR validator)
                    # can compare like-for-like against the rendered valueCoding.
                    coding: dict = {}
                    if r["option_code"]:
                        coding["code"] = r["option_code"]
                        if r["option_system"]:
                            coding["system"] = r["option_system"]
                    else:
                        coding["code"] = val
                    ew["answerCoding"] = coding
                else:
                    try:
                        ew["answerDecimal"] = float(val)
                    except (ValueError, TypeError):
                        ew["answerString"] = val

        result.append(ew)
    return result


def _scoring_extensions(conn: sqlite3.Connection, questionnaire_id: int) -> list[dict]:
    rules = conn.execute(
        "SELECT * FROM scoring_rule WHERE questionnaire_id = ?",
        (questionnaire_id,),
    ).fetchall()
    if not rules:
        return []

    exts = []
    for rule in rules:
        inner = [
            {"url": "name",    "valueString": rule["name"]},
            {"url": "formula", "valueString": rule["formula"]},
        ]
        if rule["description"]:
            inner.append({"url": "description", "valueString": rule["description"]})
        exts.append({"url": f"{_EXT}/scoring-rule", "extension": inner})
    return exts
