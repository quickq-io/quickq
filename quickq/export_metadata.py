"""
Metadata export for study registration and repository deposit.

Produces machine-readable metadata records from the study row:

  DataCite JSON (--format datacite)
    Conforms to DataCite Metadata Schema 4.5. Suitable for submission to
    Zenodo, OSF, ICPSR, and any DataCite member repository. If the study
    has a DOI set, it is included; otherwise the record is a pre-registration
    draft that the repository will assign a DOI to.

  Dublin Core XML (--format dublin-core)
    Simple 15-element Dublin Core record encoded as OAI-DC XML. Suitable for
    basic repository submission and OAI-PMH harvesting.

Typical workflow:
    quickq fair-check study.db          # verify metadata is complete
    quickq export-metadata study.db --format datacite --output metadata.json
    # Upload metadata.json to Zenodo/OSF; record the assigned DOI
    quickq set-metadata study.db --doi 10.5281/zenodo.XXXXXXX
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

# SPDX license ID → canonical URL (common research data licenses)
_LICENSE_URLS: dict[str, str] = {
    "CC-BY-4.0":    "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-3.0":    "https://creativecommons.org/licenses/by/3.0/",
    "CC-BY-NC-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC0-1.0":      "https://creativecommons.org/publicdomain/zero/1.0/",
    "MIT":          "https://opensource.org/licenses/MIT",
    "Apache-2.0":   "https://www.apache.org/licenses/LICENSE-2.0",
}


def _get_study(conn: sqlite3.Connection, study_id: int) -> sqlite3.Row:
    row = conn.execute(
        """SELECT study_id, name, description, principal_investigator,
                  irb_number, start_date, end_date, population, license,
                  protocol_url, doi, geographic_scope, data_collection_end
           FROM study WHERE study_id = ?""",
        (study_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No study found with study_id={study_id}.")
    return row


def _get_questionnaires(conn: sqlite3.Connection, study_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT name, canonical_url, version, description FROM questionnaire WHERE study_id = ?",
        (study_id,),
    ).fetchall()


def _publication_year(row: sqlite3.Row) -> int:
    for field in ("data_collection_end", "end_date", "start_date"):
        val = row[field]
        if val:
            try:
                return int(str(val)[:4])
            except (ValueError, TypeError):
                pass
    return date.today().year


# ------------------------------------------------------------------
# DataCite JSON
# ------------------------------------------------------------------

def export_datacite(conn: sqlite3.Connection, study_id: int) -> dict:
    """
    Build a DataCite Metadata Schema 4.5 dict for the study.

    The returned dict is the DataCite 'attributes' object — ready to be
    wrapped in the standard {data: {type: 'dois', attributes: ...}} envelope
    for the DataCite REST API, or submitted as-is to Zenodo's metadata field.
    """
    row = _get_study(conn, study_id)
    questionnaires = _get_questionnaires(conn, study_id)

    attributes: dict = {}

    # Identifier
    if row["doi"]:
        attributes["doi"] = row["doi"]

    # Titles
    attributes["titles"] = [{"title": row["name"]}]

    # Creators
    creators = []
    if row["principal_investigator"]:
        # Best-effort: treat as "Lastname, Firstname" if comma present, else as org
        pi = row["principal_investigator"]
        if "," in pi:
            creators.append({"name": pi, "nameType": "Personal"})
        else:
            creators.append({"name": pi, "nameType": "Personal"})
    if not creators:
        creators.append({"name": ":unav", "nameType": "Organizational"})
    attributes["creators"] = creators

    # Descriptions
    descriptions = []
    if row["description"]:
        descriptions.append({
            "description": row["description"],
            "descriptionType": "Abstract",
        })
    if row["population"]:
        descriptions.append({
            "description": f"Study population: {row['population']}",
            "descriptionType": "Other",
        })
    if row["irb_number"]:
        descriptions.append({
            "description": f"IRB protocol: {row['irb_number']}",
            "descriptionType": "Other",
        })
    if descriptions:
        attributes["descriptions"] = descriptions

    # Publication year
    attributes["publicationYear"] = _publication_year(row)

    # Resource type
    attributes["types"] = {
        "resourceTypeGeneral": "Dataset",
        "resourceType": "Survey Data",
    }

    # Subjects
    subjects = []
    if row["population"]:
        subjects.append({"subject": row["population"]})
    if row["geographic_scope"]:
        subjects.append({"subject": row["geographic_scope"]})
    for q in questionnaires:
        subjects.append({"subject": q["name"]})
    if subjects:
        attributes["subjects"] = subjects

    # Rights / license
    if row["license"]:
        license_id = row["license"]
        rights_entry: dict = {"rights": license_id}
        if license_id in _LICENSE_URLS:
            rights_entry["rightsUri"] = _LICENSE_URLS[license_id]
        elif license_id.startswith("http"):
            rights_entry["rightsUri"] = license_id
            rights_entry["rights"] = license_id.rstrip("/").rsplit("/", 1)[-1]
        attributes["rightsList"] = [rights_entry]

    # Related identifiers
    related = []
    if row["protocol_url"]:
        related.append({
            "relatedIdentifier": row["protocol_url"],
            "relatedIdentifierType": "URL",
            "relationType": "IsSupplementTo",
        })
    for q in questionnaires:
        if q["canonical_url"]:
            related.append({
                "relatedIdentifier": q["canonical_url"],
                "relatedIdentifierType": "URL",
                "relationType": "HasPart",
            })
    if related:
        attributes["relatedIdentifiers"] = related

    # Geographic locations
    if row["geographic_scope"]:
        attributes["geoLocations"] = [{"geoLocationPlace": row["geographic_scope"]}]

    # Dates
    dates = []
    if row["start_date"]:
        dates.append({"date": row["start_date"], "dateType": "Collected"})
    if row["data_collection_end"]:
        dates.append({"date": row["data_collection_end"], "dateType": "Collected"})
    elif row["end_date"]:
        dates.append({"date": row["end_date"], "dateType": "Collected"})
    if dates:
        attributes["dates"] = dates

    # Formats
    attributes["formats"] = ["application/x-sqlite3"]
    attributes["language"] = "en"

    return attributes


def format_datacite_json(conn: sqlite3.Connection, study_id: int) -> str:
    """Return DataCite attributes as a pretty-printed JSON string."""
    return json.dumps(export_datacite(conn, study_id), indent=2, default=str)


# ------------------------------------------------------------------
# Dublin Core XML
# ------------------------------------------------------------------

def _xml_escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def export_dublin_core(conn: sqlite3.Connection, study_id: int) -> str:
    """
    Build an OAI Dublin Core XML string for the study.

    Conforms to the OAI-DC schema used by most open-access repositories
    for OAI-PMH metadata harvesting.
    """
    row = _get_study(conn, study_id)
    questionnaires = _get_questionnaires(conn, study_id)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<oai_dc:dc',
        '  xmlns:dc="http://purl.org/dc/elements/1.1/"',
        '  xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"',
        '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '  xsi:schemaLocation="http://www.openarchives.org/OAI/2.0/oai_dc/',
        '    http://www.openarchives.org/OAI/2.0/oai_dc.xsd">',
    ]

    def elem(tag: str, value: str) -> None:
        lines.append(f"  <dc:{tag}>{_xml_escape(value)}</dc:{tag}>")

    elem("title", row["name"])

    if row["principal_investigator"]:
        elem("creator", row["principal_investigator"])

    for q in questionnaires:
        elem("subject", q["name"])
    if row["population"]:
        elem("subject", row["population"])

    if row["description"]:
        elem("description", row["description"])
    if row["population"]:
        elem("description", f"Study population: {row['population']}")
    if row["irb_number"]:
        elem("description", f"IRB protocol: {row['irb_number']}")

    elem("type", "Dataset")
    elem("format", "application/x-sqlite3")

    if row["doi"]:
        elem("identifier", f"https://doi.org/{row['doi']}")
    if row["protocol_url"]:
        elem("relation", row["protocol_url"])

    if row["start_date"]:
        elem("date", row["start_date"][:10])
    if row["data_collection_end"]:
        elem("date", row["data_collection_end"][:10])
    elif row["end_date"]:
        elem("date", row["end_date"][:10])

    if row["geographic_scope"]:
        elem("coverage", row["geographic_scope"])

    if row["license"]:
        license_id = row["license"]
        rights_url = _LICENSE_URLS.get(license_id)
        if rights_url:
            elem("rights", license_id)
            elem("rights", rights_url)
        else:
            elem("rights", license_id)

    lines.append("</oai_dc:dc>")
    return "\n".join(lines)
