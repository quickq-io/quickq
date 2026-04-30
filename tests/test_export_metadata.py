"""
Tests for quickq.export_metadata: DataCite JSON and Dublin Core XML export.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from quickq.schema import init_oltp
from quickq.authoring import insert_study, insert_questionnaire
from quickq.models import QuestionnaireDef
from quickq.compliance import set_study_metadata
from quickq.export_metadata import export_datacite, format_datacite_json, export_dublin_core

from click.testing import CliRunner
from quickq.cli import main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_study(tmp_path: Path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    study_id = insert_study(conn, name="Minimal Study")
    conn.commit()
    return db, conn, study_id


def _full_study(tmp_path: Path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    study_id = insert_study(
        conn,
        name="Depression Cohort Study",
        principal_investigator="Smith, Jane",
        irb_number="IRB-2025-001",
    )
    set_study_metadata(
        conn, study_id,
        description="A longitudinal cohort study of depression outcomes.",
        population="Adults 18–65 in the United States",
        license="CC-BY-4.0",
        protocol_url="https://clinicaltrials.gov/ct2/show/NCT00000001",
        doi="10.5281/zenodo.0000001",
        geographic_scope="United States",
        start_date="2024-01-01",
        data_collection_end="2025-12-31",
    )
    insert_questionnaire(
        conn,
        QuestionnaireDef(
            name="PHQ-9",
            canonical_url="http://quickq.io/instruments/phq9",
            version="1.0",
        ),
        study_id=study_id,
    )
    conn.commit()
    return db, conn, study_id


# ---------------------------------------------------------------------------
# export_datacite — structure
# ---------------------------------------------------------------------------

def test_datacite_returns_dict(tmp_path):
    _, conn, study_id = _minimal_study(tmp_path)
    result = export_datacite(conn, study_id)
    assert isinstance(result, dict)


def test_datacite_unknown_study_raises(tmp_path):
    _, conn, _ = _minimal_study(tmp_path)
    with pytest.raises(ValueError, match="No study found"):
        export_datacite(conn, 9999)


def test_datacite_required_fields_present(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert "titles" in d
    assert "creators" in d
    assert "publicationYear" in d
    assert "types" in d


def test_datacite_title_matches_study_name(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert d["titles"][0]["title"] == "Depression Cohort Study"


def test_datacite_doi_included_when_set(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert d["doi"] == "10.5281/zenodo.0000001"


def test_datacite_doi_absent_when_not_set(tmp_path):
    _, conn, study_id = _minimal_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert "doi" not in d


def test_datacite_creator_from_pi(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert d["creators"][0]["name"] == "Smith, Jane"


def test_datacite_fallback_creator_when_no_pi(tmp_path):
    _, conn, study_id = _minimal_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert len(d["creators"]) == 1
    assert d["creators"][0]["name"] == ":unav"


def test_datacite_description_from_study(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    texts = [desc["description"] for desc in d["descriptions"]]
    assert any("longitudinal" in t for t in texts)


def test_datacite_irb_in_descriptions(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    texts = [desc["description"] for desc in d["descriptions"]]
    assert any("IRB-2025-001" in t for t in texts)


def test_datacite_resource_type_dataset(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert d["types"]["resourceTypeGeneral"] == "Dataset"


def test_datacite_license_included(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert "rightsList" in d
    assert d["rightsList"][0]["rights"] == "CC-BY-4.0"
    assert "creativecommons.org" in d["rightsList"][0]["rightsUri"]


def test_datacite_no_rights_when_no_license(tmp_path):
    _, conn, study_id = _minimal_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert "rightsList" not in d


def test_datacite_protocol_url_as_related_identifier(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    urls = [r["relatedIdentifier"] for r in d.get("relatedIdentifiers", [])]
    assert "https://clinicaltrials.gov/ct2/show/NCT00000001" in urls


def test_datacite_questionnaire_canonical_url_as_related(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    urls = [r["relatedIdentifier"] for r in d.get("relatedIdentifiers", [])]
    assert "http://quickq.io/instruments/phq9" in urls


def test_datacite_geographic_scope(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert d["geoLocations"][0]["geoLocationPlace"] == "United States"


def test_datacite_dates_included(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    date_values = [dt["date"] for dt in d.get("dates", [])]
    assert "2024-01-01" in date_values
    assert "2025-12-31" in date_values


def test_datacite_publication_year_from_collection_end(tmp_path):
    # full study has data_collection_end=2025-12-31; that takes priority
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert d["publicationYear"] == 2025


def test_datacite_publication_year_falls_back_to_start_date(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    study_id = insert_study(conn, name="S")
    set_study_metadata(conn, study_id, start_date="2023-06-01")
    d = export_datacite(conn, study_id)
    assert d["publicationYear"] == 2023


def test_datacite_subjects_include_population(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    subjects = [s["subject"] for s in d.get("subjects", [])]
    assert any("United States" in s for s in subjects)


def test_datacite_format_is_sqlite(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    d = export_datacite(conn, study_id)
    assert "application/x-sqlite3" in d["formats"]


# ---------------------------------------------------------------------------
# format_datacite_json — valid JSON
# ---------------------------------------------------------------------------

def test_format_datacite_returns_valid_json(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    text = format_datacite_json(conn, study_id)
    parsed = json.loads(text)
    assert "titles" in parsed


# ---------------------------------------------------------------------------
# Dublin Core XML
# ---------------------------------------------------------------------------

def test_dublin_core_returns_string(tmp_path):
    _, conn, study_id = _minimal_study(tmp_path)
    xml = export_dublin_core(conn, study_id)
    assert isinstance(xml, str)


def test_dublin_core_is_valid_xml(tmp_path):
    import xml.etree.ElementTree as ET
    _, conn, study_id = _full_study(tmp_path)
    xml = export_dublin_core(conn, study_id)
    # Should parse without exception
    ET.fromstring(xml)


def test_dublin_core_has_title(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    xml = export_dublin_core(conn, study_id)
    assert "Depression Cohort Study" in xml


def test_dublin_core_has_creator(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    xml = export_dublin_core(conn, study_id)
    assert "Smith, Jane" in xml


def test_dublin_core_has_doi(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    xml = export_dublin_core(conn, study_id)
    assert "10.5281/zenodo.0000001" in xml


def test_dublin_core_has_license(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    xml = export_dublin_core(conn, study_id)
    assert "CC-BY-4.0" in xml


def test_dublin_core_has_type_dataset(tmp_path):
    _, conn, study_id = _full_study(tmp_path)
    xml = export_dublin_core(conn, study_id)
    assert "<dc:type>Dataset</dc:type>" in xml


def test_dublin_core_escapes_ampersand(tmp_path):
    db = tmp_path / "study.db"
    conn = init_oltp(db)
    study_id = insert_study(conn, name="Health & Wellbeing Study")
    conn.commit()
    xml = export_dublin_core(conn, study_id)
    assert "&amp;" in xml
    assert "Health & Wellbeing" not in xml  # raw ampersand must not appear


def test_dublin_core_unknown_study_raises(tmp_path):
    _, conn, _ = _minimal_study(tmp_path)
    with pytest.raises(ValueError, match="No study found"):
        export_dublin_core(conn, 9999)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_export_metadata_datacite(tmp_path):
    db, conn, study_id = _full_study(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["export-metadata", str(db), "--study-id", str(study_id)])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "titles" in parsed


def test_cli_export_metadata_dublin_core(tmp_path):
    db, conn, study_id = _full_study(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, [
        "export-metadata", str(db),
        "--study-id", str(study_id),
        "--format", "dublin-core",
    ])
    assert result.exit_code == 0
    assert "<dc:title>" in result.output


def test_cli_export_metadata_to_file(tmp_path):
    db, conn, study_id = _full_study(tmp_path)
    out = tmp_path / "metadata.json"
    runner = CliRunner()
    result = runner.invoke(main, [
        "export-metadata", str(db),
        "--study-id", str(study_id),
        "--output", str(out),
    ])
    assert result.exit_code == 0
    assert out.exists()
    parsed = json.loads(out.read_text())
    assert "titles" in parsed


def test_cli_export_metadata_unknown_study(tmp_path):
    db, conn, _ = _minimal_study(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["export-metadata", str(db), "--study-id", "9999"])
    assert result.exit_code != 0
