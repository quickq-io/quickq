# User-Level Abstractions

Design rationale for how user-facing surfaces (SDKs, recipes, CLI) sit on top of quickq's low-level foundation (SQL, OLTP, OLAP, FHIR). Captures decisions reached in conversation 2026-05-14 but not yet implemented — file as ADRs or break into beads tickets when execution is in scope.

The triggering observation: the [2026-05-14 persona audit](audits/2026-05-14-persona-lay-epi.md) showed that "analyze with SQL" is a barrier rather than a feature for the target persona. The conversation that followed worked out *how* to abstract over SQL without weakening the low-level foundation.

## The problem

Today's Python SDK is **implementer-grade**: a clean interface to the two databases, designed for someone who already writes SQL. The audit personas — working epidemiologists with Stata fluency, not SQL fluency — bounce at the first JOIN.

The temptation is to "fix the SDK" by adding convenience methods or hiding SQL behind ORM-style objects. Both lead to a heavier SDK that obscures the substrate without simplifying the user's job.

The right move is to introduce a **user-level layer** above the existing SDK — a small, deliberate vocabulary of operations researchers think in (frequencies, scores, missingness, completion) — leaving the low-level SDK and SQL substrate intact for power users.

## Layered architecture

```
Recipes              ad hoc, notebook-shaped, unbounded growth
  ↓                  (compose verbs into specific analyses)

Verbs                ~30 operations, bounded growth, stable contract
  ↓                  (frequencies, scores, missingness, completion, cross_tab, …)

OLAP schema          the analytical contract — star schema, stable across studies
  ↓                  (fact_response, dim_question, agg_respondent_scores, …)

OLTP schema          canonical truth, FHIR-aligned, refactor-protected
  ↓                  (question, response, session, scoring_rule, …)

YAML                 authoring source
```

Each layer:
- Has a stable contract with the layer immediately below
- Does not reach more than one layer down (no skip-jumps)
- Is sized appropriately: YAML and OLTP are large (correctness); OLAP is medium (analytical contract); verbs are small (~30); recipes are unbounded but compositional

**Verbs target the OLAP only.** Verbs do not reach into the OLTP. This keeps the OLAP as the user-facing analytical contract — refactoring the OLTP (column names, table splits, indexing) does not break user code. Changing the OLAP is rarer and requires coordinated migration.

**Recipes are the unbounded-growth absorber.** Every "can we add an analysis for X?" request goes here, not into the verb library. Recipes can be language-specific, user-contributed, and free-form because they don't add to the SDK contract.

## What makes an abstraction "user-level"

Five markers separating user-level from implementer-grade:

**1. Vocabulary is research-shaped, not schema-shaped.**
- ✅ `analytics.frequencies("phq9.1")`
- ❌ `analytics.group_by("question_id").count()`

The first uses the researcher's term. The second exposes the SQL pattern. They might produce identical output; only the first is user-level.

**2. Inputs match how the researcher thinks about the data.**
- ✅ `analytics.scores("PHQ-9")` — known by instrument name
- ❌ `analytics.scores(scoring_rule_id=3)` — primary keys are implementation detail

The SDK accepts human references and resolves them internally.

**3. Output shape is the shape they'd export.**
- ✅ Always a DataFrame; labeled columns; `.to_csv(labels=True)` / `.to_stata()` / `.to_sas()` available
- ❌ Connection cursors, iterators, unlabeled tuple lists

User-level code shouldn't have to know how to materialize results.

**4. Refusal is explicit, not implicit.**
- ✅ `analytics.frequencies("gout.notes")` raises `"frequencies not meaningful for free-text questions; use .text_summary() instead"`
- ❌ Returns an empty table

Implementer-grade APIs let users shoot themselves in the foot. User-level ones decline cleanly and point at the right verb.

**5. Errors are debuggable by the researcher, not the developer.**
- ✅ `"question 'phq9.1' has no responses; check that you've imported responses or refreshed the OLAP"`
- ❌ `KeyError: 'phq9.1'`

The error tells them what to do next.

## The verb pattern

A verb is a named operation that maps researcher intent (frequency table, score distribution, missingness summary) to a parameterized SQL query against the OLAP. Each verb is a three-file kit:

```
quickq/verbs/<verb-name>/
  <verb-name>.sql         # the SQL template
  <verb-name>.spec.yaml   # signature: params, types, defaults, output shape, valid inputs
  <verb-name>.test.yaml   # fixtures + expected outputs
```

Adding a verb means writing three files; both language SDKs then either auto-derive their bindings (preferred, if codegen tooling is built) or are mechanically updated to expose the verb (acceptable, if discipline holds).

### Why a spec, not just a function

The spec is a declaration of intent, language-independent. It says: "this verb takes these parameters with these types and returns this shape." Python and R SDKs derive their bindings from the spec — the signature is identical because the spec is identical.

Without a spec, Python and R drift. Python adds a parameter; R adds it months later under a different name; the CLI has the old shape. Spec-as-source-of-truth makes drift impossible by construction.

### One reference SDK; others mirror

**Python is the reference SDK.** New verbs ship in Python first; R mirrors only after the Python verb is stable. The R SDK should not contain verbs Python doesn't have. The cost of cross-language consistency is upfront; the value is permanent.

Stata and SAS are not getting SDKs. They get one-line exports (`df.to_stata()`, `df.to_sas7bdat()`) from the Python SDK. The export is forward-compatible and free; the SDK would be a maintenance commitment we don't want.

### The verb cap

**Soft cap of ~30 verbs.** Every new verb proposal must displace an existing verb, fall into the recipes layer instead, or document why the cap should grow. Without this constraint, the verb library inflates verb-by-verb into an ORM over two years.

Capping the verb library is a real constraint, not a guideline. The maintenance work of the SDK is mostly the work of saying no to verbs.

## Recipes as the growth absorber

Anything that doesn't fit the verb pattern goes into the recipes layer:

```
quickq/recipes/                   # or per-user notebooks, package contributions, etc.
  demographics_table.py
  longitudinal_score_trend.py
  flagged_response_review.py
  ...
```

A recipe is a Python (or R) function that composes verbs. Recipes:

- Can be ad hoc, language-specific, user-contributed
- Don't need cross-language mirroring
- Don't add to the SDK contract
- Are the right place for "show me the demographics table" or "make me a longitudinal plot"

The verb cap holds because the recipe layer absorbs all the growth pressure.

## Maintenance discipline

Three rules that prevent the user-level layer from rotting:

**1. Doc examples are tests.** Every verb's docs page includes at least one fully-worked example. That example runs in CI against the same fixture used in the verb's `test.yaml`. If the example breaks, CI fails. There's no path for docs and behavior to drift.

**2. Refusal is required.** Every verb declares what inputs it supports. Calling a verb on an unsupported input (e.g. `frequencies` on a text question) produces a structured error that names the constraint and points at the right alternative. Silent garbage is not acceptable.

**3. One reference SDK.** Python is canonical. R mirrors. Don't merge a verb into R that Python doesn't have. Don't merge a parameter into one SDK that the other doesn't have. The discipline cost is worth the consistency value.

## What this architecture deliberately does NOT do

A few patterns we're consciously rejecting:

**It doesn't try to be every analysis tool.** Researchers who outgrow the verb library write SQL directly against the OLAP. The OLAP schema is a contract; users who want power have it. This is a feature, not a failure mode.

**It doesn't reach into the OLTP from user-facing code.** The OLAP is the analytical surface for user-facing code. If a verb needs OLTP data, the right fix is to surface it in the OLAP via the refresh, not to give the verb a backdoor.

**It doesn't auto-generate verbs from the schema.** Every verb is a deliberate design decision about a researcher's question. Auto-generated verbs lead to names like `select_by_question_id_and_session_id_grouped_by_option_id`. The point of verbs is they sit at the researcher's vocabulary, not the schema's.

**It doesn't expose connections, transactions, or low-level objects in the verb layer.** Those are SDK-internal. Users get `analytics`, an object that returns DataFrames.

## Strategic effect

The verb SDK changes which audience adopts first.

Today's docs say "analyze with SQL." Maria bounces.

With verb SDKs, the docs say "analyze with `quickq.frequencies()` in Python or `qq_frequencies()` in R; write SQL directly against the OLAP if you want the full set." The SQL story moves from "barrier to entry" to "implementation detail." This single substitution moves the docs' target audience from SQL-fluent biostatisticians to working epidemiologists — without dropping the biostatisticians.

That repositioning has effects on every public-facing surface (landing page, walkthrough, reference). When the verb SDK ships, the landing page can finally lead with researcher outcomes (a portable study file, reproducible analyses, multi-site without DUAs) instead of stack components.

## Where this fits in the backlog

Existing tickets that this design rationale informs:

- **`quickq-io-tw8`** — No-SQL analysis surface. This document describes the architecture for that ticket. tw8's "saved-query templates" is the verb library plus the recipes layer.
- **`quickq-io-3if`** — Landing page benefit-first rewrite. The strategic-effect section above describes what becomes possible *after* the verb SDK ships — the rewrite should anticipate it.
- **`quickq-io-wyo`** — R package wrapper (P4, held). The R SDK described here is what wyo eventually becomes. The "one reference SDK" rule means R waits until Python verbs stabilize.

Not yet filed as tickets (this document is the durable record until execution is in scope):

- Python verb SDK design + initial 5–6 verb implementation
- The verb-spec + three-file-kit tooling (whether to auto-generate or hand-write language bindings)
- The recipe-layer convention and where contributed recipes live

When any of those become active work, file them as P2/P3 with a reference back to this document.

## Pragmatic starting point

If verb-SDK work moves from rationale to execution, sequence:

1. **Define 5–6 starter verbs** with their three-file kits: `frequencies`, `scores`, `missingness`, `completion`, `cross_tab`, `flags`. These cover ~80% of typical first-month analyses.
2. **Build the Python SDK as a thin pass-through** over those verbs. Today's existing Python SDK stays underneath; the new verb-shaped SDK sits on top.
3. **Re-run the Maria persona audit** against the new SDK with a docs example. If the analysis-step bounce point shifts (she actually gets a frequency table without writing SQL), expand to 10 verbs.
4. **Build the R mirror** only after the Python verb signatures stabilize. Don't start R until at least one Python verb has been in use long enough to confirm its shape.
5. **Don't touch recipes until the verb layer is stable.** Writing `demographics_table` before `frequencies` is the wrong order.

The risk to watch for through all of this: the boundary between user-level and implementer-grade is obvious in slides and gets blurry the moment someone needs an edge case. **Defending the boundary — especially against your own desire to "just add one parameter" — is the actual maintenance work.**
