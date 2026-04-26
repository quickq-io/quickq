# Tutorial: Collecting Responses

This tutorial covers the response collection phase: exporting your questionnaire for delivery, importing FHIR QuestionnaireResponse JSON files, and verifying what landed in the database.

Survey delivery is intentionally out of scope for quickq. The FHIR handoff is the interface: quickq exports a `Questionnaire` JSON, a delivery tool renders and collects responses, and quickq ingests the resulting `QuestionnaireResponse` JSON. This keeps the collection layer pluggable — any FHIR-compliant tool works.

---

## Export for delivery

After loading your instrument, export it as a FHIR R4 Questionnaire JSON:

```bash
quickq export-fhir study.db 1 --output phq9_questionnaire.json
```

This produces a standard FHIR Questionnaire resource. Hand it to any delivery tool that accepts FHIR Questionnaire JSON — a web app, a mobile app, a clinical portal, REDCap, or LHC-Forms.

### Reference delivery tool: LHC-Forms

**[LHC-Forms](https://lhncbc.nlm.nih.gov/LHC-forms/)** (NLM) is the reference delivery tool for quickq. It is an open-source JavaScript widget that renders FHIR Questionnaires in a browser with no server dependency. Participants complete the form; LHC-Forms produces a FHIR QuestionnaireResponse that quickq can import directly.

!!! note "CDN access"
    LHC-Forms is served from the NLM CDN. Browser extensions that block third-party scripts may prevent it from loading. The quickq `preview` command serves LHC-Forms assets from localhost to avoid this.

```bash
# Preview a questionnaire locally before handing off to a delivery tool
quickq preview study.db 1
```

---

## Import responses

When FHIR QuestionnaireResponse JSON files come back from the delivery tool, import them into the OLTP:

```bash
# Single response file
quickq import-fhir-response response.json study.db

# Batch: a JSON array of multiple QuestionnaireResponse resources
quickq import-fhir-response batch.json study.db --study-id 1
```

The `--study-id` flag associates respondents with a study row. If omitted, respondents are created without a study association.

Each call returns the session ID(s) created:

```
Imported 3 response session(s): ids=[1, 2, 3].
```

### Deduplication

Sessions are deduplicated by `fhir_response_id` (the `id` field of the FHIR QuestionnaireResponse resource). Importing the same file twice is safe — the second import is a no-op.

---

## Import via Python SDK

For programmatic import — automated ingestion pipelines, batch processing, or custom delivery tooling:

```python
import json
from quickq.schema import open_oltp
from quickq.parser_fhir_response import import_fhir_response

conn = open_oltp("study.db")

# Single response
response = json.loads(Path("response.json").read_text())
session_id = import_fhir_response(conn, response, study_id=1, admin_mode="web")
conn.commit()

# Batch
responses = json.loads(Path("batch.json").read_text())
resources = responses if isinstance(responses, list) else [responses]
for r in resources:
    import_fhir_response(conn, r, study_id=1)
conn.commit()
```

`admin_mode` accepts `web`, `paper`, `phone`, or `kiosk` and is recorded on the session — used in mode-effect analysis later.

---

## The one-writer rule

SQLite enforces a single concurrent writer. For automated ingestion pipelines where responses arrive continuously, run a single ingestor process per site rather than writing from multiple concurrent processes:

```python
# ingestor.py — watch a directory for incoming FHIR response files
import time, json
from pathlib import Path
from quickq.schema import open_oltp
from quickq.parser_fhir_response import import_fhir_response

INBOX = Path("inbox/")
conn  = open_oltp("study.db")

while True:
    for f in sorted(INBOX.glob("*.json")):
        data = json.loads(f.read_text())
        resources = data if isinstance(data, list) else [data]
        for r in resources:
            import_fhir_response(conn, r, study_id=1)
        conn.commit()
        f.rename(f.with_suffix(".imported"))
    time.sleep(5)
```

This pattern processes files in arrival order, commits each file atomically, and renames processed files so they are not reprocessed. For cloud deployments, replace the directory watcher with an SQS queue consumer — the write serialization principle is the same.

---

## Verify the import

After importing, check the response counts in the OLTP:

```python
from quickq.schema import open_oltp

conn = open_oltp("study.db", read_only=True)

sessions = conn.execute("SELECT COUNT(*) FROM response_session").fetchone()[0]
responses = conn.execute("SELECT COUNT(*) FROM response").fetchone()[0]
print(f"{sessions} sessions, {responses} responses")
```

Check for import warnings in the data quality flag table:

```python
flags = conn.execute("""
    SELECT rule_name, severity, message, COUNT(*) AS n
    FROM data_quality_flag
    WHERE is_resolved = 0
    GROUP BY rule_name, severity, message
    ORDER BY n DESC
""").fetchall()

for row in flags:
    print(dict(row))
```

Flags are written rather than exceptions for any response that had an unrecognisable answer format or an unresolvable `linkId`. A clean import produces no flags. See [Data Quality](data-quality.md) for how to interpret and resolve them.

---

## Next step

Once responses are imported, run `quickq refresh` to build the analytical layer:

```bash
quickq refresh study.db analytics.duckdb
```

See [Analyzing Study Data](analytics.md) for what to do next.
