# Compliance & Governance

quickq's compliance commands cover the regulatory surface that real research studies actually need to navigate: HIPAA-aligned pseudonymization for sharing, FAIR/DataCite metadata for repository deposit, NIH DMS plan auditing, GDPR right to erasure, and IRB-style participant withdrawal. They are deliberately separate from the analytical commands so a study's compliance state is auditable on its own.

All six commands operate on a `study.db`. None requires network access; outputs are local files (a pseudonymized DB, a metadata JSON/XML, an audit report).

> *For the design rationale (why `compliance pseudonymize` exists alongside `compliance delete`, why FAIR metadata is in the database not in a sidecar) see [Design Decisions](../design_decisions.md#data-sovereignty-and-the-10-year-rule).*

---

## `quickq compliance pseudonymize`

Produces a copy of `study.db` with direct identifiers replaced by stable HMAC tokens. Used as the first step toward a HIPAA limited dataset, or before sharing a study under a DUA.

```bash
quickq compliance pseudonymize study.db \
    --output study_anon.db \
    --key-file pseudonymization_key.bin
```

What changes:

- `respondent.external_id` becomes a 32-character HMAC token (deterministic given the same key, so the same source always pseudonymizes to the same token; useful for re-derivation across waves)
- `person_map` is cleared
- `response_session.interviewer_id` is set to NULL
- Free-text `response.response_text` values are left in place; the command warns about which `link_id`s carry free text so a reviewer can decide whether to redact
- Institutional metadata (`study.principal_investigator`, `study.irb_number`) is left in place; the command warns about it

The HMAC key is the only thing that can re-identify participants. Save the key file securely if you may need to merge the pseudonymized data back with later collection waves; destroy it if the study should be permanently de-identifiable.

For the full sharing workflow including warehouse export, see the [Share & Publish tutorial](../tutorials/share.md).

---

## `quickq compliance set-metadata`

Sets regulatory and FAIR metadata fields on a study. These fields satisfy NIH Data Management and Sharing Plan requirements and feed `quickq compliance fair-check` and `quickq compliance export-metadata` downstream.

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

Audits a study against FAIR sub-principles (Findable, Accessible, Interoperable, Reusable) and NIH DMS plan requirements. Reports which metadata fields are populated (pass), incomplete (warn), or missing (fail). Run this before `quickq compliance export-metadata` to ensure the metadata record is complete.

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

GDPR right-to-erasure: permanently deletes all data for a participant (sessions, responses, quality flags, identity row). Irreversible.

```bash
quickq compliance delete study.db <external_id>
```

Use `--study-id N` to disambiguate when the same `external_id` exists in multiple studies. The action is recorded in `tool_audit_log`.

If the participant requested withdrawal rather than erasure, use `quickq compliance withdraw` instead. Most IRB withdrawal protocols require data retention for already-consented responses.

---

## `quickq compliance withdraw`

Records a participant's withdrawal without deleting their data. Stops future data collection for this participant while retaining all previously collected responses. A `withdrawn` event is written to `admin_event`.

```bash
quickq compliance withdraw study.db <external_id> --notes "Participant withdrew via study portal"
```

This is the legally-distinct operation from `compliance delete`. Most IRB withdrawal protocols require data retention; full erasure is the GDPR right that may go further. Choose based on the request you received and the protocol you operate under.

---

## When to use which

| Goal | Command |
|---|---|
| Share data outside the institution | `compliance pseudonymize` |
| Deposit study in a repository (Zenodo, OSF, ICPSR) | `compliance set-metadata`, then `compliance export-metadata` |
| Verify NIH DMS plan / FAIR readiness | `compliance fair-check` |
| Honor a GDPR erasure request | `compliance delete` |
| Honor an IRB withdrawal request | `compliance withdraw` |
