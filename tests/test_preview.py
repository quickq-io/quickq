"""
Tests for quickq preview.

Two paths are covered:
  - build_preview_html — the static HTML export used by `quickq preview -o`
    (LHC-Forms-based, retained for single-file static export)
  - preview() — delegates to quickq-forms preview mode (default path for
    `quickq preview`); tested via a backgrounded server when quickq-forms
    is importable.
"""
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
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


# ------------------------------------------------------------------
# quickq-forms delegation (default preview path)
# ------------------------------------------------------------------

_quickq_forms = pytest.importorskip(
    "quickq_forms",
    reason="quickq-forms not installed — `pip install -e ../quickq-forms`",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.2)
    return False


@pytest.fixture
def preview_server(phq9_db):
    """Start quickq.preview.preview() on a free port in a thread; tear down after."""
    from quickq.preview import preview

    port = _free_port()
    thread = threading.Thread(
        target=preview,
        args=(phq9_db, 1),
        kwargs={"port": port, "open_browser": False},
        daemon=True,
    )
    thread.start()
    assert _wait_for_health(port), "quickq.preview did not become healthy"
    yield port
    # uvicorn doesn't expose a graceful shutdown from the thread without
    # the Server handle. Daemon thread + test process exit is sufficient.


def test_preview_delegates_to_quickq_forms_in_preview_mode(preview_server):
    port = preview_server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/config", timeout=2) as r:
        body = json.load(r)
    assert body == {"preview": True}


def test_preview_rejects_submissions(preview_server):
    port = preview_server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/response",
        data=json.dumps({
            "resourceType": "QuestionnaireResponse",
            "questionnaire": "http://example.com/instruments/phq-9",
            "status": "completed",
            "item": [],
        }).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=2)
    assert exc.value.code == 403


def test_preview_serves_the_exported_fhir_questionnaire(preview_server):
    port = preview_server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/questionnaire", timeout=2) as r:
        q = json.load(r)
    assert q["resourceType"] == "Questionnaire"
    assert any(it.get("linkId", "").startswith("phq9.") for it in q.get("item", []))
