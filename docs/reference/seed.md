# Synthetic Data: `quickq seed`

`quickq seed` generates plausible synthetic responses against a loaded questionnaire. It is the fastest way to populate the analytics layer for development, demos, scoring-rule validation, and tutorial walkthroughs without collecting real data.

```bash
quickq seed study.db <questionnaire_id> --n 50 --seed 42
```

The generator reads the questionnaire structure (question types, option sets, numeric ranges, skip rules) directly from the OLTP and produces responses that respect each. Output is one new `response_session` per call to the per-respondent loop, plus the corresponding `response` rows.

---

## Arguments

- `DB_PATH` (required): the OLTP `study.db` to write into.
- `QUESTIONNAIRE_ID` (required): the integer ID returned by `quickq load` (also visible in `quickq list surveys`).
- `--n N` (default: `50`): number of synthetic response sessions to generate.
- `--seed N`: random seed for reproducible output. Same seed plus same questionnaire produces identical responses across runs.
- `--study-id N`: associate the synthetic respondents with an existing study; defaults to the questionnaire's study.

After seeding, run `quickq refresh study.db analytics.duckdb` to materialize the OLAP layer with scores, distributions, and aggregates ready to query.

---

## What gets generated

| Layer | Behaviour |
|---|---|
| **Question types** | All 12 supported types (`single_choice`, `multiple_choice`, `sata_other`, `boolean`, `numeric`, `date`, `datetime`, `slider`, `likert`, `ranked`, `grid`, `text`, `repeating_group`) produce values shaped to their type and constraints. For repeating groups, seed honors `count_from`: if the group is linked to a numeric count question, the seeded answer to that question drives the number of instances. For free-add groups (no `count_from`) seed picks a small random N (0–5). |
| **Option sets** | Choice questions sample uniformly from the configured options. Exclusive options ("None of the above") are honoured: if selected, no other option is chosen alongside. |
| **Numeric ranges** | `numeric` and `slider` items sample uniformly from the `[min, max]` range when set; otherwise from a sensible default (`0–100`). |
| **Skip logic** | A question is answered only when its `show_when` condition evaluates true against the prior generated answers in the same session. Skipped questions produce no `response` row, exactly mirroring real data collection. |
| **Free text** | `text` items get one of a small library of plausible short strings; `sata_other` produces a short phrase only when "Other" was selected. |

The point is not realism (the distributions are uniform, not survey-realistic). The point is structural correctness: a query that works against seeded data works against real data.

---

## Reproducibility

The same `--seed` value produces identical synthetic responses across runs and across machines (Python's `random` module is deterministic; quickq does not introduce any non-deterministic shuffling). This makes seeded data suitable for:

- Regression tests that assert on aggregate counts or score distributions
- Tutorial walkthroughs where readers expect to see the same numbers as the prose
- CI builds that compare report output across changes

If `--seed` is omitted, output varies run to run.

---

## When to use seed vs. real data

| Use case | Recommended path |
|---|---|
| Build the analytical layer to learn quickq | `quickq seed` then `quickq refresh` |
| Validate scoring rules before collection begins | `quickq seed` to produce edge cases, then inspect `agg_respondent_scores` |
| Demo the analytics tutorials with realistic instrument structure | `scripts/generate_demo.py` (uses the FHIR import path; richer than seed) |
| Demonstrate the full FHIR round-trip | `quickq fhir export` followed by `quickq fhir import-response` against external responses |

For repeating-group instruments, `quickq seed` writes child sub-question rows with sequential `repeat_index` values per session. Per-instance skip logic is not evaluated (each instance gets the same set of children); for fully realistic per-instance behaviour, `quickq fhir import-response` against synthetic FHIR `QuestionnaireResponse` JSON gives finer control. The demo script (`scripts/generate_demo.py`) takes that route for the prenatal-visit-log example.

---

## Where seed fits in the pipeline

```
   YAML
     ↓ quickq load
study.db (instruments)
     ↓ quickq seed --n 50 --seed 42
study.db (synthetic responses)
     ↓ quickq refresh
analytics.duckdb (queryable OLAP)
     ↓ quickq report / quickq analytics
```

`quickq seed` writes to the same `response` and `response_session` tables that `quickq fhir import-response` writes to. Downstream commands cannot tell the difference between seeded and real responses; that is the design.

---

## See also

- [Question Types reference](question-types.md) for the per-type seed coverage status.
- [End-to-End Walkthrough](../tutorials/end-to-end.md) for `quickq seed` in context (Step 9).
- [The Study Journey](../tutorial.md) for how seed compares to the demo-script path used by the analytics tutorials.
