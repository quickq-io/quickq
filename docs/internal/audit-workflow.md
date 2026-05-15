# Audit Workflow

How we run, file, and act on independent audits of quickq. Lightweight on process; heavy on discipline about which findings become tickets vs which become notes.

The first two audits — see [audits/INDEX.md](audits/INDEX.md) — emerged organically without a framework. This document captures the pattern they revealed and the failure modes to avoid as we run more.

## Why we audit

We commission external audits as a proxy for early user feedback. quickq doesn't yet have enough real users to surface the framing, jargon, and friction problems that an outsider notices in 30 minutes. Until we do, audits fill the gap.

Two specific audit purposes:

1. **Comparative.** A generalist or domain expert reads the public docs and reports on merits, weaknesses, and recommendations. Tests the value proposition against external knowledge.
2. **Experiential.** A constrained persona walks through the docs as the target user would, in character. Tests whether the framing and onboarding actually work for the audience we hope to reach.

Both are valuable; they surface different problems. The persona audit on 2026-05-14 caught friction the generalist audit didn't, and vice versa.

## Structure

```
docs/internal/
  audit-workflow.md                    # this file
  audits/
    INDEX.md                           # chronological list + summary
    personas.md                        # checked-in persona definitions
    YYYY-MM-DD-<slug>.md               # one file per audit
```

Each audit doc follows the same shape:

1. **Header.** Date, persona/lens, position in the audit series.
2. **Context.** Why this audit was commissioned at this moment. What hypothesis (if any) it was meant to test.
3. **Prompt summary.** The brief the auditor was given. Not verbatim — the gist, with the persona block if applicable.
4. **The audit, verbatim.** What the auditor said. No editing for tone or selection; this is the durable record.
5. **How it translated into work.** Three sub-sections: tickets filed, comments added to existing tickets, things deliberately not filed (with reasons).
6. **Cross-cutting observations.** What this audit suggests about quickq beyond the individual recommendations — patterns worth carrying forward.

The "deliberately not filed" section is critical. Not every recommendation becomes a ticket. The goal is to be intentional about which findings to act on, not exhaustive.

## Lifecycle

```
Commission        →    Run            →    File           →    Close
- pick a lens          - brief the         - new tickets        - regression-test
- pick a moment          auditor             where warranted      against the same
- write the              (agent or         - comments on          persona/lens
  prompt                  human)             existing tickets     once fixes ship
                       - capture           - explicit "not
                          verbatim            filed" notes
                          response
```

The **regression-test** step is what makes audits a narrative rather than a series of snapshots. When a ticket filed in response to an audit closes, re-run the relevant audit (against the same persona, same prompt structure, against the updated docs) and either:

- Confirm the original finding is fixed → note in the audit doc and the closing ticket comment
- Discover the fix didn't land for the persona → reopen the ticket with what's still wrong

The regression check costs roughly 2 minutes of agent time. It's the single highest-leverage piece of the workflow.

## Cadence

Loose, not prescriptive. The right frequency is "when something has changed enough that a fresh outside read would surface new things, or when we've fixed enough of the last round's findings to deserve a regression check."

A few rough guidelines:

- **Don't audit everything every cycle.** Pick one lens per audit round. Running four audits at once dilutes attention and bloats the backlog.
- **Audit after material changes to public-facing surfaces.** A landing-page rewrite (e.g. `quickq-io-3if`) deserves a regression audit. A skip-logic engine refactor probably doesn't.
- **Audit when stuck on a strategic question.** "Are we positioned correctly?" is a question external audits answer better than internal debate. The 2026-05-14 generalist audit was commissioned exactly this way.
- **Don't audit for validation.** If you suspect a fourth audit will tell you something you already believe, skip it. Audits earn their cost by surprising you.

The persona library in [audits/personas.md](audits/personas.md) is the place where audit *intent* accumulates. When a new persona feels worth commissioning, add the sketch there first; the prompt writes itself once the persona is concrete.

## Anti-patterns

Three failure modes to watch for:

**Audit-as-busywork.** Every recommendation becomes a ticket → the backlog inflates → nobody reads the audits → they stop being commissioned. Counterweight: each audit doc has a "deliberately not filed" section. Be willing to use it. Some findings are notes-to-self, not work.

**Audit-as-validation.** Commissioning an audit when you already know what it will say. Counterweight: before running, write down what you expect the auditor to find. If you find yourself writing the audit *for* the auditor, skip it.

**Audit-without-regression.** Running fresh audits forever without ever re-testing whether the previous findings landed. Counterweight: every closed ticket that came from an audit gets a "regression-tested via 2026-05-14 audit re-run, persona-Maria" comment, or an honest "not regression-tested" if it wasn't. Visibility forces the discipline.

A fourth, milder concern: **audit-persona drift.** A persona that gets re-used across many audits starts behaving as the audit-runner expects, not as a real researcher would. Counterweight: update personas in [audits/personas.md](audits/personas.md) when real-user encounters teach us something new. When a real user shows up and disagrees with Maria, Maria changes.

## Filing principles

When deciding what to file from an audit:

- **High-impact framing problems → file as P2.** The audit's strongest findings are usually framing problems (vocabulary, ordering, missing context) — they're cheap to fix and high-leverage.
- **Buried capabilities → file as P3.** Features that work but aren't visible in the docs are real opportunities and well-bounded.
- **Aspirational extensions → file as P4 with "held for trigger."** Things that would broaden appeal but need a partner or concrete user. Don't build speculatively.
- **Framing meta-recommendations → don't file, fold into existing landing/docs tickets.** "Reframe X" is rarely a standalone ticket; it's usually a comment on the parent docs work.
- **Findings that contradict an active design decision → don't file; record in the audit doc's cross-cutting section.** If we're deliberately doing the thing the auditor flagged, the audit doc is where that reasoning lives. Don't bury it in a ticket comment.

## Audits to date

See [audits/INDEX.md](audits/INDEX.md) for the chronological list. As of 2026-05-14: two run (generalist, lay-epi persona), three queued (privacy/IRB, CDISC, technical deep-dive).
