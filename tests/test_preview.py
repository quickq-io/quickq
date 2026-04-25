"""
Tests for quickq preview (renderer_md.py + preview server).

We test via build_preview_html() — same output as the server, no browser needed.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quickq.schema import init_oltp
from quickq.loader import load_yaml
from quickq.preview import build_preview_html, _find_port, _LHCFORMS_VERSION, _CDN, _CDN_FHIR

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def phq9_db(tmp_path):
    conn = init_oltp(str(tmp_path / "study.db"))
    load_yaml(conn, str(FIXTURES / "phq9.yaml"))
    conn.close()
    return str(tmp_path / "study.db")


@pytest.fixture()
def phq9_html(phq9_db):
    return build_preview_html(phq9_db, 1)


# ------------------------------------------------------------------
# HTML content
# ------------------------------------------------------------------

def test_html_is_string(phq9_html):
    assert isinstance(phq9_html, str)
    assert len(phq9_html) > 500


def test_html_has_doctype(phq9_html):
    assert phq9_html.strip().startswith("<!DOCTYPE html>")


def test_html_has_title(phq9_html):
    assert "PHQ-9" in phq9_html


def test_html_embeds_lhcforms_cdn(phq9_html):
    # build_preview_html uses CDN URLs (no local server)
    assert _LHCFORMS_VERSION in phq9_html
    assert "lhcforms-static.nlm.nih.gov" in phq9_html
    assert "lhc-forms.js" in phq9_html
    assert "lformsFHIRAll.min.js" in phq9_html
    assert _CDN in phq9_html
    assert _CDN_FHIR in phq9_html


def test_html_embeds_fhir_json(phq9_html):
    # The FHIR questionnaire JSON is inlined as a JS variable
    assert '"resourceType"' in phq9_html
    assert '"Questionnaire"' in phq9_html
    assert "phq9.1" in phq9_html




def test_html_has_preview_notice(phq9_html):
    assert "not collected" in phq9_html


def test_html_has_form_container(phq9_html):
    assert "formContainer" in phq9_html
    assert "addFormToPage" in phq9_html
    assert "_loadScript" in phq9_html


def test_embedded_json_is_valid(phq9_html):
    """The inlined FHIR JSON should be parseable."""
    # Extract the JSON assigned to `questionnaire = ...`
    marker = "const questionnaire = "
    start = phq9_html.index(marker) + len(marker)
    # Find the matching closing brace by counting braces
    depth = 0
    end = start
    for i, ch in enumerate(phq9_html[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    parsed = json.loads(phq9_html[start:end])
    assert parsed["resourceType"] == "Questionnaire"
    assert len(parsed["item"]) > 0


# ------------------------------------------------------------------
# Port finder
# ------------------------------------------------------------------

def test_find_port_returns_integer():
    port = _find_port(5173)
    assert isinstance(port, int)
    assert 1024 <= port <= 65535


def test_find_port_consistent_for_same_input():
    # Calling twice with the same preferred port gives the same result
    p1 = _find_port(19999)
    p2 = _find_port(19999)
    assert p1 == p2


# ------------------------------------------------------------------
# Unknown questionnaire
# ------------------------------------------------------------------

def test_unknown_questionnaire_raises(phq9_db):
    with pytest.raises(ValueError):
        build_preview_html(phq9_db, 999)
