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

## Two-suite test strategy

The two columns aren't redundant — they ask different questions:

| Suite | Location | Question it answers | When to run |
|---|---|---|---|
| **LHC-Forms** (interop) | `quickq/tests/test_e2e_lhcforms.py`, marked `interop` + `e2e` | Is quickq's FHIR contract portable to a third-party renderer (the NLM reference implementation)? | Before any FHIR-export change ships (`renderer_fhir.py`, schema), before releases, nightly in CI. `pytest -m interop` |
| **quickq-forms** (product) | `quickq-forms/tests/e2e/test_render_round_trip.py` | Does the default delivery product work end-to-end (render → submit → OLTP → OLAP)? | Every commit. Fast (~10s), deterministic, no network |

A passing quickq-forms test with a failing LHC-Forms test on the same fixture is the signal "our FHIR shape is wrong but our renderer hides it." Drop the LHC-Forms suite and that class of bug becomes invisible — including the one that surfaced the `answerString`-for-non-choice-triggers bug fixed in May 2026.

A passing LHC-Forms test with a failing quickq-forms test is the signal "our FHIR is fine, our renderer has a regression." Both suites cover most fixtures, so the gap shows up immediately.

When adding a new fixture or question type, add coverage to **both** suites.

## Question types

| Type | LHC-Forms | quickq-forms | Test path | Notes |
|---|---|---|---|---|
| `single_choice` | ✅ | ✅ | LHC: `test_frequency_options_present` · qq-forms: `test_phq9_basic_render_and_submit` | PHQ-9 frequency options |
| `multiple_choice` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_question_labels_render` | gout `attack_joints`, `family_gout` |
| `sata_other` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_prapare_sata_other_renders_a_combobox` | LHC-Forms renders sata_other (FHIR `open-choice`) as a multi-select combobox; options visible after dropdown interaction |
| `boolean` | ✅ | ✅ | LHC: `test_gout_checkin_question_labels_render` · qq-forms: `test_enable_behavior_all_round_trip` | gout `on_ult` (LHC); prenatal `visits.concern` + enable_behavior_all triggers (qq-forms) |
| `text` | ✅ | ✅ | LHC: `test_gout_checkin_text_area_present` · qq-forms: `test_enable_behavior_all_round_trip` | gout `notes` (LHC); gated `cessation` (qq-forms) |
| `numeric` | ✅ | ✅ | LHC: `test_gout_checkin_question_labels_render` · qq-forms: `test_prenatal_repeating_group_emits_separate_parent_items` | gout `attacks_12mo`, `uric_acid` (LHC); prenatal `visit_count` and per-instance `visits.week` (qq-forms) |
| `date` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_date_input_present` | gout `last_attack_date`, `uric_acid_date` |
| `datetime` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_date_input_present` | gout `last_attack_datetime` |
| `likert` | ✅ | ⚪ | `test_e2e_lhcforms.py::test_audit_likert_*` | All 10 AUDIT items are explicitly `type: likert`. LHC-Forms renders them as comboboxes with ordered options (Never / Monthly or less / 2-4 times a month / ...) |
| `grid` | ✅ | ✅ | LHC: `test_grid_in_repeating_renders_as_horizontal_table` · qq-forms: `test_grid_in_repeating_group_per_instance_keys` | Both verified inside a repeating-group parent; standalone-grid case implied by the same component but not directly tested in either suite |
| `ranked` | 🟡 | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_question_labels_render` | **Partial support in LHC-Forms.** Renders as a single-select combobox with placeholder "Select one" — no drag-to-rank, no multi-position ordering. A respondent can pick *one* of the ranked options but cannot order them. The `ordinalValue` extension we emit is silently ignored. Empirical finding from `quickq-io-r4m` audit. quickq-forms component renders as numbered dropdowns (one per option) but is not yet E2E-tested. |
| `slider` | 🟡 | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_slider_question_renders_an_input` | **Partial support in LHC-Forms.** Renders as a plain text input with placeholder "Type a number" — the min/max metadata from the FHIR export is silently ignored. The question doesn't disappear; the slider *affordance* does. Empirical finding from `quickq-io-r4m` audit. quickq-forms renders a real range input with min/max/labels; not yet E2E-tested. |
| `repeating_group` | ✅ | ✅ | LHC: `test_grid_in_repeating_first_instance_renders_week` · qq-forms: `test_prenatal_repeating_group_emits_separate_parent_items` + `test_grid_in_repeating_group_per_instance_keys` | qq-forms verified both flat-child and grid-child shapes, including the fix to emit N separate parent items with the same linkId (one per instance) |

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
| `repeating_group` with simple-type children | ✅ | ✅ | LHC: `test_prenatal_basic_repeating_group_first_instance_renders` + `test_prenatal_repeating_group_add_control_present`. qq-forms: `test_prenatal_repeating_group_emits_separate_parent_items` — explicitly verifies the FHIR shape (N parent items with same linkId) that import_fhir_response expects |
| `repeating_group` with a `grid` child | ✅ | ✅ | LHC: bv8 + 9u0 spike + `test_grid_in_repeating_round_trip_closes_bv8`. qq-forms: `test_grid_in_repeating_group_per_instance_keys` — verified per-instance keying (`parent[i]:child`) of grid cells inside the second visit's grid |
| `grid` (standalone, not inside a repeating group) | 🟡 | 🟡 | The horizontal-table rendering pattern is the same as the verified grid-in-repeating case in both suites; standalone case is implied to work but not directly tested |
| Multi-level `enableWhen` (multiple rules per item, `enable_behavior=any`) | ✅ | ✅ | LHC: `test_difficulty_appears_after_nonzero_answer`. qq-forms: `test_phq9_skip_logic_reveals_difficulty` — both exercise the PHQ-9 `difficulty` item with 3 `any-OR` rules |
| Multi-level `enableWhen` with `enable_behavior=all` | ✅ | ✅ | LHC: `test_e2e_lhcforms.py::test_enable_behavior_all_*`. qq-forms: `test_enable_behavior_all_round_trip` — both use the dedicated `enable_behavior_all.yaml` fixture. The original LHC-Forms test **surfaced a real bug in `quickq/renderer_fhir.py`**: enableWhen answer type was always `answerString` for non-choice triggers, but FHIR requires the type to match the trigger. LHC-Forms (correctly) refused to match. Fix in same commit: dispatch on trigger type for `answerBoolean` / `answerDate` / `answerDateTime` / `answerDecimal`. |
| `operator: in` (post-expansion to N flat `=` rules) | 🟡 | 🟡 | Lands as N standard FHIR `enableWhen` entries with `enable_behavior=any`; should render in any FHIR-compliant renderer but not directly tested |

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
| PHQ-9 fill + submit → import | ✅ | ✅ | LHC: `test_phq9_submission_round_trips_through_import_fhir_response`. qq-forms: `test_phq9_imports_into_study_db` — fills all 9 PHQ items + the gated `difficulty`, submits via the FastAPI server, imports the saved QuestionnaireResponse JSON through `quickq.parser_fhir_response.import_fhir_response`, asserts 10 rows land in `response` table with zero data_quality_flag rows |
| gout_checkin fill (5 types) + submit → import | ✅ | ⚪ | `test_e2e_lhcforms.py::test_gout_checkin_submission_round_trips_through_import_fhir_response` — fills boolean, numeric, date, multiple_choice, text; asserts each lands in the correct typed column with zero data_quality_flag rows |
| Repeating-group fill → import (basic) | ✅ | ✅ | LHC: `test_prenatal_visits_repeating_group_round_trips_through_import`. qq-forms: `test_prenatal_visits_import_records_repeat_index` — adds a second instance via the React component, fills both, submits, imports, asserts both `0` and `1` appear in the `repeat_index` column |
| Grid-in-repeating fill → import | ✅ | ⚪ (renderer ✅, full import not yet) | LHC: `test_e2e_lhcforms.py::test_grid_in_repeating_round_trip_closes_bv8`. qq-forms: `test_grid_in_repeating_group_per_instance_keys` proves the renderer emits the correct FHIR shape, but the import → DB-row assertion is not yet wired (the renderer shape is what bv8's third leg actually depends on) |

## quickq-forms coverage

quickq-forms has its own Playwright suite at `quickq-forms/tests/e2e/test_render_round_trip.py`. The pattern mirrors the LHC-Forms suite: boot the server with the FileAdapter pointing at a shared FHIR fixture, drive the DOM, read the saved QuestionnaireResponse from disk, optionally round-trip through `quickq.parser_fhir_response.import_fhir_response`.

What's verified today (May 2026):
- All four core simple types: `single_choice`, `boolean`, `text`, `numeric`
- `grid` (inside a repeating-group parent)
- `repeating_group` with both flat children and a grid child — including the FHIR-shape requirement to emit N separate parent items with the same linkId
- Skip logic with `enable_behavior=any` (PHQ-9 difficulty reveal) and `enable_behavior=all` (cessation gate)
- Preview mode: banner visible, every input disabled, `/response` returns 403
- Full DB round-trip for PHQ-9 and prenatal_visits (the repeating-group + repeat_index case)

Remaining ⚪ cells reflect untested types (datetime, likert, slider, ranked, multiple_choice, sata_other, standalone grid) and the gout_checkin multi-type round-trip. They aren't broken — the components exist and the parser handles them — they just aren't yet driven by the Playwright suite. Filling those cells is the same incremental pattern as the existing tests.

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
| 2026-05-13 | **quickq-forms parity pass.** New Playwright suite at `quickq-forms/tests/e2e/test_render_round_trip.py` (10 tests) fills the previously-⚪ cells for `single_choice`, `boolean`, `text`, `numeric`, `grid` (in repeating), `repeating_group` (flat children + grid child), and both `enable_behavior` modes. Surfaced and fixed four correctness bugs along the way: (1) `serializeRepeatingGroup` was flattening all instances into a single parent item — fixed to emit N separate parents with the same linkId, matching what `import_fhir_response` expects; (2) grid cells inside repeating-group instances were keyed by bare `row.linkId`, ignoring the `parent[i]:` prefix — fixed in both component and serializer; (3) plain FHIR `group` items threw `Unsupported type` — now render as section headers; (4) FHIR `display` items were silently filtered out — now render as instructional text. The four bug fixes are the concrete payoff of the two-suite strategy: each was caught by the new quickq-forms tests, not the LHC-Forms ones. Two end-to-end DB round-trips also wired (PHQ-9 + prenatal repeat_index). | `quickq-io-sy2` (closing) |
| 2026-05-13 | **Two-suite split formalized.** Added `pytest.mark.interop` to the LHC-Forms suite (registered in `pyproject.toml`); the suite is now selectable as `pytest -m interop`. Cadence documented in this page's intro: quickq-forms every commit (fast, deterministic), LHC-Forms before FHIR-export changes / nightly. The split makes the "interop canary" role explicit — LHC-Forms is the independent renderer that catches FHIR-correctness bugs our own renderer would silently mirror, and that role doesn't go away when quickq-forms becomes the default delivery tool. | n/a (policy decision recorded as part of `quickq-io-sy2`) |

## Related

- `quickq-io-r4m` — this audit (tracking issue, closed)
- `quickq-io-sy2` — quickq-forms renderer-coverage parity (closed; filled the quickq-forms column)
- `quickq-io-9u0` — grid-in-repeating spike (closed; populated 4 cells)
- `quickq-io-ckf` — strategic unify-on-quickq-forms decision; the two-suite split formalized above is the *practical* answer (keep both, different cadences) rather than the unification originally implied
- `quickq-io-bv8` — data-model side of grid-in-repeating (closed)
- `quickq-io-ap8` — skip-logic schema extensions
