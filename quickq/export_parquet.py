"""
Export the quickq OLAP star schema to Parquet files.

One .parquet file is written per table into the output directory.
The resulting files can be ingested by BigQuery, Snowflake, Databricks,
or any columnar warehouse without those tools needing to know about quickq.

Typical workflow:
    quickq refresh study.db analytics.duckdb
    quickq export-parquet analytics.duckdb ./parquet_export/
    # Upload ./parquet_export/ to cloud storage / warehouse
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb


# Tables exported by default, in dependency order.
# refresh_log is intentionally excluded (internal operational metadata).
_DEFAULT_TABLES = [
    # Dimensions
    "dim_date",
    "dim_study",
    "dim_questionnaire",
    "dim_concept",
    "dim_question",
    "dim_response_option",
    "dim_respondent",
    "dim_session",
    # Versioning dimensions
    "dim_question_lineage",
    "dim_question_equivalence",
    # Fact
    "fact_response",
    # Aggregates
    "agg_question_distribution",
    "agg_numeric_stats",
    "agg_session_completion",
    "agg_respondent_scores",
    # OMOP
    "omop_survey_conduct",
    "omop_observation",
    "omop_unmapped_questions",
]


@dataclass
class ExportParquetResult:
    olap_path: str
    output_dir: str
    files: dict[str, str] = field(default_factory=dict)   # table → file path
    rows: dict[str, int] = field(default_factory=dict)    # table → row count
    skipped: list[str] = field(default_factory=list)      # tables not found in OLAP


def export_parquet(
    olap_path: str | Path,
    output_dir: str | Path,
    *,
    tables: list[str] | None = None,
    overwrite: bool = False,
) -> ExportParquetResult:
    """
    Export OLAP tables from olap_path to Parquet files in output_dir.

    tables: list of table names to export. Defaults to all star schema tables.
    overwrite: if False, raises FileExistsError if any output file already exists.
    """
    olap_path  = Path(olap_path)
    output_dir = Path(output_dir)
    tables     = tables if tables is not None else list(_DEFAULT_TABLES)

    output_dir.mkdir(parents=True, exist_ok=True)

    if not overwrite:
        conflicts = [output_dir / f"{t}.parquet" for t in tables
                     if (output_dir / f"{t}.parquet").exists()]
        if conflicts:
            names = ", ".join(p.name for p in conflicts)
            raise FileExistsError(
                f"Output files already exist: {names}. Pass overwrite=True to replace them."
            )

    result = ExportParquetResult(
        olap_path=str(olap_path),
        output_dir=str(output_dir),
    )

    conn = duckdb.connect(str(olap_path), read_only=True)
    try:
        existing = {
            row[0] for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }

        for table in tables:
            if table not in existing:
                result.skipped.append(table)
                continue

            out_file = output_dir / f"{table}.parquet"
            conn.execute(
                f"COPY {table} TO '{out_file}' (FORMAT PARQUET)"
            )
            row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            result.files[table] = str(out_file)
            result.rows[table] = row_count
    finally:
        conn.close()

    return result
