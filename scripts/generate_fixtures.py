"""
Generate FHIR test fixtures from questionnaire YAML files.

Outputs:
  tests/fixtures/phq9_fhir_questionnaire.json      — FHIR R4 Questionnaire
  tests/fixtures/phq9_fhir_responses.json          — synthetic QuestionnaireResponses
  tests/fixtures/promis10_fhir_questionnaire.json  — FHIR R4 Questionnaire
  tests/fixtures/promis10_fhir_responses.json      — synthetic QuestionnaireResponses

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
    finally:
        conn.close()
        os.unlink(tmp_path)


if __name__ == "__main__":
    main()
