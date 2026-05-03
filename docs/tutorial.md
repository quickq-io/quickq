# The Study Journey

quickq tutorials follow several paths. Pick the one that matches what you want to learn first; come back for the others when you need them.

| Path | Time | Use when |
|---|---|---|
| [Quickstart, end-to-end](tutorials/end-to-end.md) | ~15 min | You want to see the full loop work. Author from scratch, serve, collect, refresh, report. Gout symptoms running example. |
| [Authoring deep-dive](tutorials/authoring.md) | ~30 min | You want to learn the YAML format properly. Builds GAD-7 step by step (option sets, skip logic, scoring rules, FHIR export). |
| [Analytics phase tutorials](#analytics-phase-tutorials) | varies | You want to explore the analytical layer with realistic data. Uses the bundled demo database (PHQ-9 + prenatal, 400 synthetic sessions, 4 300+ responses). |
| [Multi-site lifecycle](tutorials/multi-site.md) | ~45 min | You want to run a study across multiple sites. Independent collection, merging, pseudonymization, federated analysis. Three-site PHQ-9 scenario. |

---

## Analytics phase tutorials

These tutorials all query against the same demo database. Generate it once before working through them:

```bash
uv run python scripts/generate_demo.py
```

The script loads the PHQ-9 and Prenatal Visit Log instruments, imports 250 PHQ-9 responses and 150 prenatal visit logs with realistic distributions, runs `quickq refresh`, and creates six analytical views. Output: `demo/study.db` (OLTP) and `demo/analytics.duckdb` (OLAP).

The tutorials work in order, but each is also independently readable.

### [1. Collect responses](tutorials/collect.md)

The FHIR handoff: export `Questionnaire.json`, hand it to any delivery tool, ingest the resulting `QuestionnaireResponse.json`. Generic; works against any quickq study, not just the demo.

### [2. Analyze](tutorials/analytics.md)

Run scoring queries, response distributions, and cross-instrument joins. The analytical model pre-computes PHQ-9 severity categories from the scoring rule, makes each loop instance a distinct row via `repeat_index`, and puts `admin_mode` on every session. Cross-instrument joins are a single SQL join on the shared `respondent_id`.

### [3. Data quality](tutorials/data-quality.md)

Check for unexpected sparsity, distinguish skip-logic non-responses from genuine missingness, audit concept mapping coverage before federated export, review import flags.

### [4. Share & publish](tutorials/share.md)

Pseudonymize participant identifiers, refresh the OLAP, export to Parquet for warehouse ingestion or data repository deposit. `quickq compliance pseudonymize` replaces `external_id` values with stable HMAC tokens; the result is analytically complete and safe to share.

---

## Multi-site studies

For studies that collect independently at multiple sites and merge at a coordinating center, see [Multi-Site Study Operations](tutorials/multi-site.md). It builds its own three-site scenario from scratch (no shared demo database), and covers the full lifecycle: initializing site databases, recording mid-collection errata, merging, pseudonymizing, and running cross-site analyses.
