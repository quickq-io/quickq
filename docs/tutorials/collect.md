# Tutorial: Collecting Responses

This tutorial covers the response collection phase: serving the form to respondents (or handing the FHIR export to a third-party delivery tool), importing FHIR QuestionnaireResponse JSON files, and verifying what landed in the database.

The FHIR boundary is the interface: a delivery tool renders the `Questionnaire`, collects responses, and produces `QuestionnaireResponse` JSON that quickq ingests. The default tool is `quickq-forms` (bundled when you `uv tool install` quickq), but the contract is FHIR — any FHIR-compliant tool works.

---

## Default delivery: `quickq serve`

For most studies — small lab cohorts through n=10–30 distributed pilots — `quickq serve` is the path of least resistance. It launches `quickq-forms` against your `study.db`, opens a browser, and writes submitted responses directly back to the OLTP.

```bash
quickq serve study.db
```

For pilot deployments with a known respondent list, add a roster file so only listed IDs can submit:

```bash
printf "R001\nR002\nR003\n" > codes.txt
quickq serve study.db --respondents codes.txt
# email each respondent http://your-host:8000/?r=R001
```

See the [End-to-End walkthrough](end-to-end.md) for the full local flow, and [Third-party FHIR renderers](../reference/third-party-renderers.md) for the interop story (REDCap, LHC-Forms, custom mobile clients).

---

## Export for an external delivery tool

If you need to hand the questionnaire to a tool that's not `quickq-forms` (REDCap, a custom mobile app, LHC-Forms for an interop demo), export the FHIR Questionnaire JSON:

```bash
quickq fhir export study.db 1 --output phq9_questionnaire.json
```

The output is a standard FHIR R4 `Questionnaire` resource. Any FHIR-compliant delivery tool accepts it. When responses come back as `QuestionnaireResponse` JSON, import them with `quickq fhir import-response` (below).

---

## Import responses

When FHIR QuestionnaireResponse JSON files come back from the delivery tool, import them into the OLTP:

```bash
# Single response file
quickq fhir import-response response.json study.db

# Batch: a JSON array of multiple QuestionnaireResponse resources
quickq fhir import-response batch.json study.db --study-id 1
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
