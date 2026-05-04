"""
Fork a quickq SQLite study database: copy structure and metadata for a single
questionnaire into a fresh output database, leaving responses behind.

Useful for:
  - Multi-site federation: each site gets a structural fork to collect against;
    the existing merge command reassembles them.
  - Dev / staging / prod handoff: scaffold a new DB that mirrors prod's
    structure for testing without exposing real responses.
  - Generational handoff: pass a study's bones to another investigator with
    explicit provenance and no leaked respondent data.
  - Versioning: fork v1's structure as the starting point for v2.

Inclusion / exclusion is an explicit allowlist (never a blocklist) so the
default never leaks response data. Provenance is recorded in the output
database's tool_audit_log so the new study carries a queryable link back to
its source.

Usage:
    from quickq.fork import fork_database
    result = fork_database(
        source_path="prod/study.db",
        questionnaire_id=1,
        output_path="dev/scratch.db",
        new_version="2.0",
    )
"""
from __future__ import annotations

import getpass
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .schema import init_oltp, open_oltp


class ForkError(Exception):
    pass


@dataclass
class ForkResult:
    source: str
    output: str
    source_questionnaire_id: int
    new_questionnaire_id: int
    source_canonical_url: str | None
    source_version: str
    new_version: str
    questions_copied: int = 0
    options_copied: int = 0
    grid_rows_copied: int = 0
    grid_columns_copied: int = 0
    skip_rules_copied: int = 0
    scoring_rules_copied: int = 0
    concepts_copied: int = 0
    lineage_records_copied: int = 0


def fork_database(
    source_path: str | Path,
    questionnaire_id: int,
    output_path: str | Path,
    *,
    new_version: str | None = None,
    site_id: str | None = None,
    reset_study_metadata: bool = False,
    note: str | None = None,
    overwrite: bool = False,
) -> ForkResult:
    """
    Fork the structure and metadata of one questionnaire from source_path
    into a freshly initialized output_path. No responses, sessions,
    respondents, audit history, or compliance records are copied.

    Raises ForkError if:
      - output_path already exists and overwrite is False
      - questionnaire_id is not present in the source
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if output_path.exists():
        if not overwrite:
            raise ForkError(
                f"{output_path} already exists. Pass overwrite=True to replace it."
            )
        output_path.unlink()

    src = open_oltp(source_path, read_only=True)
    out = init_oltp(output_path)
    try:
        result = _fork_one(
            src,
            out,
            source_path=str(source_path),
            output_path=str(output_path),
            questionnaire_id=questionnaire_id,
            new_version=new_version,
            site_id=site_id,
            reset_study_metadata=reset_study_metadata,
            note=note,
        )
        out.commit()
    except Exception:
        out.close()
        src.close()
        output_path.unlink(missing_ok=True)
        raise

    out.close()
    src.close()
    return result


def _fork_one(
    src: sqlite3.Connection,
    out: sqlite3.Connection,
    *,
    source_path: str,
    output_path: str,
    questionnaire_id: int,
    new_version: str | None,
    site_id: str | None,
    reset_study_metadata: bool,
    note: str | None,
) -> ForkResult:
    src.row_factory = sqlite3.Row

    # ----- locate the source questionnaire ---------------------------------
    q_row = src.execute(
        "SELECT * FROM questionnaire WHERE questionnaire_id = ?",
        (questionnaire_id,),
    ).fetchone()
    if q_row is None:
        raise ForkError(
            f"questionnaire_id {questionnaire_id} not found in {source_path}"
        )

    source_canonical_url = q_row["canonical_url"]
    source_version = q_row["version"]
    new_version_value = new_version or source_version

    # ----- gather the question_id set referenced by this questionnaire -----
    qq_rows = src.execute(
        "SELECT * FROM questionnaire_question WHERE questionnaire_id = ?",
        (questionnaire_id,),
    ).fetchall()
    question_ids = sorted({r["question_id"] for r in qq_rows})

    # ----- copy vocabulary (small global registry, copy whole table) -------
    # Direct row-by-row copy (rather than ATTACH + INSERT...SELECT) so we
    # don't conflict with the read-only connection already open on `src`.
    for v in src.execute("SELECT * FROM vocabulary").fetchall():
        _insert_row(out, "vocabulary", dict(v), or_ignore=True)

    # concepts: copy only those referenced by our question / option / grid set
    concept_ids = _collect_concept_ids(src, question_ids)
    result_concepts = 0
    for c_id in concept_ids:
        crow = src.execute(
            "SELECT * FROM concept WHERE concept_id = ?", (c_id,)
        ).fetchone()
        if crow is not None:
            _insert_row(out, "concept", dict(crow), or_ignore=True)
            result_concepts += 1

    try:
        # study row referenced by this questionnaire
        study_row = src.execute(
            "SELECT * FROM study WHERE study_id = ?", (q_row["study_id"],)
        ).fetchone() if q_row["study_id"] is not None else None
        if study_row is not None:
            study_dict = dict(study_row)
            if reset_study_metadata:
                study_dict.update({
                    "name": study_dict["name"],  # name kept; rest blanked
                    "description": None,
                    "principal_investigator": None,
                    "irb_number": None,
                    "start_date": None,
                    "end_date": None,
                })
            _insert_row(out, "study", study_dict)

        # questionnaire row (with optional version bump)
        q_dict = dict(q_row)
        q_dict["version"] = new_version_value
        # New fork starts as draft; the recipient marks it active when ready.
        q_dict["fhir_status"] = "draft"
        q_dict["superseded_by"] = None
        _insert_row(out, "questionnaire", q_dict)
        new_questionnaire_id = q_dict["questionnaire_id"]

        # questions: copy each question row (respecting INSERT OR IGNORE in
        # case a library-shared question_id is already present after init).
        for qid in question_ids:
            qrow = src.execute(
                "SELECT * FROM question WHERE question_id = ?", (qid,)
            ).fetchone()
            _insert_row(out, "question", dict(qrow), or_ignore=True)
        result_questions = len(question_ids)

        # response_option_sets (copy only those referenced)
        option_set_ids = sorted({
            r["option_set_id"] for r in src.execute(
                f"SELECT DISTINCT option_set_id FROM response_option WHERE question_id IN ({','.join('?' for _ in question_ids)}) AND option_set_id IS NOT NULL",
                tuple(question_ids),
            ).fetchall() if r["option_set_id"] is not None
        }) if question_ids else []
        for set_id in option_set_ids:
            os_row = src.execute(
                "SELECT * FROM response_option_set WHERE option_set_id = ?",
                (set_id,),
            ).fetchone()
            _insert_row(out, "response_option_set", dict(os_row), or_ignore=True)

        # response_option (rows for our question set)
        result_options = 0
        if question_ids:
            placeholders = ",".join("?" for _ in question_ids)
            opt_rows = src.execute(
                f"SELECT * FROM response_option WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            ).fetchall()
            for opt in opt_rows:
                _insert_row(out, "response_option", dict(opt))
            result_options = len(opt_rows)

        # grid_row + grid_column for our question set
        result_grid_rows = 0
        result_grid_cols = 0
        if question_ids:
            placeholders = ",".join("?" for _ in question_ids)
            grow_rows = src.execute(
                f"SELECT * FROM grid_row WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            ).fetchall()
            for r in grow_rows:
                _insert_row(out, "grid_row", dict(r))
            result_grid_rows = len(grow_rows)

            gcol_rows = src.execute(
                f"SELECT * FROM grid_column WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            ).fetchall()
            for r in gcol_rows:
                _insert_row(out, "grid_column", dict(r))
            result_grid_cols = len(gcol_rows)

        # section + questionnaire_question (placement)
        sec_rows = src.execute(
            "SELECT * FROM section WHERE questionnaire_id = ?",
            (questionnaire_id,),
        ).fetchall()
        for s in sec_rows:
            _insert_row(out, "section", dict(s))

        for qq in qq_rows:
            _insert_row(out, "questionnaire_question", dict(qq))

        # skip_rule (rows for our placement set)
        qq_ids = [r["qq_id"] for r in qq_rows]
        result_skip = 0
        if qq_ids:
            placeholders = ",".join("?" for _ in qq_ids)
            skip_rows = src.execute(
                f"SELECT * FROM skip_rule WHERE qq_id IN ({placeholders})",
                tuple(qq_ids),
            ).fetchall()
            for sr in skip_rows:
                _insert_row(out, "skip_rule", dict(sr))
            result_skip = len(skip_rows)

        # scoring_rule + scoring_category + scoring_rule_item
        scoring_rows = src.execute(
            "SELECT * FROM scoring_rule WHERE questionnaire_id = ?",
            (questionnaire_id,),
        ).fetchall()
        for sr in scoring_rows:
            _insert_row(out, "scoring_rule", dict(sr))
        rule_ids = [r["scoring_rule_id"] for r in scoring_rows]
        if rule_ids:
            placeholders = ",".join("?" for _ in rule_ids)
            cat_rows = src.execute(
                f"SELECT * FROM scoring_category WHERE scoring_rule_id IN ({placeholders})",
                tuple(rule_ids),
            ).fetchall()
            for r in cat_rows:
                _insert_row(out, "scoring_category", dict(r))
            item_rows = src.execute(
                f"SELECT * FROM scoring_rule_item WHERE scoring_rule_id IN ({placeholders})",
                tuple(rule_ids),
            ).fetchall()
            for r in item_rows:
                _insert_row(out, "scoring_rule_item", dict(r))

        # question_lineage (only when both endpoints are in our question set)
        result_lineage = 0
        if question_ids:
            placeholders = ",".join("?" for _ in question_ids)
            lineage_rows = src.execute(
                f"""SELECT * FROM question_lineage
                    WHERE question_id IN ({placeholders})
                      AND parent_question_id IN ({placeholders})""",
                tuple(question_ids) * 2,
            ).fetchall()
            for r in lineage_rows:
                _insert_row(out, "question_lineage", dict(r))
            result_lineage = len(lineage_rows)
    finally:
        pass

    # ----- record fork provenance in the output's audit log ----------------
    try:
        performed_by = getpass.getuser()
    except Exception:
        performed_by = None
    details = {
        "source_path": source_path,
        "source_canonical_url": source_canonical_url,
        "source_version": source_version,
        "source_questionnaire_id": questionnaire_id,
        "new_questionnaire_id": new_questionnaire_id,
        "new_version": new_version_value,
        "site_id": site_id,
        "reset_study_metadata": reset_study_metadata,
        "note": note,
        "forked_at": datetime.now(timezone.utc).isoformat(),
    }
    out.execute(
        "INSERT INTO tool_audit_log (study_id, operation, performed_by, details) VALUES (?, ?, ?, ?)",
        (q_row["study_id"], "fork", performed_by, json.dumps(details)),
    )

    return ForkResult(
        source=source_path,
        output=output_path,
        source_questionnaire_id=questionnaire_id,
        new_questionnaire_id=new_questionnaire_id,
        source_canonical_url=source_canonical_url,
        source_version=source_version,
        new_version=new_version_value,
        questions_copied=result_questions,
        options_copied=result_options,
        grid_rows_copied=result_grid_rows,
        grid_columns_copied=result_grid_cols,
        skip_rules_copied=result_skip,
        scoring_rules_copied=len(scoring_rows),
        concepts_copied=result_concepts,
        lineage_records_copied=result_lineage,
    )


def _insert_row(
    conn: sqlite3.Connection,
    table: str,
    row: dict,
    *,
    or_ignore: bool = False,
) -> None:
    """Insert a dict-shaped row into `table`, preserving the source PK."""
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    columns = ",".join(cols)
    verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
    conn.execute(
        f"{verb} INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(row[c] for c in cols),
    )


def _collect_concept_ids(
    src: sqlite3.Connection,
    question_ids: list[int],
) -> list[int]:
    """Gather every concept_id referenced (transitively) by the question set."""
    if not question_ids:
        return []
    ids: set[int] = set()
    placeholders = ",".join("?" for _ in question_ids)
    for query in (
        f"SELECT concept_id FROM question        WHERE question_id IN ({placeholders}) AND concept_id IS NOT NULL",
        f"SELECT concept_id FROM response_option WHERE question_id IN ({placeholders}) AND concept_id IS NOT NULL",
        f"SELECT concept_id FROM grid_row        WHERE question_id IN ({placeholders}) AND concept_id IS NOT NULL",
        f"SELECT concept_id FROM grid_column     WHERE question_id IN ({placeholders}) AND concept_id IS NOT NULL",
    ):
        for r in src.execute(query, tuple(question_ids)).fetchall():
            ids.add(r["concept_id"])
    return sorted(ids)
