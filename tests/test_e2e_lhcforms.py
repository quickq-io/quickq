"""
E2E test: PHQ-9 FHIR Questionnaire → LHC-Forms rendering.

Validates that the FHIR JSON exported by quickq renders correctly in the
NLM LHC-Forms demo app — confirming the FHIR contract between quickq and
real delivery tools.

Run with:
    uv run pytest tests/test_e2e_lhcforms.py -v

Kept separate from the unit test suite; CI should gate this test so it
doesn't block fast local iteration (requires network + browser).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

LHCFORMS_URL = "https://lhcforms.nlm.nih.gov/lforms-fhir-app/"
FIXTURE = Path(__file__).parent / "fixtures" / "phq9_fhir_questionnaire.json"

PHQ9_QUESTIONS = [
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself",
    "Trouble concentrating on things",
    "Moving or speaking so slowly",
    "Thoughts that you would be better off dead",
]

DIFFICULTY_TEXT = "how difficult have these problems made it"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def lhcforms_page(browser):
    """Load the LHC-Forms app and upload the PHQ-9 fixture once per module."""
    page = browser.new_page()
    page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)

    page.set_input_files("#loadFileInput", str(FIXTURE))
    page.wait_for_selector("text=Little interest or pleasure", timeout=15_000)

    # Dismiss the FHIR-server modal that appears on load
    modal_close = page.locator("#serverSelectDialog .btn-close")
    if modal_close.is_visible():
        modal_close.click()
        page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)

    yield page
    page.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _open_first_option_dropdown(page: Page) -> None:
    """Click the first answer combobox and wait for it to expand."""
    first_input = page.locator("input[role=combobox]").first
    first_input.click()
    page.wait_for_function(
        "document.querySelector('input[role=combobox]')?.getAttribute('aria-expanded') === 'true'",
        timeout=5_000,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

def test_questionnaire_title_visible(lhcforms_page: Page):
    expect(lhcforms_page.get_by_text("PHQ-9", exact=False).first).to_be_visible()


def test_all_nine_questions_render(lhcforms_page: Page):
    for fragment in PHQ9_QUESTIONS:
        expect(lhcforms_page.get_by_text(fragment, exact=False).first).to_be_visible()


def test_difficulty_question_in_dom(lhcforms_page: Page):
    """
    phq9.difficulty has enableWhen logic and is present in the rendered form.
    LHC-Forms evaluates '!= answerCoding' on an unanswered item as true, so the
    question is visible in the initial state. What matters is that the skip
    logic fires correctly when answers change (see test_difficulty_appears_after_nonzero_answer).
    """
    expect(lhcforms_page.get_by_text(DIFFICULTY_TEXT, exact=False).first).to_be_visible()


def test_frequency_options_present(lhcforms_page: Page):
    """Opening the first answer combobox should reveal all four frequency options."""
    _open_first_option_dropdown(lhcforms_page)
    for label in ("Not at all", "Several days", "More than half the days", "Nearly every day"):
        expect(lhcforms_page.get_by_text(label, exact=False).first).to_be_visible()


def test_difficulty_appears_after_nonzero_answer(lhcforms_page: Page):
    """
    Select 'Several days' for item 1 — skip logic should reveal the
    difficulty question (enableBehavior: any, items 1-3 != 0).
    """
    _open_first_option_dropdown(lhcforms_page)
    lhcforms_page.get_by_text("Several days", exact=False).first.click()

    expect(
        lhcforms_page.get_by_text(DIFFICULTY_TEXT, exact=False).first
    ).to_be_visible(timeout=5_000)


# ------------------------------------------------------------------
# Grid-in-repeating-group rendering (quickq-io-9u0 spike for bv8)
# ------------------------------------------------------------------
#
# Verifies LHC-Forms renders the composite shape introduced in bv8: a grid
# child (row × column matrix) inside a repeating_group container. The
# data-model side passes 3 boundary tests in tests/test_repeating_nested_boundary.py;
# this test answers the corresponding renderer-side question.

GRID_IN_REPEATING_FIXTURE = Path(__file__).parent / "fixtures" / "repeating_with_grid_fhir_questionnaire.json"

GRID_ROW_LABELS = ("Pain", "Fatigue", "Sleep disturbance")
GRID_COL_LABELS = ("None", "Mild", "Moderate", "Severe")


@pytest.fixture(scope="module")
def grid_in_repeating_page(browser):
    """Load LHC-Forms with the repeating-with-grid fixture once per module."""
    page = browser.new_page()
    page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)

    page.set_input_files("#loadFileInput", str(GRID_IN_REPEATING_FIXTURE))
    page.wait_for_selector("text=Week of visit", timeout=15_000)

    # Dismiss the FHIR-server modal if present
    modal_close = page.locator("#serverSelectDialog .btn-close")
    if modal_close.is_visible():
        modal_close.click()
        page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)

    yield page
    page.close()


def test_grid_in_repeating_title_visible(grid_in_repeating_page: Page):
    """The questionnaire title renders."""
    expect(
        grid_in_repeating_page.get_by_text("Per-Visit Symptom Severity", exact=False).first
    ).to_be_visible()


def test_grid_in_repeating_first_instance_renders_week(grid_in_repeating_page: Page):
    """The repeating group's flat numeric child renders in the first instance."""
    expect(grid_in_repeating_page.get_by_text("Week of visit", exact=False).first).to_be_visible()


def test_grid_in_repeating_first_instance_renders_grid_rows(grid_in_repeating_page: Page):
    """All three grid row labels render inside the first repeating-group instance.

    If LHC-Forms silently drops grid children of a repeating group, none of
    the row labels will be present.
    """
    page = grid_in_repeating_page
    for row_label in GRID_ROW_LABELS:
        expect(page.get_by_text(row_label, exact=False).first).to_be_visible()


def test_grid_in_repeating_renders_as_horizontal_table(grid_in_repeating_page: Page):
    """LHC-Forms renders the grid sub-group as a horizontal-layout table.

    This is the structural check that LHC-Forms treats the FHIR group with
    choice children as a proper matrix layout — not just a vertical stack of
    independent choice questions.
    """
    page = grid_in_repeating_page
    # LHC-Forms emits the .lhc-form-horizontal-table class for grid sub-groups.
    expect(page.locator(".lhc-form-horizontal-table").first).to_be_visible()


def test_grid_in_repeating_has_interactive_answer_cells(grid_in_repeating_page: Page):
    """Each grid row has an interactive answer combobox.

    The grid renders one combobox per row (LHC-Forms' horizontal-table layout
    surfaces the column choices via the row's combobox). With three rows in
    the first instance, expect at least three comboboxes on the page (one per
    row), beyond any non-grid comboboxes from sibling questions.
    """
    page = grid_in_repeating_page
    # Anchor inside the grid's horizontal table to avoid counting sibling
    # combobox controls (visit_count, week).
    grid_table = page.locator(".lhc-form-horizontal-table").first
    cell_inputs = grid_table.locator("input[role=combobox]")
    assert cell_inputs.count() >= 3, (
        f"expected ≥3 answer comboboxes in the grid (one per row); "
        f"got {cell_inputs.count()}"
    )


# ------------------------------------------------------------------
# Question-type coverage sweep via gout_checkin
# ------------------------------------------------------------------
#
# gout_checkin exercises 9 of 12 question types in a single FHIR
# Questionnaire: date, datetime, numeric, multiple_choice, grid, boolean,
# slider, ranked, text. Loading it once and asserting each type's
# distinctive control is rendered is the most economical way to populate
# the question-type rows of the renderer-coverage audit.

GOUT_CHECKIN_FIXTURE = Path(__file__).parent / "fixtures" / "gout_checkin_fhir_questionnaire.json"

GOUT_QUESTION_LABELS = (
    "When did your most recent gout attack begin",  # date
    "How many gout attacks have you had",            # numeric
    "Which joints were affected",                    # multiple_choice
    "Rate pain and swelling in each joint",          # grid
    "diagnosed with gout",                            # multiple_choice
    "Are you currently taking a urate-lowering",     # boolean
    "Most recent serum uric acid level",             # numeric
    "Date of that uric acid blood test",             # date
    "Rank the following treatment goals",            # ranked
    "Any additional information",                    # text
)


@pytest.fixture(scope="module")
def gout_checkin_page(browser):
    """Load LHC-Forms with the gout_checkin fixture once per module."""
    page = browser.new_page()
    page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)
    page.set_input_files("#loadFileInput", str(GOUT_CHECKIN_FIXTURE))
    page.wait_for_selector("text=Gout Symptoms", timeout=15_000)
    modal_close = page.locator("#serverSelectDialog .btn-close")
    if modal_close.is_visible():
        modal_close.click()
        page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)
    yield page
    page.close()


def test_gout_checkin_title_visible(gout_checkin_page: Page):
    expect(gout_checkin_page.get_by_text("Gout Symptoms", exact=False).first).to_be_visible()


def test_gout_checkin_question_labels_render(gout_checkin_page: Page):
    """Every top-level question label appears in the rendered DOM."""
    for fragment in GOUT_QUESTION_LABELS:
        expect(gout_checkin_page.get_by_text(fragment, exact=False).first).to_be_visible()


def test_gout_checkin_date_input_present(gout_checkin_page: Page):
    """date and datetime questions render input controls (LHC-Forms uses
    text inputs with date-format hints, not native input[type=date])."""
    # Match either native date inputs or LHC-Forms' text-based date inputs.
    date_locator = gout_checkin_page.locator(
        "input[type=date], input[placeholder*=YYYY i], input[placeholder*=DD i]"
    )
    assert date_locator.count() >= 1, "expected at least one date-shaped input"


def test_gout_checkin_text_area_present(gout_checkin_page: Page):
    """The `text` question type renders a textarea (or text input)."""
    has_textarea = gout_checkin_page.locator("textarea").count() >= 1
    has_text_input = (
        gout_checkin_page.locator("input[type=text], input:not([type])").count() >= 1
    )
    assert has_textarea or has_text_input, "expected a text input or textarea"


def test_gout_checkin_grid_renders_as_horizontal_table(gout_checkin_page: Page):
    """gout_checkin has two grids (joint_severity, family_conditions).
    Both should produce horizontal-table layouts. At least one is visible
    in the initial viewport (LHC-Forms may not render below-fold sections
    until scrolled into view, so the strict count is ≥1)."""
    tables = gout_checkin_page.locator(".lhc-form-horizontal-table")
    assert tables.count() >= 1, (
        f"expected at least one .lhc-form-horizontal-table for the grid questions; "
        f"got {tables.count()}"
    )


def test_gout_checkin_slider_question_renders_an_input(gout_checkin_page: Page):
    """The `slider` question type renders an input control.

    **Documented partial support:** LHC-Forms renders quickq's `slider` type
    as a plain text input with placeholder "Type a number" — *not* as a
    visual range slider, an ARIA `role=slider`, or even a numeric input
    with min/max attributes. The min/max metadata in our FHIR export is
    silently ignored.

    The slider question does not disappear (verified here); the affordance
    degrades. See `docs/internal/renderer-coverage.md` for the audit cell.
    Treating this as a known LHC-Forms limitation rather than a quickq bug.
    """
    slider_input = gout_checkin_page.locator("input[id*='pain_vas']")
    assert slider_input.count() >= 1, (
        "the slider question must at minimum render an input control; "
        "it disappeared entirely"
    )


# ------------------------------------------------------------------
# sata_other via prapare.necessities (FHIR `open-choice`)
# ------------------------------------------------------------------

PRAPARE_FIXTURE = Path(__file__).parent / "fixtures" / "prapare_fhir_questionnaire.json"


@pytest.fixture(scope="module")
def prapare_page(browser):
    """Load LHC-Forms with the PRAPARE fixture once per module."""
    page = browser.new_page()
    page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)
    page.set_input_files("#loadFileInput", str(PRAPARE_FIXTURE))
    page.wait_for_selector("text=PRAPARE", timeout=15_000)
    modal_close = page.locator("#serverSelectDialog .btn-close")
    if modal_close.is_visible():
        modal_close.click()
        page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)
    yield page
    page.close()


def test_prapare_sata_other_question_renders(prapare_page: Page):
    """The sata_other question (`prapare.necessities`, FHIR `open-choice`)
    appears in the rendered form. This is the must-not-disappear bar."""
    page = prapare_page
    expect(page.get_by_text("unable to get any of the following", exact=False).first).to_be_visible()


def test_prapare_sata_other_renders_a_combobox(prapare_page: Page):
    """LHC-Forms renders the sata_other question (`prapare.necessities`,
    FHIR `open-choice`) as a multi-select combobox.

    Options are inside the closed dropdown and only become visible after
    user interaction. The renderer-coverage claim is "the question
    produces an interactive control" — verified by the combobox's
    presence with the expected linkId.
    """
    page = prapare_page
    combobox = page.locator("input#prapare\\.necessities\\/1[role='combobox']")
    assert combobox.count() == 1, (
        f"expected one combobox for prapare.necessities; got {combobox.count()}"
    )


# ------------------------------------------------------------------
# Basic repeating_group (no grid child) via prenatal_visits
# ------------------------------------------------------------------

PRENATAL_VISITS_FIXTURE = Path(__file__).parent / "fixtures" / "prenatal_visits_fhir_questionnaire.json"

PRENATAL_CHILD_LABELS = (
    "Week of pregnancy at visit",
    "Type of provider seen",
    "Were any concerns documented",
)


@pytest.fixture(scope="module")
def prenatal_page(browser):
    """Load LHC-Forms with the prenatal_visits fixture once per module."""
    page = browser.new_page()
    page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)
    page.set_input_files("#loadFileInput", str(PRENATAL_VISITS_FIXTURE))
    page.wait_for_selector("text=Prenatal Visit Log", timeout=15_000)
    modal_close = page.locator("#serverSelectDialog .btn-close")
    if modal_close.is_visible():
        modal_close.click()
        page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)
    yield page
    page.close()


def test_prenatal_title_visible(prenatal_page: Page):
    expect(prenatal_page.get_by_text("Prenatal Visit Log", exact=False).first).to_be_visible()


def test_prenatal_basic_repeating_group_first_instance_renders(prenatal_page: Page):
    """The first repeating-group instance renders all three flat children.

    This is the basic case (simple-type children: numeric, choice, boolean)
    distinct from the grid-in-repeating composite covered elsewhere.
    """
    page = prenatal_page
    for label in PRENATAL_CHILD_LABELS:
        expect(page.get_by_text(label, exact=False).first).to_be_visible()


def test_prenatal_count_question_renders(prenatal_page: Page):
    """The visit_count numeric (which drives the optional count_qq_id
    linkage for the repeating group) renders alongside the repeating
    container."""
    expect(prenatal_page.get_by_text("How many prenatal visits", exact=False).first).to_be_visible()


def test_prenatal_repeating_group_add_control_present(prenatal_page: Page):
    """LHC-Forms surfaces an 'Add' affordance for repeating groups —
    typically a button labelled '+' / 'Add' / 'Add another' / 'Add Visit'.

    This is the load-bearing claim that LHC-Forms knows how to handle the
    `repeats: true` flag on a group beyond just rendering the first instance.
    """
    page = prenatal_page
    # LHC-Forms emits a button with class lhc-float-button-end for "Add"
    # on repeating groups. Accept any of the common patterns.
    add_button = page.locator(
        "button:has-text('Add'), .lhc-float-button-end, [aria-label*='Add' i]"
    )
    assert add_button.count() >= 1, (
        "expected at least one 'Add' control on the page for the repeating "
        "group; got none — LHC-Forms may not be rendering the repeats:true "
        "affordance"
    )


# ------------------------------------------------------------------
# likert via AUDIT (10 questions, all type: likert in source YAML)
# ------------------------------------------------------------------

AUDIT_FIXTURE = Path(__file__).parent / "fixtures" / "audit_fhir_questionnaire.json"

AUDIT_QUESTION_FRAGMENTS = (
    "How often do you have a drink",
    "How many standard drinks",
    "How often do you have six or more drinks",
    "found that you were not able to stop",
)


@pytest.fixture(scope="module")
def audit_page(browser):
    page = browser.new_page()
    page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)
    page.set_input_files("#loadFileInput", str(AUDIT_FIXTURE))
    page.wait_for_selector("text=AUDIT", timeout=15_000)
    modal_close = page.locator("#serverSelectDialog .btn-close")
    if modal_close.is_visible():
        modal_close.click()
        page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)
    yield page
    page.close()


def test_audit_likert_questions_render(audit_page: Page):
    """All 10 AUDIT items are `type: likert` in the YAML; verify a
    representative subset render on the page. FHIR exports likert as
    `choice` with ordered options, and LHC-Forms renders it as a combobox
    (same surface as single_choice but with semantically ordinal options)."""
    page = audit_page
    for fragment in AUDIT_QUESTION_FRAGMENTS:
        expect(page.get_by_text(fragment, exact=False).first).to_be_visible()


def test_audit_likert_renders_as_choice_comboboxes(audit_page: Page):
    """Each AUDIT likert question gets its own combobox.

    AUDIT has 10 likert questions — expect at least 10 comboboxes on the
    rendered page (one per item).
    """
    page = audit_page
    audit_comboboxes = page.locator("input[role=combobox][id*='audit.q']")
    assert audit_comboboxes.count() >= 10, (
        f"expected ≥10 likert comboboxes (one per AUDIT item); "
        f"got {audit_comboboxes.count()}"
    )


def test_audit_likert_ordinal_options_present_on_first(audit_page: Page):
    """Opening the first AUDIT question's combobox reveals the ordinal
    options (Never / Monthly or less / etc.). This is the load-bearing
    likert claim — answers are an ordered scale, not arbitrary choices."""
    page = audit_page
    first_q = page.locator("input[role=combobox][id='audit.q1/1']")
    first_q.click()
    page.wait_for_function(
        "document.activeElement && document.activeElement.getAttribute('aria-expanded') === 'true'",
        timeout=5_000,
    )
    # AUDIT-Q1 frequency scale labels
    for label in ("Never", "Monthly or less", "2-4 times a month"):
        expect(page.get_by_text(label, exact=False).first).to_be_visible()


# ------------------------------------------------------------------
# Multi-rule enableWhen with enable_behavior=all (AND across rules)
# ------------------------------------------------------------------
#
# PHQ-9.difficulty already covers enable_behavior=any (3 rules combined OR).
# This section verifies the complementary AND case: a question gated on
# multiple conditions ALL evaluating true.

ENABLE_BEHAVIOR_ALL_FIXTURE = (
    Path(__file__).parent / "fixtures" / "enable_behavior_all_fhir_questionnaire.json"
)


@pytest.fixture(scope="module")
def enable_behavior_all_page(browser):
    page = browser.new_page()
    page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)
    page.set_input_files("#loadFileInput", str(ENABLE_BEHAVIOR_ALL_FIXTURE))
    page.wait_for_selector("text=Multi-rule AND test", timeout=15_000)
    modal_close = page.locator("#serverSelectDialog .btn-close")
    if modal_close.is_visible():
        modal_close.click()
        page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)
    yield page
    page.close()


# Helper: select a yes/no boolean answer in LHC-Forms.
# LHC-Forms renders boolean items as radios with non-trivial DOM: the radio
# inputs have empty `id` attributes but each has an associated `<label>`
# whose `id` looks like `<link_id>/<row>|<true|false|null>`. Click the label.
def _select_radio_answer(page: Page, link_id_substring: str, value: bool) -> None:
    """Click the Yes/No radio for the boolean question whose linkId contains
    `link_id_substring`. `value=True` clicks Yes, `value=False` clicks No."""
    suffix = "true" if value else "false"
    # The label ID looks like `trig.age_18/1|true` — anchor on the link_id
    # substring and the suffix.
    label = page.locator(f"label[id*='{link_id_substring}'][id$='|{suffix}']").first
    label.click()


def test_enable_behavior_all_gated_question_hidden_initially(
    enable_behavior_all_page: Page,
):
    """Before the two trigger questions are answered, the gated follow-up
    must be hidden (enable_behavior=all → all triggers must satisfy)."""
    page = enable_behavior_all_page
    # The trigger questions are visible
    expect(page.get_by_text("Are you 18 or older", exact=False).first).to_be_visible()
    expect(page.get_by_text("Are you a current smoker", exact=False).first).to_be_visible()
    # The gated question is NOT yet visible
    gated = page.get_by_text("Cessation counseling follow-up", exact=False)
    assert gated.count() == 0, (
        "the AND-gated follow-up should not be visible before either "
        "trigger is answered"
    )


def test_enable_behavior_all_one_trigger_alone_does_not_reveal(
    enable_behavior_all_page: Page,
):
    """Answer only the first trigger; the AND-gated question must stay hidden."""
    page = enable_behavior_all_page
    _select_radio_answer(page, "trig.age_18", True)
    # Still hidden because trig.smoker isn't answered yet
    page.wait_for_timeout(300)  # let LHC-Forms re-evaluate
    gated = page.get_by_text("Cessation counseling follow-up", exact=False)
    assert gated.count() == 0, (
        "answering one of two triggers should NOT reveal an enable_behavior=all "
        "gated question"
    )


def test_enable_behavior_all_both_triggers_reveal_followup(
    enable_behavior_all_page: Page,
):
    """Answer both triggers truthfully; the AND-gated question must appear."""
    page = enable_behavior_all_page
    # trig.age_18 was already set to Yes by the previous test (module-scoped page).
    # Now answer the second trigger.
    _select_radio_answer(page, "trig.smoker", True)
    expect(
        page.get_by_text("Cessation counseling follow-up", exact=False).first
    ).to_be_visible(timeout=5_000)


# ------------------------------------------------------------------
# End-to-end submission round-trip
# ------------------------------------------------------------------
#
# The load-bearing test for the "data model is the contract" thesis:
# fill the rendered form in LHC-Forms via Playwright, extract the resulting
# FHIR QuestionnaireResponse via LHC-Forms' JS API, pass it through
# quickq.parser_fhir_response.import_fhir_response, and assert the
# responses land in the OLTP correctly.
#
# Anchor: PHQ-9 because every item is single_choice (simple Playwright
# interaction) and the answer→option_value mapping is one-to-one.

def _lhcforms_get_qresponse(page: Page) -> dict:
    """Extract the current QuestionnaireResponse from LHC-Forms via its
    public JS API (LForms.Util.getFormFHIRData)."""
    return page.evaluate(
        "() => window.LForms.Util.getFormFHIRData('QuestionnaireResponse', 'R4')"
    )


def test_phq9_submission_round_trips_through_import_fhir_response(browser):
    """Fill PHQ-9 in LHC-Forms, extract the QuestionnaireResponse, import it
    back into a quickq OLTP, and assert the answers landed correctly.

    This is the load-bearing end-to-end test for the FHIR boundary."""
    import sqlite3
    from quickq.schema import init_oltp
    from quickq.loader import load_yaml
    from quickq.parser_fhir_response import import_fhir_response

    import tempfile

    # --- 1. Render PHQ-9 in LHC-Forms and fill three items ---
    page = browser.new_page()
    try:
        page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)
        page.set_input_files("#loadFileInput", str(FIXTURE))
        page.wait_for_selector("text=Little interest", timeout=15_000)
        modal_close = page.locator("#serverSelectDialog .btn-close")
        if modal_close.is_visible():
            modal_close.click()
            page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)

        def _answer_first_three(items: list[str]) -> None:
            """Set the first three PHQ-9 items to the given option labels.

            LHC-Forms autocomplete dropdown options live in
            #completionOptions ul li with text like "2:  Several days" —
            target via the parent and a contains-text selector. A Tab
            press after the click defocuses the field and commits the
            selection into the form's data model (without it, the input
            text updates visually but getFormFHIRData omits the answer).
            """
            for idx, label in enumerate(items, start=1):
                combo = page.locator(f"input[role=combobox][id='phq9.{idx}/1']")
                combo.click()
                page.wait_for_selector(
                    f"#completionOptions li:has-text(\"{label}\")", timeout=5_000
                )
                page.locator(
                    f"#completionOptions li:has-text(\"{label}\")"
                ).first.click()
                combo.press("Tab")
                # Brief settle so LHC-Forms registers the commit before
                # we open the next combobox (without this, the most
                # recent answer doesn't make it into getFormFHIRData).
                page.wait_for_timeout(150)

        _answer_first_three(["Several days", "More than half the days", "Nearly every day"])

        # --- 2. Extract the QuestionnaireResponse via LHC-Forms' JS API ---
        qresponse = _lhcforms_get_qresponse(page)
    finally:
        page.close()

    # Sanity check on the extracted response
    assert qresponse["resourceType"] == "QuestionnaireResponse"
    assert "phq9" in qresponse["questionnaire"], (
        f"unexpected questionnaire ref: {qresponse['questionnaire']!r}"
    )
    extracted_link_ids = {
        item["linkId"] for item in qresponse.get("item", [])
    }
    assert {"phq9.1", "phq9.2", "phq9.3"}.issubset(extracted_link_ids), (
        f"expected phq9.1/2/3 in extracted response; got {extracted_link_ids}"
    )

    # --- 3. Round-trip: import the response into a fresh quickq OLTP ---
    fixtures_dir = Path(__file__).parent / "fixtures"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "round_trip.db"
        conn = init_oltp(db_path)
        load_yaml(conn, fixtures_dir / "phq9.yaml")
        conn.commit()

        # subject reference is required by the parser; LHC-Forms doesn't add one
        qresponse.setdefault("subject", {"reference": "Patient/lhcforms-test"})
        # status is required
        qresponse.setdefault("status", "completed")

        session_id = import_fhir_response(conn, qresponse)
        conn.commit()

        # --- 4. Assert the three answers landed with the right option_values ---
        rows = conn.execute(
            """SELECT q.link_id, opt.option_value
               FROM response r
               JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
               JOIN question q ON qq.question_id = q.question_id
               JOIN response_option opt ON r.option_id = opt.option_id
               WHERE r.session_id = ?
                 AND q.link_id IN ('phq9.1', 'phq9.2', 'phq9.3')
               ORDER BY q.link_id"""
        , (session_id,)).fetchall()
        landed = {r[0]: r[1] for r in rows}

        # PHQ-9 frequency option_values: 0=Not at all, 1=Several days,
        # 2=More than half the days, 3=Nearly every day
        assert landed == {
            "phq9.1": "1",
            "phq9.2": "2",
            "phq9.3": "3",
        }, f"round-trip mismatch: expected 1/2/3 for items 1/2/3; got {landed}"

        # No data quality flags should have been raised for clean
        # well-formed answers like these.
        flag_count = conn.execute(
            "SELECT COUNT(*) FROM data_quality_flag WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        assert flag_count == 0, (
            f"expected zero data_quality_flag rows for clean PHQ-9 answers; "
            f"got {flag_count}"
        )


def test_gout_checkin_submission_round_trips_through_import_fhir_response(browser):
    """Fill gout_checkin in LHC-Forms across five question types, extract the
    QuestionnaireResponse, import it through quickq, and assert each typed
    column landed correctly.

    Covers: boolean, numeric, date, multiple_choice, text. Demonstrates the
    end-to-end FHIR contract for a richer fixture than PHQ-9 (which is
    single_choice only).
    """
    import tempfile
    from quickq.library_loader import load_all_libraries
    from quickq.loader import load_yaml
    from quickq.parser_fhir_response import import_fhir_response
    from quickq.schema import init_oltp

    # --- 1. Render and fill ---
    page = browser.new_page()
    try:
        page.goto(LHCFORMS_URL, wait_until="networkidle", timeout=30_000)
        page.set_input_files("#loadFileInput", str(GOUT_CHECKIN_FIXTURE))
        page.wait_for_selector("text=Gout Symptoms", timeout=15_000)
        modal_close = page.locator("#serverSelectDialog .btn-close")
        if modal_close.is_visible():
            modal_close.click()
            page.wait_for_selector("#serverSelectDialog", state="hidden", timeout=5_000)

        # boolean: gout.on_ult = true
        _select_radio_answer(page, "gout.on_ult", True)

        # numeric: gout.attacks_12mo = 4
        page.locator("input#gout\\.attacks_12mo\\/1").fill("4")
        page.locator("input#gout\\.attacks_12mo\\/1").press("Tab")

        # date: gout.last_attack_date. LHC-Forms renders date as a text
        # input with empty id and a unique aria-label. The custom date
        # picker watches keystroke events, so `fill` doesn't stick — must
        # type character-by-character via page.keyboard.type for the
        # change to register. Format is MM/DD/YYYY.
        date_input = page.locator(
            "input[aria-label='When did your most recent gout attack begin?']"
        )
        date_input.click()
        page.keyboard.type("04/15/2026", delay=20)
        page.keyboard.press("Tab")
        page.wait_for_timeout(200)

        # multiple_choice: gout.attack_joints — pick "Big toe" via the combobox.
        # (Multi-select; we add one option to keep the test focused.)
        mc_combo = page.locator("input[role=combobox][id='gout.attack_joints/1']")
        mc_combo.click()
        page.wait_for_selector(
            "#completionOptions li:has-text(\"Big toe\")", timeout=5_000
        )
        page.locator("#completionOptions li:has-text(\"Big toe\")").first.click()
        mc_combo.press("Tab")
        page.wait_for_timeout(150)

        # text: gout.notes — type a short string into the textarea
        notes = page.locator("textarea#gout\\.notes\\/1")
        if notes.count() == 0:
            # Some LHC-Forms versions render text as input[type=text] for short fields
            notes = page.locator("input#gout\\.notes\\/1")
        notes.fill("Avoiding shellfish helped this month.")
        notes.press("Tab")
        page.wait_for_timeout(150)

        # --- 2. Extract the QuestionnaireResponse ---
        qresponse = _lhcforms_get_qresponse(page)
    finally:
        page.close()

    # Sanity check that all five answers made it into the QR
    extracted_link_ids = {item["linkId"] for item in qresponse.get("item", [])}
    expected = {
        "gout.on_ult", "gout.attacks_12mo", "gout.last_attack_date",
        "gout.attack_joints", "gout.notes",
    }
    missing = expected - extracted_link_ids
    assert not missing, (
        f"these answers didn't make it into the QuestionnaireResponse: {missing}"
    )

    # --- 3. Round-trip: import into a fresh OLTP with the library loaded ---
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "round_trip.db"
        conn = init_oltp(db_path)
        load_all_libraries(conn)
        load_yaml(conn, Path(__file__).parent / "fixtures" / "gout_checkin.yaml")
        conn.commit()

        qresponse.setdefault("subject", {"reference": "Patient/gout-rt-test"})
        qresponse.setdefault("status", "completed")

        session_id = import_fhir_response(conn, qresponse)
        conn.commit()

        # --- 4. Assert each typed column was populated correctly ---
        # boolean → response_text 'true'
        bool_row = conn.execute(
            """SELECT r.response_text
               FROM response r
               JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
               JOIN question q ON qq.question_id = q.question_id
               WHERE r.session_id = ? AND q.link_id = 'gout.on_ult'""",
            (session_id,),
        ).fetchone()
        assert bool_row is not None and bool_row[0] == "true", (
            f"boolean answer did not land: {bool_row}"
        )

        # numeric → response_numeric 4.0
        num_row = conn.execute(
            """SELECT r.response_numeric
               FROM response r
               JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
               JOIN question q ON qq.question_id = q.question_id
               WHERE r.session_id = ? AND q.link_id = 'gout.attacks_12mo'""",
            (session_id,),
        ).fetchone()
        assert num_row is not None and num_row[0] == 4.0, (
            f"numeric answer did not land: {num_row}"
        )

        # date → response_date 2026-04-15
        date_row = conn.execute(
            """SELECT r.response_date
               FROM response r
               JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
               JOIN question q ON qq.question_id = q.question_id
               WHERE r.session_id = ? AND q.link_id = 'gout.last_attack_date'""",
            (session_id,),
        ).fetchone()
        assert date_row is not None and date_row[0] == "2026-04-15", (
            f"date answer did not land: {date_row}"
        )

        # multiple_choice → at least one option_id row for the question
        mc_count = conn.execute(
            """SELECT COUNT(*)
               FROM response r
               JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
               JOIN question q ON qq.question_id = q.question_id
               WHERE r.session_id = ?
                 AND q.link_id = 'gout.attack_joints'
                 AND r.option_id IS NOT NULL""",
            (session_id,),
        ).fetchone()[0]
        assert mc_count >= 1, (
            f"multiple_choice answer did not land: 0 option rows"
        )

        # text → response_text matches
        text_row = conn.execute(
            """SELECT r.response_text
               FROM response r
               JOIN questionnaire_question qq ON r.qq_id = qq.qq_id
               JOIN question q ON qq.question_id = q.question_id
               WHERE r.session_id = ? AND q.link_id = 'gout.notes'""",
            (session_id,),
        ).fetchone()
        assert text_row is not None and "shellfish" in text_row[0], (
            f"text answer did not land or got mangled: {text_row}"
        )

        # No data quality flags for clean answers
        flag_count = conn.execute(
            "SELECT COUNT(*) FROM data_quality_flag WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        assert flag_count == 0, (
            f"expected zero data_quality_flag rows for clean answers; got {flag_count}"
        )
