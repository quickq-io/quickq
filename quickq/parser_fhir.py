"""
FHIR R4 Questionnaire import.

Parses a FHIR R4 Questionnaire resource (dict or JSON string) and writes it
into the OLTP database, returning the questionnaire_id.

Idempotent: a second import of the same canonical_url + version is a no-op.

Mapping notes
-------------
FHIR item.type → question_type:
  choice            → single_choice  (repeats=true → multiple_choice)
  open-choice       → sata_other
  boolean           → boolean
  text / string     → text
  decimal / integer → numeric
  date              → date
  dateTime          → datetime
  group             → flattened (sub-items are promoted to questionnaire level)
  display           → skipped

enableWhen:
  operator=exists, answerBoolean=true  → operator=exists
  operator=exists, answerBoolean=false → operator=not_exists
  other operators with answerDecimal / answerInteger → numeric trigger_value
  other operators with answerString / answerCoding   → string trigger_value

Non-FHIR extensions under https://quickq.io/fhir/StructureDefinition are
round-tripped: help-text, internal-note, source-instrument, source-item-id.
"""
from __future__ import annotations

import json
import sqlite3

from .authoring import upsert_question, place_question
from .models import QuestionDef


_EXT = "https://quickq.io/fhir/StructureDefinition"

_FHIR_TO_QTYPE: dict[str, str] = {
    "choice":     "single_choice",
    "open-choice": "sata_other",
    "boolean":    "boolean",
    "text":       "text",
    "string":     "text",
    "decimal":    "numeric",
    "integer":    "numeric",
    "date":       "date",
    "dateTime":   "datetime",
    "time":       "text",
    "url":        "text",
    "quantity":   "numeric",
}

_SKIP_TYPES = frozenset({"display", "reference", "attachment"})


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def import_fhir(conn: sqlite3.Connection, source: str | dict) -> int:
    """
    Import a FHIR R4 Questionnaire resource into the OLTP database.

    source can be a JSON string or a pre-parsed dict.
    Returns the questionnaire_id (existing or newly created).
    Raises ValueError for invalid resources or unresolvable conflicts.
    """
    res: dict = json.loads(source) if isinstance(source, str) else dict(source)

    if res.get("resourceType") != "Questionnaire":
        raise ValueError(
            f"Expected resourceType 'Questionnaire', got {res.get('resourceType')!r}"
        )

    url     = res.get("url")
    version = res.get("version", "1.0")
    title   = res.get("title") or res.get("name") or "Imported Questionnaire"
    status  = res.get("status", "unknown")
    desc    = res.get("description")

    # Idempotency: return existing questionnaire if url+version already loaded
    if url:
        row = conn.execute(
            "SELECT questionnaire_id FROM questionnaire WHERE canonical_url=? AND version=?",
            (url, version),
        ).fetchone()
        if row:
            return row[0]

    conn.execute(
        """
        INSERT INTO questionnaire (name, canonical_url, version, fhir_status, description)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, url, version, status, desc),
    )
    qnaire_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Top-level scoring rule extensions
    for ext in res.get("extension", []):
        if ext.get("url") == f"{_EXT}/scoring-rule":
            _import_scoring_rule(conn, qnaire_id, ext)

    # Collect flat item list (groups are flattened, display items are skipped)
    flat = _collect_items(res.get("item", []))

    # Pass 1 — create questions, placements, and answer options
    link_id_to_qq: dict[str, int] = {}
    deferred_rules: list[tuple[int, list[dict], str]] = []  # (qq_id, enableWhen[], behavior)
    deferred_counts: list[tuple[int, str]] = []              # (group_qq_id, count_link_id)

    for order, item in enumerate(flat):
        link_id = item.get("linkId")
        if not link_id:
            continue

        qq_id = _import_item(conn, qnaire_id, item, order)
        link_id_to_qq[link_id] = qq_id

        enable_when = item.get("enableWhen", [])
        if enable_when:
            behavior = item.get("enableBehavior", "all")
            deferred_rules.append((qq_id, enable_when, behavior))

        # Count-driven repetition (custom quickq extension): a `group` item
        # carrying our count-from extension names the link_id of the numeric
        # question whose answer drives N. We resolve the linkage in pass 2
        # because the count question may appear later in the document.
        if item.get("type") == "group":
            for ext in item.get("extension", []):
                if ext.get("url") == f"{_EXT}/count-from":
                    val = ext.get("valueString")
                    if val:
                        deferred_counts.append((qq_id, val))

    # Pass 2 — insert skip rules now that all link_ids are resolved
    for qq_id, enable_when_list, behavior in deferred_rules:
        for ew in enable_when_list:
            trigger_link = ew.get("question")
            trigger_qq = link_id_to_qq.get(trigger_link)
            if trigger_qq is None:
                continue  # unresolvable reference — skip silently
            _insert_skip_rule(conn, qq_id, trigger_qq, ew, behavior)

    # Pass 2b — resolve count_qq_id linkages now that all link_ids are mapped
    for group_qq_id, count_link_id in deferred_counts:
        count_qq_id = link_id_to_qq.get(count_link_id)
        if count_qq_id is None:
            continue  # unresolvable; skip silently to match enableWhen behaviour
        conn.execute(
            "UPDATE questionnaire_question SET count_qq_id = ? WHERE qq_id = ?",
            (count_qq_id, group_qq_id),
        )

    conn.commit()
    return qnaire_id


# ------------------------------------------------------------------
# Item processing
# ------------------------------------------------------------------

def _collect_items(items: list[dict]) -> list[dict]:
    """
    Flatten the FHIR item tree. Non-repeating groups are promoted (children
    bubble up); repeating groups (group + repeats:true) are kept as-is so
    their children can be imported with parent_qq_id set.
    """
    result: list[dict] = []
    for item in items:
        fhir_type = item.get("type", "string")
        if fhir_type == "group" and item.get("repeats"):
            result.append(item)          # repeating group: preserve with children
        elif fhir_type == "group":
            result.extend(_collect_items(item.get("item", [])))
        elif fhir_type not in _SKIP_TYPES:
            result.append(item)
    return result


def _import_item(
    conn: sqlite3.Connection,
    qnaire_id: int,
    item: dict,
    order: int,
    parent_qq_id: int | None = None,
) -> int:
    link_id   = item["linkId"]
    fhir_type = item.get("type", "string")
    text      = item.get("text") or link_id
    required  = bool(item.get("required", False))

    # Map FHIR type
    qtype = _FHIR_TO_QTYPE.get(fhir_type, "text")
    if fhir_type == "choice" and item.get("repeats"):
        qtype = "multiple_choice"
    if fhir_type == "group" and item.get("repeats"):
        qtype = "repeating_group"
    if fhir_type == "integer":
        for ext in item.get("extension", []):
            if ext.get("url") == "http://hl7.org/fhir/StructureDefinition/questionnaire-itemControl":
                codes = [c.get("code") for c in ext.get("valueCodeableConcept", {}).get("coding", [])]
                if "slider" in codes:
                    qtype = "slider"
                    break

    # Parse our custom extensions
    ext_vals = _parse_quickq_extensions(item.get("extension", []))
    numeric_min = _ext_decimal(item.get("extension", []),
                               "http://hl7.org/fhir/StructureDefinition/minValue")
    numeric_max = _ext_decimal(item.get("extension", []),
                               "http://hl7.org/fhir/StructureDefinition/maxValue")

    q_id = upsert_question(conn, QuestionDef(
        link_id=link_id,
        text=text,
        type=qtype,
        help_text=ext_vals.get("help-text"),
        source_instrument=ext_vals.get("source-instrument"),
        source_item_id=ext_vals.get("source-item-id"),
        numeric_min=numeric_min,
        numeric_max=numeric_max,
        slider_min_label=ext_vals.get("slider-min-label"),
        slider_max_label=ext_vals.get("slider-max-label"),
    ))

    if ext_vals.get("internal-note"):
        conn.execute(
            "UPDATE question SET internal_note=? WHERE question_id=?",
            (ext_vals["internal-note"], q_id),
        )

    # Answer options / value set
    if "answerValueSet" in item:
        _link_answer_value_set(conn, q_id, item["answerValueSet"])
    elif "answerOption" in item:
        _import_answer_options(conn, q_id, item["answerOption"])

    qq_id = place_question(
        conn, qnaire_id, q_id, display_order=order, required=required,
        parent_qq_id=parent_qq_id,
    )

    # Repeating group: import child items with parent_qq_id
    if qtype == "repeating_group":
        for child_order, child in enumerate(item.get("item", [])):
            _import_item(conn, qnaire_id, child, child_order, parent_qq_id=qq_id)

    return qq_id


# ------------------------------------------------------------------
# Answer options
# ------------------------------------------------------------------

def _link_answer_value_set(conn: sqlite3.Connection, q_id: int, canonical_url: str) -> None:
    row = conn.execute(
        "SELECT option_set_id FROM response_option_set WHERE canonical_url=?",
        (canonical_url,),
    ).fetchone()
    if row:
        os_id = row[0]
    else:
        name = canonical_url.rstrip("/").rsplit("/", 1)[-1][:100] or canonical_url[:100]
        conn.execute(
            "INSERT INTO response_option_set (name, canonical_url) VALUES (?,?)"
            " ON CONFLICT (name) DO UPDATE SET canonical_url=excluded.canonical_url",
            (name, canonical_url),
        )
        os_id = conn.execute(
            "SELECT option_set_id FROM response_option_set WHERE canonical_url=?",
            (canonical_url,),
        ).fetchone()[0]
    conn.execute(
        "UPDATE question SET option_set_id=? WHERE question_id=?", (os_id, q_id)
    )


def _import_answer_options(conn: sqlite3.Connection, q_id: int, answer_options: list[dict]) -> None:
    # Idempotent: skip if options already exist for this question
    if conn.execute(
        "SELECT COUNT(*) FROM response_option WHERE question_id=?", (q_id,)
    ).fetchone()[0] > 0:
        return

    for i, opt in enumerate(answer_options):
        if "valueCoding" in opt:
            coding       = opt["valueCoding"]
            option_value = coding.get("code", str(i))
            option_text  = coding.get("display") or option_value
            concept_code = coding.get("code") if coding.get("system") else None
            concept_sys  = coding.get("system")
        elif "valueString" in opt:
            option_value = opt["valueString"]
            option_text  = option_value
            concept_code = None
            concept_sys  = None
        elif "valueInteger" in opt:
            option_value = str(opt["valueInteger"])
            option_text  = option_value
            concept_code = None
            concept_sys  = None
        else:
            continue

        conn.execute(
            """
            INSERT INTO response_option
                (question_id, option_text, option_value, display_order, concept_code, concept_system)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (q_id, option_text, option_value, i, concept_code, concept_sys),
        )


# ------------------------------------------------------------------
# Skip logic
# ------------------------------------------------------------------

def _insert_skip_rule(
    conn: sqlite3.Connection,
    qq_id: int,
    trigger_qq_id: int,
    ew: dict,
    enable_behavior: str,
) -> None:
    fhir_op = ew.get("operator", "exists")
    trigger_value: str | None = None

    if fhir_op == "exists":
        if ew.get("answerBoolean") is False:
            op = "not_exists"
        else:
            op = "exists"
    else:
        op = fhir_op
        for key in ("answerString", "answerDecimal", "answerInteger",
                    "answerBoolean", "answerDate", "answerDateTime"):
            if key in ew:
                val = ew[key]
                # Normalize numeric answers to float strings for round-trip stability
                trigger_value = str(float(val)) if key in ("answerDecimal", "answerInteger") else str(val)
                break
        if "answerCoding" in ew:
            trigger_value = ew["answerCoding"].get("code")

    conn.execute(
        """
        INSERT INTO skip_rule (qq_id, trigger_qq_id, operator, trigger_value, enable_behavior)
        VALUES (?, ?, ?, ?, ?)
        """,
        (qq_id, trigger_qq_id, op, trigger_value, enable_behavior),
    )


# ------------------------------------------------------------------
# Scoring rules
# ------------------------------------------------------------------

def _import_scoring_rule(conn: sqlite3.Connection, qnaire_id: int, ext: dict) -> None:
    inner = {e["url"].split("/")[-1]: e for e in ext.get("extension", [])}
    name    = inner.get("name", {}).get("valueString")
    formula = inner.get("formula", {}).get("valueString")
    if not name or not formula:
        return
    desc = inner.get("description", {}).get("valueString")
    conn.execute(
        "INSERT INTO scoring_rule (questionnaire_id, name, formula, description) VALUES (?,?,?,?)",
        (qnaire_id, name, formula, desc),
    )


# ------------------------------------------------------------------
# Extension helpers
# ------------------------------------------------------------------

def _parse_quickq_extensions(extensions: list[dict]) -> dict[str, str]:
    """Extract our custom extension values keyed by the local name."""
    result: dict[str, str] = {}
    prefix = _EXT + "/"
    for ext in extensions:
        url = ext.get("url", "")
        if url.startswith(prefix):
            key = url[len(prefix):]
            val = (ext.get("valueString")
                   or ext.get("valueDecimal")
                   or ext.get("valueBoolean"))
            if val is not None:
                result[key] = str(val) if not isinstance(val, str) else val
    return result


def _ext_decimal(extensions: list[dict], url: str) -> float | None:
    for ext in extensions:
        if ext.get("url") == url:
            val = ext.get("valueDecimal") if ext.get("valueDecimal") is not None else ext.get("valueInteger")
            return float(val) if val is not None else None
    return None
