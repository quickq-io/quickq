import rich_click as click
import json
from pathlib import Path

# Configure rich-click
click.rich_click.TEXT_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.COMMAND_GROUPS = {
    "quickq": [
        {
            "name": "Everyday authoring & analysis",
            "commands": ["init", "load", "library", "serve", "preview", "render",
                         "data-dict", "refresh", "report", "export-parquet"],
        },
        {
            "name": "Specialised workflows",
            "commands": ["fhir", "compliance", "federated"],
        },
    ],
}

from .config import load_config
from .schema import init_oltp, open_oltp
from .library_loader import load_all_libraries, list_library_questions
from .loader import load_yaml
from .administration import data_dictionary, format_data_dict_markdown, format_data_dict_csv
from .renderer_fhir import export_fhir_json
from .parser_fhir import import_fhir
from .parser_fhir_response import import_fhir_response
# olap_schema, renderer_md, and export_parquet import duckdb — loaded lazily
# inside the commands that need them so `quickq serve` works in envs without duckdb.
from .preview import preview as preview_questionnaire, build_preview_html
from .merge import merge_databases, MergeError
from .pseudonymize import pseudonymize

from .renderer_questionnaire import render_questionnaire_md


@click.group()
def main() -> None:
    """quickq — health and epidemiology questionnaire tool."""


@main.group()
def fhir() -> None:
    """FHIR-specific import and export."""


@main.group()
def compliance() -> None:
    """IRB and data governance commands."""


@main.group()
def federated() -> None:
    """Multi-site and federated analysis."""


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
@click.option("--strict-concepts/--no-strict-concepts", default=None,
              help="Warn on concept code collisions (default: from quickq.yml, else true).")
@click.option("--auto-concept/--no-auto-concept", default=None,
              help="Auto-assign Local OMOP-range concept codes to unmapped items "
                   "(default: from quickq.yml, else false).")
def load_cmd(yaml_path: str, db_path: str, study_id: int | None,
             strict_concepts: bool | None, auto_concept: bool | None) -> None:
    """Compile a YAML questionnaire definition into DB_PATH."""
    cfg = load_config(Path(db_path).parent)
    effective_strict = strict_concepts if strict_concepts is not None else cfg.authoring.strict_concepts
    effective_auto = auto_concept if auto_concept is not None else cfg.authoring.auto_concept
    conn = open_oltp(db_path)
    qid = load_yaml(conn, yaml_path, study_id=study_id, strict_concepts=effective_strict,
                    auto_concept=effective_auto)
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
@click.option("--format", "fmt", type=click.Choice(["markdown", "csv"]), default=None,
              help="Output format (default: from quickq.yml, else markdown).")
@click.option("--include-deprecated", is_flag=True, help="Include deprecated questions.")
@click.option("--output", "-o", type=click.Path(), default=None, help="Write to file instead of stdout.")
def data_dict_cmd(db_path: str, questionnaire_id: int, fmt: str | None, include_deprecated: bool, output: str | None) -> None:
    """Generate a data dictionary for a questionnaire."""
    cfg = load_config(Path(db_path).parent)
    fmt = fmt if fmt is not None else cfg.data_dict.format
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


@fhir.command("import")
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
    from .olap_schema import refresh as olap_refresh
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
    from .olap_schema import init_olap
    from .renderer_md import generate_report
    oconn = init_olap(olap_path, oltp_path)
    text = generate_report(oconn, questionnaire_id)
    if output:
        Path(output).write_text(text)
        click.echo(f"Report written to {output}.")
    else:
        click.echo(text)


@main.command("export-parquet")
@click.argument("olap_path", type=click.Path(exists=True))
@click.argument("output_dir", type=click.Path())
@click.option("--table", "tables", multiple=True,
              help="Table to export (repeatable). Defaults to all star schema tables.")
@click.option("--overwrite", is_flag=True, help="Overwrite existing Parquet files.")
def export_parquet_cmd(
    olap_path: str, output_dir: str, tables: tuple[str, ...], overwrite: bool
) -> None:
    """Export OLAP tables from OLAP_PATH to Parquet files in OUTPUT_DIR."""
    from .export_parquet import export_parquet
    try:
        result = export_parquet(
            olap_path, output_dir,
            tables=list(tables) if tables else None,
            overwrite=overwrite,
        )
    except FileExistsError as exc:
        raise click.ClickException(str(exc))

    total_rows = sum(result.rows.values())
    click.echo(
        f"Exported {len(result.files)} table(s) to {result.output_dir} "
        f"({total_rows:,} total rows)"
    )
    for table, path in result.files.items():
        click.echo(f"  {table}: {result.rows[table]:,} rows → {Path(path).name}")
    for table in result.skipped:
        click.echo(f"  skipped (not found): {table}", err=True)


@fhir.command("import-response")
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


@federated.command("merge")
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--output", "-o", required=True, type=click.Path(), help="Path for the merged output database.")
@click.option("--overwrite", is_flag=True, help="Overwrite the output file if it exists.")
def merge_cmd(sources: tuple[str, ...], output: str, overwrite: bool) -> None:
    """Merge multiple site databases into a single combined study database."""
    try:
        result = merge_databases(list(sources), output, overwrite=overwrite)
    except MergeError as exc:
        raise click.ClickException(str(exc))
    click.echo(
        f"Merged {len(result.sources)} source(s) into {result.output}:\n"
        f"  {result.respondents_merged} respondents\n"
        f"  {result.sessions_merged} sessions\n"
        f"  {result.responses_merged} responses\n"
        f"  {result.sessions_skipped_duplicate} duplicate sessions skipped"
    )
    for w in result.warnings:
        click.echo(f"  warning: {w}", err=True)


@compliance.command("pseudonymize")
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--output", "-o", required=True, type=click.Path(), help="Path for the pseudonymized output database.")
@click.option("--overwrite", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--key-file", type=click.Path(), default=None,
              help="File to write the HMAC key to (hex). Store securely for reversibility.")
def pseudonymize_cmd(db_path: str, output: str, overwrite: bool, key_file: str | None) -> None:
    """Produce a pseudonymized copy of DB_PATH with participant IDs replaced by tokens."""
    try:
        result = pseudonymize(db_path, output, overwrite=overwrite)
    except FileExistsError as exc:
        raise click.ClickException(str(exc))

    click.echo(
        f"Pseudonymized {result.source} → {result.output}\n"
        f"  {result.respondents_pseudonymized} respondents pseudonymized"
    )

    if key_file:
        Path(key_file).write_bytes(result.key)
        click.echo(f"  HMAC key written to {key_file} — keep this file secure.")
    else:
        click.echo(
            f"  HMAC key (hex): {result.key.hex()}\n"
            "  Store this key securely if you need to reverse the pseudonymization.",
            err=True,
        )

    for w in result.warnings:
        click.echo(f"  warning: {w}", err=True)

    click.echo("\nNext step: quickq refresh <output.db> <analytics.duckdb>")


@main.command("render")
@click.argument("db_path", type=click.Path(exists=True))
@click.argument("questionnaire_id", type=int)
@click.option("--output", "-o", type=click.Path(), default=None, help="Write to file instead of stdout.")
@click.option("--format", "fmt", type=click.Choice(["md", "pdf"]), default=None,
              help="Output format (default: from quickq.yml, else md).")
def render_cmd(db_path: str, questionnaire_id: int, output: str | None, fmt: str | None) -> None:
    """Render a questionnaire definition as Markdown or PDF."""
    cfg = load_config(Path(db_path).parent)
    fmt = fmt if fmt is not None else cfg.render.format
    conn = open_oltp(db_path, read_only=True)
    if fmt == "pdf":
        if not output:
            raise click.UsageError("--output is required when --format pdf is used.")
        from .renderer_pdf import render_questionnaire_pdf
        render_questionnaire_pdf(conn, questionnaire_id, output)
        click.echo(f"Rendered questionnaire to {output}.")
    else:
        text = render_questionnaire_md(conn, questionnaire_id)
        if output:
            Path(output).write_text(text)
            click.echo(f"Rendered questionnaire to {output}.")
        else:
            click.echo(text)


@fhir.command("export")
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


@main.command("serve")
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--questionnaire-id", default=1, show_default=True,
              help="ID of the questionnaire to serve.")
@click.option("--port", default=8000, show_default=True, help="Port for the API server.")
@click.option("--no-browser", is_flag=True, default=False, help="Do not open a browser tab.")
def serve_cmd(db_path: str, questionnaire_id: int, port: int, no_browser: bool) -> None:
    """Serve a questionnaire from DB_PATH as an interactive web form.

    Starts the quickq-forms server with the local adapter pointed at DB_PATH.
    Submitted responses are written directly to the study database.
    Requires quickq-forms to be installed (pip install quickq-forms).
    """
    try:
        import uvicorn
        from server.main import create_app
        from server.adapters.local import LocalAdapter
    except ImportError as exc:
        raise click.ClickException(
            f"quickq-forms is not installed ({exc}). "
            "Install it with: pip install quickq-forms"
        )

    db_path_abs = str(Path(db_path).resolve())
    adapter = LocalAdapter(db_path=db_path_abs, questionnaire_id=questionnaire_id)
    app = create_app(adapter)

    if not no_browser:
        import threading, webbrowser, time
        def _open():
            time.sleep(1.0)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=_open, daemon=True).start()

    click.echo(f"Serving questionnaire {questionnaire_id} from {db_path} on http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)


@compliance.command("delete")
@click.argument("db_path", type=click.Path(exists=True))
@click.argument("external_id")
@click.option("--study-id", type=int, default=None,
              help="Study ID to disambiguate when external_id appears in multiple studies.")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip confirmation prompt.")
def delete_respondent_cmd(db_path: str, external_id: str, study_id: int | None, yes: bool) -> None:
    """Permanently delete all data for a participant (GDPR right to erasure).

    Removes the respondent's sessions, responses, quality flags, and identity
    row. This operation is irreversible — use pseudonymize if you need to
    retain anonymized data.
    """
    from .compliance import delete_respondent

    if not yes:
        click.confirm(
            f"Permanently delete all data for external_id={external_id!r}? "
            "This cannot be undone.",
            abort=True,
        )

    conn = open_oltp(db_path)
    try:
        result = delete_respondent(conn, external_id, study_id=study_id)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    from .compliance import record_audit_event
    record_audit_event(conn, "delete_respondent", study_id=study_id, details={
        "external_id": external_id,
        "sessions_deleted": result.sessions_deleted,
        "responses_deleted": result.responses_deleted,
    })

    click.echo(
        f"Deleted respondent external_id={result.external_id!r} "
        f"(internal id={result.respondent_id}):\n"
        f"  {result.sessions_deleted} session(s)\n"
        f"  {result.responses_deleted} response(s)\n"
        f"  {result.flags_deleted} data quality flag(s)"
    )


@compliance.command("withdraw")
@click.argument("db_path", type=click.Path(exists=True))
@click.argument("external_id")
@click.option("--study-id", type=int, default=None,
              help="Study ID to disambiguate when external_id appears in multiple studies.")
@click.option("--notes", default=None, help="Reason for withdrawal (stored in audit log).")
def withdraw_respondent_cmd(db_path: str, external_id: str, study_id: int | None, notes: str | None) -> None:
    """Record a participant withdrawal without deleting their data.

    Withdrawal stops future data collection for this participant while
    retaining all previously collected responses. This is the legally
    distinct operation from delete-respondent — most IRB withdrawal
    protocols require data retention for already-consented responses.

    A 'withdrawn' event is written to admin_event. Use delete-respondent
    instead if the participant requests full erasure under GDPR.
    """
    from .compliance import withdraw_respondent

    conn = open_oltp(db_path)
    try:
        result = withdraw_respondent(conn, external_id, study_id=study_id, notes=notes)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    from .compliance import record_audit_event
    record_audit_event(conn, "withdraw_respondent", study_id=study_id, details={
        "external_id": external_id,
        "event_id": result.event_id,
    })

    click.echo(
        f"Recorded withdrawal for external_id={result.external_id!r} "
        f"(internal id={result.respondent_id}). "
        f"Response data retained. Admin event id={result.event_id}."
    )


@compliance.command("set-metadata")
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--study-id", type=int, default=1, show_default=True,
              help="Study to update.")
@click.option("--description",          default=None, help="Study description.")
@click.option("--population",           default=None, help="Description of the study population.")
@click.option("--license",              default=None, help="SPDX license ID or URL (e.g. CC-BY-4.0).")
@click.option("--protocol-url",         default=None, help="ClinicalTrials.gov, OSF, or other registration URL.")
@click.option("--doi",                  default=None, help="DOI assigned after repository deposit.")
@click.option("--geographic-scope",     default=None, help="Geographic scope (e.g. 'United States').")
@click.option("--data-collection-end",  default=None, help="Data collection end date (ISO 8601).")
@click.option("--pi",                   default=None, help="Principal investigator name.")
@click.option("--irb-number",           default=None, help="IRB protocol number.")
def set_metadata_cmd(
    db_path: str,
    study_id: int,
    description: str | None,
    population: str | None,
    license: str | None,
    protocol_url: str | None,
    doi: str | None,
    geographic_scope: str | None,
    data_collection_end: str | None,
    pi: str | None,
    irb_number: str | None,
) -> None:
    """Set regulatory and FAIR metadata fields on a study.

    Only fields explicitly provided are updated; omitted fields are unchanged.
    These fields satisfy NIH Data Management and Sharing Plan requirements
    and support quickq fair-check and export-metadata (planned).
    """
    from .compliance import set_study_metadata

    conn = open_oltp(db_path)
    try:
        updated = set_study_metadata(
            conn,
            study_id,
            description=description,
            population=population,
            license=license,
            protocol_url=protocol_url,
            doi=doi,
            geographic_scope=geographic_scope,
            data_collection_end=data_collection_end,
            principal_investigator=pi,
            irb_number=irb_number,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc))

    if updated:
        for k, v in updated.items():
            click.echo(f"  {k} = {v!r}")
        click.echo(f"Updated {len(updated)} field(s) on study {study_id}.")
    else:
        click.echo("No fields provided — nothing updated.")


@compliance.command("export-metadata")
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--study-id", type=int, default=1, show_default=True)
@click.option("--format", "fmt", type=click.Choice(["datacite", "dublin-core"]),
              default="datacite", show_default=True,
              help="Metadata schema to produce.")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write to file instead of stdout.")
def export_metadata_cmd(db_path: str, study_id: int, fmt: str, output: str | None) -> None:
    """Export study metadata as a DataCite JSON or Dublin Core XML record.

    Run quickq fair-check first to verify all required fields are populated.
    The output file can be submitted directly to Zenodo, OSF, or ICPSR.
    After the repository assigns a DOI, record it with:

        quickq compliance set-metadata study.db --doi <DOI>
    """
    from .export_metadata import format_datacite_json, export_dublin_core

    conn = open_oltp(db_path, read_only=True)
    try:
        if fmt == "datacite":
            text = format_datacite_json(conn, study_id)
        else:
            text = export_dublin_core(conn, study_id)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    if output:
        Path(output).write_text(text)
        click.echo(f"Wrote {fmt} metadata to {output}.")
    else:
        click.echo(text)


@compliance.command("fair-check")
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--study-id", type=int, default=1, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Output machine-readable JSON instead of formatted text.")
def fair_check_cmd(db_path: str, study_id: int, as_json: bool) -> None:
    """Audit a study against FAIR sub-principles and NIH DMS plan requirements.

    Reports which metadata fields are populated (pass), incomplete (warn),
    or missing (fail) — with specific guidance for each gap. Run this before
    quickq export-metadata to ensure the metadata record is complete.
    """
    from .fair_check import fair_check, format_fair_check

    conn = open_oltp(db_path, read_only=True)
    try:
        result = fair_check(conn, study_id)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    if as_json:
        items = [
            {
                "principle": i.principle,
                "label": i.label,
                "status": i.status,
                "detail": i.detail,
                "guidance": i.guidance,
            }
            for i in result.items
        ]
        click.echo(json.dumps({
            "study_id": result.study_id,
            "study_name": result.study_name,
            "is_ready_to_share": result.is_ready_to_share,
            "items": items,
        }, indent=2))
    else:
        click.echo(format_fair_check(result))

    if result.failures:
        raise SystemExit(1)


@federated.command("query")
@click.argument("query_path", type=click.Path(exists=True))
@click.argument("olap_path", type=click.Path(exists=True))
@click.option("--min-cell", default=5, show_default=True,
              help="Minimum cell size for disclosure control. Rows with counts below "
                   "this threshold are suppressed entirely.")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write JSON results to file instead of stdout.")
def run_query_cmd(
    query_path: str, olap_path: str, min_cell: int, output: str | None
) -> None:
    """Run a federated aggregate query against the OLAP database.

    QUERY_PATH is a .sql file containing a single SELECT statement.
    Results are aggregate-only: individual-identifier columns are blocked
    and rows with cell counts below --min-cell are suppressed.

    Output is a JSON document suitable for sharing with a coordinating center.
    It includes a disclosure_control block reporting how many rows were suppressed.
    """
    from .federated import run_federated_query, result_to_dict

    sql = Path(query_path).read_text()
    try:
        result = run_federated_query(olap_path, sql, min_cell=min_cell)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    out = json.dumps(result_to_dict(result), indent=2, default=str)

    if output:
        Path(output).write_text(out)
        suppressed_msg = (
            f"  {result.rows_suppressed} row(s) suppressed (cell size < {min_cell})"
            if result.rows_suppressed else "  No rows suppressed."
        )
        click.echo(
            f"Wrote {result.rows_total - result.rows_suppressed} row(s) to {output}.\n"
            + suppressed_msg
        )
    else:
        click.echo(out)

    # Audit log: record against the OLAP file's companion OLTP if locatable.
    # Best-effort — skip silently if the OLTP path cannot be inferred.
    _oltp_candidate = Path(olap_path).with_suffix(".db")
    if _oltp_candidate.exists():
        try:
            _aconn = open_oltp(str(_oltp_candidate))
            from .compliance import record_audit_event
            record_audit_event(_aconn, "federated_query", details={
                "query_hash": result.query_hash,
                "min_cell": min_cell,
                "rows_total": result.rows_total,
                "rows_suppressed": result.rows_suppressed,
                "output": output,
            })
            _aconn.close()
        except Exception:
            pass
