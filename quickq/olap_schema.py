"""
OLAP layer: DuckDB schema initialization and incremental refresh.

`init_olap(olap_path, oltp_path)`  — create or open the DuckDB analytics database.
`refresh(olap_path, oltp_path)`    — incremental ETL from OLTP SQLite → OLAP DuckDB.

Refresh strategy
----------------
- Dimensions (study, questionnaire, question, option, concept, respondent) are
  fully re-synced on every refresh — they are small and rarely change.
- fact_response and dim_session are incremental: only rows with
  response_id > watermark or session_id > watermark are loaded.
- Aggregate tables are rebuilt in full after each incremental fact load.
- Scores (agg_respondent_scores) are computed for all sessions that appear in
  the new fact rows.

DuckDB reads SQLite directly via the sqlite extension:
    ATTACH 'quickq.db' AS oltp (TYPE sqlite, READ_ONLY);
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

_SQL_DIR = Path(__file__).parent.parent / "sql"

# choice-like types whose scoreable value lives in response_option.option_value
_SCORED_CHOICE_TYPES = frozenset({
    "single_choice", "multiple_choice", "sata_other", "likert",
})


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def init_olap(olap_path: str, oltp_path: str) -> duckdb.DuckDBPyConnection:
    """
    Open (or create) the DuckDB analytics database and ensure all tables exist.
    Returns an open connection with the SQLite OLTP attached as 'oltp'.
    """
    conn = duckdb.connect(olap_path)
    _attach_oltp(conn, oltp_path)
    _run_ddl(conn)
    return conn


def refresh(olap_path: str, oltp_path: str) -> dict[str, Any]:
    """
    Incremental ETL from OLTP → OLAP.

    Returns a stats dict:
        {rows_loaded, sessions_loaded, scores_computed, started_at, completed_at}
    Raises on unrecoverable error after writing a 'failed' log entry.
    """
    conn = init_olap(olap_path, oltp_path)
    started_at = _now()

    refresh_id = conn.execute(
        "SELECT COALESCE(max(refresh_id), 0) + 1 FROM refresh_log"
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO refresh_log (refresh_id, started_at, max_response_id, max_session_id, rows_loaded, status)
        VALUES (?, ?, 0, 0, 0, 'running')
        """,
        [refresh_id, started_at],
    )

    try:
        watermark = _read_watermark(conn)
        stats = _run_refresh(conn, watermark)
        completed_at = _now()

        conn.execute(
            """
            UPDATE refresh_log
               SET completed_at    = ?,
                   max_response_id = ?,
                   max_session_id  = ?,
                   rows_loaded     = ?,
                   status          = 'complete'
             WHERE refresh_id = ?
            """,
            [
                completed_at,
                stats["new_max_response_id"],
                stats["new_max_session_id"],
                stats["rows_loaded"],
                refresh_id,
            ],
        )
        stats["started_at"] = started_at
        stats["completed_at"] = completed_at
        return stats

    except Exception as exc:
        conn.execute(
            "UPDATE refresh_log SET status = 'failed', error_message = ? WHERE refresh_id = ?",
            [str(exc), refresh_id],
        )
        raise


# ------------------------------------------------------------------
# Internals
# ------------------------------------------------------------------

def _attach_oltp(conn: duckdb.DuckDBPyConnection, oltp_path: str) -> None:
    try:
        conn.execute(f"ATTACH '{oltp_path}' AS oltp (TYPE sqlite, READ_ONLY)")
    except duckdb.Error as e:
        if "already attached" not in str(e).lower():
            raise


def _run_ddl(conn: duckdb.DuckDBPyConnection) -> None:
    ddl = (_SQL_DIR / "olap_schema.sql").read_text()
    for stmt in _split_sql(ddl):
        conn.execute(stmt)


def _split_sql(sql: str) -> list[str]:
    """Split on ';' that are not inside -- line comments."""
    # Strip all line comments first, then split on ';'
    no_comments = re.sub(r"--[^\n]*", "", sql)
    stmts = []
    for chunk in no_comments.split(";"):
        stripped = chunk.strip()
        if stripped:
            stmts.append(stripped)
    return stmts


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_watermark(conn: duckdb.DuckDBPyConnection) -> dict:
    """High-water marks from the last completed refresh (0 if none)."""
    row = conn.execute(
        """
        SELECT max_response_id, max_session_id
        FROM   refresh_log
        WHERE  status = 'complete'
        ORDER  BY refresh_id DESC
        LIMIT  1
        """
    ).fetchone()
    return {
        "max_response_id": row[0] if row else 0,
        "max_session_id":  row[1] if row else 0,
    }


def _run_refresh(conn: duckdb.DuckDBPyConnection, watermark: dict) -> dict:
    max_r = watermark["max_response_id"]
    max_s = watermark["max_session_id"]

    # Dimensions — full re-sync (small tables, fast)
    _sync_dim_study(conn)
    _sync_dim_questionnaire(conn)
    _sync_dim_concept(conn)
    _sync_dim_question(conn)
    _sync_dim_response_option(conn)
    _sync_dim_respondent(conn)

    # Incremental: new sessions and facts only
    new_sessions = _sync_dim_session(conn, max_s)
    rows_loaded  = _load_fact_response(conn, max_r)

    # Date dim built from session dates
    _sync_dim_date(conn)

    new_max_r = conn.execute("SELECT COALESCE(max(response_id), 0) FROM fact_response").fetchone()[0]
    new_max_s = conn.execute("SELECT COALESCE(max(session_id),  0) FROM dim_session").fetchone()[0]

    # Aggregates — full rebuild after each incremental load
    _rebuild_agg_question_distribution(conn)
    _rebuild_agg_numeric_stats(conn)
    _rebuild_agg_session_completion(conn)

    scores_computed = _compute_scores(conn)

    # Lineage and equivalence mirrors
    _sync_dim_question_lineage(conn)
    _sync_dim_question_equivalence(conn)
    _sync_equivalence_group_ids(conn)

    # OMOP extraction — only runs if person_map is populated
    _sync_omop_survey_conduct(conn)
    _sync_omop_observation(conn)
    _sync_omop_unmapped(conn)

    return {
        "rows_loaded":          rows_loaded,
        "sessions_loaded":      new_sessions,
        "scores_computed":      scores_computed,
        "new_max_response_id":  new_max_r,
        "new_max_session_id":   new_max_s,
    }


# ------------------------------------------------------------------
# Dimension sync
# ------------------------------------------------------------------

def _sync_dim_study(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO dim_study
            (study_id, name, description, principal_investigator, irb_number, start_date, end_date)
        SELECT study_id, name, description, principal_investigator, irb_number,
               TRY_CAST(start_date AS DATE),
               TRY_CAST(end_date   AS DATE)
        FROM   oltp.study
    """)


def _sync_dim_questionnaire(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO dim_questionnaire
            (questionnaire_id, study_id, name, version, canonical_url, fhir_status)
        SELECT questionnaire_id,
               COALESCE(study_id, -1),
               name, version, canonical_url,
               COALESCE(fhir_status, 'unknown')
        FROM   oltp.questionnaire
    """)


def _sync_dim_concept(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO dim_concept
            (concept_id, concept_name, domain_id, vocabulary_id, concept_class_id,
             standard_concept, concept_code)
        SELECT concept_id, concept_name, domain_id, vocabulary_id, concept_class_id,
               standard_concept, concept_code
        FROM   oltp.concept
    """)


def _sync_dim_question(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO dim_question
            (question_id, link_id, question_text, question_type, help_text,
             source_instrument, source_item_id, citation,
             concept_id, concept_name, vocabulary_id, concept_code)
        SELECT q.question_id, q.link_id, q.question_text, q.question_type,
               q.help_text, q.source_instrument, q.source_item_id, q.citation,
               c.concept_id, c.concept_name, c.vocabulary_id, c.concept_code
        FROM   oltp.question q
        LEFT JOIN oltp.concept c ON q.concept_id = c.concept_id
    """)


def _sync_dim_response_option(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO dim_response_option
            (option_id, question_id, option_text, option_value, display_order,
             option_set_id, is_other, is_exclusive,
             concept_id, concept_name, concept_code, concept_system)
        SELECT ro.option_id, ro.question_id, ro.option_text, ro.option_value,
               ro.display_order, ro.option_set_id,
               ro.is_other::BOOLEAN, ro.is_exclusive::BOOLEAN,
               c.concept_id, c.concept_name, c.concept_code, ro.concept_system
        FROM   oltp.response_option ro
        LEFT JOIN oltp.concept c ON ro.concept_id = c.concept_id
    """)


def _sync_dim_respondent(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO dim_respondent
            (respondent_id, study_id, external_id)
        SELECT respondent_id,
               COALESCE(study_id, -1),
               external_id
        FROM   oltp.respondent
    """)


def _sync_dim_session(conn: duckdb.DuckDBPyConnection, max_session_id: int) -> int:
    conn.execute(f"""
        INSERT OR REPLACE INTO dim_session
            (session_id, questionnaire_id, respondent_id, study_id,
             started_at, completed_at, is_complete, admin_mode, is_proxy,
             duration_sec, session_date_key)
        SELECT rs.session_id,
               rs.questionnaire_id,
               rs.respondent_id,
               COALESCE(r.study_id, -1),
               TRY_CAST(rs.started_at   AS TIMESTAMP),
               TRY_CAST(rs.completed_at AS TIMESTAMP),
               rs.is_complete::BOOLEAN,
               rs.admin_mode,
               rs.is_proxy::BOOLEAN,
               CASE
                 WHEN rs.completed_at IS NOT NULL AND rs.started_at IS NOT NULL
                 THEN EPOCH(TRY_CAST(rs.completed_at AS TIMESTAMP))
                    - EPOCH(TRY_CAST(rs.started_at   AS TIMESTAMP))
               END,
               TRY_CAST(COALESCE(rs.completed_at, rs.started_at) AS DATE)
        FROM   oltp.response_session rs
        JOIN   oltp.respondent r ON rs.respondent_id = r.respondent_id
        WHERE  rs.session_id > {max_session_id}
    """)
    return conn.execute(
        f"SELECT count(*) FROM dim_session WHERE session_id > {max_session_id}"
    ).fetchone()[0]


# ------------------------------------------------------------------
# Fact table
# ------------------------------------------------------------------

def _load_fact_response(conn: duckdb.DuckDBPyConnection, max_response_id: int) -> int:
    conn.execute(f"""
        INSERT INTO fact_response (
            response_id, session_id, respondent_id, questionnaire_id, study_id,
            question_id, qq_id, option_id, grid_row_id, grid_column_id, repeat_index,
            response_text, response_numeric, response_date, option_value,
            question_concept_id, option_concept_id,
            response_date_key, session_start_key,
            admin_mode, is_proxy, interviewer_id
        )
        SELECT
            r.response_id,
            r.session_id,
            rs.respondent_id,
            rs.questionnaire_id,
            COALESCE(resp.study_id, -1),
            q.question_id,
            r.qq_id,
            r.option_id,
            r.grid_row_id,
            r.grid_column_id,
            r.repeat_index,
            r.response_text,
            CASE
              WHEN r.response_numeric IS NOT NULL
              THEN r.response_numeric
              WHEN ro.option_value IS NOT NULL
               AND q.question_type IN ('single_choice','multiple_choice',
                                       'sata_other','likert','ranked')
              THEN TRY_CAST(ro.option_value AS DOUBLE)
            END,
            TRY_CAST(r.response_date AS DATE),
            ro.option_value,
            q.concept_id,
            ro.concept_id,
            TRY_CAST(COALESCE(rs.completed_at, rs.started_at) AS DATE),
            TRY_CAST(rs.started_at AS DATE),
            rs.admin_mode,
            rs.is_proxy::BOOLEAN,
            rs.interviewer_id
        FROM   oltp.response r
        JOIN   oltp.response_session     rs   ON r.session_id    = rs.session_id
        JOIN   oltp.questionnaire_question qq  ON r.qq_id         = qq.qq_id
        JOIN   oltp.question              q    ON qq.question_id  = q.question_id
        JOIN   oltp.respondent            resp ON rs.respondent_id = resp.respondent_id
        LEFT JOIN oltp.response_option    ro   ON r.option_id     = ro.option_id
        WHERE  r.response_id > {max_response_id}
    """)
    return conn.execute(
        f"SELECT count(*) FROM fact_response WHERE response_id > {max_response_id}"
    ).fetchone()[0]


# ------------------------------------------------------------------
# Aggregate rebuilds
# ------------------------------------------------------------------

def _rebuild_agg_question_distribution(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DELETE FROM agg_question_distribution")
    conn.execute("""
        INSERT INTO agg_question_distribution
            (study_id, questionnaire_id, question_id, question_concept_id,
             option_id, option_value, option_concept_id, n, pct)
        WITH totals AS (
            SELECT questionnaire_id, question_id,
                   COUNT(DISTINCT session_id) AS n_total
            FROM   fact_response
            WHERE  option_value IS NOT NULL
            GROUP  BY questionnaire_id, question_id
        )
        SELECT
            f.study_id,
            f.questionnaire_id,
            f.question_id,
            f.question_concept_id,
            f.option_id,
            f.option_value,
            f.option_concept_id,
            COUNT(*)                                                  AS n,
            ROUND(100.0 * COUNT(*) / NULLIF(t.n_total, 0), 2)        AS pct
        FROM   fact_response f
        JOIN   totals t USING (questionnaire_id, question_id)
        WHERE  f.option_value IS NOT NULL
        GROUP  BY f.study_id, f.questionnaire_id, f.question_id,
                  f.question_concept_id, f.option_id, f.option_value,
                  f.option_concept_id, t.n_total
    """)


def _rebuild_agg_numeric_stats(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DELETE FROM agg_numeric_stats")
    conn.execute("""
        INSERT INTO agg_numeric_stats
            (study_id, questionnaire_id, question_id, question_concept_id,
             n, mean, median, std_dev, min_val, max_val, p25, p75)
        SELECT
            f.study_id,
            f.questionnaire_id,
            f.question_id,
            f.question_concept_id,
            COUNT(f.response_numeric)                                        AS n,
            AVG(f.response_numeric)                                          AS mean,
            MEDIAN(f.response_numeric)                                       AS median,
            STDDEV(f.response_numeric)                                       AS std_dev,
            MIN(f.response_numeric)                                          AS min_val,
            MAX(f.response_numeric)                                          AS max_val,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY f.response_numeric) AS p25,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY f.response_numeric) AS p75
        FROM   fact_response f
        JOIN   dim_question dq USING (question_id)
        WHERE  dq.question_type IN ('numeric', 'slider')
          AND  f.response_numeric IS NOT NULL
        GROUP  BY f.study_id, f.questionnaire_id, f.question_id, f.question_concept_id
    """)


def _rebuild_agg_session_completion(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DELETE FROM agg_session_completion")
    conn.execute("""
        INSERT INTO agg_session_completion
            (study_id, questionnaire_id, session_date_key, admin_mode,
             n_started, n_completed, completion_rate, median_duration_sec)
        SELECT
            study_id,
            questionnaire_id,
            session_date_key,
            COALESCE(admin_mode, 'unknown'),
            COUNT(*)                                                   AS n_started,
            SUM(is_complete::INTEGER)                                  AS n_completed,
            ROUND(SUM(is_complete::INTEGER)::DOUBLE / COUNT(*), 4)    AS completion_rate,
            CAST(MEDIAN(duration_sec) AS INTEGER)                     AS median_duration_sec
        FROM   dim_session
        WHERE  session_date_key IS NOT NULL
        GROUP  BY study_id, questionnaire_id, session_date_key, admin_mode
    """)


# ------------------------------------------------------------------
# Scoring
# ------------------------------------------------------------------

def _compute_scores(conn: duckdb.DuckDBPyConnection) -> int:
    """
    Evaluate all scoring rules against every session present in fact_response.
    Upserts agg_respondent_scores — safe to call repeatedly.
    """
    rules = conn.execute(
        "SELECT scoring_rule_id, questionnaire_id, name, formula "
        "FROM oltp.scoring_rule"
    ).fetchall()

    total = 0
    for rule_id, q_id, rule_name, formula in rules:
        items = conn.execute(
            "SELECT qq_id, weight, reverse_score "
            "FROM oltp.scoring_rule_item WHERE scoring_rule_id = ?",
            [rule_id],
        ).fetchall()
        if not items:
            continue

        weights     = {row[0]: (row[1], bool(row[2])) for row in items}
        qq_id_list  = ",".join(str(row[0]) for row in items)
        items_total = len(items)

        # Pre-compute max option_value per qq_id (needed for reverse scoring)
        max_vals: dict[int, float] = {}
        if any(w[1] for w in weights.values()):
            for qq_id in weights:
                row = conn.execute(f"""
                    SELECT MAX(TRY_CAST(ro.option_value AS DOUBLE))
                    FROM   oltp.response_option ro
                    JOIN   oltp.questionnaire_question qq ON ro.question_id = qq.question_id
                    WHERE  qq.qq_id = {qq_id}
                """).fetchone()
                if row and row[0] is not None:
                    max_vals[qq_id] = row[0]

        # Fetch all scoreable responses for this rule
        responses = conn.execute(f"""
            SELECT session_id, qq_id, response_numeric
            FROM   fact_response
            WHERE  qq_id IN ({qq_id_list})
              AND  questionnaire_id = {q_id}
              AND  response_numeric IS NOT NULL
        """).fetchall()

        # Group by session
        by_session: dict[int, list[tuple[int, float]]] = {}
        for session_id, qq_id, value in responses:
            by_session.setdefault(session_id, []).append((qq_id, value))

        if not by_session:
            continue

        # Delete stale scores for affected sessions
        sid_list = ",".join(str(s) for s in by_session)
        conn.execute(
            f"DELETE FROM agg_respondent_scores "
            f"WHERE scoring_rule_id = {rule_id} AND session_id IN ({sid_list})"
        )

        for session_id, item_responses in by_session.items():
            weighted: list[float] = []
            for qq_id, raw in item_responses:
                w, reverse = weights.get(qq_id, (1.0, False))
                val = (max_vals.get(qq_id, 0.0) - raw) if reverse else raw
                weighted.append(val * w)

            score = (
                sum(weighted) / len(weighted) if formula == "mean"
                else sum(weighted)
            )
            category = _lookup_category(conn, rule_id, score)

            resp_row = conn.execute(
                "SELECT respondent_id FROM dim_session WHERE session_id = ?",
                [session_id],
            ).fetchone()
            if resp_row is None:
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO agg_respondent_scores
                    (respondent_id, questionnaire_id, scoring_rule_id, scoring_rule_name,
                     session_id, score_raw, score_category, items_answered, items_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [resp_row[0], q_id, rule_id, rule_name, session_id,
                 score, category, len(item_responses), items_total],
            )
            total += 1

    return total


def _lookup_category(
    conn: duckdb.DuckDBPyConnection, scoring_rule_id: int, score: float | None
) -> str | None:
    if score is None:
        return None
    row = conn.execute(
        """
        SELECT label FROM oltp.scoring_category
        WHERE  scoring_rule_id = ?
          AND  (min_score IS NULL OR ? >= min_score)
          AND  (max_score IS NULL OR ? <= max_score)
        ORDER  BY display_order
        LIMIT  1
        """,
        [scoring_rule_id, score, score],
    ).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------
# dim_date
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# Lineage and equivalence mirrors
# ------------------------------------------------------------------

def _sync_dim_question_lineage(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DELETE FROM dim_question_lineage")
    conn.execute("""
        INSERT INTO dim_question_lineage
            (lineage_id, question_id, parent_question_id, change_type,
             change_description, effective_date, refreshed_at)
        SELECT lineage_id, question_id, parent_question_id, change_type,
               change_description,
               TRY_CAST(effective_date AS DATE),
               NOW()
        FROM oltp.question_lineage
    """)


def _sync_dim_question_equivalence(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DELETE FROM dim_question_equivalence")
    conn.execute("""
        INSERT INTO dim_question_equivalence
            (question_id_1, question_id_2, relationship, confidence,
             harmonization_notes, refreshed_at)
        SELECT question_id_1, question_id_2, relationship, confidence,
               harmonization_notes, NOW()
        FROM oltp.question_equivalence
    """)


def _sync_equivalence_group_ids(conn: duckdb.DuckDBPyConnection) -> None:
    """
    Compute connected components of the question_equivalence graph and write
    equivalence_group_id onto dim_question.

    Questions with no declared equivalences get NULL (singleton, no group).
    Questions linked by equivalent/near_equivalent edges share a group_id.
    """
    edges = conn.execute(
        "SELECT question_id_1, question_id_2 FROM oltp.question_equivalence "
        "WHERE relationship IN ('equivalent', 'near_equivalent')"
    ).fetchall()

    all_ids = [r[0] for r in conn.execute(
        "SELECT question_id FROM oltp.question"
    ).fetchall()]

    if not all_ids:
        return

    parent: dict[int, int] = {q: q for q in all_ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[min(px, py)] = max(px, py)

    for q1, q2 in edges:
        if q1 in parent and q2 in parent:
            union(q1, q2)

    # Only assign a group_id to questions in multi-member components
    root_members: dict[int, list[int]] = {}
    for q in all_ids:
        root = find(q)
        root_members.setdefault(root, []).append(q)

    roots_with_group = sorted(r for r, members in root_members.items() if len(members) > 1)
    root_to_group = {root: i + 1 for i, root in enumerate(roots_with_group)}

    for q in all_ids:
        group_id = root_to_group.get(find(q))  # None for singletons
        conn.execute(
            "UPDATE dim_question SET equivalence_group_id = ? WHERE question_id = ?",
            [group_id, q],
        )


# ------------------------------------------------------------------
# OMOP extraction
# ------------------------------------------------------------------

def _sync_omop_survey_conduct(conn: duckdb.DuckDBPyConnection) -> None:
    """One row per response_session → omop_survey_conduct."""
    conn.execute("DELETE FROM omop_survey_conduct")
    conn.execute("""
        INSERT INTO omop_survey_conduct
            (survey_conduct_id, person_id, survey_concept_id,
             survey_start_date, survey_end_date,
             assisted_concept_id, survey_source_value, refreshed_at)
        SELECT
            rs.session_id,
            pm.omop_person_id,
            q.concept_id,
            TRY_CAST(rs.started_at   AS DATE),
            TRY_CAST(rs.completed_at AS DATE),
            CASE WHEN rs.is_proxy = 1 THEN 532063 ELSE 0 END,
            rs.admin_mode,
            NOW()
        FROM   oltp.response_session rs
        JOIN   oltp.questionnaire     q  ON rs.questionnaire_id = q.questionnaire_id
        LEFT JOIN oltp.person_map     pm ON rs.respondent_id    = pm.respondent_id
    """)


def _sync_omop_observation(conn: duckdb.DuckDBPyConnection) -> None:
    """One row per concept-mapped answer atom → omop_observation."""
    conn.execute("DELETE FROM omop_observation")
    conn.execute("""
        INSERT INTO omop_observation
            (observation_id, person_id, observation_concept_id,
             observation_date, questionnaire_response_id,
             value_as_concept_id, value_as_string, value_as_number,
             observation_source_value, observation_type_concept_id, refreshed_at)
        SELECT
            r.response_id,
            pm.omop_person_id,
            q.concept_id,
            TRY_CAST(COALESCE(rs.completed_at, rs.started_at) AS DATE),
            r.session_id,
            ro.concept_id,
            r.response_text,
            COALESCE(r.response_numeric, TRY_CAST(ro.option_value AS DOUBLE)),
            q.link_id,
            32836,
            NOW()
        FROM   oltp.response              r
        JOIN   oltp.response_session      rs  ON r.session_id    = rs.session_id
        JOIN   oltp.questionnaire_question qq  ON r.qq_id         = qq.qq_id
        JOIN   oltp.question              q   ON qq.question_id  = q.question_id
        LEFT JOIN oltp.person_map         pm  ON rs.respondent_id = pm.respondent_id
        LEFT JOIN oltp.response_option    ro  ON r.option_id      = ro.option_id
        WHERE  q.concept_id IS NOT NULL
    """)


def _sync_omop_unmapped(conn: duckdb.DuckDBPyConnection) -> None:
    """Questions with no concept_id — pre-flight data quality check."""
    conn.execute("DELETE FROM omop_unmapped_questions")
    conn.execute("""
        INSERT INTO omop_unmapped_questions
            (question_id, link_id, question_text, source_instrument,
             response_count, refreshed_at)
        SELECT
            q.question_id,
            q.link_id,
            q.question_text,
            q.source_instrument,
            COUNT(r.response_id),
            NOW()
        FROM   oltp.question              q
        JOIN   oltp.questionnaire_question qq ON q.question_id = qq.question_id
        LEFT JOIN oltp.response            r  ON qq.qq_id      = r.qq_id
        WHERE  q.concept_id IS NULL
        GROUP  BY q.question_id, q.link_id, q.question_text, q.source_instrument
    """)


def _sync_dim_date(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO dim_date
            (date_key, year, quarter, month, week, day_of_week, day_of_year, is_weekend)
        SELECT DISTINCT
            session_date_key                          AS date_key,
            YEAR(session_date_key)                    AS year,
            QUARTER(session_date_key)                 AS quarter,
            MONTH(session_date_key)                   AS month,
            WEEK(session_date_key)                    AS week,
            DAYOFWEEK(session_date_key)               AS day_of_week,
            DAYOFYEAR(session_date_key)               AS day_of_year,
            DAYOFWEEK(session_date_key) IN (0, 6)     AS is_weekend
        FROM   dim_session
        WHERE  session_date_key IS NOT NULL
          AND  session_date_key NOT IN (SELECT date_key FROM dim_date)
    """)
