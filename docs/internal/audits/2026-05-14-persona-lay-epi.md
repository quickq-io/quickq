# External Audit — Lay Epidemiologist Persona — 2026-05-14

Second of two audits run on 2026-05-14. The first was a [data-applications generalist](2026-05-14-generalist.md) — sharp on architecture and competitive positioning. This second one tests something the generalist couldn't: **does the landing page and walkthrough work for the persona we actually want to adopt the tool?**

## Context

The generalist audit closed with a note that "a researcher comparing tools reads it and bounces to REDCap." That's a hypothesis. This audit is the test of it.

We commissioned a persona-constrained audit: an AI agent asked to stay strictly in character as **Dr. Maria Reyes, MPH, 6 years post-MPH, mid-sized AMC, runs n=50–500 observational studies in chronic disease epi**. She knows REDCap, Stata, basic R; she does *not* know SQL beyond having heard of it, has never edited a YAML file, has heard of FHIR only as a word and doesn't know what it means for her study, and hasn't installed Node or Docker. She has 30 minutes.

The deliverable was a 1000–1500 word first-person reaction (in Maria's voice) plus a separate "FOR THE QUICKQ TEAM" debrief from the audit-runner.

## Prompt summary

The agent was given:
- Maria's full persona (what she knows, what she doesn't, what she cares about, current study context)
- Specific instructions to stay in character through the reaction, drop persona only for the team note
- Constraint: don't be artificially negative; flag positives only when the persona would have actually understood them
- A specific narrative: a colleague at lunch suggested quickq; 30-minute window between meetings; decide whether to spend an afternoon evaluating
- Instruction to skip `CLAUDE.md` and `docs/internal/` (not user-visible)

## The audit, verbatim

### Maria's reaction (in character)

> I'm Maria. Thirty-minute window, coffee in hand. A colleague at lunch said "quickq" might be a better fit for the next observational study than REDCap. I'm skeptical because the last time someone said that to me it was about a tool that no longer exists, and I had to re-key 400 records.

#### Landing page

> The headline says "survey authoring and analytics toolkit for health and epidemiology research." OK, good, at least it's pitched at me and not at software engineers. The first sentence under the headline says I author instruments in YAML, deliver over FHIR, collect through a bundled web form, and analyze with SQL.
>
> That's already four things I have a reaction to:
>
> - **YAML.** I know it's a file format with indentation. I've never written one. The PHQ-9 example further down the page actually does look readable. Colons, dashes, indents. If I had to add a question, I think I could copy-paste an existing one and edit it. Cautious thumbs up.
> - **FHIR.** I have heard this word. I think it's a healthcare data standard? Something Epic uses? I don't know what it does for me as a researcher. The page keeps saying "FHIR-compatible," "FHIR R4," "FHIR Questionnaire," and at no point does it tell me, in one sentence, *what FHIR means for my study*. Why do I care that it's FHIR-compatible? Will my IRB care? Will my biostatistician care?
> - **Bundled web form.** Good. I need something my participants can fill out.
> - **SQL.** Stop. I don't write SQL. Or rather, I have written maybe fifteen SELECT statements in my life and I had to look up syntax for every one. The whole page seems to take it for granted that "analyze with SQL" is a feature. For me it is a barrier. My analyses are in Stata. Are they telling me I have to learn SQL to use this tool?
>
> Then there's the bulleted feature list. "Bundled instrument library" — PHQ-9, GAD-7, AUDIT-10, PRAPARE, PROMIS-10. I know all of those. That's a real point in their favor. "FHIR-compliant delivery" — same word again, still not landing. "Data dictionary that can't drift" — this one I do react positively to. Anyone who's run a REDCap study knows what it's like when the data dictionary and the data are out of sync. If this is real, that's meaningful.
>
> "Skip logic and QC as queries." Skip logic I know cold from REDCap. QC I know from doing it by hand in Stata. "As queries" presumably means SQL queries again. The phrase "structurally-missing-vs-truly-missing distinction" actually lights up for me — that's something I always have to explain to grad students and it's something I always end up handling badly in REDCap exports. So if they've thought about that, they understand my world. But I'd want to see the actual example, not the bullet point.
>
> "Versioning and provenance" — questions are immutable once used, rewording produces a new versioned definition. I'd have to think about whether that helps me or makes my life worse. It sounds careful, in a way I associate with regulated trials. I run observational studies, not RCTs, but reviewers do ask about wording changes.
>
> "Federated analytics" and "concept harmonization." Concept codes I sort of get — LOINC and SNOMED ring a bell from chart abstraction work. Federated analytics I've never done. We're a single-site lab. I read past this.
>
> "Compliance scaffolding" — FAIR, GDPR-style erasure, IRB-style withdrawal. The FAIR phrasing tells me this was designed by someone aware of open-science conventions, which I appreciate. The GDPR thing I'd flag for our IRB.

> Now the Quick Start block:
>
> ```
> uv tool install git+https://github.com/quickq-io/quickq.git \
>     --with git+https://github.com/quickq-io/quickq-forms.git
> ```
>
> What is `uv`? It's not explained. Down in the walkthrough they link to an installation guide, which is good, but on the landing page it's just there as a word I don't know. I would also note that this command, with the backslash continuation and the `--with` flag and the git URLs, is more complex than the `pip install thing` I'm used to. The page does say "if you prefer plain pip" — that's reassuring, I would absolutely prefer plain pip.
>
> Below the Quick Start there's a mermaid diagram with boxes labeled OLTP, OLAP, FHIR, SQLite, DuckDB, Parquet. I count five terms in one diagram I'd have to look up.
>
> - OLTP / OLAP — I can sort of intuit from context that one is "where data goes in" and one is "where you analyze," but I've never used either word in a sentence.
> - SQLite — I've heard of it. It's a database, right?
> - DuckDB — never heard of it.
> - Parquet — never heard of it.
>
> For me as a working researcher, this diagram is decorative. I'd skim past it.
>
> The "Why this stays simple" section is well-meaning but it lost me. "The data dictionary is a query, not a Word document" — fine, but I don't know what that means in practice for me. Do I run a command and get something I can hand to a coordinator? "Every question type stores answers in `fact_response` the same way" — this matters to someone who's going to write SQL. It doesn't matter to me.
>
> The "What quickq is not" section saved them. "A replacement for managed survey platforms. REDCap and Qualtrics solve overlapping problems with built-in respondent management, role-based access, and institutional support." That's honest, and it's the first place I felt like the doc was talking to me directly. I respect a tool that knows its limits.

#### End-to-end walkthrough

> I clicked through. Prerequisites: Python 3.11+, uv, Node.js 18+ with npm. Python I have. uv I don't have, but they link to an install guide. Node.js with npm — I've never installed Node. I'd have to ask my postdoc or grit my teeth and follow the link.
>
> Step 1 is one shell command and a verify. OK.
>
> Step 2 scaffolds a study directory. They mention `.gitignore` and `git init` as if everyone uses git from the command line. I use GitHub Desktop. I assume the directory still works fine without git, and the `--no-git` flag suggests yes.
>
> Step 3 is the meat: authoring an instrument in YAML, in four stages. **This is actually where the tool earned the most goodwill from me.** The YAML for a date question and a numeric question with a 0–10 range is short and obvious. The option set example — define a frequency scale once, use it for both alcohol and red meat — is something I'd want in REDCap and don't have. When I add a sixth option I'd have to edit two fields in REDCap. Here it's one place.
>
> Stage 3 introduces skip logic with `show_when`. The YAML is plain English:
>
> ```yaml
> show_when:
>   question: gout.last_attack
>   operator: exists
> ```
>
> I can read that. I could write that. Compared with REDCap's branching logic syntax, which I have to test by saving the form and viewing it, this is friendlier.
>
> Stage 4 pulls library questions. `{ library: phq9.1 }` to drop in PHQ-9 item 1 with its LOINC code and answer options. That's genuinely nice. In REDCap I rebuild PHQ-9 from a Word document every time, or import a template someone in another lab made.
>
> Steps 4 through 7: export FHIR, start the form server, take the survey. The form-server step is `quickq serve study.db` and a localhost URL. Realistic for my laptop. But this is the question I'd have for real deployment: **how do my actual respondents fill this out?** A localhost URL on my Mac doesn't help me. The page mentions a hosted adapter is future work. I'd need to know what to do today.
>
> Step 8 seeds 50 fake responses. Fine for a demo. I get the appeal of being able to see distributions before real data arrives.
>
> Steps 9–11 are where I lost cabin pressure. Step 9 builds the analytics layer with `quickq refresh study.db analytics.duckdb`. OK, I ran a command. Step 11 says open the analytics database in the local DuckDB UI, which requires installing the DuckDB CLI separately via `brew install duckdb`. That's a second install, of a thing I've never heard of, just to look at my data.
>
> Then it shows three SQL queries. The first is a GROUP BY with two joins. The second has a subquery and a join on session_id and computes AVG over `response_numeric`. The third uses `BOOL_OR` and a subquery I'd have to reason about for a while.
>
> These are not absurdly hard queries. A biostatistician would read them in ten seconds. But me, on a Tuesday afternoon, between meetings? I would copy them, run them, get a result, and have no confidence that I understood why. I could not modify them to ask a different question without a substantial learning investment.
>
> In REDCap, I click "Export," I get a Stata `.do` file with labels, I open it in Stata and `tabulate`. I don't write joins. So the "analyze with SQL" pitch is, for me, a genuine cost rather than a feature.

#### When to use quickq / reference pages

> I scrolled down to "When to use quickq." This was the most useful page on the site. Two paragraphs that actually distinguished it from REDCap clearly:
>
> - REDCap and Qualtrics: polished, integrated, built-in respondent management, role-based access, e-consent, institutional support.
> - quickq: long-term programs where the instrument is version-controlled alongside analysis code, multi-site harmonization, federated analysis, handoff to institutions with their own analytics infrastructure.
>
> That is honest. By that framing, quickq is *not* obviously the right tool for me. I'm single-site, n=200, observational, IRB-approved REDCap project. The tool's own page says I might be a REDCap user.
>
> The "ad-hoc data model" table — wide tables, drifting Excel dictionaries, skip-logic invisibility — is well-argued, but the comparison is to a CSV-and-Excel workflow, not to REDCap. That's a strawman from my position. REDCap doesn't give me a great data dictionary either, but it's not Excel-and-CSV.

#### Verdict

> Honestly? Not for this study. The reasons:
>
> 1. My respondents fill out forms on phones, not my laptop. The story for actually deploying this on the open internet, with authentication, with HIPAA-compliant hosting, is not yet here.
> 2. The analytics layer requires SQL fluency I don't have. I'd need my postdoc to write every query. Or I could just use Stata.
> 3. The install path uses three tools I don't have set up (uv, Node, DuckDB CLI).
> 4. The genuine wins — bundled instrument library, YAML-as-source-of-truth, skip logic that's readable, data dictionary that doesn't drift — are real and would make me consider it for my *next* study if I can pair with someone who already knows the stack.
>
> I'm going to forward this to my postdoc and ask her to spend half a day with the walkthrough. If she comes back saying it's tractable, I'll revisit before the next IRB cycle. **For this study, REDCap.**

### Audit-runner debrief (out of character)

> 1. **The landing page is written for a reader who already knows the modern data stack.** OLTP / OLAP / DuckDB / Parquet / FHIR / SQLite / YAML all appear in the first screen, and most of them are not glossed. The persona who could most benefit from quickq (a working epi researcher fed up with REDCap) is also the persona most likely to bounce off the vocabulary. Consider a "for researchers coming from REDCap" landing variant, or a glossary tooltip on first use of each term. At minimum, FHIR deserves a one-sentence "what this means for your study" framing.
>
> 2. **The analysis story leads with SQL, and SQL is the single biggest barrier for the target user.** Researchers who can already write `BOOL_OR` and CTE subqueries are mostly already using OMOP / dbt / a warehouse. The Maria persona — who is the realistic adopter — analyzes in Stata or R. The walkthrough should show, near the top of Step 10, a recipe for exporting clean labeled data to Stata or R (or at least CSV with labels) before showing SQL.
>
> 3. **The "When to use quickq" section is the strongest piece of writing on the site and is buried.** It honestly distinguishes quickq from REDCap and accurately scopes the user. It belongs on the landing page, not three levels in.
>
> 4. **The deployment story for real respondents is the missing middle of the docs.** The walkthrough goes laptop → localhost → done. A real researcher's next question is "how do my 200 participants fill this out from their phones, with my IRB's approval?" The hosted adapter is mentioned as future work; in the meantime, document the file adapter + a static-site deployment pattern, or explicitly say "for real respondent collection today, export FHIR to REDCap/Qualtrics and import responses back" with a worked example.
>
> 5. **Prerequisites multiply silently.** The walkthrough requires Python, uv, Node, npm, and (for analytics) the DuckDB CLI separately. Each is well-justified internally; cumulatively they're a wall for the Maria persona. Either a one-shot installer script, or a clear "minimum to try authoring and previewing" path that doesn't require Node or DuckDB, would let researchers get a first win in twenty minutes without a sysadmin.

## How this audit translated into work

### Already covered (with refinements added to existing tickets)

- **`quickq-io-3if` (landing page rewrite)** — comment added with Maria's specific findings: jargon glossing, FHIR translation, "When to use quickq" elevation, REDCap-comparison framing.
- **`quickq-io-tw8` (no-SQL analysis surface)** — comment added: priority should be a **Stata/R/CSV-with-labels export from the walkthrough**, BEFORE introducing SQL queries. The audit's clearest finding: SQL leads, persona bounces.
- **`quickq-io-8p0` (deploy story)** — comment added with the persona's specific "what do I do TODAY for real respondents" question. Reinforces that the deploy story must answer "20-respondent phone-based collection from a regular IRB-approved single-site study" not just "Docker behind nginx."

### Filed in response to this audit

- **NEW** — Minimum-prereqs onboarding path: try authoring and preview without Node or DuckDB CLI
- **NEW** — Documentation bridge: "deploying real respondent collection today" (between localhost demo and the future hosted adapter)

### Cross-cutting observations from this audit

1. **The generalist's hypothesis was confirmed.** "A researcher comparing tools reads it and bounces to REDCap." Maria did.

2. **YAML authoring was the strongest positive.** Maria, who has never edited YAML, said "I can read that. I could write that." This is the persona's "I can use this" moment — earlier in the walkthrough than expected, and not where the docs lead. The team's instinct that YAML is a barrier to adoption appears to be wrong; **YAML is closer to a strength than a weakness for this persona.** Lead with it.

3. **The bundled instrument library was the second-strongest positive.** `{ library: phq9.1 }` to drop in a validated scale with concept codes — Maria said this is "genuinely nice" and recognized the immediate value. This should be more prominent in the docs; right now it appears at Stage 4 of the walkthrough.

4. **The "FHIR" word is doing no work for this audience.** Maria mentioned it five times and never figured out what it does for her. It's a powerful technical decision; it needs translation. "FHIR-compliant delivery" doesn't sell anything to someone who doesn't know FHIR. Reframe: "Works with any standards-compliant form tool you might switch to later — no lock-in." Then the word "FHIR" can appear as the technical underpinning.

5. **The "When to use quickq" page is the most credible writing on the site.** Maria specifically said she respects the tool for knowing its limits. This is a docs strength to lean into — there should be more pages that honestly bound the tool's scope, not fewer. The honest framing converts skeptical readers; the marketing framing doesn't.

6. **Persona-test the next landing-page rewrite before publishing.** Once the 3if rewrite happens, run the Maria agent against the new page (or a real Maria-like researcher) before declaring it done. The persona test took 2 minutes and surfaced bigger framing problems than the generalist audit.
