# Compliance & Governance

quickq's compliance commands support several common workflows that research studies need: FAIR / DataCite metadata for repository deposit, a FAIR self-audit, GDPR-style right-to-erasure deletion, and IRB-style participant withdrawal. They are deliberately separate from the analytical commands so a study's compliance state is auditable on its own.

All commands operate on a `study.db`. None requires network access; outputs are local files (a metadata JSON / XML, an audit report).

!!! warning "What these commands are and aren't"
    quickq is a tool. Regulatory compliance is a property of your *deployment* (your data flows, access controls, BAAs, IRB protocol, infrastructure), not of the tool. These commands implement specific mechanical operations that *support* compliance workflows; they do not certify HIPAA, GDPR, or any other framework on your behalf.

    In particular:

    - **The FAIR self-audit is informational.** It reports which FAIR-aligned metadata fields are populated and which are missing. NIH Data Management and Sharing plans, repository deposit policies, and journal data-availability requirements each have their own criteria; check them directly.
    - **GDPR / IRB references describe the workflow shape, not certification.** `compliance delete` implements a hard delete that aligns with GDPR Article 17's right to erasure as researchers typically execute it. Whether your specific deployment satisfies GDPR for a given participant request depends on consent records, retention policy, and backups outside this tool.

> *For the design rationale (why FAIR metadata is in the database not in a sidecar) see [Design Decisions](../design_decisions.md#data-sovereignty-and-the-10-year-rule).*

---

## `quickq compliance set-metadata`

Sets FAIR-aligned and regulatory metadata fields on a study. These fields are useful for NIH Data Management and Sharing Plan documentation and feed `quickq compliance fair-check` and `quickq compliance export-metadata` downstream.

```bash
quickq compliance set-metadata study.db \
    --license CC-BY-4.0 \
    --protocol-url https://clinicaltrials.gov/study/NCTXXXXXXXX \
    --description "Three-site cohort study of perinatal depression screening" \
    --population "Pregnant adults aged 18-45 in the southeastern United States" \
    --geographic-scope "United States" \
    --pi "Smith, Jane" \
    --irb-number "IRB-2025-XYZ-001"
```

Only fields explicitly provided are updated; omitted fields are unchanged. After the study is deposited in a repository and assigned a DOI, record it back:

```bash
quickq compliance set-metadata study.db --doi 10.5281/zenodo.XXXXXXX
```

Available fields: `--description`, `--population`, `--license` (SPDX ID or URL), `--protocol-url`, `--doi`, `--geographic-scope`, `--data-collection-end`, `--pi`, `--irb-number`. Use `--study-id N` to target a specific study when the database holds more than one.

---

## `quickq compliance fair-check`

Self-audit of a study against the FAIR sub-principles (Findable, Accessible, Interoperable, Reusable). Reports which metadata fields are populated (pass), incomplete (warn), or missing (fail). Useful preparation for NIH Data Management and Sharing Plan documentation and for repository-deposit checklists, though those each have their own specific requirements you should verify separately.

```bash
quickq compliance fair-check study.db
```

The command exits non-zero if any required field is missing, so it is suitable for use in a CI or pre-deposit check.

```bash
quickq compliance fair-check study.db --json   # machine-readable for scripts
```

---

## `quickq compliance export-metadata`

Produces a DataCite JSON or Dublin Core XML record from the study's metadata fields, suitable for direct submission to Zenodo, OSF, ICPSR, or Dataverse.

```bash
quickq compliance export-metadata study.db \
    --format datacite \
    --output study_metadata.json
```

Run `quickq compliance fair-check` first to ensure all required fields are populated. The output file can be uploaded to a repository as the deposit's metadata record.

---

## `quickq compliance delete`

Permanently deletes all data for a participant (sessions, responses, quality flags, identity row, person_map row). Aligns with the workflow shape of GDPR Article 17 (right to erasure). Irreversible within this database; whether deletion is complete in your deployment depends on backups and any downstream copies, which are outside this tool's scope.

```bash
quickq compliance delete study.db <external_id>
```

Use `--study-id N` to disambiguate when the same `external_id` exists in multiple studies. The action is recorded in `tool_audit_log`. Modifies the OLTP only — rerun `quickq refresh` to propagate the deletion into the analytics layer.

If the participant requested withdrawal rather than erasure, use `quickq compliance withdraw` instead. Most IRB withdrawal protocols require data retention for already-consented responses.

---

## `quickq compliance withdraw`

Records a participant's withdrawal without deleting their data. Stops future data collection for this participant while retaining all previously collected responses. A `withdrawn` event is written to `admin_event`. Modifies the OLTP only — rerun `quickq refresh` to propagate the new event into the analytics layer.

```bash
quickq compliance withdraw study.db <external_id> --notes "Participant withdrew via study portal"
```

This is the legally-distinct operation from `compliance delete`. Most IRB withdrawal protocols require data retention; full erasure is the GDPR right that may go further. Choose based on the request you received and the protocol you operate under.

---

## When to use which

| Goal | Command |
|---|---|
| Deposit study in a repository (Zenodo, OSF, ICPSR) | `compliance set-metadata`, then `compliance export-metadata` |
| Self-audit FAIR-aligned metadata coverage | `compliance fair-check` |
| Hard-delete a participant's data (GDPR-style erasure) | `compliance delete` |
| Record an IRB-style withdrawal without deletion | `compliance withdraw` |
