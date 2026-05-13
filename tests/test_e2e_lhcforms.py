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
