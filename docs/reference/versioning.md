# Instrument Versioning and Data Governance

Survey instruments change. IRBs request wording revisions. Bugs in delivery tools corrupt a batch of responses. A question that lives in one study turns out to be useful in another. This reference covers how quickq handles all three situations without touching collected response data.

The core principle: **response rows are never modified or deleted**. All governance metadata is additive — lineage records, equivalence declarations, errata notes, and version diffs sit alongside the data without changing it.

---

## The question bank model

Questions are reusable bank items independent of any questionnaire. The `question` table holds the item; `questionnaire_question` records its placement in a specific instrument. This separation is what makes migration straightforward and versioning tractable.

```
question (bank item — immutable once created)
  ↑ referenced by many
questionnaire_question (placement in a specific questionnaire version)
  ↑ belongs to
questionnaire (a versioned instrument)
```

**Immutability rule:** once a question row exists, its `question_text` cannot change. Attempting to load a YAML with the same `link_id` but different text raises a `ValueError`. To intentionally revise a question, you create a new row with a new `link_id` and declare the relationship via `record_question_lineage`.

---

## Workflow 1 — IRB-requested change

**Scenario:** The IRB approves protocol amendment PA-2025-003. GAD-7 item 3 ("Worrying too much about different things") must be changed to "Worrying too much about many different things" for an adolescent cohort. Amendment takes effect 2025-03-15. Sessions before that date used the original wording.

### Step 1 — Create the revised question

```python
from quickq.authoring import upsert_question
from quickq.models import QuestionDef

q_old = conn.execute(
    "SELECT question_id FROM question WHERE link_id = 'gad7.3'"
).fetchone()[0]

q_new = upsert_question(conn, QuestionDef(
    link_id="gad7.3.v2",
    text="Worrying too much about many different things",
    type="single_choice",
    concept="LOINC:69733-4",   # same LOINC code — same construct
))
conn.commit()
```

### Step 2 — Record the lineage

```python
from quickq.versioning import record_question_lineage

record_question_lineage(
    conn, q_new, q_old,
    change_type="reword",
    change_description="IRB amendment PA-2025-003: age-appropriate language for adolescent cohort",
    effective_date="2025-03-15",
)
conn.commit()
```

`change_type` must be one of: `reword`, `option_added`, `option_removed`, `option_reworded`, `split`, `merge`, `other`.

### Step 3 — Create the new questionnaire version

```python
from quickq.authoring import insert_questionnaire, place_question
from quickq.models import QuestionnaireDef

gad7_v2 = insert_questionnaire(conn, QuestionnaireDef(
    name="GAD-7 v2.0 (Adolescent)",
    canonical_url="http://quickq.io/instruments/gad7",
    version="2.0",
))
# place all questions, using gad7.3.v2 instead of gad7.3
```

### Step 4 — Record the version diff

```python
from quickq.versioning import diff_questionnaire_versions, record_questionnaire_diff

# Auto-detect adds/removes/reorders:
diffs = diff_questionnaire_versions(conn, gad7_v1_id, gad7_v2_id, auto_record=True)

# Manually declare the reword (diff doesn't auto-detect this — different link_ids
# look like an add + remove):
record_questionnaire_diff(
    conn, gad7_v1_id, gad7_v2_id,
    change_type="item_reworded",
    qq_id_from=qq_id_of_old_item,
    qq_id_to=qq_id_of_new_item,
    notes="IRB PA-2025-003. Original: 'Worrying too much about different things'",
)
conn.commit()
```

### Step 5 — Declare equivalence

```python
from quickq.versioning import declare_equivalence

declare_equivalence(
    conn, q_old, q_new,
    relationship="near_equivalent",
    confidence="high",
    harmonization_notes=(
        "Single word addition ('many'). No evidence of response scale shift. "
        "Treat as equivalent for scoring; note wording cohort in sensitivity analyses."
    ),
    declared_by="Dr. Smith",
)
conn.commit()
```

### Step 6 — Log the errata entry

```python
from quickq.versioning import record_errata

record_errata(
    conn,
    event_type="irb_action",
    title="GAD-7 item 3 reworded per IRB amendment PA-2025-003",
    description=(
        "Amendment PA-2025-003 approved 2025-02-28. "
        "Item 3 wording updated for adolescent pilot cohort."
    ),
    severity="major",
    questionnaire_id=gad7_v1_id,
    question_id=q_old,
    affects_date_to="2025-03-14",
    analyst_guidance=(
        "Sessions before 2025-03-15 used link_id 'gad7.3'. "
        "Sessions from 2025-03-15 use 'gad7.3.v2'. "
        "Use equivalence_group_id in the OLAP to query across both versions."
    ),
    reported_by="jane.doe@institution.edu",
)
conn.commit()
```

### Querying across versions after the change

After `quickq refresh`, `dim_question.equivalence_group_id` is set to the same value for `gad7.3` and `gad7.3.v2` (they are in the same connected component). Use it to span both versions without caring about `link_id`:

```sql
-- All GAD-7 item-3 responses, regardless of which version the respondent saw
SELECT
    dr.external_id  AS respondent,
    dq.link_id,
    fr.response_numeric AS score,
    ds.session_date_key
FROM fact_response fr
JOIN dim_question   dq USING (question_id)
JOIN dim_respondent dr USING (respondent_id)
JOIN dim_session    ds USING (session_id)
WHERE dq.equivalence_group_id = (
    SELECT equivalence_group_id FROM dim_question WHERE link_id = 'gad7.3'
)
ORDER BY dr.external_id;
```

To annotate which cohort saw which wording, join through `dim_question_lineage`:

```sql
SELECT
    dq.link_id,
    dql.change_type,
    dql.effective_date,
    dql.change_description,
    COUNT(fr.response_id) AS responses
FROM dim_question dq
LEFT JOIN dim_question_lineage dql ON dq.question_id = dql.question_id
LEFT JOIN fact_response fr USING (question_id)
WHERE dq.equivalence_group_id = (
    SELECT equivalence_group_id FROM dim_question WHERE link_id = 'gad7.3'
)
GROUP BY dq.link_id, dql.change_type, dql.effective_date, dql.change_description
ORDER BY dq.link_id;
```

---

## Workflow 2 — Bug or known data quality issue

**Scenario:** Post-collection review finds that a delivery platform bug reversed the `ob` and `midwife` option values for sessions 1–50. The raw response rows are not correctable (no authoritative ground truth); the issue must be documented so analysts know to exclude those sessions from provider-type analysis.

### Step 1 — Log the errata entry

```python
record_errata(
    conn,
    event_type="delivery_bug",
    title="Provider type reversed in sessions 1–50 (LHC-Forms option order bug)",
    description=(
        "LHC-Forms rendered the visits.provider answer options in reverse order "
        "due to a display_order indexing bug in the FHIR export. "
        "Affected sessions: 1 through 50 inclusive."
    ),
    severity="critical",
    questionnaire_id=prenatal_questionnaire_id,
    question_id=provider_question_id,
    affects_session_from=1,
    affects_session_to=50,
    analyst_guidance=(
        "Exclude sessions 1–50 from any analysis of visits.provider. "
        "All other variables in those sessions are unaffected."
    ),
    reported_by="data.manager@institution.edu",
)
conn.commit()
```

### Step 2 — Query with errata awareness

Analysts should join to the errata log to flag or exclude affected sessions. Add this pattern to your analysis views:

```sql
-- Sessions affected by open critical errata on visits.provider
SELECT DISTINCT rs.session_id
FROM study_errata_log el
JOIN response_session rs
  ON rs.session_id BETWEEN el.affects_session_from AND el.affects_session_to
WHERE el.status = 'open'
  AND el.severity = 'critical'
  AND el.question_id = (SELECT question_id FROM question WHERE link_id = 'visits.provider');
```

```sql
-- Provider distribution, excluding errata-flagged sessions
SELECT provider, COUNT(*) AS n
FROM v_prenatal_visits
WHERE session_id NOT IN (
    SELECT DISTINCT rs.session_id
    FROM study_errata_log el
    JOIN response_session rs
      ON rs.session_id BETWEEN el.affects_session_from AND el.affects_session_to
    WHERE el.status = 'open' AND el.severity IN ('critical', 'major')
      AND el.question_id = (SELECT question_id FROM question WHERE link_id = 'visits.provider')
)
GROUP BY provider;
```

### Step 3 — Mark as resolved (if corrected)

If a correction is later applied:

```python
conn.execute(
    """
    UPDATE study_errata_log
       SET status = 'resolved', resolved_by = ?, resolved_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
     WHERE errata_id = ?
    """,
    ("data.manager@institution.edu", errata_id),
)
conn.commit()
```

And log the correction as a separate entry:

```python
record_errata(
    conn,
    event_type="correction",
    title="Provider values re-coded for sessions 1–50",
    description="Systematic recode applied: all 'ob' → 'midwife' and vice versa.",
    severity="major",
    affects_session_from=1, affects_session_to=50,
    analyst_guidance="Sessions 1–50 provider values are now correct. Original errata #1 resolved.",
)
```

---

## Workflow 3 — Moving a question to another survey

**Scenario:** The `visits.concern` boolean question from the Prenatal Visit Log is also wanted in a new Postpartum Follow-up survey. The question is identical — same text, same type, same concept code.

### Reuse, don't copy

Because questions are bank items, reuse is a `place_question` call. No new question row is needed:

```python
# Look up the existing question
concern_q_id = conn.execute(
    "SELECT question_id FROM question WHERE link_id = 'visits.concern'"
).fetchone()[0]

# Place it in the new questionnaire at the desired position
place_question(conn, postpartum_questionnaire_id, concern_q_id, display_order=5)
conn.commit()
```

The `link_id`, `concept_id`, and all option definitions are inherited automatically. Response rows from both questionnaires will reference the same `question_id`, so cross-questionnaire analysis of this item requires no special handling.

### When to create a new question instead

Create a new question with a new `link_id` if the item was adapted for the new context — different wording, different options, different response scale. Then use `record_question_lineage` and `declare_equivalence` to express the relationship, exactly as in Workflow 1.

---

## API reference

### `record_question_lineage`

```python
record_question_lineage(
    conn, question_id, parent_question_id,
    change_type,                      # reword | option_added | option_removed |
                                      # option_reworded | split | merge | other
    change_description=None,
    effective_date=None,              # ISO date string, e.g. "2025-03-15"
) -> int                              # lineage_id
```

### `get_lineage_ancestors`

```python
get_lineage_ancestors(conn, question_id) -> list[dict]
# Returns: [{ question_id, link_id, question_text, change_type,
#              change_description, effective_date }, ...]
# Immediate parent first.
```

### `declare_equivalence`

```python
declare_equivalence(
    conn, question_id_1, question_id_2,
    relationship,     # equivalent | near_equivalent | related | supersedes
    confidence="medium",  # high | medium | low
    harmonization_notes=None,
    declared_by=None,
) -> tuple[int, int]  # (forward_id, reverse_id)
```

Idempotent — a second call with the same `(q1, q2, relationship)` updates confidence and notes without inserting a duplicate.

### `get_equivalence_group`

```python
get_equivalence_group(conn, question_id) -> list[dict]
# Returns all questions declared equivalent or near_equivalent to question_id.
# Self excluded. Each entry: { question_id, link_id, question_text,
#                               relationship, confidence, harmonization_notes }
```

### `compute_equivalence_groups`

```python
compute_equivalence_groups(conn) -> dict[int, int]
# Returns {question_id: group_id} for every question.
# Questions with no equivalences each get a unique singleton group_id.
```

Called automatically by `quickq refresh`; result is written to `dim_question.equivalence_group_id` (NULL for singletons in the OLAP).

### `diff_questionnaire_versions`

```python
diff_questionnaire_versions(
    conn, from_questionnaire_id, to_questionnaire_id,
    auto_record=False,   # if True, writes diffs to questionnaire_version_diff
) -> list[dict]
# Detects: item_added, item_removed, item_reordered.
# Does NOT auto-detect rewording (different link_ids → declare via lineage).
```

### `record_questionnaire_diff`

```python
record_questionnaire_diff(
    conn, from_questionnaire_id, to_questionnaire_id,
    change_type,    # item_added | item_removed | item_reworded | item_reordered |
                    # skip_rule_changed | scoring_changed | option_changed
    qq_id_from=None, qq_id_to=None, notes=None,
) -> int           # diff_id
```

### `record_errata`

```python
record_errata(
    conn,
    event_type,         # delivery_bug | question_error | irb_action |
                        # correction | deprecation | note
    title, description,
    severity="minor",   # critical | major | minor | informational
    study_id=None, questionnaire_id=None, question_id=None,
    affects_session_from=None, affects_session_to=None,
    affects_date_from=None, affects_date_to=None,
    analyst_guidance=None,
    reported_by=None,
) -> int               # errata_id
```

---

## Open errata checklist

Run this before any data export or publication to surface all unresolved issues:

```sql
SELECT errata_id, event_type, severity, title, affects_date_from, affects_date_to,
       affects_session_from, affects_session_to, analyst_guidance
FROM study_errata_log
WHERE status = 'open'
ORDER BY
    CASE severity
        WHEN 'critical'      THEN 1
        WHEN 'major'         THEN 2
        WHEN 'minor'         THEN 3
        WHEN 'informational' THEN 4
    END,
    reported_at DESC;
```
