"""
PDF rendering for quickq instruments.

Pipeline: render_questionnaire_md() → HTML (via markdown package) → PDF (via weasyprint).

Install optional dependencies with:
    pip install quickq[pdf]
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .renderer_questionnaire import render_questionnaire_md

_CSS = """
@page {
    margin: 2cm 2.2cm;
    size: A4;
    @bottom-right {
        content: counter(page) " / " counter(pages);
        font-size: 8pt;
        color: #888;
    }
}

body {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 11pt;
    line-height: 1.55;
    color: #1a1a1a;
}

h1 {
    font-size: 17pt;
    margin: 0 0 0.3em 0;
    line-height: 1.2;
}

h2 {
    font-size: 12pt;
    font-variant: small-caps;
    letter-spacing: 0.03em;
    margin: 1.6em 0 0.4em 0;
    padding-bottom: 3px;
    border-bottom: 1px solid #bbb;
    page-break-after: avoid;
}

h3 {
    font-size: 11pt;
    margin: 1.2em 0 0.3em 0;
    page-break-after: avoid;
}

p {
    margin: 0.25em 0;
}

/* Link-id / type metadata line below each question heading */
p > code {
    font-family: "Courier New", Courier, monospace;
    font-size: 9.5pt;
    background: #f4f4f4;
    padding: 1px 4px;
    border-radius: 2px;
}

ul {
    margin: 0.3em 0 0.6em 1.4em;
    padding: 0;
}

li {
    margin: 0.15em 0;
}

em {
    font-style: italic;
    color: #555;
}

strong {
    font-weight: bold;
}

hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 1.2em 0;
}

/* ── Tables (grid questions) ── */
table {
    border-collapse: collapse;
    width: 100%;
    font-size: 8.5pt;
    line-height: 1.35;
    margin: 0.4em 0 0.8em 0;
    table-layout: fixed;
    overflow-wrap: break-word;
}

th {
    background: #f0f0f0;
    font-weight: bold;
    text-align: center;
    padding: 4px 5px;
    border: 1px solid #bbb;
    vertical-align: bottom;
}

td {
    padding: 3px 5px;
    border: 1px solid #bbb;
    vertical-align: top;
}

td:first-child {
    font-weight: normal;
    text-align: left;
    width: 28%;
}

/* ── Admonition-style note block (generated-output disclaimer) ── */
blockquote {
    border-left: 3px solid #888;
    margin: 0 0 1em 0;
    padding: 0.4em 0.8em;
    background: #f8f8f8;
    font-size: 9.5pt;
    color: #444;
}

/* Keep individual question blocks together where possible */
p + ul        { page-break-before: avoid; }
h2 + p        { page-break-before: avoid; }
"""


def render_questionnaire_pdf(
    conn: sqlite3.Connection,
    questionnaire_id: int,
    output_path: str | Path,
) -> None:
    """Render questionnaire as a PDF file at output_path."""
    try:
        import markdown as _md
        from weasyprint import HTML as _HTML
    except ImportError as exc:
        raise ImportError(
            "PDF rendering requires the 'pdf' extras: pip install quickq[pdf]"
        ) from exc

    md_text = render_questionnaire_md(conn, questionnaire_id)
    body_html = _md.markdown(md_text, extensions=["tables", "fenced_code"])
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>{_CSS}</style>
</head>
<body>
{body_html}
</body>
</html>"""
    _HTML(string=full_html).write_pdf(str(output_path))
