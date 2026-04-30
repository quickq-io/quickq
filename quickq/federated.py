"""
Federated query executor for multi-site studies.

Runs a researcher-authored SQL query against the local OLAP database and
returns aggregate-only results with disclosure control applied.

Enforcement has two layers:

  1. Pre-execution validation (sqlglot AST):
     - Blocked columns (respondent_id, session_id, external_id) must not
       appear anywhere in the statement — they identify individual participants.
     - At least one aggregate function must appear in the outermost SELECT.
     - Window functions (OVER ...) are blocked. They satisfy the aggregate
       check syntactically but return one row per input row, not aggregated
       results, so they are not safe for federated output.
     - File-reading functions (read_csv, sqlite_scan, read_parquet, etc.) are
       blocked. DuckDB can read arbitrary files even on a read-only connection;
       allowing them would let a query reach outside the OLAP schema.
     - Aggregate output aliases are collected and returned so suppression is
       applied to COUNT(*) AS responses just as it would be to COUNT(*) AS n.

  2. Post-execution cell suppression:
     - Count columns are identified by two independent mechanisms that are
       unioned: (a) name-pattern match (n, count, *_count, n_*, freq*, total),
       and (b) the set of aliases of COUNT/SUM expressions from the validator.
       Gap (b) ensures suppression applies regardless of what alias the query
       author chose.
     - Any row where a count column falls below min_cell is removed entirely.
       Whole-row removal prevents information leakage through subtraction
       (known total − visible cells = suppressed cell value).
     - The number of suppressed rows is reported so the coordinating center
       knows the result set is incomplete.

Supported SQL
-------------
The following aggregate functions satisfy the aggregate requirement and may
appear as the sole aggregate in a query:

    COUNT(*), COUNT(col), COUNT(DISTINCT col)
    SUM(col), AVG(col), MIN(col), MAX(col)
    STDDEV(col), STDDEV_SAMP(col), STDDEV_POP(col)
    VARIANCE(col), VAR_POP(col)
    MEDIAN(col), PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY col)
    APPROX_COUNT_DISTINCT(col), APPROX_QUANTILE(col, p)
    ANY_VALUE(col)

The following SQL features work freely alongside any of the above:

    GROUP BY, HAVING, DISTINCT
    GROUPING SETS, CUBE, ROLLUP       -- multi-level crosstabs
    WITH (CTEs), subqueries, JOINs    -- across any OLAP tables
    CASE WHEN inside aggregates
    COUNT(*) FILTER (WHERE condition) -- conditional counts
    CAST, string functions, arithmetic on aggregate outputs

The following DuckDB aggregates also work but need a companion aggregate
from the list above to satisfy the aggregate gate (e.g. add COUNT(*) AS n):

    STRING_AGG(col, sep), LIST_AGG(col)
    LIST(col), ARRAY_AGG(col)
    MODE(col), HISTOGRAM(col)
    KURTOSIS(col), SKEWNESS(col)
    BIT_AND(col), BIT_OR(col), BOOL_AND(col), BOOL_OR(col)

What is not permitted:

    Window functions (OVER ...)           -- return one row per input row
    File-reading functions                -- read_csv, read_parquet, sqlite_scan, etc.
    Columns that identify participants    -- respondent_id, session_id, external_id,
                                             subject_id, interviewer_id

Typical workflow:
    quickq refresh study.db analytics.duckdb
    quickq federated-query prevalence.sql analytics.duckdb --min-cell 5 --output site_a.json
    # Send site_a.json to coordinating center
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import sqlglot
import sqlglot.expressions as exp


# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

_BLOCKED_COLUMNS: frozenset[str] = frozenset({
    "respondent_id",
    "session_id",
    "external_id",
    "subject_id",
    "interviewer_id",
})

# Functions that read files or external databases — blocked even on a
# read-only DuckDB connection.
_BLOCKED_FUNCTIONS: frozenset[str] = frozenset({
    "read_csv", "read_csv_auto",
    "read_parquet",
    "read_json", "read_json_auto", "read_json_objects", "read_ndjson",
    "read_text", "read_blob",
    "sqlite_scan", "sqlite_query",
    "mysql_scan", "mysql_query",
    "postgres_scan", "postgres_query",
    "glob", "delta_scan", "iceberg_scan", "hudi_scan",
})

# All aggregate function types that prove the SELECT produces aggregate output.
_AGG_TYPES = (
    exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max,
    exp.Stddev, exp.StddevSamp, exp.StddevPop,
    exp.Variance, exp.VariancePop,
    exp.Median, exp.PercentileCont, exp.PercentileDisc,
    exp.ApproxDistinct, exp.ApproxQuantile,
    exp.AnyValue,
)

# Subset of aggregates that produce counts — their output aliases are always
# candidates for cell suppression regardless of naming convention.
_COUNT_AGG_TYPES = (exp.Count, exp.Sum)

# Name patterns for count columns — matched against output column aliases.
_COUNT_COLUMN_RE = re.compile(
    r"^(n|count|cnt|freq(uency)?|total|n_.+|.+_count|.+_n|num_.+|.+_num|.+_freq)$",
    re.IGNORECASE,
)


# ------------------------------------------------------------------
# Result type
# ------------------------------------------------------------------

@dataclass
class FederatedQueryResult:
    rows: list[dict]
    columns: list[str]
    rows_total: int
    rows_suppressed: int
    suppression_threshold: int
    count_columns: list[str]
    query_hash: str


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def run_federated_query(
    olap_path: str | Path,
    sql: str,
    *,
    min_cell: int = 5,
) -> FederatedQueryResult:
    """
    Validate and execute sql against the OLAP database at olap_path.

    Returns a FederatedQueryResult with disclosure control applied.
    Raises ValueError if the query fails validation or execution.
    """
    sql = sql.strip().rstrip(";")
    count_agg_aliases = _validate_query(sql)

    conn = duckdb.connect(str(olap_path), read_only=True)
    try:
        rel = conn.execute(sql)
        columns = [desc[0] for desc in rel.description]
        raw_rows = [dict(zip(columns, row)) for row in rel.fetchall()]
    except duckdb.Error as exc:
        raise ValueError(f"Query execution failed: {exc}") from exc
    finally:
        conn.close()

    count_cols = _find_count_columns(columns, raw_rows, forced=count_agg_aliases)
    kept_rows, suppressed = _suppress_small_cells(raw_rows, count_cols, min_cell)

    return FederatedQueryResult(
        rows=kept_rows,
        columns=columns,
        rows_total=len(raw_rows),
        rows_suppressed=suppressed,
        suppression_threshold=min_cell,
        count_columns=count_cols,
        query_hash=hashlib.sha256(sql.encode()).hexdigest()[:16],
    )


def result_to_dict(result: FederatedQueryResult) -> dict:
    """Serialise a FederatedQueryResult to a JSON-compatible dict."""
    return {
        "query_hash": result.query_hash,
        "columns": result.columns,
        "rows": result.rows,
        "disclosure_control": {
            "suppression_threshold": result.suppression_threshold,
            "count_columns": result.count_columns,
            "rows_total": result.rows_total,
            "rows_suppressed": result.rows_suppressed,
        },
    }


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------

def _validate_query(sql: str) -> set[str]:
    """
    Parse sql and raise ValueError if it violates aggregate-only rules.

    Returns the set of output column aliases that are COUNT/SUM expressions,
    so the caller can enforce suppression on them regardless of naming.
    """
    try:
        parsed = sqlglot.parse_one(sql, dialect="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise ValueError(f"Invalid SQL: {exc}") from exc

    # Rule 1: no blocked columns anywhere in the statement.
    for col in parsed.find_all(exp.Column):
        if col.name.lower() in _BLOCKED_COLUMNS:
            raise ValueError(
                f"Column {col.name!r} is not permitted in federated queries — "
                "it directly identifies individual participants."
            )

    # Rule 2: no file-reading functions anywhere in the statement.
    # Some DuckDB readers (read_csv, read_parquet) are named expression types
    # in sqlglot; others (sqlite_scan, mysql_scan, ...) parse as Anonymous.
    _BLOCKED_EXPR_TYPES: dict[type, str] = {
        exp.ReadCSV: "read_csv",
        exp.ReadParquet: "read_parquet",
        exp.Glob: "glob",
    }
    for node in parsed.walk():
        if type(node) in _BLOCKED_EXPR_TYPES:
            name = _BLOCKED_EXPR_TYPES[type(node)]
            raise ValueError(
                f"Function {name!r} is not permitted in federated queries — "
                "it can read files outside the OLAP schema."
            )
    for func in parsed.find_all(exp.Anonymous):
        if func.name.lower() in _BLOCKED_FUNCTIONS:
            raise ValueError(
                f"Function {func.name!r} is not permitted in federated queries — "
                "it can read files outside the OLAP schema."
            )

    # Rule 3: no window functions. They satisfy the aggregate check syntactically
    # but return one row per input row.
    for _ in parsed.find_all(exp.Window):
        raise ValueError(
            "Window functions (OVER ...) are not permitted in federated queries. "
            "They return one row per input row, not aggregate results."
        )

    # Rule 4: at least one aggregate function in the outermost SELECT.
    top_select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
    if top_select is None:
        raise ValueError("Only SELECT statements are permitted in federated queries.")

    select_exprs = top_select.args.get("expressions", [])
    has_agg = any(
        isinstance(node, _AGG_TYPES)
        for expr in select_exprs
        for node in [expr] + list(expr.find_all(*_AGG_TYPES))
    )
    if not has_agg:
        raise ValueError(
            "Federated queries must produce aggregate results. "
            "Include at least one of: COUNT(), SUM(), AVG(), MIN(), MAX(), STDDEV()."
        )

    # Collect aliases of COUNT/SUM expressions for downstream suppression.
    count_agg_aliases: set[str] = set()
    for expr in select_exprs:
        if not isinstance(expr, exp.Alias):
            continue
        inner = expr.this
        is_count_agg = isinstance(inner, _COUNT_AGG_TYPES) or any(
            isinstance(n, _COUNT_AGG_TYPES) for n in inner.find_all(*_COUNT_AGG_TYPES)
        )
        if is_count_agg and expr.alias:
            count_agg_aliases.add(expr.alias.lower())

    return count_agg_aliases


# ------------------------------------------------------------------
# Cell suppression
# ------------------------------------------------------------------

def _find_count_columns(
    columns: list[str],
    rows: list[dict],
    *,
    forced: set[str] | None = None,
) -> list[str]:
    """
    Return column names that should be checked for cell-size suppression.

    A column qualifies by either mechanism:
    - Name-pattern match (_COUNT_COLUMN_RE), OR
    - Present in forced (COUNT/SUM output aliases from the validator)

    In both cases, values must be non-negative integers in the sample rows —
    this prevents suppressing AVG or STDDEV outputs that happen to have a
    matching alias name.
    """
    forced = forced or set()
    if not rows:
        return list(forced & set(columns))

    found = []
    for col in columns:
        if not (_COUNT_COLUMN_RE.match(col) or col.lower() in forced):
            continue
        sample = [row[col] for row in rows[:20] if row.get(col) is not None]
        if not sample:
            continue
        if all(isinstance(v, (int, float)) and v >= 0 and float(v).is_integer() for v in sample):
            found.append(col)
    return found


def _suppress_small_cells(
    rows: list[dict],
    count_cols: list[str],
    min_cell: int,
) -> tuple[list[dict], int]:
    """
    Remove rows where any count column is below min_cell.

    Whole-row removal prevents information leakage through subtraction.
    """
    if not count_cols:
        return rows, 0

    kept: list[dict] = []
    suppressed = 0
    for row in rows:
        below = any(
            isinstance(row.get(c), (int, float)) and row[c] < min_cell
            for c in count_cols
        )
        if below:
            suppressed += 1
        else:
            kept.append(row)
    return kept, suppressed
