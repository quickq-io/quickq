# Quickstart: End-to-End Walkthrough

This guide walks through the complete quickq loop using a gout symptoms survey as the running example. You will author the instrument from scratch, build it up incrementally, collect a response through the web form, and view the results.

By the end you will have:

- A study database with a custom questionnaire
- A running web form that accepts responses
- An imported response in the analytics layer
- A summary report

---

## Prerequisites

!!! info "You will need"
    - **Python 3.11+** ([python.org](https://www.python.org/downloads/) or your system package manager)
    - **uv** ([installation guide](https://docs.astral.sh/uv/getting-started/installation/))
    - **Node.js 18+** with npm ([nodejs.org](https://nodejs.org/), LTS includes npm)

    If a command below fails because one of these is missing, install it and re-run.

---

## Step 1 — Install quickq

Pick a parent directory to hold the cloned repos (this tutorial uses `~/code`). In Step 6 you will clone `quickq-forms` next to `quickq` in the same parent and install both together.

```bash
mkdir -p ~/code && cd ~/code
git clone https://github.com/quickq-io/quickq.git
uv tool install ./quickq
```

This installs `quickq` as a standalone command on your PATH. Verify it and get a quick overview of what the tool does:

```bash
quickq --help
```

You should see the full command list grouped by function — Core, FHIR, Compliance, and Federated. Spend a moment reading through it before continuing.

---

## Step 2 — Create a study directory

quickq is a tool you install once and use across many studies. Your study files — the YAML instrument definitions, the SQLite database, and any exported files — live in their own directory, separate from the quickq source code.

```bash
mkdir gout-study && cd gout-study
```

Everything from here on happens inside `gout-study/`. The YAML files you author and the `study.db` database will both live here. If you add a `quickq.yml` config file later (for things like default output format), it goes here too.

---

## Step 3 — Create a study database

```bash
quickq init study.db --with-library
```

`--with-library` loads the bundled question bank (PHQ-9, GAD-7, PRAPARE, and others) so you can reference their questions in your own instruments.

---

## Step 4 — Author the questionnaire

Create a file called `gout.yaml`. We will build it up in three stages.

### Stage 1 — Minimal instrument

Start with two plain questions:

```yaml
questionnaire:
  name: "Gout Symptoms Check-In"
  canonical_url: "http://example.com/instruments/gout-checkin"
  version: "1.0"

  questions:
    - link_id: gout.last_attack
      text: "When did you last have a gout attack?"
      type: date

    - link_id: gout.pain_now
      text: "How would you rate your current joint pain? (0 = none, 10 = worst)"
      type: numeric
      range: [0, 10]
```

Load and verify:

```bash
quickq load gout.yaml study.db
quickq list surveys study.db
```

Preview it in your browser:

```bash
quickq preview study.db 1
```

### Stage 2 — Add an option set

Option sets define a reusable list of choices that multiple questions can share. Add a frequency scale and two questions that use it:

```yaml
questionnaire:
  name: "Gout Symptoms Check-In"
  canonical_url: "http://example.com/instruments/gout-checkin"
  version: "1.0"

  option_sets:
    frequency:
      - { text: "Never",       value: "never" }
      - { text: "Rarely",      value: "rarely" }
      - { text: "Sometimes",   value: "sometimes" }
      - { text: "Often",       value: "often" }
      - { text: "Very often",  value: "very_often" }

  questions:
    - link_id: gout.last_attack
      text: "When did you last have a gout attack?"
      type: date

    - link_id: gout.pain_now
      text: "How would you rate your current joint pain? (0 = none, 10 = worst)"
      type: numeric
      range: [0, 10]

    - link_id: gout.alcohol
      text: "How often do you drink alcohol?"
      type: single_choice
      options: $frequency

    - link_id: gout.red_meat
      text: "How often do you eat red meat or shellfish?"
      type: single_choice
      options: $frequency
```

Reload — quickq will overwrite the previous version since the `canonical_url` matches:

```bash
quickq load gout.yaml study.db
quickq preview study.db 1
```

The `$frequency` reference means the option list is defined once and shared across both questions. If you add a sixth option later, both questions pick it up automatically.

### Stage 3 — Add skip logic and a multiple-choice question

Skip logic hides or shows questions based on earlier answers:

```yaml
questionnaire:
  name: "Gout Symptoms Check-In"
  canonical_url: "http://example.com/instruments/gout-checkin"
  version: "1.0"

  option_sets:
    frequency:
      - { text: "Never",       value: "never" }
      - { text: "Rarely",      value: "rarely" }
      - { text: "Sometimes",   value: "sometimes" }
      - { text: "Often",       value: "often" }
      - { text: "Very often",  value: "very_often" }

    joints:
      - { text: "Big toe",     value: "big_toe" }
      - { text: "Ankle",       value: "ankle" }
      - { text: "Knee",        value: "knee" }
      - { text: "Wrist",       value: "wrist" }
      - { text: "Elbow",       value: "elbow" }
      - { text: "Other",       value: "other", is_other: true }

  questions:
    - link_id: gout.last_attack
      text: "When did you last have a gout attack?"
      type: date

    - link_id: gout.joints_today
      text: "Which joints were affected? Select all that apply."
      type: multiple_choice
      options: $joints
      show_when:
        question: gout.last_attack
        operator: exists

    - link_id: gout.pain_now
      text: "How would you rate your current joint pain? (0 = none, 10 = worst)"
      type: numeric
      range: [0, 10]

    - link_id: gout.alcohol
      text: "How often do you drink alcohol?"
      type: single_choice
      options: $frequency

    - link_id: gout.red_meat
      text: "How often do you eat red meat or shellfish?"
      type: single_choice
      options: $frequency

    - { library: gout.notes }
```

```bash
quickq load gout.yaml study.db
quickq preview study.db 1
```

The joint question now only appears if the date question has been answered.

### Stage 4 — Pull a validated question from the library

The `--with-library` flag in Step 2 loaded a bank of validated questions, including both PHQ-9 items. You can pull any of them into your questionnaire with a single line instead of redefining them:

```yaml
    - { library: gout.notes }
    - { library: phq9.1 }
    - { library: phq9.2 }
```

This inserts the first two PHQ-9 items — "Little interest or pleasure in doing things" and "Feeling down, depressed, or hopeless" — with their original wording, LOINC concept codes, and answer options intact. No copy-pasting or manual coding required.

```bash
quickq load gout.yaml study.db
quickq preview study.db 1
```

You can browse all available link_ids with:

```bash
quickq list library study.db
```

---

## Step 5 — Export as FHIR

```bash
quickq fhir export study.db 1 --output gout.json
```

This produces a standard FHIR R4 Questionnaire resource. Any FHIR-compliant delivery tool can render it. In the next step, quickq-forms reads directly from `study.db`, so you won't need this file today — but it's what you'd hand to an external tool like LHC-Forms or REDCap.

---

## Step 6 — Start the form server

`quickq serve` launches a web form for your study and opens it in your browser. Submitted responses write straight back to `study.db`.

The serve command lives in a separate package, `quickq-forms`. Reinstall `quickq` with the `serve` extra so both end up on your PATH together:

```bash
cd ~/code            # the parent directory from Step 1
git clone https://github.com/quickq-io/quickq-forms.git
uv tool install --reinstall ./quickq --with ./quickq-forms
```

Your layout should now look like:

```
~/code/
├── quickq/          # cloned in Step 1
├── quickq-forms/    # cloned just now
└── gout-study/      # created in Step 2; contains study.db
```

Start the server from inside `gout-study/`:

```bash
cd ~/code/gout-study
quickq serve study.db
```

You should see:

```
Serving questionnaire 1 from /Users/yourname/code/gout-study/study.db on http://localhost:8000
```

A browser tab opens at **http://localhost:8000** showing the form.

!!! note
    If the form doesn't load, try a private/incognito window — browser extensions sometimes block localhost requests.

---

## Step 7 — Take the survey

You should see the Gout Symptoms Check-In form rendered in your browser. Work through it:

1. **When did you last have a gout attack?** — enter a date. As soon as you do, the next question appears.
2. **Which joints were affected?** — this question is hidden until the date field is answered. This is the skip logic you defined with `show_when` in Stage 3. Select one or more joints.
3. **Current joint pain** — enter a number between 0 and 10.
4. **Alcohol frequency** and **red meat frequency** — select from the frequency scale you defined as an option set.
5. **Notes**, **PHQ-9 items** — complete the remaining questions.

Submit the form. You should see a confirmation that your response was recorded.

---

## Step 8 — Confirm the response arrived

Back in your `gout-study/` terminal:

```bash
quickq list surveys study.db
```

The response count next to your questionnaire should now show 1.

---

## Step 9 — Seed synthetic responses

One response produces a sparse report. To see realistic distributions across all questions, generate a batch of synthetic responses:

```bash
quickq seed study.db 1 --n 50 --seed 42
```

This generates 50 plausible responses that respect the questionnaire's question types, option sets, numeric ranges, and skip logic — the joint question only gets answers in sessions where the date question was answered. Your real response from Step 7 is still in the database alongside the synthetic ones.

---

## Step 10 — Build the analytics layer

```bash
quickq refresh study.db analytics.duckdb
```

This reads all responses from `study.db` and builds the analytical layer in `analytics.duckdb` — answer distributions, session summaries, and scores for any scoring rules on the instrument.

---

## Step 11 — View the report

```bash
quickq report analytics.duckdb study.db 1
```

The report shows answer distributions for each question, completion statistics, and scores for any scoring rules defined on the instrument. With 50+ responses you should see meaningful distributions — how often each frequency option was chosen, the spread of pain ratings, which joints came up most often.

To export a human-readable document for sharing with colleagues, an IRB, or a coordinating center:

```bash
quickq report analytics.duckdb study.db 1 --output report.md
```

---

## Step 12 — Explore the analytics layer

Open the analytics database in the local DuckDB UI:

```bash
quickq analytics
```

`quickq analytics` defaults to `./analytics.duckdb` and opens a SQL editor in your browser. It requires the [DuckDB CLI](https://duckdb.org/docs/installation/) on your PATH (`brew install duckdb` on macOS); the interactive UI needs DuckDB ≥ 1.2.

For a non-interactive run — handy for notebooks or scripts — pass `--queries-file`:

```bash
echo "SELECT COUNT(*) FROM dim_session;" > count.sql
quickq analytics --queries-file count.sql
```

Try the following queries in the UI.

---

**Which joints were most commonly affected?**

Each selected option in a multiple-choice question is its own row in `fact_response`. The same GROUP BY pattern works for every question type — no schema knowledge required.

```sql
SELECT ro.option_text AS joint, COUNT(*) AS n
FROM fact_response f
JOIN dim_question q USING (question_id)
JOIN dim_response_option ro USING (option_id)
WHERE q.link_id = 'gout.joints_today'
GROUP BY ro.option_text
ORDER BY n DESC;
```

---

**Does alcohol frequency correlate with pain score?**

Two questions with completely different types — a choice and a numeric — joined only by `session_id`. This query is structurally identical for any pair of questions in any instrument, without any schema changes.

```sql
SELECT
    alcohol.option_text AS alcohol_frequency,
    COUNT(*)            AS n,
    ROUND(AVG(pain.response_numeric), 1) AS avg_pain_score
FROM (
    SELECT f.session_id, ro.option_text
    FROM fact_response f
    JOIN dim_question q USING (question_id)
    JOIN dim_response_option ro USING (option_id)
    WHERE q.link_id = 'gout.alcohol'
) alcohol
JOIN (
    SELECT f.session_id, f.response_numeric
    FROM fact_response f
    JOIN dim_question q USING (question_id)
    WHERE q.link_id = 'gout.pain_now'
) pain USING (session_id)
GROUP BY alcohol.option_text
ORDER BY avg_pain_score DESC;
```

---

**Did skip logic fire correctly?**

The questionnaire structure is queryable. `skip_violated` should be 0 — no session should have answered the joints question without first answering the date question.

```sql
SELECT
    SUM(CASE WHEN date_answered AND joints_answered THEN 1 ELSE 0 END)       AS skip_respected,
    SUM(CASE WHEN NOT date_answered AND joints_answered THEN 1 ELSE 0 END)    AS skip_violated
FROM (
    SELECT session_id,
           BOOL_OR(q.link_id = 'gout.last_attack')   AS date_answered,
           BOOL_OR(q.link_id = 'gout.joints_today') AS joints_answered
    FROM fact_response f
    JOIN dim_question q USING (question_id)
    GROUP BY session_id
);
```

---

## What to look for

Note anything that caused friction, confusion, or required guessing. Specifically:

- Did the YAML format feel natural? Were any field names or structures surprising?
- Did the option set feature feel useful and clear?
- Did skip logic behave as expected in the preview?
- Did the form render correctly and match what you authored?
- Was submission clearly confirmed?
- Did the report reflect the answers you submitted?

All feedback is useful — the goal is to find rough edges before real studies depend on this.
