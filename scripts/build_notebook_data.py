"""Build a notebook data bundle: load YAML, seed responses, refresh OLAP,
emit denormalized parquet files into an output directory.

Generic. Any marimo notebook that needs a seeded study can call this with
its own arguments rather than maintaining a per-notebook copy.

Usage:
    uv run python scripts/build_notebook_data.py \\
        --yaml examples/health_intake_demo.yaml \\
        --output-dir examples/notebooks/public \\
        --n 500 \\
        --seed 20260503

The output directory is wiped and recreated. Files emitted:
    fact_response.parquet         (denormalized, includes option_text + grid labels)
    dim_question.parquet
    dim_response_option.parquet

dim_session and dim_respondent are deliberately excluded: they carry
wall-clock timestamps from the seed run (started_at, enrollment_date) that
break parquet-byte determinism across days. The current notebooks don't use
them. If a future notebook needs respondent or session metadata, extend this
list and either (a) accept rebuild-on-build, or (b) normalize the timestamps
to a fixed baseline before export.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent

TABLES = [
    "fact_response",
    "dim_question",
    "dim_response_option",
]


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    if result.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}")


def build(yaml_path: Path, output_dir: Path, n: int, seed: int) -> None:
    if not yaml_path.exists():
        sys.exit(f"missing yaml: {yaml_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        oltp_path = tmp / "study.db"
        olap_path = tmp / "analytics.duckdb"

        _run(["uv", "run", "quickq", "init", str(oltp_path)])
        _run(["uv", "run", "quickq", "load", str(yaml_path), str(oltp_path)])

        qid_row = duckdb.connect(":memory:").execute(
            "SELECT questionnaire_id FROM sqlite_scan(?, 'questionnaire') ORDER BY questionnaire_id LIMIT 1",
            [str(oltp_path)],
        ).fetchone()
        if qid_row is None:
            sys.exit("no questionnaire found after load")
        qid = qid_row[0]

        _run(["uv", "run", "quickq", "seed", str(oltp_path), str(qid),
              "--n", str(n), "--seed", str(seed)])
        _run(["uv", "run", "quickq", "refresh", str(oltp_path), str(olap_path)])

        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)

        con = duckdb.connect(":memory:")
        con.execute(f"ATTACH '{olap_path}' AS olap (READ_ONLY)")
        con.execute(f"ATTACH '{oltp_path}' AS oltp (TYPE sqlite, READ_ONLY)")

        con.execute("""
            CREATE TEMP TABLE fact_response AS
            SELECT
                fr.response_id,
                fr.session_id,
                fr.respondent_id,
                fr.question_id,
                fr.option_id,
                fr.option_value,
                opt.option_text,
                fr.response_numeric,
                fr.response_text,
                fr.response_boolean,
                fr.response_date,
                fr.repeat_index,
                fr.grid_row_id,
                fr.grid_column_id,
                gr.row_text     AS grid_row_text,
                gc.column_text  AS grid_column_text,
                gc.column_value AS grid_column_value
            FROM olap.fact_response fr
            LEFT JOIN olap.dim_response_option opt ON fr.option_id      = opt.option_id
            LEFT JOIN oltp.grid_row             gr ON fr.grid_row_id    = gr.row_id
            LEFT JOIN oltp.grid_column          gc ON fr.grid_column_id = gc.column_id
        """)
        con.execute("CREATE TEMP TABLE dim_question        AS SELECT * FROM olap.dim_question")
        con.execute("CREATE TEMP TABLE dim_response_option AS SELECT * FROM olap.dim_response_option")

        for table in TABLES:
            out_path = output_dir / f"{table}.parquet"
            con.execute(
                f"COPY {table} TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        con.close()

    print()
    total_kb = 0.0
    for table in TABLES:
        size_kb = (output_dir / f"{table}.parquet").stat().st_size / 1024
        total_kb += size_kb
        print(f"  {table + '.parquet':<32} {size_kb:>8.1f} KB")
    print(f"  {'total':<32} {total_kb:>8.1f} KB")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--yaml", required=True, type=Path,
                   help="Path to the questionnaire YAML")
    p.add_argument("--output-dir", required=True, type=Path,
                   help="Directory to write parquet files (wiped before write)")
    p.add_argument("--n", type=int, default=500,
                   help="Number of synthetic respondents to seed (default: 500)")
    p.add_argument("--seed", type=int, default=20260503,
                   help="Seed for reproducible synthetic data (default: 20260503)")
    args = p.parse_args()
    build(args.yaml.resolve(), args.output_dir.resolve(), args.n, args.seed)


if __name__ == "__main__":
    main()
