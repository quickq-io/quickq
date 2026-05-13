"""
Local browser preview for a quickq questionnaire.

Default path delegates to quickq-forms (the same renderer used by `quickq
serve`): a tighter visual match to what respondents actually see, no CDN
dependency, no internet required after install. Read-only — submissions are
rejected at the server level.

The LHC-Forms-based ``build_preview_html`` is retained for the
``quickq preview --output file.html`` use case (single-file static export,
useful for emailing a preview).

Usage:
    from quickq.preview import preview
    preview("study.db", questionnaire_id=1)

    # or from the CLI:
    quickq preview study.db 1
"""
from __future__ import annotations

import json
import mimetypes
import socket
import tempfile
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_LHCFORMS_VERSION = "41.2.0"
_CDN = f"https://lhcforms-static.nlm.nih.gov/lforms-versions/{_LHCFORMS_VERSION}/webcomponent"
_CDN_FHIR = f"https://lhcforms-static.nlm.nih.gov/lforms-versions/{_LHCFORMS_VERSION}/fhir"

# Assets downloaded once to ~/.cache/quickq/lhcforms/<version>/
_CACHE_DIR = Path.home() / ".cache" / "quickq" / "lhcforms" / _LHCFORMS_VERSION
_ASSETS = [
    "webcomponent/assets/lib/zone.min.js",
    "webcomponent/lhc-forms.js",
    "webcomponent/styles.css",
    "fhir/lformsFHIRAll.min.js",
]

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — quickq preview</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 860px;
      margin: 0 auto;
      padding: 32px 24px 80px;
      color: #222;
      background: #fafafa;
    }}
    h1 {{ margin: 0 0 4px; font-size: 1.4rem; }}
    .meta {{ color: #666; font-size: 0.85rem; margin-bottom: 20px; }}
    .notice {{
      background: #e8f4fd;
      border-left: 4px solid #3b82f6;
      padding: 10px 14px;
      margin-bottom: 24px;
      font-size: 0.875rem;
      border-radius: 0 4px 4px 0;
    }}
    #formContainer {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 24px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="meta">Version {version}{url_part}</p>
  <div class="notice">
    <strong>Preview mode</strong> — responses are not collected or saved.
    Press <kbd>Ctrl+C</kbd> in your terminal to stop the server.
  </div>
  <div id="formContainer"></div>
  <script>
    const questionnaire = {fhir_json};
    function _loadScript(src) {{
      return new Promise(function(res, rej) {{
        var s = document.createElement('script');
        s.src = src; s.onload = res; s.onerror = rej;
        document.head.appendChild(s);
      }});
    }}
    // Scripts loaded dynamically so Angular bootstraps against the live DOM.
    // styles.css is fire-and-forget — <link> onload is unreliable cross-browser.
    (async function() {{
      try {{
        await _loadScript('{wc_base}/assets/lib/zone.min.js');
        await _loadScript('{wc_base}/lhc-forms.js');
        await _loadScript('{fhir_base}/lformsFHIRAll.min.js');
        var link = document.createElement('link');
        link.rel = 'stylesheet'; link.href = '{wc_base}/styles.css';
        document.head.appendChild(link);
        LForms.Util.addFormToPage(questionnaire, 'formContainer', {{ fhirVersion: 'R4' }});
      }} catch(e) {{
        document.getElementById('formContainer').innerHTML =
          '<p style="color:#c00;padding:16px"><strong>Could not load LHC-Forms renderer.</strong><br>' +
          'Try opening this page in a private/incognito window, or run <code>quickq preview</code> ' +
          'instead of opening the static file directly.<br><small>' + e + '</small></p>';
      }}
    }})();
  </script>
</body>
</html>
"""


# ------------------------------------------------------------------
# Asset cache
# ------------------------------------------------------------------

def _ensure_lhcforms_cache() -> None:
    """Download LHC-Forms assets to ~/.cache/quickq/lhcforms/<version>/ if needed."""
    missing = [a for a in _ASSETS if not (_CACHE_DIR / a).exists()]
    if not missing:
        return
    print(f"Downloading LHC-Forms {_LHCFORMS_VERSION} assets (~4 MB, one-time)...")
    base = f"https://lhcforms-static.nlm.nih.gov/lforms-versions/{_LHCFORMS_VERSION}"
    for asset in missing:
        dest = _CACHE_DIR / asset
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(f"{base}/{asset}", dest)
        print(f"  ✓ {asset}")


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def preview(
    db_path: str,
    questionnaire_id: int,
    *,
    port: int = 5173,
    open_browser: bool = True,
) -> None:
    """
    Render the questionnaire in a local browser tab via quickq-forms.

    Blocks until the user presses Ctrl+C. Read-only — the server rejects any
    submission with HTTP 403.
    """
    try:
        from quickq_forms.serve import run as _serve_run
    except ImportError as e:
        raise RuntimeError(
            "quickq-forms is not installed. Install with:\n"
            "  pip install quickq[serve]\n"
            "(or for development: pip install -e ../quickq-forms)"
        ) from e

    from .schema import open_oltp
    from .renderer_fhir import export_fhir

    conn = open_oltp(db_path, read_only=True)
    fhir_dict = export_fhir(conn, questionnaire_id)
    conn.close()

    # quickq-forms reads the questionnaire from a JSON file. Write a temp
    # copy and hand the path off. The file lives for the duration of the
    # preview server; uvicorn blocks here until Ctrl+C.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".QuestionnaireResponse.json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(fhir_dict, tmp)
        tmp_path = tmp.name

    title = fhir_dict.get("title", f"Questionnaire {questionnaire_id}")
    version = fhir_dict.get("version", "")
    print(f"quickq preview  →  http://localhost:{port}")
    print(f"Questionnaire:     {title} (v{version})")
    print("Press Ctrl+C to stop.\n")

    try:
        _serve_run(
            questionnaire_path=tmp_path,
            port=port,
            open_browser=open_browser,
            preview=True,
        )
    finally:
        try:
            Path(tmp_path).unlink()
        except FileNotFoundError:
            pass


def preview_lhcforms(
    db_path: str,
    questionnaire_id: int,
    *,
    port: int = 5173,
    open_browser: bool = True,
) -> None:
    """
    Legacy LHC-Forms-based preview server. Retained for parity testing and
    as a fallback when quickq-forms is unavailable. The CLI does not call
    this by default — `quickq preview` uses `preview()` above.
    """
    from .schema import open_oltp
    from .renderer_fhir import export_fhir, export_fhir_json

    _ensure_lhcforms_cache()

    conn = open_oltp(db_path, read_only=True)
    fhir_dict = export_fhir(conn, questionnaire_id)
    fhir_json_str = export_fhir_json(conn, questionnaire_id, indent=2)
    conn.close()

    title    = fhir_dict.get("title", f"Questionnaire {questionnaire_id}")
    version  = fhir_dict.get("version", "")
    url      = fhir_dict.get("url", "")
    url_part = f" · {url}" if url else ""

    port = _find_port(port)

    html = _HTML_TEMPLATE.format(
        title=title,
        version=version,
        url_part=url_part,
        wc_base=f"http://localhost:{port}/lhcforms/webcomponent",
        fhir_base=f"http://localhost:{port}/lhcforms/fhir",
        fhir_json=fhir_json_str,
    )

    server = _make_server(html, port)
    local_url = f"http://localhost:{port}"

    print(f"quickq preview (LHC-Forms)  →  {local_url}")
    print(f"Questionnaire:               {title} (v{version})")
    print("Press Ctrl+C to stop.\n")

    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=[local_url]).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.shutdown()


def build_preview_html(
    db_path: str,
    questionnaire_id: int,
) -> str:
    """
    Return the rendered HTML string without starting a server.
    Uses CDN URLs. Useful for testing and static file export.
    """
    from .schema import open_oltp
    from .renderer_fhir import export_fhir, export_fhir_json

    conn = open_oltp(db_path, read_only=True)
    fhir_dict = export_fhir(conn, questionnaire_id)
    fhir_json_str = export_fhir_json(conn, questionnaire_id, indent=2)
    conn.close()

    title    = fhir_dict.get("title", f"Questionnaire {questionnaire_id}")
    version  = fhir_dict.get("version", "")
    url      = fhir_dict.get("url", "")
    url_part = f" · {url}" if url else ""

    return _HTML_TEMPLATE.format(
        title=title,
        version=version,
        url_part=url_part,
        wc_base=_CDN,
        fhir_base=_CDN_FHIR,
        fhir_json=fhir_json_str,
    )


# ------------------------------------------------------------------
# Server internals
# ------------------------------------------------------------------

def _find_port(preferred: int) -> int:
    """Return preferred port if free, otherwise an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("localhost", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _make_server(html: str, port: int) -> HTTPServer:
    cache_dir = _CACHE_DIR

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/lhcforms/"):
                asset = self.path[len("/lhcforms/"):]
                cached = cache_dir / asset
                if cached.exists():
                    body = cached.read_bytes()
                    mime = mimetypes.guess_type(str(cached))[0] or "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):
            pass  # suppress access log

    return HTTPServer(("", port), _Handler)
