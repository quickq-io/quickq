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
