"""Smoke-test the WASM-exported marimo notebook bundles in a built site.

Walks the NOTEBOOKS registry in hooks/marimo_export.py, serves the given site
directory over HTTP, opens each /notebooks/<slug>/ URL in headless Chromium,
waits for the kernel to start and cells to execute, and asserts the
`smoke_assertions` strings each notebook declared appear in the rendered DOM.

Exits non-zero if any notebook fails to render its expected content. Catches
the regressions the manual one-shot Playwright check would miss:

  - parquet asset 404s (data file not bundled into site/)
  - missing pyodide deps (a new import that isn't WASM-compatible)
  - cell exceptions that reduce a chart to an empty placeholder

Usage:
    uv run python scripts/smoke_test_notebooks.py site/
"""
from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- load notebook registry ---------------------------------------------------

_spec = importlib.util.spec_from_file_location(
    "marimo_export", REPO_ROOT / "hooks" / "marimo_export.py"
)
_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hook)
NOTEBOOKS = _hook.NOTEBOOKS


# --- helpers ------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout_sec: float = 10.0) -> None:
    import urllib.request
    import urllib.error
    deadline = time.monotonic() + timeout_sec
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1.0)
            return
        except (urllib.error.URLError, ConnectionError) as e:
            last_err = e
            time.sleep(0.2)
    raise RuntimeError(f"http server never came up at {url}: {last_err}")


def _check_notebook(page, base_url: str, nb: dict) -> list[str]:
    """Return list of failure messages (empty = pass)."""
    failures: list[str] = []
    url = f"{base_url}/notebooks/{nb['slug']}/"

    page.goto(url, wait_until="networkidle", timeout=120_000)
    # Pyodide + marimo kernel boot + cell execution. Generous in CI.
    page.wait_for_timeout(75_000)

    # Walk shadow DOM too — marimo wraps widgets and outputs in custom elements.
    body = page.evaluate(
        """() => {
            const parts = [document.body.innerText];
            function walk(node) {
                if (node.shadowRoot) {
                    parts.push(node.shadowRoot.textContent || '');
                    walk(node.shadowRoot);
                }
                for (const el of node.querySelectorAll('*')) {
                    if (el.shadowRoot) {
                        parts.push(el.shadowRoot.textContent || '');
                        walk(el.shadowRoot);
                    }
                }
            }
            walk(document);
            return parts.join('\\n');
        }"""
    )

    for needle in nb.get("smoke_assertions", []):
        if needle not in body:
            failures.append(f"missing assertion: {needle!r}")

    # Surface explicit cell exceptions if marimo rendered them.
    err_count = page.evaluate(
        "document.querySelectorAll('marimo-traceback, .marimo-error, [data-cell-error]').length"
    )
    if err_count:
        failures.append(f"{err_count} cell error element(s) on page")

    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: smoke_test_notebooks.py SITE_DIR", file=sys.stderr)
        return 2
    site_dir = Path(sys.argv[1]).resolve()
    if not site_dir.is_dir():
        print(f"not a directory: {site_dir}", file=sys.stderr)
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; run `uv sync` and `playwright install chromium`",
              file=sys.stderr)
        return 2

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=site_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    exit_code = 0
    try:
        _wait_for_http(f"{base_url}/")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1800})
            for nb in NOTEBOOKS:
                print(f"[smoke] {nb['slug']} ...", flush=True)
                failures = _check_notebook(page, base_url, nb)
                if failures:
                    exit_code = 1
                    for msg in failures:
                        print(f"  FAIL  {msg}", flush=True)
                else:
                    print(f"  OK", flush=True)
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
