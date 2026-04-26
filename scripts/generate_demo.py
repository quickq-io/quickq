"""
Generate demo data for the quickq tutorial.

Loads two instruments (PHQ-9 and Prenatal Visit Log) and generates
250 synthetic PHQ-9 responses + 150 prenatal visit logs with realistic
distributions, then runs quickq refresh to produce the OLAP database.

Output:
  demo/study.db           — SQLite OLTP
  demo/analytics.duckdb   — DuckDB OLAP with 5 analytical views

Usage:
  uv run python scripts/generate_demo.py

Then open the DuckDB UI:
  duckdb -ui demo/analytics.duckdb
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.loader import load_yaml
from quickq.parser_fhir_response import import_fhir_response
from quickq.olap_schema import refresh, init_olap

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
DEMO_DIR = Path(__file__).parent.parent / "demo"

random.seed(42)

# ── PHQ-9 ────────────────────────────────────────────────────────────

PHQ9_URL   = "http://quickq.io/instruments/phq9"
PHQ9_ITEMS = [f"phq9.{i}" for i in range(1, 10)]
PHQ9_FREQ  = [
    ("LA6568-5", "Not at all"),
    ("LA6569-3", "Several days"),
    ("LA6570-1", "More than half the days"),
    ("LA6571-9", "Nearly every day"),
]
PHQ9_DIFF = [
    ("LA6572-7", "Not difficult at all"),
    ("LA6573-5", "Somewhat difficult"),
    ("LA6574-3", "Very difficult"),
    ("LA6575-0", "Extremely difficult"),
]

# Severity → plausible per-item score pool.
# Real PHQ-9 data is right-skewed; most respondents land in minimal/mild.
_SEVERITY_POOLS = {
    "minimal":           [0, 0, 0, 0, 0, 0, 1],      # total 0–4
    "mild":              [0, 0, 1, 1, 1, 2],           # total 5–9
    "moderate":          [1, 1, 1, 2, 2, 2],           # total 10–14
    "moderately_severe": [1, 2, 2, 2, 3, 3],           # total 15–19
    "severe":            [2, 2, 3, 3, 3, 3],           # total 20–27
}


def _phq9_scores(severity: str) -> list[int]:
    pool = _SEVERITY_POOLS[severity]
    return [random.choice(pool) for _ in range(9)]


def _phq9_response(subject_id: str, scores: list[int], mode: str, authored: str) -> dict:
    total = sum(scores)
    items = [
        {
            "linkId": lid,
            "answer": [{
                "valueCoding": {
                    "system": "http://loinc.org",
                    "code": PHQ9_FREQ[s][0],
                    "display": PHQ9_FREQ[s][1],
                }
            }],
        }
        for lid, s in zip(PHQ9_ITEMS, scores)
    ]
    if total > 0:
        diff_idx = min(3, total // 7)
        items.append({
            "linkId": "phq9.difficulty",
            "answer": [{
                "valueCoding": {
                    "system": "http://loinc.org",
                    "code": PHQ9_DIFF[diff_idx][0],
                    "display": PHQ9_DIFF[diff_idx][1],
                }
            }],
        })
    return {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "questionnaire": PHQ9_URL,
        "authored": authored,
        "subject": {"reference": f"Patient/{subject_id}"},
        "item": items,
    }


# ── Prenatal Visit Log ───────────────────────────────────────────────

PRENATAL_URL = "http://quickq.io/instruments/prenatal-visits"

# Typical prenatal schedule weeks; we sample n of them per respondent
VISIT_WEEKS = [8, 12, 16, 20, 24, 28, 32, 36, 38]

# Provider distribution: ob 55%, midwife 35%, np 10%
_PROVIDERS = (
    ["ob"] * 11 + ["midwife"] * 7 + ["np"] * 2
)


def _prenatal_response(subject_id: str, n_visits: int, authored: str) -> dict:
    weeks = sorted(random.sample(VISIT_WEEKS, n_visits))
    items: list[dict] = [
        {"linkId": "visit_count", "answer": [{"valueDecimal": n_visits}]}
    ]
    for week in weeks:
        provider = random.choice(_PROVIDERS)
        concern  = random.random() < 0.15
        items.append({
            "linkId": "visits",
            "item": [
                {"linkId": "visits.week",     "answer": [{"valueDecimal": week}]},
                {"linkId": "visits.provider", "answer": [{"valueCoding": {"code": provider}}]},
                {"linkId": "visits.concern",  "answer": [{"valueBoolean": concern}]},
            ],
        })
    return {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "questionnaire": PRENATAL_URL,
        "authored": authored,
        "subject": {"reference": f"Patient/{subject_id}"},
        "item": items,
    }


# ── Views ────────────────────────────────────────────────────────────

_VIEWS = [
    (
        "v_phq9_scores",
        """
        SELECT
            dr.external_id                  AS respondent,
            ars.score_raw                   AS phq9_total,
            ars.score_category              AS severity,
            ds.admin_mode,
            DATE(ds.completed_at)           AS completed_date
        FROM      agg_respondent_scores ars
        JOIN      dim_respondent        dr  USING (respondent_id)
        JOIN      dim_session           ds  USING (session_id)
        WHERE ars.scoring_rule_name = 'PHQ-9 Total Score'
        """,
    ),
    (
        "v_phq9_severity_distribution",
        """
        SELECT
            severity,
            COUNT(*)                                                AS n,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)     AS pct,
            ROUND(AVG(phq9_total), 1)                               AS mean_score
        FROM v_phq9_scores
        GROUP BY severity
        ORDER BY mean_score
        """,
    ),
    (
        "v_phq9_by_admin_mode",
        """
        SELECT
            admin_mode,
            COUNT(*)                    AS n,
            ROUND(AVG(phq9_total), 1)   AS mean_score,
            MIN(phq9_total)             AS min_score,
            MAX(phq9_total)             AS max_score
        FROM v_phq9_scores
        GROUP BY admin_mode
        ORDER BY admin_mode
        """,
    ),
    (
        "v_prenatal_visits",
        """
        SELECT
            dr.external_id                  AS respondent,
            fr.repeat_index                 AS visit_number,
            MAX(CASE WHEN dq.link_id = 'visits.week'
                     THEN fr.response_numeric END)  AS gestational_week,
            MAX(CASE WHEN dq.link_id = 'visits.provider'
                     THEN fr.option_value END)       AS provider,
            MAX(CASE WHEN dq.link_id = 'visits.concern'
                     THEN fr.response_text END)      AS concern_noted
        FROM      fact_response fr
        JOIN      dim_question   dq USING (question_id)
        JOIN      dim_respondent dr USING (respondent_id)
        WHERE dq.link_id IN ('visits.week', 'visits.provider', 'visits.concern')
        GROUP BY dr.external_id, fr.session_id, fr.repeat_index
        ORDER BY respondent, visit_number
        """,
    ),
    (
        "v_prenatal_summary",
        """
        SELECT
            respondent,
            COUNT(*)                                            AS total_visits,
            MIN(gestational_week)                               AS first_visit_week,
            MAX(gestational_week)                               AS last_visit_week,
            COUNT(CASE WHEN concern_noted = 'true' THEN 1 END)  AS visits_with_concern
        FROM v_prenatal_visits
        GROUP BY respondent
        """,
    ),
    (
        "v_phq9_prenatal_overlap",
        """
        SELECT
            phq.respondent,
            phq.phq9_total,
            phq.severity,
            phq.admin_mode,
            pre.total_visits,
            pre.visits_with_concern
        FROM v_phq9_scores       phq
        JOIN v_prenatal_summary  pre USING (respondent)
        ORDER BY phq.phq9_total
        """,
    ),
]


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    DEMO_DIR.mkdir(exist_ok=True)

    db_path   = str(DEMO_DIR / "study.db")
    olap_path = str(DEMO_DIR / "analytics.duckdb")

    for p in [db_path, olap_path]:
        Path(p).unlink(missing_ok=True)

    # ── OLTP setup ───────────────────────────────────────────────────
    conn = init_oltp(db_path)
    print("Loading instruments...")
    load_yaml(conn, FIXTURES / "phq9.yaml")
    load_yaml(conn, FIXTURES / "prenatal_visits.yaml")
    print("  PHQ-9 Patient Health Questionnaire")
    print("  Prenatal Visit Log")

    # ── PHQ-9 responses ──────────────────────────────────────────────
    # Realistic distribution: right-skewed, most respondents minimal/mild
    severities = (
        ["minimal"]           * 100 +   # 40 %
        ["mild"]              * 63  +   # 25 %
        ["moderate"]          * 50  +   # 20 %
        ["moderately_severe"] * 25  +   # 10 %
        ["severe"]            * 12       #  5 %
    )
    random.shuffle(severities)

    admin_modes = random.choices(
        ["web", "phone", "paper"],
        weights=[55, 30, 15],
        k=len(severities),
    )

    base = datetime(2025, 1, 15, tzinfo=timezone.utc)
    n_phq9 = len(severities)

    print(f"\nImporting {n_phq9} PHQ-9 responses...")
    for i, (severity, mode) in enumerate(zip(severities, admin_modes)):
        subject_id = f"respondent-{i + 1:03d}"
        days = int(i * 340 / n_phq9) + random.randint(-2, 2)
        authored = (base + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        scores   = _phq9_scores(severity)
        resource = _phq9_response(subject_id, scores, mode, authored)
        import_fhir_response(conn, resource, admin_mode=mode)

    conn.commit()

    # ── Prenatal responses ───────────────────────────────────────────
    # First 150 respondents also completed the prenatal log
    n_prenatal = 150
    print(f"Importing {n_prenatal} prenatal visit logs...")
    for i in range(n_prenatal):
        subject_id = f"respondent-{i + 1:03d}"
        # Most respondents have 3–5 visits; a few have 1–2 (late entry/dropout)
        n_visits = random.choices(
            [2, 3, 4, 5],
            weights=[10, 25, 45, 20],
        )[0]
        n_visits = min(n_visits, len(VISIT_WEEKS))
        days     = random.randint(0, 270)
        authored = (base + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resource = _prenatal_response(subject_id, n_visits, authored)
        import_fhir_response(conn, resource, admin_mode="web")

    conn.commit()
    conn.close()

    # ── Refresh ──────────────────────────────────────────────────────
    print("\nRunning quickq refresh...")
    refresh(olap_path, db_path)
    oconn = init_olap(olap_path, db_path)

    # ── Views ────────────────────────────────────────────────────────
    print("Creating analytical views...")
    for name, query in _VIEWS:
        oconn.execute(f"CREATE OR REPLACE VIEW {name} AS {query}")
    oconn.close()

    # ── Summary ──────────────────────────────────────────────────────
    oconn = init_olap(olap_path, db_path)
    n_sessions  = oconn.execute("SELECT COUNT(*) FROM dim_session").fetchone()[0]
    n_responses = oconn.execute("SELECT COUNT(*) FROM fact_response").fetchone()[0]
    n_scored    = oconn.execute("SELECT COUNT(*) FROM agg_respondent_scores").fetchone()[0]

    dist = oconn.execute(
        "SELECT severity, n, pct FROM v_phq9_severity_distribution"
    ).fetchall()

    print()
    print("── Demo data ready ─────────────────────────────────────────")
    print(f"  OLTP:       demo/study.db")
    print(f"  OLAP:       demo/analytics.duckdb")
    print(f"  Sessions:   {n_sessions}")
    print(f"  Responses:  {n_responses}")
    print(f"  Scored:     {n_scored} PHQ-9 sessions")
    print()
    print("  PHQ-9 severity distribution:")
    for severity, n, pct in dist:
        bar = "█" * int(pct / 2)
        print(f"    {severity:<30s} {n:>3d}  ({pct:4.1f}%)  {bar}")
    print()
    print(f"  Views created: {', '.join(v[0] for v in _VIEWS)}")
    print()
    print("── Open the DuckDB UI ──────────────────────────────────────")
    print("  duckdb -ui demo/analytics.duckdb")
    oconn.close()


if __name__ == "__main__":
    main()
