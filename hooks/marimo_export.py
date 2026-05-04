"""mkdocs hook: regenerate notebook data + export marimo notebooks to WASM.

For each notebook in the registry below:

  1. (Optional) regenerate its bundled parquet data via scripts/build_notebook_data.py.
     Skipped when SKIP_NOTEBOOK_DATA_BUILD=1 (e.g. in CI after the freshness check).
  2. Run `marimo export html-wasm` and drop the bundle into site/notebooks/<slug>/.

Each entry is self-describing so adding a new notebook is one dict.

Wire-up (in mkdocs.yml):
    hooks:
      - hooks/marimo_export.py

Environment variables:
    SKIP_MARIMO_EXPORT          skip everything (markdown-only build)
    SKIP_NOTEBOOK_DATA_BUILD    skip data regen, only run marimo export
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

NOTEBOOKS = [
    {
        "source": "examples/notebooks/health_intake_demo.py",
        "slug": "health-intake-demo",
        "data": {
            "yaml": "examples/health_intake_demo.yaml",
            "output_dir": "examples/notebooks/public",
            "n": 500,
            "seed": 20260503,
        },
        # Strings the smoke test asserts appear in the rendered bundle.
        # Use markdown-cell text (always in DOM) rather than chart titles
        # (rendered as SVG <text>, not always picked up by textContent walks).
        "smoke_assertions": [
            "500 respondents",                              # intro markdown
            "Skip-logic integrity",                         # cell-output markdown table
            "How confident are respondents managing",       # likert section header
            "What do patients prioritize",                  # ranked section header
            "Three analyses, three SQL queries",            # outro markdown
        ],
    },
]


def _build_data(nb: dict) -> None:
    if "data" not in nb:
        return
    data = nb["data"]
    print(f"[marimo_export] regenerating data for {nb['slug']}")
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "build_notebook_data.py"),
            "--yaml", data["yaml"],
            "--output-dir", data["output_dir"],
            "--n", str(data["n"]),
            "--seed", str(data["seed"]),
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"[marimo_export] data build failed for {nb['slug']}")


def _export_wasm(nb: dict, site_dir: Path) -> None:
    source = REPO_ROOT / nb["source"]
    out_dir = site_dir / "notebooks" / nb["slug"]
    if not source.exists():
        sys.exit(f"[marimo_export] notebook source missing: {source}")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)

    marimo_bin = shutil.which("marimo")
    if marimo_bin is None:
        sys.exit("[marimo_export] `marimo` not found on PATH")

    print(f"[marimo_export] exporting {nb['source']} -> site/notebooks/{nb['slug']}")
    result = subprocess.run(
        [
            marimo_bin,
            "export", "html-wasm",
            str(source),
            "-o", str(out_dir),
            "--mode", "run",
            "--no-show-code",
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"[marimo_export] export failed for {nb['source']}")


def on_post_build(config, **kwargs):
    if os.environ.get("SKIP_MARIMO_EXPORT"):
        print("[marimo_export] SKIP_MARIMO_EXPORT set, skipping")
        return

    site_dir = Path(config["site_dir"])

    for nb in NOTEBOOKS:
        if not os.environ.get("SKIP_NOTEBOOK_DATA_BUILD"):
            _build_data(nb)
        _export_wasm(nb, site_dir)
