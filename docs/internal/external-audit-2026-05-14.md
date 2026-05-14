# External Audit — 2026-05-14

A naive outside generalist audit of quickq, commissioned to balance the inside-out perspective the project has accumulated. Filed alongside the existing internal docs (renderer-coverage, design rationale).

## Context

The recent landing-page rework surfaced a worry that the project's framing might be too mechanism-forward ("the data model is the contract") for a researcher landing cold on the public docs. Rather than iterate on that question internally, we asked an independent data-applications generalist — fresh context, no exposure to our internal docs (CLAUDE.md, docs/internal/) — to read the public docs and give a candid audit.

The audit is **anchored to quickq's own self-described scope** (health and epidemiology survey research) rather than recommending pivots toward general survey tooling.

## Prompt

The auditor was briefed as follows (paraphrased):

> You're an experienced data-applications generalist — someone who's built production survey/data platforms, knows the modern data stack (dbt, Airflow, DuckDB, Parquet), has consulted on tooling decisions for research orgs, and is familiar with REDCap, Qualtrics, KoBoToolbox, Open Data Kit, LimeSurvey, OpenEpi, ResearchKit.
>
> Form your own view from the public docs site (https://quickq-io.github.io/quickq/) and the two repos. Don't lean on `CLAUDE.md` files or `docs/internal/` — internal AI-agent notes, not the user-facing view.
>
> Deliver: (1) merits within the stated scope, (2) weaknesses, (3) three categories of recommendation — **simplify**, **extend**, **broaden appeal within reason** (stay in health/epi research). Compare honestly to REDCap and KoBoToolbox at minimum. Be candid. Strong opinions held loosely.

Result was ~1800 words of prose with clear section headers per ask. Reproduced in full below.

## The audit, verbatim

> I spent time on the public docs site, the README, and enough of the source to spot-check claims. I deliberately ignored anything under `CLAUDE.md` or `docs/internal/`. What follows is what a candid generalist would tell you over coffee, not a recruitment pitch.

### Merits

**The data-model-as-deliverable framing is the strongest thing here.** The pitch that "the `.db` file is the portable study artifact" (README, `philosophy.md`, `design_decisions.md`) is genuinely differentiated. REDCap and Qualtrics treat the platform as the deliverable — the data is something you export *out* of the system, and the schema is opaque, undocumented, or wildly per-instance. quickq inverts this. A researcher with `sqlite3` and no quickq installed at all can open `study.db`, see the schema, and reconstruct the study. That's a real archival/reproducibility property that the incumbents don't have, and it lands well for the docs/IRB/FAIR-deposit story.

**The OLTP/OLAP separation is grown-up engineering, not over-engineered theater.** `quickq/sql/oltp_schema.sql` (~27 tables, 530 lines) and `olap_schema.sql` (~19 tables/views) are sensibly partitioned: normalized authoring/responses on the SQLite side, a star schema with materialized scoring views on the DuckDB side. The "wide-table-per-instrument is the wrong default" critique in `design_decisions.md` is correct in my experience — REDCap's pivot-on-export model is exactly the pain quickq is responding to. The same `fact_response` query shape working across question types and instruments (`reference/query-patterns/`) is a real productivity unlock for anyone doing cross-instrument analysis.

**FHIR as the only boundary is a sharper architectural commitment than I expected.** Most "FHIR-compatible" tools tack FHIR on as one of several exports. quickq has actually structured the repos around the FHIR contract: `quickq-forms` doesn't import from `quickq`, the renderer test suite (`reference/third-party-renderers/`) explicitly verifies that LHC-Forms and REDCap can render the exported `Questionnaire`. That's a meaningfully different posture from REDCap (whose FHIR support is limited and instance-specific) and KoBoToolbox (XLSForm-native, no FHIR).

**The bundled instrument library is a real differentiator if it grows.** `quickq/library/` ships PHQ-9, GAD-7, PHQ-2, AUDIT (referenced), PRAPARE, PROMIS-10, BRFSS tobacco, CDC demographics. REDCap has a "shared library" but it's behind an instance and not standardized across deployments. KoBoToolbox has nothing comparable. Public-domain validated scales as versioned, FHIR-canonical YAML, with LOINC mappings — that's a useful artifact even for researchers who never adopt the rest of the stack.

**The federated mode is honestly scoped.** `reference/federated/` is clear that this is query-distribute-aggregate, not true distributed computation, with row-level suppression. That's the right tradeoff for the audience and it sidesteps the DUA/IRB-amendment trap that kills most multi-site epi studies. The fact that it works because every site has the same OLAP schema is exactly the payoff of the schema-as-contract decision.

**The CLI surface is coherent.** ~26 commands across 5 subgroups in `cli.py` (912 lines) — that's a lot but it organizes cleanly: `new/init/load`, `preview/serve`, `fhir export/import`, `refresh/analytics/report`, `compliance *`, `federated *`. Verbs are consistent and the noun model (study/survey/questionnaire/response) maps directly onto the data model.

### Weaknesses

**The "researcher in YAML" persona is in tension with the actual onboarding path.** The end-to-end tutorial is fifteen-ish commands, requires `uv`, Python 3.11+, and Node.js, and walks the user through four progressive YAML rewrites. That's a 45–60-minute onramp before a single real respondent. REDCap's onramp for the equivalent task is "log in, click New Project, click Add Field" — sub-five-minutes for a single-instrument study. The portability/reproducibility argument is real but the upfront cost is high, and the docs don't honestly acknowledge that REDCap exists or why a researcher would pay this cost. The README doesn't even mention the incumbents.

**SQL literacy is an unstated hard prerequisite.** `tutorials/analytics/`, `tutorials/data-quality/`, `reference/query-patterns/`, and the R recipes all assume the user reads and writes SQL. For an epi PI working through an RA, that's fine. For a solo grad student or a public-health-department analyst with Excel skills and maybe some Stata, that's a wall. REDCap's report builder and KoBo's basic dashboards lower this bar substantially. The docs should either say "this tool is for SQL-literate teams" loudly, or build a thin no-SQL path (saved reports, parameterized queries) for the common cases.

**The grid and likert reporting gaps are quietly admitted in `reference/question-types/`.** Grid renders as a flat list rather than a row×column matrix; likert renders as categorical rather than ordinal. These are exactly the two question types epi researchers use most for symptom batteries and Likert scales. "Full pipeline" with a footnote that the report doesn't render them correctly is a credibility crack — these should be either fixed or surfaced more prominently as known limitations.

**The synthetic seed is uniform-distribution-only.** `reference/seed/` acknowledges this honestly but it limits the feature to structural smoke-testing. For sample-size simulation, scoring-rule validation against realistic distributions, or pre-IRB power analysis (all things the audience actually wants), uniform sampling is useless. This wants either configurable per-question distributions, calibration against published norms (PHQ-9 has known prevalences), or a "seed-from-real-data" mode.

**`quickq serve` is local-only.** `tutorials/collect/` is honest that it's "small lab cohorts through n=10–30 distributed pilots," but the docs gloss over what happens at n=200 or n=2000. There's a roster file and a "single writer per site" caveat, but no story for HTTPS termination, no auth model beyond roster IDs, no production deployment recipe, no Docker image, no "stand this behind nginx" guide. The hosted adapter is on the architecture diagram but not built. So either the realistic ceiling is much lower than the marketing implies, or there's a missing chapter on deployment.

**OMOP integration is narrower than "OMOP integration" suggests.** `reference/omop/` projects to `omop_survey_conduct`, `omop_observation`, and an unmapped-questions checklist. That's a useful piece for federated survey queries but it's not a CDM and OHDSI researchers will notice. Calling this "OMOP interoperability" sets up an expectation that the docs then have to walk back. "OMOP-aligned survey extracts for federated queries" would be more accurate and still differentiating.

**The compliance commands are mechanical, not workflow-shaped.** `compliance fair-check`, `compliance set-metadata`, `compliance delete`, `compliance withdraw` are real (`quickq/compliance.py`, `fair_check.py`) but they're plumbing. There's no DMP-plan generator, no audit-log export in a form an IRB actually accepts, no consent-document linkage, no e-signature trail. For the "IRB workflows" claim on the landing page to land, this needs more than five commands that operate on a SQLite file.

**`fork` and `merge` are present in the CLI but absent from the public docs.** `quickq/fork.py` and `quickq/merge.py` exist as commands. The use case (probably multi-site Tier-3 work, possibly cross-cohort harmonization) is exactly the differentiating workflow, and it's invisible to a docs-landing-page reader. This is buried capital.

**Three-renderer story has cracks.** `reference/third-party-renderers/` says LHC-Forms drops slider affordances and ranked-question multi-position UI, and "we haven't end-to-end-tested every REDCap variant." For something marketed as headless-via-FHIR, the FHIR-out story needs to be ironclad. Either the renderer matrix gets tested in CI against every supported tool, or the marketing dials back to "quickq-forms with experimental third-party render targets."

**No mobile / iOS / ResearchKit story.** The docs say FHIR-out plus a custom mobile client is supported in principle, but there's no SDK, no example, no template, no ResearchKit bridge. For a tool aimed at epi/health, this is a meaningful gap — momentary-EMA and remote-assessment studies are exactly where you can't use REDCap and where FHIR-native delivery would shine.

### Simplify

**Demote OMOP from a top-line feature to a section under "Federated Analytics."** It's a projection layer, the docs admit this, and calling it "OMOP Interoperability" creates an expectation the tool can't meet. Rename to "OMOP-aligned survey extracts." This costs nothing and de-risks the credibility of the rest of the docs.

**Cut or hide the four-stage YAML tutorial.** The end-to-end walkthrough rebuilds the same instrument four times to introduce features progressively. A reader who already knows what they want is doing busywork; a beginner is being asked to absorb option sets, skip logic, and library references before they've seen a single response. Replace with one complete annotated example plus a "patterns" reference page.

**Consolidate the renderer matrix into a single capabilities table.** Right now the question-types page, third-party-renderers page, and various tutorials all hint at "this works in quickq-forms but degrades in LHC-Forms" in different places. One table, fully matrixed (question type × renderer × status), would replace several pages of scattered caveats and tell researchers up front what they can actually deliver where.

**Hide `fork`/`merge` behind a "Multi-site" section header or remove them from the public CLI until documented.** Undocumented commands are worse than absent ones — researchers find them, try them, and lose trust when they don't work as expected.

**The `data-dict`, `report`, `render`, and `preview` commands all produce documentation artifacts.** Consider consolidating under `quickq docs <kind>`. The current verb-soup makes the CLI feel larger than the underlying capability set.

### Extend

**A no-SQL analysis surface.** Even a small library of `quickq report-builder` saved queries — frequency tables per question, scored-instrument distributions, missingness by item, response-rate-over-time — would broaden the audience substantially. Not every epidemiologist writes SQL; almost every one needs frequency tables and scored distributions. The OLAP schema is stable enough to support this; it's a docs-and-templates project, not an engineering project.

**Realistic synthetic data.** Per-question-type distribution overrides in YAML (`distribution: {mean: 1.2, sd: 0.8}` for PHQ items, `prevalence: 0.12` for boolean), plus a "calibrate from real responses" mode. This unlocks pre-IRB power analysis, scoring-rule validation, and educational demos.

**A `quickq deploy` story.** A Docker image, an example reverse-proxy config, an auth-token bolt-on, and a "responding to 500 emails" guide. This doesn't require building the hosted adapter — it requires acknowledging that real pilots run on real servers and giving people a sane default.

**Score-as-data, not score-as-doc.** The PHQ-9 example shows scoring as metadata. What an analyst wants is `fact_score` populated automatically by `refresh`, with severity bands from the YAML, so that the "depression severity by site" query is one SELECT. If this already exists I missed it; if it doesn't, it's the single highest-leverage extension.

**Mobile / iOS template.** A bare-bones ResearchKit or Capacitor template that reads a FHIR Questionnaire and POSTs a QuestionnaireResponse to a hosted quickq endpoint. Even an "experimental" version would be a meaningful credibility unlock for the headless-delivery claim.

**Longitudinal / EMA support.** The repeating-group type and session model can probably be coaxed into supporting daily diaries, but there's no story for scheduling, reminders, or per-respondent timeline analytics. This is where REDCap and Qualtrics actively lose to specialized tools (MetricWire, Avicenna) — there's a gap to fill.

### Broaden appeal (within health/epi)

**Clinical research coordinators at academic medical centers.** They live in REDCap and are forced into it. The pitch they'd respond to: "Your REDCap project export, in a portable database file, with FHIR for EHR integration." A `quickq import-redcap` command that ingests a REDCap data dictionary CSV and produces a quickq YAML would do more for adoption than any new feature. Frame quickq as a *destination* for REDCap projects, not a replacement.

**Epi grad students.** The audience that already knows R/Stata and is teaching themselves SQL. The pitch: "Your dissertation study, reproducibly archived in a single file you can deposit." Lean harder into the FAIR / data-deposit story; partner with a repository (Dryad, ICPSR) on a `quickq compliance export-for-deposit` profile. Build a tidyverse-shaped R package wrapping the DuckDB queries — the R recipes page is good content but a real package would move the needle.

**Federally-funded center data coordinators (PCORnet, All of Us, NIH Common Fund consortia).** The federated-aggregation pattern is exactly what these networks need below the level of full CDM integration. The pitch: "Distribute a query to twelve sites, get back twelve JSON files, assemble." Document a real worked example — fake but realistic three-site deployment with disclosure controls — and this audience will find it. The OMOP framing should be rewritten for this audience specifically.

**Public health departments.** Surveillance instruments (BRFSS-style, post-outbreak case-investigation forms, vaccine-adverse-event intake) are good fits for the YAML-versioned, FHIR-deliverable model, and these teams don't have REDCap budgets. The bundled `brfss_tobacco.yaml` and `cdc_demographics.yaml` already gesture at this. A "Public Health Surveillance" worked example using a realistic case-investigation form would land.

**CDISC-aligned consortia / regulatory submissions.** This is further afield but the data-model-as-contract framing speaks directly to the CDISC SDTM crowd, who think the same way. A worked example showing quickq's `fact_response` mapped to SDTM `QS` (Questionnaire) and `QSCG` (Custom Grouping) domains would attract a small but high-value audience and complement the OMOP story. The triad — FHIR for delivery, OMOP for observational queries, CDISC for regulatory — is a tighter pitch than any of the three alone.

**ResearchKit / DHT (digital health technology) folks.** The DHT-as-medical-device space (Apple Heart Study, Verily Baseline, big pharma decentralized trials) is FHIR-native and starved for portable instrument tooling. A minimal Swift/Capacitor reference client that consumes the FHIR export would put quickq on the map for this audience, which is far better-resourced than typical epi research.

### Closing note

> One last note. The single biggest framing risk in the docs is that the landing page leads with mechanism ("YAML, FHIR, SQLite, DuckDB") rather than benefit. A researcher who already trusts the stack reads that and gets excited. A researcher comparing tools reads it and bounces to REDCap. Lead with what a researcher gets — a portable study file, reproducible analytics, multi-site without DUAs, FAIR-ready deposits — and let the stack be the proof, not the pitch.

## How this audit translated into work

Below is the index of beads tickets filed in direct response to this audit. Items already tracked by pre-existing tickets are noted rather than duplicated.

### Filed

**P2 (high-impact, do soon):**
- `quickq-io-3if` — Landing page benefit-first rewrite (audit follow-up)
- `quickq-io-3g1` — `quickq import-redcap`: read REDCap data dictionary CSV → quickq YAML
- `quickq-io-8p0` — `quickq deploy`: Docker image + reverse-proxy recipe + auth-token bolt-on

**P3 (recommended improvements):**
- `quickq-io-c5j` — Grid/likert report rendering: ordinal + matrix output
- `quickq-io-8ra` — OMOP feature: rename + reframe as "OMOP-aligned survey extracts"
- `quickq-io-u67` — Document fork and merge in public docs (currently buried capital)
- `quickq-io-tw8` — No-SQL analysis surface: saved-query templates
- `quickq-io-enz` — Synthetic seed: per-question distribution overrides
- `quickq-io-zso` — Surface `agg_respondent_scores` in public docs *(the feature exists; the auditor missed it, which means the surfacing is weak)*

**P4 (held for trigger):**
- `quickq-io-bbc` — Mobile / ResearchKit reference client
- `quickq-io-fvm` — Longitudinal / EMA support: scheduling, reminders, timeline analytics
- `quickq-io-wyo` — R package wrapper for OLAP queries

### Already tracked
- `quickq-io-uql` — Strategic: position quickq as a CDISC + FHIR + OMOP regulatory data tool (covers the CDISC-aligned-consortia recommendation)
- `quickq-io-bt9` — Concise repository acknowledgement / related-work surface (touches the Dryad/ICPSR repository-partnership recommendation)
- `quickq-io-4lc` — Multi-site tutorial rebuild (covers part of the federated worked-example recommendation)

### Deliberately not filed
- "Consolidate `data-dict`, `report`, `render`, `preview` under `quickq docs <kind>`" — interesting suggestion but the verb-soup framing is the auditor's outside view; internally these produce semantically different outputs (a dict, a Markdown summary, a rendered questionnaire, a live preview server) and conflating them would muddle the CLI for current users. Held without a ticket.
- Auditor's pure framing/marketing recommendations (e.g. "the README doesn't acknowledge REDCap exists") — absorbed into the landing-page benefit-first rewrite ticket rather than filed separately.

## Things to keep in mind from this audit on future work

A few cross-cutting observations that don't map to a single ticket but should shape ongoing decisions:

1. **A first-time visitor comparing tools is not the same persona as a researcher already invested in YAML + SQL.** The docs currently serve the second well and the first poorly. The recent landing-page reorder helped; further passes should keep this distinction in mind.

2. **The "credibility cracks" framing.** Small admissions in passing — "we haven't end-to-end-tested every REDCap variant," "grid renders as a flat list" — accumulate into a sense of "almost-shipped." Each one is small to fix; collectively they compound. Worth periodically scanning the public docs for "but" and "however" sentences and deciding which deserve a fix versus a more prominent caveat.

3. **REDCap is the unavoidable comparison.** quickq doesn't have to win against REDCap on every axis; it has to be clear about which axes it's choosing to win on. The audit's strongest framing — "frame quickq as a *destination* for REDCap projects, not a replacement" — is a sharp positioning move that the docs could lean into without changing any code.

4. **Score-as-data may or may not already exist.** Worth a quick verification pass against the OLAP schema and scoring tests before filing the corresponding ticket.
