"""
FAIR self-audit for quickq study databases.

Checks a study row against the FAIR sub-principles and reports which fields
are satisfied, which are partial, and which are missing — with specific
guidance for each gap.

Maps to the NIH Data Management and Sharing Plan requirement for a documented
self-assessment of data findability, accessibility, interoperability, and
reusability.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class FAIRCheckItem:
    principle: str       # e.g. "F1", "R1.1"
    label: str           # short description
    status: str          # "pass" | "warn" | "fail"
    detail: str          # what was found or what is missing
    guidance: str        # what to do to fix it


@dataclass
class FAIRCheckResult:
    study_id: int
    study_name: str
    items: list[FAIRCheckItem] = field(default_factory=list)

    @property
    def passed(self) -> list[FAIRCheckItem]:
        return [i for i in self.items if i.status == "pass"]

    @property
    def warnings(self) -> list[FAIRCheckItem]:
        return [i for i in self.items if i.status == "warn"]

    @property
    def failures(self) -> list[FAIRCheckItem]:
        return [i for i in self.items if i.status == "fail"]

    @property
    def is_ready_to_share(self) -> bool:
        return len(self.failures) == 0


def fair_check(conn: sqlite3.Connection, study_id: int) -> FAIRCheckResult:
    """
    Run a FAIR audit on the study and its questionnaires.

    Returns a FAIRCheckResult with one FAIRCheckItem per sub-principle checked.
    Raises ValueError if the study is not found.
    """
    row = conn.execute(
        """SELECT study_id, name, description, principal_investigator,
                  irb_number, start_date, population, license, protocol_url,
                  doi, geographic_scope, data_collection_end
           FROM study WHERE study_id = ?""",
        (study_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No study found with study_id={study_id}.")

    result = FAIRCheckResult(study_id=study_id, study_name=row["name"])
    add = result.items.append

    # ------------------------------------------------------------------
    # F — Findable
    # ------------------------------------------------------------------

    # F1: persistent identifier
    if row["doi"]:
        add(FAIRCheckItem("F1", "Persistent identifier (DOI)",
            "pass", f"doi = {row['doi']}", ""))
    elif row["protocol_url"]:
        add(FAIRCheckItem("F1", "Persistent identifier (DOI)",
            "warn", "No DOI assigned; protocol_url is set as a proxy.",
            "Register the dataset with Zenodo, OSF, or ICPSR to obtain a DOI, "
            "then set it with: quickq set-metadata --doi <DOI>"))
    else:
        add(FAIRCheckItem("F1", "Persistent identifier (DOI)",
            "fail", "No DOI and no protocol_url.",
            "Register the study protocol (ClinicalTrials.gov, OSF) and the "
            "dataset (Zenodo, ICPSR). Set both with quickq set-metadata."))

    # F2: rich metadata
    missing_f2 = [f for f, v in [
        ("description", row["description"]),
        ("population", row["population"]),
        ("geographic_scope", row["geographic_scope"]),
    ] if not v]
    if not missing_f2:
        add(FAIRCheckItem("F2", "Rich metadata (description, population, scope)",
            "pass", "All descriptive fields populated.", ""))
    else:
        add(FAIRCheckItem("F2", "Rich metadata (description, population, scope)",
            "fail", f"Missing: {', '.join(missing_f2)}.",
            f"Set missing fields with: quickq set-metadata "
            + " ".join(f"--{f.replace('_', '-')} '...'" for f in missing_f2)))

    # ------------------------------------------------------------------
    # A — Accessible
    # ------------------------------------------------------------------

    # A1: open file format (SQLite is always open — automatic pass)
    add(FAIRCheckItem("A1", "Open, free file format",
        "pass", "Study data stored in SQLite — open format, no proprietary software required.", ""))

    # A2: metadata accessible independently of data
    if row["doi"]:
        add(FAIRCheckItem("A2", "Metadata accessible independently",
            "pass", "DOI resolves to a metadata record in a public repository.", ""))
    else:
        add(FAIRCheckItem("A2", "Metadata accessible independently",
            "warn", "No DOI — metadata is coupled to the .db file.",
            "Run quickq export-metadata to produce a separable DataCite record, "
            "then deposit it in Zenodo or OSF independently of the data file."))

    # ------------------------------------------------------------------
    # I — Interoperable
    # ------------------------------------------------------------------

    # I1: FHIR / standard format for questionnaires
    n_questionnaires = conn.execute(
        "SELECT COUNT(*) FROM questionnaire WHERE study_id = ?", (study_id,)
    ).fetchone()[0]
    n_with_url = conn.execute(
        "SELECT COUNT(*) FROM questionnaire WHERE study_id = ? AND canonical_url IS NOT NULL",
        (study_id,),
    ).fetchone()[0]
    if n_questionnaires == 0:
        add(FAIRCheckItem("I1", "Instruments in standard format (FHIR)",
            "warn", "No questionnaires linked to this study.", ""))
    elif n_with_url == n_questionnaires:
        add(FAIRCheckItem("I1", "Instruments in standard format (FHIR)",
            "pass", f"All {n_questionnaires} questionnaire(s) have canonical URLs and export as FHIR R4.", ""))
    else:
        add(FAIRCheckItem("I1", "Instruments in standard format (FHIR)",
            "warn", f"{n_questionnaires - n_with_url} questionnaire(s) missing canonical_url.",
            "Set canonical_url on each questionnaire so FHIR export produces a "
            "globally unique instrument identifier."))

    # I2: concept codes on questions
    n_questions = conn.execute(
        """SELECT COUNT(DISTINCT q.question_id)
           FROM question q
           JOIN questionnaire_question qq ON qq.question_id = q.question_id
           JOIN questionnaire qn ON qn.questionnaire_id = qq.questionnaire_id
           WHERE qn.study_id = ?""",
        (study_id,),
    ).fetchone()[0]
    n_mapped = conn.execute(
        """SELECT COUNT(DISTINCT q.question_id)
           FROM question q
           JOIN questionnaire_question qq ON qq.question_id = q.question_id
           JOIN questionnaire qn ON qn.questionnaire_id = qq.questionnaire_id
           WHERE qn.study_id = ? AND q.concept_id IS NOT NULL""",
        (study_id,),
    ).fetchone()[0]
    if n_questions == 0:
        add(FAIRCheckItem("I2", "Standard vocabulary (concept codes)",
            "warn", "No questions found for this study.", ""))
    elif n_mapped == n_questions:
        add(FAIRCheckItem("I2", "Standard vocabulary (concept codes)",
            "pass", f"All {n_questions} question(s) have concept codes.", ""))
    else:
        pct = int(100 * n_mapped / n_questions)
        add(FAIRCheckItem("I2", "Standard vocabulary (concept codes)",
            "warn" if pct >= 50 else "fail",
            f"{n_mapped}/{n_questions} question(s) mapped to standard concepts ({pct}%).",
            "Use quickq data-dict --format csv to see unmapped questions, "
            "then assign LOINC or SNOMED codes via the concept mapper."))

    # ------------------------------------------------------------------
    # R — Reusable
    # ------------------------------------------------------------------

    # R1.1: license
    if row["license"]:
        add(FAIRCheckItem("R1.1", "Clear usage license",
            "pass", f"license = {row['license']}", ""))
    else:
        add(FAIRCheckItem("R1.1", "Clear usage license",
            "fail", "No license specified.",
            "Set a license with: quickq set-metadata --license CC-BY-4.0 "
            "(or another SPDX identifier). CC-BY-4.0 is standard for open "
            "research data; check your institution's policy."))

    # R1.2: link to registered protocol
    if row["protocol_url"]:
        add(FAIRCheckItem("R1.2", "Linked to registered study protocol",
            "pass", f"protocol_url = {row['protocol_url']}", ""))
    else:
        add(FAIRCheckItem("R1.2", "Linked to registered study protocol",
            "fail", "No protocol_url.",
            "Register the study on ClinicalTrials.gov or OSF, then set: "
            "quickq set-metadata --protocol-url <URL>"))

    # R1.3: IRB / provenance
    if row["irb_number"] and row["principal_investigator"]:
        add(FAIRCheckItem("R1.3", "Provenance (PI, IRB number)",
            "pass", f"PI = {row['principal_investigator']}, IRB = {row['irb_number']}", ""))
    else:
        missing_prov = [f for f, v in [
            ("principal_investigator (--pi)", row["principal_investigator"]),
            ("irb_number (--irb-number)", row["irb_number"]),
        ] if not v]
        add(FAIRCheckItem("R1.3", "Provenance (PI, IRB number)",
            "warn", f"Missing: {', '.join(missing_prov)}.",
            "Set with quickq set-metadata."))

    return result


def format_fair_check(result: FAIRCheckResult) -> str:
    """Format a FAIRCheckResult as a human-readable text report."""
    lines = [
        f"FAIR check — {result.study_name} (study_id={result.study_id})",
        "=" * 60,
        "",
    ]
    status_symbol = {"pass": "✓", "warn": "~", "fail": "✗"}
    for item in result.items:
        sym = status_symbol[item.status]
        lines.append(f"  {sym}  {item.principle:6s}  {item.label}")
        lines.append(f"         {item.detail}")
        if item.guidance:
            lines.append(f"         → {item.guidance}")
        lines.append("")

    n_pass = len(result.passed)
    n_warn = len(result.warnings)
    n_fail = len(result.failures)
    lines.append(f"Summary: {n_pass} passed, {n_warn} warnings, {n_fail} failures")
    if result.is_ready_to_share:
        lines.append("Status:  ready to share (no failures)")
    else:
        lines.append("Status:  not ready — resolve failures before sharing")
    return "\n".join(lines)
