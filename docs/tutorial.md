# The Study Journey

quickq tutorials follow several paths. Pick the one that matches what you want to learn first; come back for the others when you need them.

| Path | Time | Use when |
|---|---|---|
| [Quickstart, end-to-end](tutorials/end-to-end.md) | ~15 min | You want to see the full loop work. Author from scratch, serve, collect, refresh, report. Gout symptoms running example. |
| [Authoring deep-dive](tutorials/authoring.md) | ~30 min | You want to learn the YAML format properly. Builds GAD-7 step by step (option sets, skip logic, scoring rules, FHIR export). |
| [Analytics phase tutorials](#analytics-phase-tutorials) | varies | You want to explore the analytical layer with realistic data. Uses the bundled demo database (PHQ-9 + prenatal, 400 synthetic sessions, 4 300+ responses). |

---

## Analytics phase tutorials

These tutorials all query against the same demo database. Generate it once before working through them. The demo-builder is a script in the quickq source repo (it loads PHQ-9 and Prenatal Visit Log instruments, imports 250 PHQ-9 responses + 150 prenatal visit logs with realistic distributions, runs `quickq refresh`, and creates six analytical views):

```bash
# One-time: clone the source so you can run the demo builder
git clone https://github.com/quickq-io/quickq.git /tmp/quickq-src
cd /tmp/quickq-src
uv run python scripts/generate_demo.py
```

Output: `demo/study.db` (OLTP) and `demo/analytics.duckdb` (OLAP). Both can be copied wherever you want to run the tutorials from. The source clone is only needed for this one-time demo build; you don't need to keep it around.

!!! note "Future: built-in demo command"
    A future `quickq demo` command will produce the same output without needing to clone the source. Until then, the script-based path above is the cleanest way to get the demo data set up.

The tutorials work in order, but each is also independently readable.

### [1. Collect responses](tutorials/collect.md)

The FHIR handoff: export `Questionnaire.json`, hand it to any delivery tool, ingest the resulting `QuestionnaireResponse.json`. Generic; works against any quickq study, not just the demo.

### [2. Analyze](tutorials/analytics.md)

Run scoring queries, response distributions, and cross-instrument joins. The analytical model pre-computes PHQ-9 severity categories from the scoring rule, makes each loop instance a distinct row via `repeat_index`, and puts `admin_mode` on every session. Cross-instrument joins are a single SQL join on the shared `respondent_id`.

### [3. Data quality](tutorials/data-quality.md)

Check for unexpected sparsity, distinguish skip-logic non-responses from genuine missingness, audit concept mapping coverage before federated export, review import flags.

---

## Multi-site studies

Multi-site studies are a primary use case for quickq's `fork`, `merge`, and `federated query` commands: distribute a canonical instrument from a coordinating center, collect independently at each site, and either merge the resulting databases at the coordinating center or run aggregate-only queries against them in place. A polished end-to-end recipe with realistic synthetic data and worked examples is still in development. If you are running a multi-site study and want to discuss how to wire up these primitives for your specific design, please [open a GitHub issue](https://github.com/quickq-io/quickq/issues) — that conversation will inform the recipe we publish next.
