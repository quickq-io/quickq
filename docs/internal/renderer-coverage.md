# Renderer Coverage Audit

quickq exports FHIR Questionnaire JSON; downstream delivery tools (LHC-Forms, quickq-forms, REDCap, custom mobile clients) render that JSON. This page catalogs which features each tested renderer actually supports.

The audit answers the question "is our data-model-as-contract thesis honored end to end?" — a feature is only meaningfully supported if the data model can express it *and* a delivery tool renders it correctly *and* the resulting QuestionnaireResponse round-trips cleanly back through `quickq fhir import-response`.

## How to read this page

Each cell carries one of four statuses:

| Status | Meaning |
|---|---|
| **✅ Tested** | Automated test verifies the feature in this renderer |
| **🟡 Claimed** | FHIR-spec-compliant; renderer claims general support; not directly tested |
| **⚪ Unknown** | Not yet verified; treat with appropriate caution |
| **❌ Broken** | Verified to fail or render incorrectly; either a documented quickq-side bug or a known renderer gap |

The test-path column points to the Playwright test (or other automation) that backs a ✅. When a `⚪` becomes a ✅, update the cell and add a row to the change log at the bottom.

## Question types

| Type | LHC-Forms | quickq-forms | Test path | Notes |
|---|---|---|---|---|
| `single_choice` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_frequency_options_present` | PHQ-9 frequency options |
| `multiple_choice` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_question_labels_render` | gout `attack_joints`, `family_gout` |
| `sata_other` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_prapare_sata_other_renders_a_combobox` | LHC-Forms renders sata_other (FHIR `open-choice`) as a multi-select combobox; options visible after dropdown interaction |
| `boolean` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_question_labels_render` | gout `on_ult` |
| `text` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_text_area_present` | gout `notes` |
| `numeric` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_question_labels_render` | gout `attacks_12mo`, `uric_acid` |
| `date` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_date_input_present` | gout `last_attack_date`, `uric_acid_date` |
| `datetime` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_date_input_present` | gout `last_attack_datetime` |
| `likert` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_audit_likert_*` | All 10 AUDIT items are explicitly `type: likert`. LHC-Forms renders them as comboboxes with ordered options (Never / Monthly or less / 2-4 times a month / ...) |
| `grid` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_grid_in_repeating_renders_as_horizontal_table` | Spike `quickq-io-9u0` verified `.lhc-form-horizontal-table` rendering; coverage is *grid as child of a repeating group*, not standalone — but the spec-level pattern is identical |
| `ranked` | 🟡 | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_question_labels_render` | **Partial support in LHC-Forms.** Renders as a single-select combobox with placeholder "Select one" — no drag-to-rank, no multi-position ordering. A respondent can pick *one* of the ranked options but cannot order them. The `ordinalValue` extension we emit is silently ignored. Empirical finding from `quickq-io-r4m` audit. |
| `slider` | 🟡 | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_slider_question_renders_an_input` | **Partial support in LHC-Forms.** Renders as a plain text input with placeholder "Type a number" — the min/max metadata from the FHIR export is silently ignored. The question doesn't disappear; the slider *affordance* does. Empirical finding from `quickq-io-r4m` audit. |
| `repeating_group` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_grid_in_repeating_first_instance_renders_week` | Verified with the grid-child case; basic flat-child case (prenatal_visits) not yet E2E-tested |

## FHIR extensions emitted by quickq

LHC-Forms' behavior toward each of these extensions follows from the
question-type rows above: where quickq emits an extension to enrich rendering
(slider min/max, ordinalValue for ranked, itemControl), LHC-Forms appears to
silently ignore the extension and fall back to a generic control. The
quickq side of the round-trip (emit on export, parse on import) is verified
through unit tests; the renderer side is the limitation.

| Extension | LHC-Forms | quickq-forms | Notes |
|---|---|---|---|
| `questionnaire-maxOccurs` (count_qq_id linkage) | 🟡 (ignored) | ⚪ | quickq emits this for repeating groups with `count_from`. LHC-Forms doesn't appear to use it to constrain the Add button (untested at the limit case); the rendering of repeating groups itself works regardless. |
| `ordinalValue` (ranked items) | 🟡 (ignored) | ⚪ | quickq emits and parses correctly. LHC-Forms renders ranked as a single-select combobox; the extension is not surfaced as an ordering UI. See `ranked` row above. |
| `questionnaire-sliderStepValue` / min / max | 🟡 (ignored) | ⚪ | LHC-Forms renders slider as a plain text input; min/max metadata is silently ignored. See `slider` row above. |
| `questionnaire-itemControl` (slider hint) | 🟡 (ignored) | ⚪ | Same as above — LHC-Forms falls back to text input for sliders. |

## Composite shapes

| Shape | LHC-Forms | quickq-forms | Notes |
|---|---|---|---|
| `repeating_group` with simple-type children | ✅ | ⚪ | `test_e2e_lhcforms.py::test_prenatal_basic_repeating_group_first_instance_renders` + `test_prenatal_repeating_group_add_control_present` — LHC-Forms renders the first instance with all flat children plus an Add control |
| `repeating_group` with a `grid` child | ✅ | ⚪ | bv8 + 9u0 spike verified end-to-end on LHC-Forms; tests in `test_e2e_lhcforms.py` |
| `grid` (standalone, not inside a repeating group) | 🟡 | ⚪ | The horizontal-table rendering pattern is the same as the verified grid-in-repeating case; standalone case is implied to work but not directly tested |
| Multi-level `enableWhen` (multiple rules per item, `enable_behavior=any`) | ✅ | ⚪ | PHQ-9 `difficulty` exercises 3 rules with `enable_behavior=any`, verified in `test_difficulty_appears_after_nonzero_answer` |
| Multi-level `enableWhen` with `enable_behavior=all` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_enable_behavior_all_*` | Uses dedicated fixture `enable_behavior_all.yaml` (two boolean triggers AND-gating a follow-up). **Surfaced a real bug in `quickq/renderer_fhir.py`**: enableWhen answer type was always `answerString` for non-choice triggers, but FHIR requires the type to match the trigger. LHC-Forms (correctly) refused to match. Fix in same commit: dispatch on trigger type for `answerBoolean` / `answerDate` / `answerDateTime` / `answerDecimal`. |
| `operator: in` (post-expansion to N flat `=` rules) | 🟡 | ⚪ | Lands as N standard FHIR `enableWhen` entries with `enable_behavior=any`; should render in any FHIR-compliant renderer but not directly tested |

## Skip-logic features (post-ap8)

These are quickq-side schema choices; some are visible to renderers, some are not.

| Feature | Visible to renderer? | Status |
|---|---|---|
| `operator: in` shorthand | Indirectly (expanded to N FHIR rules pre-export) | See "Composite shapes" row above |
| `on_missing` default | **No** — analytics-only; the FHIR export omits it because there's no native enableWhen equivalent | N/A for this page |
| Nested boolean composition (`display_condition` FHIRPath) | **No** — quickq does not evaluate `display_condition` today; field is currently a documentation surface | N/A |
| Cross-attribute references | N/A — feature deferred; workaround is "demographics as questions" | N/A |
| Cross-instrument references | N/A — feature deferred | N/A |

## End-to-end submission flow

Beyond rendering, does the renderer correctly produce a FHIR `QuestionnaireResponse` that round-trips through `quickq fhir import-response`? This is the load-bearing test for the "data model is the contract" claim.

| Path | LHC-Forms | quickq-forms | Test path |
|---|---|---|---|
| PHQ-9 fill + submit → import | ✅ | 🟡 (server-tested) | `test_e2e_lhcforms.py::test_phq9_submission_round_trips_through_import_fhir_response` — fills three items via Playwright, extracts the QuestionnaireResponse via `LForms.Util.getFormFHIRData`, imports through `quickq.parser_fhir_response.import_fhir_response`, asserts the right `option_value`s landed with zero data_quality_flag rows |
| gout_checkin fill (5 types) + submit → import | ✅ | 🟡 (server-tested) | `test_e2e_lhcforms.py::test_gout_checkin_submission_round_trips_through_import_fhir_response` — fills boolean, numeric, date, multiple_choice, text; asserts each lands in the correct typed column with zero data_quality_flag rows |
| Repeating-group fill → import (basic) | ✅ | ⚪ | `test_e2e_lhcforms.py::test_prenatal_visits_repeating_group_round_trips_through_import` — fills two visit instances in the rendered form (week / provider / concern × 2), clicks "+ Visit details" to add the second instance, asserts each child lands with the correct `repeat_index` (0 for instance 1, 1 for instance 2) |
| Grid-in-repeating fill → import | ✅ | ⚪ | `test_e2e_lhcforms.py::test_grid_in_repeating_round_trip_closes_bv8` — fills two visit instances with a 3-row severity grid each (6 grid cells total), submits, imports, and asserts every grid cell lands with the correct `(grid_row_id, grid_column_id, repeat_index)` triple. Closes the third leg of the bv8 verification trifecta. |

## quickq-forms coverage

Tracked separately as **`quickq-io-<spinout>`**. The LHC-Forms column of this page reflects the renderer most users will deploy; quickq-forms is a distinct renderer (a React frontend served by our own FastAPI shim) that needs its own Playwright harness against the running dev server. Until that spinout lands, the `⚪` cells in the quickq-forms column should be read as un-verified, not as broken.

When quickq-forms gains its own E2E Playwright suite, those cells get filled cell-by-cell using the same pattern proved out here for LHC-Forms (load fixture, render, structural checks, then submission round-trip via the frontend's serialization equivalent of `LForms.Util.getFormFHIRData`).

## Change log

| Date | Update | Reference |
|---|---|---|
| 2026-05-12 | Initial audit; populated empirical cells from existing tests | `quickq-io-r4m` |
| 2026-05-12 | `grid` and `repeating_group` cells marked ✅ for LHC-Forms | `quickq-io-9u0` spike + commit `b37cb09` |
| 2026-05-12 | gout_checkin sweep added: `multiple_choice` / `boolean` / `text` / `numeric` / `date` / `datetime` → ✅ for LHC-Forms; `slider` and `ranked` → 🟡 with notes on partial support. Stale FHIR fixtures regenerated (8 files). | `quickq-io-r4m` |
| 2026-05-12 | `sata_other` ✅ via PRAPARE (renders as multi-select combobox in LHC-Forms). `repeating_group` (simple children) ✅ via prenatal_visits (first instance renders + Add control). LHC-Forms covers 11 of 12 question types now; only `likert` remains 🟡 (covered implicitly via PHQ-9 ordinal-choice but no distinct test). | `quickq-io-r4m` |
| 2026-05-12 | `likert` ✅ via AUDIT (10 dedicated likert items). `enable_behavior=all` ✅ via new `enable_behavior_all.yaml` fixture. **Surfaced FHIR export bug in `renderer_fhir.py`**: `enableWhen` answer was always emitted as `answerString` for non-choice triggers, but FHIR requires the type to match the trigger's data type. LHC-Forms correctly refused to match — gated questions never appeared. Fixed by dispatching on trigger type (`answerBoolean` / `answerDate` / `answerDateTime` / `answerDecimal`). All 6 checked-in FHIR Questionnaire fixtures regenerated. **Every question type in the LHC-Forms column now empirically verified (8 ✅ + 2 🟡).** | `quickq-io-r4m` |
| 2026-05-12 | **End-to-end submission round-trip** ✅ for LHC-Forms via PHQ-9. Test fills three items in the rendered form via Playwright, extracts the QuestionnaireResponse via `LForms.Util.getFormFHIRData`, imports through `quickq.parser_fhir_response.import_fhir_response`, asserts the option_values landed with zero data_quality_flag rows. **Also fixed a second FHIR resolver bug**: `_resolve_questionnaire` did an exact `canonical_url` match, but LHC-Forms emits the URL with a `|<version>` suffix per FHIR convention. Fixed to split on `|` before matching. This is the load-bearing test that closes the data-model-as-contract loop end-to-end. | `quickq-io-r4m` |
| 2026-05-13 | **Multi-type round-trip** ✅ via gout_checkin: a single test fills boolean / numeric / date / multiple_choice / text in the rendered LHC-Forms output and verifies each lands in the correct typed column (`response_text`, `response_numeric`, `response_date`, `option_id`). Surfaced a Playwright mechanics finding worth recording for future tests: LHC-Forms' date input uses a custom keystroke listener, so `locator.fill()` doesn't stick — must `click()` then `page.keyboard.type()` character-by-character. Format is MM/DD/YYYY, not ISO. | `quickq-io-r4m` |
| 2026-05-13 | **Repeating-group submission round-trip** ✅ via prenatal_visits: test fills two visit instances (week / provider / concern × 2), clicks the "+ Visit details" button to add the second instance, and asserts the imported responses carry the correct `repeat_index` per instance (0 for the first, 1 for the second). This closes the matching renderer-side loop for `quickq-io-bv8` — until now the import path was only exercised against hand-built FHIR JSON. Two more Playwright mechanics worth recording: (1) Add buttons can be partially overlapped by sticky UI elements, so use `click(force=True)`; (2) LHC-Forms numbers the second instance's children with the parent's repeat-index in the FIRST slot (`visits.week/2/1`, not `visits.week/1/2`). | `quickq-io-r4m` |
| 2026-05-13 | **Grid-in-repeating submission round-trip** ✅ — the third leg of the bv8 verification trifecta. Test fills two visit instances each with a 3-row severity grid (6 grid cells total), submits, imports, and asserts every cell lands with the correct `(grid_row_id, grid_column_id, repeat_index)` triple. With this commit bv8 is fully verified end-to-end: data-model (3 boundary tests), structural rendering (5 tests), and submission round-trip (1 test). Grid-cell IDs follow the pattern `linkId/parent_repeat/grid_repeat/root` — instance 2's first grid row is `rg.visits.severity.r0/2/1/1`, not `/1/2/1` (recording in case the next nested-shape work re-encounters this). | `quickq-io-r4m` / `quickq-io-bv8` |
| 2026-05-13 | **r4m closeout pass for LHC-Forms.** Three more round-trips: AUDIT likert (3 q1-q3 items), gout slider (numeric via the text-input fallback), and enable_behavior=all (two boolean triggers Yes + the gated text answer). Probed ranked empirically: LHC-Forms renders quickq's `ranked` type as a single-select combobox ("Select one") — no drag-to-rank, no multi-position ordering, `ordinalValue` extension silently ignored. Marked the `ranked` cell explicitly as 🟡 partial-support and reorganized the FHIR extensions table to reflect that LHC-Forms silently ignores `questionnaire-maxOccurs`, `ordinalValue`, slider min/max, and `itemControl` — known LHC-Forms limitations, not quickq bugs. quickq-forms column spun out to its own issue. | `quickq-io-r4m` (closing) |

## Related

- `quickq-io-r4m` — this audit (tracking issue)
- `quickq-io-9u0` — grid-in-repeating spike (closed; populated 4 cells)
- `quickq-io-ckf` — strategic unify-on-quickq-forms decision; may halve future verification work
- `quickq-io-bv8` — data-model side of grid-in-repeating (closed)
- `quickq-io-ap8` — skip-logic schema extensions
