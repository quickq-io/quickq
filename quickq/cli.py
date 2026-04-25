import click
from pathlib import Path
from .schema import init_oltp, open_oltp
from .library_loader import load_all_libraries, list_library_questions
from .loader import load_yaml
from .administration import data_dictionary, format_data_dict_markdown, format_data_dict_csv
from .renderer_fhir import export_fhir_json
from .parser_fhir import import_fhir
from .parser_fhir_response import import_fhir_response
from .olap_schema import refresh as olap_refresh, init_olap
from .renderer_md import generate_report
from .preview import preview as preview_questionnaire, build_preview_html


@click.group()
def main() -> None:
    """quickq — health and epidemiology questionnaire tool."""


@main.command()
@click.argument("db_path", type=click.Path())
@click.option("--with-library", is_flag=True, help="Seed the standard question library.")
def init(db_path: str, with_library: bool) -> None:
    """Create a new OLTP database at DB_PATH."""
    conn = init_oltp(db_path)
    if with_library:
        counts = load_all_libraries(conn)
        total = sum(counts.values())
        instruments = ", ".join(counts.keys())
        click.echo(f"Initialized {db_path} with {total} library questions ({instruments}).")
    else:
        click.echo(f"Initialized {db_path}.")


@main.command("load")
@click.argument("yaml_path", type=click.Path(exists=True))
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--study-id", type=int, default=None, help="Associate with an existing study.")
def load_cmd(yaml_path: str, db_path: str, study_id: int | None) -> None:
    """Compile a YAML questionnaire definition into DB_PATH."""
    conn = open_oltp(db_path)
    qid = load_yaml(conn, yaml_path, study_id=study_id)
    click.echo(f"Loaded questionnaire id={qid}.")


@main.command("library")
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--instrument", default=None, help="Filter by instrument name.")
def library_cmd(db_path: str, instrument: str | None) -> None:
    """List available library questions in DB_PATH."""
    conn = open_oltp(db_path, read_only=True)
    questions = list_library_questions(conn)
    if instrument:
        questions = [q for q in questions if q["source_instrument"] == instrument]
    if not questions:
        click.echo("No library questions found.")
        return
    for q in questions:
        concept = f"  [{q['vocabulary_id']}:{q['concept_code']}]" if q["concept_code"] else ""
        click.echo(f"{q['source_instrument']:20s}  {q['link_id']:30s}  {q['question_text'][:60]}{concept}")


@main.command("data-dict")
@click.argument("db_path", type=click.Path(exists=True))
@click.argument("questionnaire_id", type=int)
@click.option("--format", "fmt", type=click.Choice(["markdown", "csv"]), default="markdown")
@click.option("--include-deprecated", is_flag=True, help="Include deprecated questions.")
@click.option("--output", "-o", type=click.Path(), default=None, help="Write to file instead of stdout.")
def data_dict_cmd(db_path: str, questionnaire_id: int, fmt: str, include_deprecated: bool, output: str | None) -> None:
    """Generate a data dictionary for a questionnaire."""
    conn = open_oltp(db_path, read_only=True)
    rows = data_dictionary(conn, questionnaire_id, include_deprecated=include_deprecated)
    if fmt == "csv":
        text = format_data_dict_csv(rows)
    else:
        q_name = conn.execute(
            "SELECT name FROM questionnaire WHERE questionnaire_id = ?", (questionnaire_id,)
        ).fetchone()
        title = q_name["name"] if q_name else f"Questionnaire {questionnaire_id}"
        text = format_data_dict_markdown(rows, title=title)
    if output:
        Path(output).write_text(text)
        click.echo(f"Wrote {len(rows)} rows to {output}.")
    else:
        click.echo(text)


@main.command("import-fhir")
@click.argument("fhir_path", type=click.Path(exists=True))
@click.argument("db_path", type=click.Path(exists=True))
def import_fhir_cmd(fhir_path: str, db_path: str) -> None:
    """Import a FHIR R4 Questionnaire JSON file into DB_PATH."""
    conn = open_oltp(db_path)
    text = Path(fhir_path).read_text()
    qid = import_fhir(conn, text)
    conn.commit()
    click.echo(f"Imported questionnaire id={qid}.")


@main.command("refresh")
@click.argument("db_path",   type=click.Path(exists=True))
@click.argument("olap_path", type=click.Path())
def refresh_cmd(db_path: str, olap_path: str) -> None:
    """Refresh the OLAP analytics database from DB_PATH (OLTP SQLite)."""
    stats = olap_refresh(olap_path, db_path)
    click.echo(
        f"Refresh complete: {stats['rows_loaded']} fact rows, "
        f"{stats['sessions_loaded']} sessions, "
        f"{stats['scores_computed']} scores computed."
    )


@main.command("preview")
@click.argument("db_path", type=click.Path(exists=True))
@click.argument("questionnaire_id", type=int)
@click.option("--port", default=5173, show_default=True, help="Local port for the preview server.")
@click.option("--no-browser", is_flag=True, help="Start server without opening a browser tab.")
@click.option("--output", "-o", type=click.Path(), default=None, help="Write static HTML to file instead of serving.")
def preview_cmd(db_path: str, questionnaire_id: int, port: int, no_browser: bool, output: str | None) -> None:
    """Render a questionnaire in a local browser via LHC-Forms (read-only)."""
    if output:
        html = build_preview_html(db_path, questionnaire_id)
        Path(output).write_text(html)
        click.echo(f"Preview HTML written to {output}.")
    else:
        preview_questionnaire(db_path, questionnaire_id, port=port, open_browser=not no_browser)


@main.command("report")
@click.argument("olap_path", type=click.Path(exists=True))
@click.argument("oltp_path", type=click.Path(exists=True))
@click.argument("questionnaire_id", type=int)
@click.option("--output", "-o", type=click.Path(), default=None, help="Write to file instead of stdout.")
def report_cmd(olap_path: str, oltp_path: str, questionnaire_id: int, output: str | None) -> None:
    """Generate a Markdown report for a questionnaire from the OLAP database."""
    oconn = init_olap(olap_path, oltp_path)
    text = generate_report(oconn, questionnaire_id)
    if output:
        Path(output).write_text(text)
        click.echo(f"Report written to {output}.")
    else:
        click.echo(text)


@main.command("import-fhir-response")
@click.argument("fhir_path", type=click.Path(exists=True))
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--study-id", type=int, default=None, help="Associate respondents with an existing study.")
def import_fhir_response_cmd(fhir_path: str, db_path: str, study_id: int | None) -> None:
    """Import a FHIR R4 QuestionnaireResponse (or JSON array) into DB_PATH."""
    conn = open_oltp(db_path)
    import json
    data = json.loads(Path(fhir_path).read_text())
    resources = data if isinstance(data, list) else [data]
    session_ids = []
    for resource in resources:
        sid = import_fhir_response(conn, resource, study_id=study_id)
        session_ids.append(sid)
    click.echo(f"Imported {len(session_ids)} response session(s): ids={session_ids}.")


@main.command("export-fhir")
@click.argument("db_path", type=click.Path(exists=True))
@click.argument("questionnaire_id", type=int)
@click.option("--output", "-o", type=click.Path(), default=None, help="Write to file instead of stdout.")
def export_fhir_cmd(db_path: str, questionnaire_id: int, output: str | None) -> None:
    """Export a questionnaire as a FHIR R4 Questionnaire JSON resource."""
    conn = open_oltp(db_path, read_only=True)
    text = export_fhir_json(conn, questionnaire_id)
    if output:
        Path(output).write_text(text)
        click.echo(f"Wrote FHIR Questionnaire to {output}.")
    else:
        click.echo(text)
