# Audit Index

Chronological list of external audits run against quickq. Each entry links to the audit doc + summarizes what it surfaced and what it spawned in `bd`.

See [audit-workflow.md](../audit-workflow.md) for the framework these follow, and [personas.md](personas.md) for checked-in persona definitions.

---

## 2026-05-14 — Generalist (data-applications expert)

[Full audit](2026-05-14-generalist.md) · ~1800 words

**Lens.** Independent data-applications generalist; familiar with the modern data stack and the research-tools landscape (REDCap, KoBoToolbox, OpenEpi, ResearchKit). Asked to ignore internal design notes.

**Headline findings.**
- The data-model-as-deliverable framing is the strongest differentiation from REDCap/Qualtrics
- The landing page leads with mechanism rather than benefit; comparing researchers will bounce to REDCap
- OMOP integration is narrower than the docs imply
- `fork` / `merge` are buried capital — present in the CLI, absent from public docs
- "`quickq import-redcap` would do more for adoption than any new feature" — frame quickq as destination, not replacement

**Spawned 12 tickets.** P2: 3if (landing), 3g1 (import-redcap), 8p0 (deploy). P3: c5j, 8ra, u67, tw8, enz, zso. P4: bbc, fvm, wyo.

---

## 2026-05-14 — Lay epidemiologist persona (Maria Reyes)

[Full audit](2026-05-14-persona-lay-epi.md) · 1500 words in character + team debrief

**Lens.** Persona-constrained: Maria Reyes, MPH, mid-sized AMC, REDCap-fluent, SQL-naive. 30-minute first look. Tests the generalist's bounce-to-REDCap hypothesis directly.

**Headline findings.**
- Hypothesis confirmed: Maria bounced.
- SQL is the single biggest barrier for the target persona — not a feature
- YAML authoring is closer to a strength than a weakness (Maria, who's never edited YAML: "I can read that. I could write that.")
- Bundled-library composition (`{ library: phq9.1 }`) lands as a clear differentiator from REDCap
- The "When to use quickq" page is the strongest writing on the site and is buried three levels deep
- Prerequisites multiply silently (Python + uv + Node + npm + DuckDB CLI = wall)

**Spawned 2 new tickets** (`99l` minimum-prereqs onboarding, `dmk` deploy-today bridge) + comments on `3if`, `tw8`, `8p0` with specific persona findings.

**Regression run 2026-05-16** — re-ran Maria against the landing page after `quickq-io-3if` shipped (commit `fec2a69`). **Decision moved**: from "back to REDCap" to "going to try `quickq new` on a side test this week." 6 of 8 original complaints fully fixed; 1 partially addressed (`99l` still open); 1 reinforced (YAML positive moment now above the fold). New ask filed for a long-tail "study repo at 6 months" worked example. Full regression notes in the [audit doc](2026-05-14-persona-lay-epi.md#regression-2026-05-16). **First formal regression-audit exercise of the workflow.**

---

## Queued / planned

- `quickq-io-lxa` — Security / privacy / IRB compliance lens (P2; wait until `8p0` deploy story is scoped so the auditor has something concrete to probe)
- `quickq-io-4yc` — CDISC / regulatory submissions lens (P3; loosely linked to `uql` strategic positioning)
- `quickq-io-tyr` — Technical deep-dive / systems-engineering lens (P4; held until there's reason to scale)
