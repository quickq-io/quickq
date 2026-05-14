# Third-Party FHIR Renderers

quickq's default delivery tool is `quickq-forms` — bundled when you install quickq, used by `quickq serve` and `quickq preview`. But the contract between quickq and any delivery tool is **FHIR R4**, not a quickq-specific API. Any tool that accepts a FHIR `Questionnaire` and produces a FHIR `QuestionnaireResponse` works without modification.

This page covers when you'd use a third-party tool, how to preview your instrument in one, and what quickq tests to keep that contract honest.

---

## When to use a third-party renderer

Three common cases:

1. **You already use REDCap / Qualtrics / a clinical EHR portal.** Your IRB, your respondents, or your institution mandate a specific tool. Hand them the FHIR Questionnaire JSON; collect the responses; import them via `quickq fhir import-response`.
2. **You need a custom mobile app** for in-field data collection. Build it against the FHIR R4 spec; quickq doesn't care what's in front.
3. **You want to demonstrate interop** to a regulator, a collaborator, or yourself — "this study's data model is portable, not locked to one tool." Render the FHIR export through NLM's reference renderer to make the contract concrete.

If none of these apply, stick with `quickq-forms`. It's the renderer with the best fidelity to what quickq actually emits (correctly honors the slider, ranked-with-`ordinalValue`, grid, and skip-logic extensions; see the renderer-coverage audit for the empirical comparison).

---

## Preview through LHC-Forms

[LHC-Forms](https://lhncbc.nlm.nih.gov/LHC-forms/) is the reference FHIR Questionnaire renderer maintained by the U.S. National Library of Medicine. It's the closest thing to a "reference implementation" of the FHIR Questionnaire spec.

To preview your instrument through it:

```bash
quickq preview study.db 1 --renderer=lhc-forms
```

This opens a localhost server that bundles LHC-Forms (cached at `~/.cache/quickq/lhcforms/` after first download) and renders your exported FHIR Questionnaire. Inputs are interactive but no responses are saved — it's purely a visual / interop check.

What you'll see:

- **Simple types** (single_choice, multiple_choice, boolean, text, numeric, date, datetime, likert) render correctly. The FHIR contract is honored end-to-end.
- **Sliders** fall back to a plain text input. LHC-Forms doesn't currently honor the `itemControl=slider` extension or the min/max metadata. The question is still answerable but you lose the affordance.
- **Ranked questions** render as a single-select combobox. LHC-Forms doesn't currently honor the `ordinalValue` extension or surface a multi-position ordering UI. Respondents can pick one option but cannot rank them.
- **Grids** render correctly as horizontal tables.
- **Skip logic** (`enableWhen`, `enableBehavior=all|any`) works.

These limitations are LHC-Forms gaps, not quickq bugs — the FHIR export is correct, LHC-Forms just doesn't render every detail. If you need real slider/ranked affordances, use `quickq-forms` (or your custom renderer).

---

## Static HTML export

To produce a single-file HTML page that renders your instrument anywhere with a browser — useful for emailing a preview to a collaborator who doesn't have quickq installed:

```bash
quickq preview study.db 1 --output instrument-preview.html
```

The file uses LHC-Forms loaded from the NLM CDN. No server required to view; just open the HTML.

---

## REDCap

REDCap supports importing FHIR Questionnaires. Workflow:

```bash
quickq fhir export study.db 1 --output instrument.json
# in REDCap: Project Setup → Import a FHIR Questionnaire → upload instrument.json
```

When responses come back from REDCap (REDCap exports FHIR `QuestionnaireResponse` JSON):

```bash
quickq fhir import-response responses.json study.db
```

We haven't end-to-end-tested every REDCap variant. If you hit a parser warning, check the `data_quality_flag` table — quickq writes warnings rather than throwing, so partial imports are recoverable.

---

## Custom mobile / web clients

If you're building a custom renderer (React Native, Flutter, a clinical portal frontend), the contract is:

| Direction | What you send / receive |
|---|---|
| **In** | FHIR R4 `Questionnaire` JSON from `quickq fhir export` |
| **Out** | FHIR R4 `QuestionnaireResponse` JSON to `quickq fhir import-response` |

quickq's `quickq-forms` is itself a reference implementation of this — it's open source and you can fork it, study its serializer for the exact shape of nested items, repeating groups, grids, and ranked answers, or just import the `quickq_forms.engine.fhir_serializer` module to skip writing your own.

The key invariants:

- Repeating groups: emit **N separate top-level items with the same `linkId`**, one per instance. Children inside each parent item are scoped to that instance.
- Grids: emit a parent item with the grid's `linkId` and a nested `item[]` where each child is a row's response (linkIds suffixed `.r0`, `.r1`, …).
- Ranked: emit `valueCoding` answers in rank order, each with an `extension` of type `http://hl7.org/fhir/StructureDefinition/ordinalValue` carrying the rank as `valueDecimal`.
- Skip-logic exclusion: do not include answers for disabled items in the output. quickq's importer treats their absence as "structurally missing" (the question was hidden), not "truly missing" (the respondent skipped a visible question).

---

## How quickq keeps the interop story honest

Two test suites guard the FHIR contract:

- `quickq-forms/tests/e2e/test_render_round_trip.py` (28+ tests) — the **product correctness** suite. Verifies quickq-forms renders every question type and round-trips through `import_fhir_response` back into the OLTP. Runs on every commit.
- `quickq/tests/test_e2e_lhcforms.py` (35 tests) — the **interop canary**. Verifies the FHIR JSON exported by quickq renders correctly in LHC-Forms. Runs before any FHIR-export change ships and nightly in CI. Selectable as `pytest -m interop`.

Both suites use the same fixture set. A passing quickq-forms test with a failing LHC-Forms test on the same fixture is the signal "our FHIR shape is wrong but our renderer hides it" — which has caught real bugs (most recently a 2026-05-12 fix where `enableWhen` answer types were always emitted as `answerString` regardless of the trigger's actual type).

For the full status grid of which features are verified in which renderer, see [renderer-coverage.md](../internal/renderer-coverage.md) (internal).
