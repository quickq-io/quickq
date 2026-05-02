# End-to-End User Testing Walkthrough

This guide walks through the complete quickq loop: creating a study, loading an instrument, collecting a response through the web form, and viewing results. It is intended for early testers validating the full pipeline.

By the end you will have:

- A working study database with a PHQ-9 questionnaire
- A running web form that accepts responses
- An imported response in the analytics layer
- A summary report

---

## Prerequisites

- **Python 3.11+** and **[uv](https://docs.astral.sh/uv/)** — for quickq
- **Node.js 18+** and **npm** — for the quickq-forms web frontend

Check what you have:

```bash
python --version
uv --version
node --version
npm --version
```

---

## Step 1 — Install quickq

Clone the repo and install dependencies:

```bash
git clone https://github.com/quickq-io/quickq.git
cd quickq
uv sync
```

Verify:

```bash
uv run quickq --help
```

---

## Step 2 — Create a study database

```bash
uv run quickq init study.db --with-library
```

`--with-library` seeds the bundled question library so you can browse available instruments.

Confirm it worked:

```bash
uv run quickq list studies study.db
```

---

## Step 3 — Load the PHQ-9

The PHQ-9 is included in the bundled library. Load it into your study:

```bash
uv run quickq load quickq/library/phq9.yaml study.db
```

Confirm it loaded:

```bash
uv run quickq list surveys study.db
```

You should see PHQ-9 with ID `1`.

---

## Step 4 — Export as FHIR

Export the questionnaire as a FHIR R4 JSON file for the web form:

```bash
uv run quickq fhir export study.db 1 --output phq9.json
```

---

## Step 5 — Install and start quickq-forms

In a separate terminal, clone quickq-forms and install its dependencies:

```bash
git clone https://github.com/quickq-io/quickq-forms.git
cd quickq-forms
uv sync
cd frontend && npm install && cd ..
```

Start the form pointing at your study database:

```bash
bash scripts/dev.sh --db /path/to/study.db --questionnaire-id 1
```

Replace `/path/to/study.db` with the absolute path to the database you created in Step 2.

You should see:

```
Starting API server (local adapter) on :8000
Starting Vite dev server on :5173

  API   → http://localhost:8000/health
  Form  → http://localhost:5173
```

Open **http://localhost:5173** in your browser.

---

## Step 6 — Submit a response

Fill out the PHQ-9 form and submit it. The response is written directly to `study.db` by the local adapter.

!!! note
    If the form does not load, check that no browser extensions are blocking localhost requests. Try a private/incognito window if needed.

Confirm the response was recorded:

```bash
uv run python -c "
from quickq.schema import open_oltp
conn = open_oltp('study.db', read_only=True)
n = conn.execute('SELECT COUNT(*) FROM response_session').fetchone()[0]
print(f'{n} session(s) in database')
"
```

---

## Step 7 — Build the analytics layer

Run `refresh` to load the collected responses into the DuckDB OLAP layer:

```bash
uv run quickq refresh study.db analytics.duckdb
```

---

## Step 8 — View the report

```bash
uv run quickq report analytics.duckdb study.db 1
```

This prints a Markdown summary of response distributions for the PHQ-9. To save it:

```bash
uv run quickq report analytics.duckdb study.db 1 --output report.md
```

---

## What to look for

As you test, note any points where you got stuck, received an unhelpful error message, or had to guess what to do next. Specifically:

- Did the form render correctly and match the PHQ-9 as you know it?
- Did submission feel complete — clear confirmation, no silent failures?
- Did the report output make sense given the answers you submitted?

Feed back anything that felt off. No issue is too small.
