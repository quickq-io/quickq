"""
Generate FHIR test fixtures from questionnaire YAML files.

Outputs:
  tests/fixtures/phq9_fhir_questionnaire.json      — FHIR R4 Questionnaire
  tests/fixtures/phq9_fhir_responses.json          — synthetic QuestionnaireResponses
  tests/fixtures/promis10_fhir_questionnaire.json  — FHIR R4 Questionnaire
  tests/fixtures/promis10_fhir_responses.json      — synthetic QuestionnaireResponses
  tests/fixtures/audit_fhir_questionnaire.json     — FHIR R4 Questionnaire
  tests/fixtures/audit_fhir_responses.json         — synthetic QuestionnaireResponses
  tests/fixtures/prapare_fhir_questionnaire.json       — FHIR R4 Questionnaire
  tests/fixtures/prapare_fhir_responses.json           — synthetic QuestionnaireResponses
  tests/fixtures/gout_checkin_fhir_questionnaire.json  — FHIR R4 Questionnaire
  tests/fixtures/gout_checkin_fhir_responses.json      — synthetic QuestionnaireResponses

Run:
  uv run python scripts/generate_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.library_loader import load_library_file
from quickq.loader import load_yaml
from quickq.renderer_fhir import export_fhir

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
LIBRARY  = Path(__file__).parent.parent / "quickq" / "library"


def _authored() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fhir_response(url: str, subject_id: str, items: list[dict]) -> dict:
    return {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "questionnaire": url,
        "authored": _authored(),
        "subject": {"reference": f"Patient/{subject_id}"},
        "item": items,
    }


def _coding(system: str, code: str, display: str) -> dict:
    return {"valueCoding": {"system": system, "code": code, "display": display}}


# ------------------------------------------------------------------
# PHQ-9
# ------------------------------------------------------------------

_PHQ9_FREQ = [
    ("LA6568-5", "Not at all"),
    ("LA6569-3", "Several days"),
    ("LA6570-1", "More than half the days"),
    ("LA6571-9", "Nearly every day"),
]
_PHQ9_DIFF = [
    ("LA6572-7", "Not difficult at all"),
    ("LA6573-5", "Somewhat difficult"),
    ("LA6574-3", "Very difficult"),
    ("LA6575-0", "Extremely difficult"),
]
_PHQ9_ITEMS = ["phq9.1", "phq9.2", "phq9.3", "phq9.4",
               "phq9.5", "phq9.6", "phq9.7", "phq9.8", "phq9.9"]

# (label, scores[0-3] per item 1-9, difficulty index or None)
_PHQ9_PROFILES = [
    ("minimal",           [0, 0, 0, 0, 0, 1, 0, 0, 0], None),
    ("mild",              [1, 1, 1, 1, 1, 1, 0, 0, 1], 0),
    ("moderate",          [2, 1, 2, 1, 2, 1, 2, 1, 2], 1),
    ("moderately_severe", [2, 3, 2, 3, 2, 2, 2, 2, 2], 2),
    ("severe",            [3, 3, 3, 3, 3, 3, 3, 3, 3], 3),
]


def _phq9_response(url: str, subject_id: str, scores: list[int], diff: int | None) -> dict:
    items = [
        {"linkId": lid, "answer": [_coding("http://loinc.org", *_PHQ9_FREQ[s])]}
        for lid, s in zip(_PHQ9_ITEMS, scores)
    ]
    if diff is not None:
        items.append({"linkId": "phq9.difficulty",
                      "answer": [_coding("http://loinc.org", *_PHQ9_DIFF[diff])]})
    return _fhir_response(url, subject_id, items)


def generate_phq9(conn) -> None:
    qid = load_yaml(conn, FIXTURES / "phq9.yaml")
    q = export_fhir(conn, qid)
    url = q.get("url", "http://quickq.io/instruments/phq9")

    (FIXTURES / "phq9_fhir_questionnaire.json").write_text(json.dumps(q, indent=2))
    print(f"wrote phq9_fhir_questionnaire.json")

    responses = [
        _phq9_response(url, f"synthetic-{i+1:03d}", scores, diff)
        for i, (_, scores, diff) in enumerate(_PHQ9_PROFILES)
    ]
    (FIXTURES / "phq9_fhir_responses.json").write_text(json.dumps(responses, indent=2))
    print(f"wrote phq9_fhir_responses.json  ({len(responses)} responses)")
    print("  Score summary:")
    for name, scores, diff in _PHQ9_PROFILES:
        print(f"    {name:25s}  total={sum(scores):2d}  difficulty={diff}")


# ------------------------------------------------------------------
# PROMIS-10
# ------------------------------------------------------------------

_LOINC = "http://loinc.org"

# option sets keyed by link_id — (code, display, value) for each option
# values match what's in the library YAML (5=best, 1=worst for rated items)
_P10_GENERAL = [
    ("LA9206-9",  "Excellent",   5),
    ("LA13913-1", "Very good",   4),
    ("LA8967-7",  "Good",        3),
    ("LA8968-5",  "Fair",        2),
    ("LA8969-3",  "Poor",        1),
]
_P10_PHYSICAL = [
    ("LA13937-0", "Completely",  5),
    ("LA13938-8", "Mostly",      4),
    ("LA13939-6", "Moderately",  3),
    ("LA13940-4", "A little",    2),
    ("LA6568-5",  "Not at all",  1),
]
_P10_FATIGUE = [
    ("LA137-2",   "None",        5),
    ("LA6752-5",  "Mild",        4),
    ("LA6751-7",  "Moderate",    3),
    ("LA6750-9",  "Severe",      2),
    ("LA13958-6", "Very severe", 1),
]
_P10_FREQ = [
    ("LA6270-8",  "Never",       5),
    ("LA10066-1", "Rarely",      4),
    ("LA10082-8", "Sometimes",   3),
    ("LA10044-8", "Often",       2),
    ("LA9933-8",  "Always",      1),
]

def _p10_option(option_set: list, value: int) -> dict:
    """Return a valueCoding answer for the option whose value == value."""
    code, display, _ = next(o for o in option_set if o[2] == value)
    return _coding(_LOINC, code, display)

# Profile: (label, g1-g6 values 1-5, g7 pain 0-10, g8 fatigue 1-5, g9 1-5, g10 1-5)
# Higher = better health for all except g7 (lower pain = better)
_P10_PROFILES = [
    ("excellent_health", [5, 5, 5, 5, 5, 5],  0, 5, 5, 5),
    ("good_health",      [4, 4, 4, 4, 4, 4],  2, 4, 4, 4),
    ("fair_health",      [3, 3, 3, 3, 3, 3],  5, 3, 3, 3),
    ("poor_health",      [2, 2, 2, 2, 2, 2],  7, 2, 2, 2),
    ("very_poor_health", [1, 1, 1, 1, 1, 1], 10, 1, 1, 1),
]


def _promis10_response(url: str, subject_id: str, profile: tuple) -> dict:
    label, general_vals, pain, fatigue_val, g9_val, g10_val = profile
    g1, g2, g3, g4, g5, g6 = general_vals
    items = [
        {"linkId": "promis10.g1",  "answer": [_p10_option(_P10_GENERAL,  g1)]},
        {"linkId": "promis10.g2",  "answer": [_p10_option(_P10_GENERAL,  g2)]},
        {"linkId": "promis10.g3",  "answer": [_p10_option(_P10_GENERAL,  g3)]},
        {"linkId": "promis10.g4",  "answer": [_p10_option(_P10_GENERAL,  g4)]},
        {"linkId": "promis10.g5",  "answer": [_p10_option(_P10_GENERAL,  g5)]},
        {"linkId": "promis10.g6",  "answer": [_p10_option(_P10_PHYSICAL, g6)]},
        {"linkId": "promis10.g7",  "answer": [{"valueDecimal": pain}]},
        {"linkId": "promis10.g8",  "answer": [_p10_option(_P10_FATIGUE,  fatigue_val)]},
        {"linkId": "promis10.g9",  "answer": [_p10_option(_P10_GENERAL,  g9_val)]},
        {"linkId": "promis10.g10", "answer": [_p10_option(_P10_FREQ,     g10_val)]},
    ]
    return _fhir_response(url, subject_id, items)


def generate_promis10(conn) -> None:
    load_library_file(conn, LIBRARY / "promis10.yaml")
    qid = load_yaml(conn, FIXTURES / "promis10.yaml")
    q = export_fhir(conn, qid)
    url = q.get("url", "http://quickq.io/instruments/promis10")

    (FIXTURES / "promis10_fhir_questionnaire.json").write_text(json.dumps(q, indent=2))
    print(f"wrote promis10_fhir_questionnaire.json")

    responses = [
        _promis10_response(url, f"synthetic-{i+1:03d}", profile)
        for i, profile in enumerate(_P10_PROFILES)
    ]
    (FIXTURES / "promis10_fhir_responses.json").write_text(json.dumps(responses, indent=2))
    print(f"wrote promis10_fhir_responses.json  ({len(responses)} responses)")
    print("  Profile summary:")
    for label, gvals, pain, fat, g9, g10 in _P10_PROFILES:
        gmh = gvals[1] + gvals[3] + gvals[4] + g10
        gph = gvals[2] + gvals[5]
        print(f"    {label:22s}  GPH_partial={gph}  GMH_raw={gmh}  pain={pain}")


# ------------------------------------------------------------------
# AUDIT
# ------------------------------------------------------------------

_AUDIT_LOINC = "http://loinc.org"

# option codings keyed by (option_set, value)
_AUDIT_FREQ = {
    "0": ("LA6270-8",  "Never"),
    "1": ("LA18926-8", "Monthly or less"),
    "2": ("LA18927-6", "2-4 times a month"),
    "3": ("LA18928-4", "2-3 times a week"),
    "4": ("LA18929-2", "4 or more times a week"),
}
_AUDIT_QTY = {
    "0": ("LA15694-5", "1 or 2"),
    "1": ("LA15695-2", "3 or 4"),
    "2": ("LA18930-0", "5 or 6"),
    "3": ("LA18931-8", "7 to 9"),
    "4": ("LA18932-6", "10 or more"),
}
_AUDIT_HARM = {
    "0": ("LA6270-8",  "Never"),
    "1": ("LA18933-4", "Less than monthly"),
    "2": ("LA18876-5", "Monthly"),
    "3": ("LA18891-4", "Weekly"),
    "4": ("LA18934-2", "Daily or almost daily"),
}
_AUDIT_YESNO = {
    "0": ("LA32-8",    "No"),
    "2": ("LA32279-4", "Yes, but not in the last year"),
    "4": ("LA32280-2", "Yes, during the last year"),
}

def _audit_coding(option_map: dict, value: str) -> dict:
    code, display = option_map[value]
    return _coding(_AUDIT_LOINC, code, display)


# Profiles: (label, q1-q8 values 0-4, q9 value 0/2/4, q10 value 0/2/4)
_AUDIT_PROFILES = [
    ("low_risk",            ["0","0","0","0","0","0","0","0"], "0", "0"),
    ("harmful_use",         ["2","1","1","1","1","0","1","1"], "0", "0"),
    ("hazardous",           ["3","2","2","2","2","1","1","1"], "0", "2"),
    ("possible_dependence", ["4","4","4","4","4","4","4","4"], "4", "4"),
]

_AUDIT_ITEMS = [f"audit.q{i}" for i in range(1, 11)]
_AUDIT_OPT_MAPS = [_AUDIT_FREQ, _AUDIT_QTY] + [_AUDIT_HARM] * 6


def _audit_response(url: str, subject_id: str, profile: tuple) -> dict:
    label, q1_8, q9_val, q10_val = profile
    items = [
        {"linkId": lid, "answer": [_audit_coding(opt_map, val)]}
        for lid, opt_map, val in zip(_AUDIT_ITEMS[:8], _AUDIT_OPT_MAPS, q1_8)
    ]
    items.append({"linkId": "audit.q9",  "answer": [_audit_coding(_AUDIT_YESNO, q9_val)]})
    items.append({"linkId": "audit.q10", "answer": [_audit_coding(_AUDIT_YESNO, q10_val)]})
    return _fhir_response(url, subject_id, items)


def generate_audit(conn) -> None:
    load_library_file(conn, LIBRARY / "audit.yaml")
    qid = load_yaml(conn, FIXTURES / "audit.yaml")
    q = export_fhir(conn, qid)
    url = q.get("url", "http://quickq.io/instruments/audit")

    (FIXTURES / "audit_fhir_questionnaire.json").write_text(json.dumps(q, indent=2))
    print(f"wrote audit_fhir_questionnaire.json")

    responses = [
        _audit_response(url, f"synthetic-{i+1:03d}", profile)
        for i, profile in enumerate(_AUDIT_PROFILES)
    ]
    (FIXTURES / "audit_fhir_responses.json").write_text(json.dumps(responses, indent=2))
    print(f"wrote audit_fhir_responses.json  ({len(responses)} responses)")
    print("  Profile summary:")
    for label, q1_8, q9, q10 in _AUDIT_PROFILES:
        total = sum(int(v) for v in q1_8) + int(q9) + int(q10)
        print(f"    {label:25s}  total={total:2d}")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# PRAPARE
# ------------------------------------------------------------------

_PRAPARE_LOINC = "http://loinc.org"


def _prapare_single(link_id: str, concept_code: str, display: str) -> dict:
    return {"linkId": link_id, "answer": [_coding(_PRAPARE_LOINC, concept_code, display)]}


def _prapare_bool(link_id: str, value: bool) -> dict:
    return {"linkId": link_id, "answer": [{"valueBoolean": value}]}


def _prapare_numeric(link_id: str, value: int | float) -> dict:
    return {"linkId": link_id, "answer": [{"valueDecimal": value}]}


def _prapare_text(link_id: str, value: str) -> dict:
    return {"linkId": link_id, "answer": [{"valueString": value}]}


def _prapare_sata(link_id: str, codings: list[tuple[str, str]]) -> dict:
    return {"linkId": link_id, "answer": [_coding(_PRAPARE_LOINC, c, d) for c, d in codings]}


# Two synthetic patients covering different social risk profiles.
# Profile fields: (label, responses as list of item dicts)

def _build_prapare_low_risk(url: str, subject_id: str) -> dict:
    """Patient with minimal social risk."""
    items = [
        _prapare_single("prapare.hispanic",        "LA32-8",    "No"),
        _prapare_single("prapare.race",            "LA4457-3",  "White"),
        _prapare_single("prapare.farm_worker",     "LA32-8",    "No"),
        _prapare_single("prapare.military",        "LA32-8",    "No"),
        _prapare_single("prapare.language",        "LA43-5",    "English"),
        _prapare_numeric("prapare.household_size", 3),
        _prapare_single("prapare.housing_status",  "LA18835-1", "I have a steady place to live"),
        _prapare_single("prapare.housing_concern", "LA32-8",    "No"),
        _prapare_text(  "prapare.address",         "123 Main St, Anytown, USA"),
        _prapare_single("prapare.education",       "LA37-7",    "More than high school"),
        _prapare_single("prapare.employment",      "LA17958-2", "Full-time work"),
        _prapare_single("prapare.insurance",       "LA22077-4", "Private insurance"),
        _prapare_numeric("prapare.income",         75000),
        _prapare_sata(  "prapare.necessities",     [("LA30122-8", "I choose not to answer this question")]),
        _prapare_bool(  "prapare.transportation",  False),
        _prapare_single("prapare.social_contact",  "LA30131-9", "3 to 5 times a week"),
        _prapare_single("prapare.stress",          "LA6568-5",  "Not at all"),
        _prapare_single("prapare.incarceration",   "LA32-8",    "No"),
        _prapare_single("prapare.refugee",         "LA32-8",    "No"),
        _prapare_single("prapare.safety",          "LA33-6",    "Yes"),
        _prapare_bool(  "prapare.partner_fear",    False),
    ]
    return _fhir_response(url, subject_id, items)


def _build_prapare_high_risk(url: str, subject_id: str) -> dict:
    """Patient with multiple unmet social needs."""
    items = [
        _prapare_single("prapare.hispanic",        "LA33-6",    "Yes"),
        _prapare_single("prapare.race",            "LA10610-6", "Black or African American"),
        _prapare_single("prapare.farm_worker",     "LA33-6",    "Yes"),
        _prapare_single("prapare.military",        "LA32-8",    "No"),
        _prapare_single("prapare.language",        "LA44-3",    "Spanish"),
        _prapare_numeric("prapare.household_size", 5),
        _prapare_single("prapare.housing_status",  "LA18837-7", "I do not have a steady place to live (temporarily staying with others, hotel, shelter, or outside)"),
        _prapare_single("prapare.housing_concern", "LA33-6",    "Yes"),
        _prapare_text(  "prapare.address",         "c/o Shelter, 456 Hope Ave, Anytown, USA"),
        _prapare_single("prapare.education",       "LA35-1",    "Less than high school degree"),
        _prapare_single("prapare.employment",      "LA17956-6", "Unemployed"),
        _prapare_single("prapare.insurance",       "LA15652-3", "Medicaid"),
        _prapare_numeric("prapare.income",         18000),
        _prapare_sata(  "prapare.necessities",     [
            ("LA30125-1", "Food"),
            ("LA30124-4", "Utilities"),
            ("LA30128-5", "Medicine or Any Health Care"),
        ]),
        _prapare_bool(  "prapare.transportation",  True),
        _prapare_single("prapare.social_contact",  "LA27722-0", "Less than once a week"),
        _prapare_single("prapare.stress",          "LA13914-9", "Very much"),
        _prapare_single("prapare.incarceration",   "LA32-8",    "No"),
        _prapare_single("prapare.refugee",         "LA32-8",    "No"),
        _prapare_single("prapare.safety",          "LA32-8",    "No"),
        _prapare_bool(  "prapare.partner_fear",    True),
    ]
    return _fhir_response(url, subject_id, items)


def generate_prapare(conn) -> None:
    load_library_file(conn, LIBRARY / "prapare.yaml")
    qid = load_yaml(conn, FIXTURES / "prapare.yaml")
    q = export_fhir(conn, qid)
    url = q.get("url", "http://quickq.io/instruments/prapare")

    (FIXTURES / "prapare_fhir_questionnaire.json").write_text(json.dumps(q, indent=2))
    print("wrote prapare_fhir_questionnaire.json")

    responses = [
        _build_prapare_low_risk( url, "synthetic-001"),
        _build_prapare_high_risk(url, "synthetic-002"),
    ]
    (FIXTURES / "prapare_fhir_responses.json").write_text(json.dumps(responses, indent=2))
    print(f"wrote prapare_fhir_responses.json  ({len(responses)} responses)")
    print("  low_risk:  minimal social needs")
    print("  high_risk: housing insecurity, food/utilities/care unmet, transportation barrier, safety concern")


# ------------------------------------------------------------------
# Gout Check-In
# ------------------------------------------------------------------

def _gout_sata(link_id: str, values: list[str]) -> dict:
    # multiple_choice questions store answers as valueCoding (FHIR `choice`
    # type expects answerOption codings). Using valueString here previously
    # caused a type_mismatch flag in import_fhir_response.
    return {"linkId": link_id, "answer": [{"valueCoding": {"code": v}} for v in values]}


def _gout_grid(link_id: str, row_answers: list[tuple[str, str]]) -> dict:
    """row_answers: [(sub_linkId, column_value), ...]"""
    return {"linkId": link_id, "answer": [], "item": [
        {"linkId": sub_lid, "answer": [{"valueCoding": {"code": col_val}}]}
        for sub_lid, col_val in row_answers
    ]}


def _gout_ranked(link_id: str, ordered_values: list[str]) -> dict:
    """Ranked response: answer list in rank order, valueInteger = rank position."""
    return {"linkId": link_id, "answer": [
        {"valueCoding": {"code": v}, "extension": [
            {"url": "http://hl7.org/fhir/StructureDefinition/ordinalValue",
             "valueDecimal": i + 1}
        ]}
        for i, v in enumerate(ordered_values)
    ]}


def _build_gout_mild(url: str, subject_id: str) -> dict:
    """Patient with mild, infrequent gout; family history in father only."""
    items = [
        {"linkId": "gout.last_attack_date",  "answer": [{"valueDate": "2026-01-15"}]},
        {"linkId": "gout.attacks_12mo",       "answer": [{"valueDecimal": 2}]},
        _gout_sata("gout.attack_joints",      ["big_toe", "ankle"]),
        _gout_grid("gout.joint_severity", [
            ("gout.joint_severity.r0", "0"),
            ("gout.joint_severity.r1", "1"),
            ("gout.joint_severity.r2", "0"),
            ("gout.joint_severity.r3", "0"),
            ("gout.joint_severity.r4", "0"),
            ("gout.joint_severity.r5", "0"),
        ]),
        _gout_sata("gout.family_gout",        ["father"]),
        {"linkId": "gout.on_ult",           "answer": [{"valueBoolean": True}]},
        {"linkId": "gout.uric_acid",         "answer": [{"valueDecimal": 6.2}]},
        {"linkId": "gout.uric_acid_date",    "answer": [{"valueDate": "2026-03-01"}]},
        _gout_ranked("gout.treatment_priorities",
                     ["prevention", "uric_acid", "pain_relief", "function", "side_effects"]),
        _gout_grid("gout.management_confidence", [
            ("gout.management_confidence.r0", "3"),
            ("gout.management_confidence.r1", "4"),
            ("gout.management_confidence.r2", "4"),
            ("gout.management_confidence.r3", "3"),
            ("gout.management_confidence.r4", "3"),
        ]),
        {"linkId": "gout.notes", "answer": [{"valueString": "Doing well on allopurinol."}]},
    ]
    return _fhir_response(url, subject_id, items)


def _build_gout_severe(url: str, subject_id: str) -> dict:
    """Patient with frequent severe attacks; strong family history; not on ULT."""
    items = [
        {"linkId": "gout.last_attack_date",  "answer": [{"valueDate": "2026-04-10"}]},
        {"linkId": "gout.attacks_12mo",       "answer": [{"valueDecimal": 8}]},
        _gout_sata("gout.attack_joints",      ["big_toe", "ankle", "knee", "wrist"]),
        _gout_grid("gout.joint_severity", [
            ("gout.joint_severity.r0", "3"),
            ("gout.joint_severity.r1", "2"),
            ("gout.joint_severity.r2", "2"),
            ("gout.joint_severity.r3", "1"),
            ("gout.joint_severity.r4", "0"),
            ("gout.joint_severity.r5", "0"),
        ]),
        _gout_sata("gout.family_gout",        ["father", "mat_gp", "pat_gp"]),
        {"linkId": "gout.on_ult",           "answer": [{"valueBoolean": False}]},
        {"linkId": "gout.uric_acid",         "answer": [{"valueDecimal": 9.8}]},
        {"linkId": "gout.uric_acid_date",    "answer": [{"valueDate": "2026-04-15"}]},
        _gout_ranked("gout.treatment_priorities",
                     ["pain_relief", "prevention", "function", "uric_acid", "side_effects"]),
        _gout_grid("gout.management_confidence", [
            ("gout.management_confidence.r0", "2"),
            ("gout.management_confidence.r1", "2"),
            ("gout.management_confidence.r2", "3"),
            ("gout.management_confidence.r3", "1"),
            ("gout.management_confidence.r4", "2"),
        ]),
        {"linkId": "gout.notes",
         "answer": [{"valueString": "Attacks getting worse. Concerned about kidneys."}]},
    ]
    return _fhir_response(url, subject_id, items)


def generate_gout_checkin(conn) -> None:
    load_library_file(conn, LIBRARY / "gout_checkin.yaml")
    qid = load_yaml(conn, FIXTURES / "gout_checkin.yaml")
    q = export_fhir(conn, qid)
    url = q.get("url", "http://quickq.io/instruments/gout-checkin")

    (FIXTURES / "gout_checkin_fhir_questionnaire.json").write_text(json.dumps(q, indent=2))
    print("wrote gout_checkin_fhir_questionnaire.json")

    responses = [
        _build_gout_mild(  url, "synthetic-001"),
        _build_gout_severe(url, "synthetic-002"),
    ]
    (FIXTURES / "gout_checkin_fhir_responses.json").write_text(json.dumps(responses, indent=2))
    print(f"wrote gout_checkin_fhir_responses.json  ({len(responses)} responses)")
    print("  mild:   2 attacks/yr, father only, on ULT, UA 6.2")
    print("  severe: 8 attacks/yr, father+grandparents, not on ULT, UA 9.8")


def main() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        conn = init_oltp(tmp_path)
        print("=== PHQ-9 ===")
        generate_phq9(conn)
        print()
        print("=== PROMIS-10 ===")
        generate_promis10(conn)
        print()
        print("=== AUDIT ===")
        generate_audit(conn)
        print()
        print("=== PRAPARE ===")
        generate_prapare(conn)
        print()
        print("=== Gout Check-In ===")
        generate_gout_checkin(conn)
    finally:
        conn.close()
        os.unlink(tmp_path)


if __name__ == "__main__":
    main()
