import sqlite3
from pathlib import Path

_SQL_DIR = Path(__file__).parent.parent / "sql"


def _read_sql(name: str) -> str:
    return (_SQL_DIR / name).read_text()


def open_oltp(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    uri = f"file:{path}{'?mode=ro' if read_only else ''}{'&' if read_only else '?'}cache=shared"
    conn = sqlite3.connect(uri, uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    if not read_only:
        conn.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA foreign_keys = ON;
            PRAGMA synchronous = NORMAL;
        """)
    return conn


def create_oltp_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_read_sql("oltp_schema.sql"))
    conn.commit()


def create_olap_schema(conn) -> None:
    """conn is a duckdb.DuckDBPyConnection."""
    conn.execute(_read_sql("olap_schema.sql"))


def init_oltp(path: str | Path) -> sqlite3.Connection:
    """Create a new OLTP database at path, apply schema, return open connection."""
    conn = open_oltp(path)
    create_oltp_schema(conn)
    return conn


def migrate_oltp(conn: sqlite3.Connection) -> list[str]:
    """
    Apply additive schema migrations to an existing OLTP database.
    Safe to run on a current database — skips columns/tables that already exist.
    Returns a list of migration steps that were applied.
    """
    applied: list[str] = []

    def _add_column(table: str, column: str, definition: str) -> None:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            applied.append(f"ADD COLUMN {table}.{column}")
        except sqlite3.OperationalError:
            pass  # column already exists

    _add_column("question",     "internal_note",  "TEXT")
    _add_column("grid_column", "column_value",   "TEXT")
    _add_column("questionnaire_question", "status",
                "TEXT NOT NULL DEFAULT 'active'")
    _add_column("questionnaire_question", "status_changed_at", "TEXT")
    _add_column("questionnaire_question", "status_notes", "TEXT")
    _add_column("questionnaire_question", "count_qq_id",
                "INTEGER REFERENCES questionnaire_question (qq_id)")
    _add_column("response", "repeat_index", "INTEGER")

    # New tables are handled by CREATE TABLE IF NOT EXISTS in the DDL
    create_oltp_schema(conn)

    conn.commit()
    return applied
