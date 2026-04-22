"""
Generate FHIR test fixtures from the PHQ-9 questionnaire YAML.

Outputs:
  tests/fixtures/phq9_fhir_questionnaire.json  — FHIR R4 Questionnaire
  tests/fixtures/phq9_fhir_responses.json      — array of synthetic QuestionnaireResponse

Run:
  uv run python scripts/generate_fixtures.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.loader import load_yaml
from quickq.renderer_fhir import export_fhir

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
PHQ9_YAML = FIXTURES / "phq9.yaml"

# Frequency option codings (LOINC)
_FREQ = [
    {"system": "http://loinc.org", "code": "LA6568-5", "display": "Not at all"},
    {"system": "http://loinc.org", "code": "LA6569-3", "display": "Several days"},
    {"system": "http://loinc.org", "code": "LA6570-1", "display": "More than half the days"},
    {"system": "http://loinc.org", "code": "LA6571-9", "display": "Nearly every day"},
]

_DIFFICULTY = [
    {"system": "http://loinc.org", "code": "LA6572-7", "display": "Not difficult at all"},
    {"system": "http://loinc.org", "code": "LA6573-5", "display": "Somewhat difficult"},
    {"system": "http://loinc.org", "code": "LA6574-3", "display": "Very difficult"},
    {"system": "http://loinc.org", "code": "LA6575-0", "display": "Extremely difficult"},
]

PHQ9_LINK_IDS = ["phq9.1", "phq9.2", "phq9.3", "phq9.4",
                  "phq9.5", "phq9.6", "phq9.7", "phq9.8", "phq9.9"]

# (scores for items 1-9, difficulty_value)
# difficulty only shown when any of items 1-3 != 0
_PROFILES = [
    ("minimal",             [0, 0, 0, 0, 0, 1, 0, 0, 0], None),
    ("mild",                [1, 1, 1, 1, 1, 1, 0, 0, 1], 0),
    ("moderate",            [2, 1, 2, 1, 2, 1, 2, 1, 2], 1),
    ("moderately_severe",   [2, 3, 2, 3, 2, 2, 2, 2, 2], 2),
    ("severe",              [3, 3, 3, 3, 3, 3, 3, 3, 3], 3),
]


def _authored(offset_days: int = 0) -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_response(
    questionnaire_url: str,
    subject_id: str,
    profile_name: str,
    scores: list[int],
    difficulty: int | None,
) -> dict:
    items = []
    for link_id, score in zip(PHQ9_LINK_IDS, scores):
        items.append({
            "linkId": link_id,
            "answer": [{"valueCoding": _FREQ[score]}],
        })

    if difficulty is not None:
        items.append({
            "linkId": "phq9.difficulty",
            "answer": [{"valueCoding": _DIFFICULTY[difficulty]}],
        })

    return {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "questionnaire": questionnaire_url,
        "authored": _authored(),
        "subject": {"reference": f"Patient/{subject_id}"},
        "item": items,
    }


def main() -> None:
    # Load PHQ-9 into a temp in-memory db and export FHIR JSON
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        conn = init_oltp(tmp_path)
        qid = load_yaml(conn, PHQ9_YAML)
        questionnaire = export_fhir(conn, qid)
    finally:
        conn.close()
        os.unlink(tmp_path)

    questionnaire_url = questionnaire.get("url", "http://quickq.io/instruments/phq9")

    # Write FHIR Questionnaire
    q_path = FIXTURES / "phq9_fhir_questionnaire.json"
    q_path.write_text(json.dumps(questionnaire, indent=2))
    print(f"wrote {q_path}")

    # Build synthetic responses
    responses = [
        _build_response(questionnaire_url, f"synthetic-{i+1:03d}", name, scores, diff)
        for i, (name, scores, diff) in enumerate(_PROFILES)
    ]

    r_path = FIXTURES / "phq9_fhir_responses.json"
    r_path.write_text(json.dumps(responses, indent=2))
    print(f"wrote {r_path}  ({len(responses)} responses)")
    print()

    # Print score summary
    print("Score summary:")
    for name, scores, diff in _PROFILES:
        total = sum(scores)
        print(f"  {name:25s}  total={total:2d}  difficulty={diff}")


if __name__ == "__main__":
    main()
