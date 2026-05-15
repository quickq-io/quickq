# Multi-Site Studies: fork and merge

A multi-site study is a study where multiple institutions collect data independently against the same instrument and pool the results for combined analysis. `quickq fork` and `quickq merge` are the two commands that support this pattern.

`fork` distributes the instrument outward; `merge` brings the populated databases back inward. Together they let a coordinating center hand each site a structurally identical `study.db`, let each site collect into its own copy, and let the coordinator assemble the result without anyone needing custom code per site.

This page is a reference for the commands. For the design rationale (why fork-and-merge vs federated vs centralized), see [Design Decisions: Scaling Architecture](../design_decisions.md#scaling-architecture).

---

## When to fork-and-merge vs federate

quickq has two patterns for multi-site work:

- **`fork` / `merge`** — each site keeps a copy of the data; merged centrally for analysis. Use this when the institutions are willing to share row-level data (under a DUA, after de-identification, etc.). The merged file is one composite study database.
- **`quickq federated query`** — each site keeps row-level data behind its institutional boundary; only aggregate query results cross over. Use this when site-level data sharing is prohibited or undesirable.

The two patterns aren't mutually exclusive. A consortium might fork the instrument, collect locally, run federated queries for routine reporting, and merge for a periodic full-cohort analysis under a DUA. See [Federated Analytics](federated.md) for the other half of the multi-site story.

---

## `quickq fork`

Scaffold a new study database from an existing one's structure — questions, options, scoring rules, skip rules — without copying responses. Used to seed sites with a canonical instrument.

```bash
quickq fork canonical.db \
    --questionnaire-id 1 \
    --output sites/site_A.db \
    --site-id site_A
```

### What gets copied

- The named questionnaire and all its questions, options, scoring rules, skip rules
- Concept codes (LOINC, SNOMED, OMOP) attached to questions and options
- Versioning lineage (the fork records its parent for provenance)

### What does NOT get copied

- Responses, sessions, respondents — the new database starts empty
- Study metadata (PI, IRB, dates) unless you keep them; use `--reset-study-metadata` to clear them so the site fills in their own

### Common options

- `-q / --questionnaire-id` (required) — which questionnaire from the source to fork
- `-o / --output` (required) — path for the new database
- `--site-id <id>` — recorded in the fork's audit trail
- `--version <new-version>` — bump the version on the forked questionnaire (default: copy as-is)
- `--reset-study-metadata` — blank out PI / IRB / start-end dates so the recipient fills them in
- `--note "..."` — free-text note recorded with the fork event
- `--overwrite` — replace `--output` if it exists

### Typical workflow

```bash
# 1. Coordinator publishes a canonical instrument
quickq init canonical.db --with-library
quickq load instrument.yaml canonical.db

# 2. Fork one database per site (loop or scripted)
for site in site_A site_B site_C; do
    quickq fork canonical.db \
        --questionnaire-id 1 \
        --output "sites/${site}.db" \
        --site-id "$site" \
        --reset-study-metadata
done

# 3. Distribute the forks. Each site fills in their own
#    study metadata, deploys collection, accumulates responses.

# 4. Periodically gather the populated DBs back at the
#    coordinating center and merge (see below).
```

---

## `quickq merge`

Combine multiple site databases into a single composite study database. Used to assemble forked sites back into one study for cross-site analysis.

```bash
quickq merge \
    sites/site_A.db \
    sites/site_B.db \
    sites/site_C.db \
    --output combined.db
```

### What gets merged

- Respondents from every source, with their site-of-origin preserved on each respondent row
- Sessions and responses, deduplicated by FHIR response ID where present
- Concept code reconciliation: if two sites independently assigned the same `Local:NNNN` code to different constructs, the merge surfaces the conflict for resolution before completing

### What it produces

A single `study.db` with the combined cohort, ready for `quickq refresh` to build an OLAP layer that treats it as one study. Site-level breakdowns are available because `respondent.site_id` is preserved end to end.

### Common options

- `-o / --output` (required) — path for the merged database
- `--overwrite` — replace `--output` if it exists

The command will report counts after each merge:

```
Merged 3 source(s) into combined.db:
  847 respondents
  3,420 sessions
  168,540 responses
  12 duplicate sessions skipped
```

### Concept-code collisions

If two sites have used `auto_concept` (the option to mint local concept codes during loading), they may independently assign the same `Local:NNNN` code to different concepts. `quickq merge` detects these collisions by comparing concept_name and domain. Resolution options:

- **Remap** — assign one site's code to a new range, re-run merge
- **Equate** — declare the codes equivalent via `concept_relationship` in the canonical DB, re-run merge

This is the same kind of harmonization any cross-institutional study has to do; quickq surfaces it at merge time rather than letting it propagate silently.

---

## The audit trail

Both commands write to the study's audit history. After a fork-and-merge cycle, the combined database knows:

- Which sites contributed (`respondent.site_id`)
- When each fork happened (questionnaire fork events)
- When each merge happened (merge events)
- Which concept-code conflicts surfaced and how they were resolved

This is the load-bearing piece for IRBs and regulatory reviewers: every row in the combined cohort traces back to a specific site's fork of the canonical instrument.

---

## Worked example

A full walked-through multi-site example — a three-site PHQ-9 deployment with realistic timelines, fork distribution, periodic merges, and a federated-query alternative — is being developed under `quickq-io-4lc`. For now, the scaling tier descriptions in [Design Decisions](../design_decisions.md#scaling-architecture) sketch the pattern at the scale level (Tier 3 multi-site, Tier 4 large institutional).
