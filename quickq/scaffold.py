"""
Scaffold a quickq study repository: a directory with the recommended
authoring layout (instrument.yaml, scripts, .gitignore, README, library/, docs/).

Used by `quickq new <study-name>`. The scaffolded directory is the
recommended starting point for any new study — version control the YAML
and library; treat the runtime databases (.db, .duckdb) as build artifacts.

The README template is the canonical documentation for the repository
pattern; the docs/reference/study-repository page (separate ticket
quickq-io-kq0) writes around this template after it ships.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ScaffoldError(Exception):
    pass


@dataclass
class ScaffoldResult:
    target: Path
    files_created: list[str]
    git_initialized: bool


_GITIGNORE = """\
# Runtime databases (regenerable from instrument.yaml + scripts/load.sh)
*.db
*.db-shm
*.db-wal
*.duckdb
*.duckdb.wal

# Build / export outputs
exports/
parquet_export/
site/

# Python / virtualenv
__pycache__/
*.pyc
.venv/

# OS
.DS_Store

# Editor / Claude / IDE
.claude/
.vscode/
.idea/
"""


_INSTRUMENT_YAML = """\
# Authoring source for this study's questionnaire(s).
#
# Edit this file to define your instrument, then run:
#     bash scripts/load.sh
# to (re)build study.db from this definition.
#
# The full YAML format reference, including every field, all skip-logic
# operators, scoring formulas, and immutability rules:
#     https://quickq-io.github.io/quickq/authoring/

questionnaire:
  name: "{name}"
  canonical_url: "http://example.com/instruments/{slug}"
  version: "1.0"
  description: "Replace with a short description of what this instrument measures."

  questions:
    # An example question to get you started. Replace with your own items.
    - link_id: example.q1
      text: "An example single-choice question. Replace this with your own."
      type: single_choice
      required: true
      options:
        - {{ text: "Yes", value: "yes" }}
        - {{ text: "No",  value: "no" }}
"""


_README = """\
# {name}

A quickq study repository.

## Quick start

Rebuild the study database from the instrument definition:

```bash
bash scripts/load.sh
```

Generate synthetic responses for development / testing:

```bash
bash scripts/seed.sh
```

Refresh the analytical layer:

```bash
bash scripts/refresh.sh
```

## Repository layout

| Path | Purpose | In git? |
|---|---|---|
| `instrument.yaml`     | Authoring source for the questionnaire(s) | Yes |
| `library/`            | Custom library extensions (shared question banks) | Yes |
| `scripts/`            | Convenience scripts to rebuild artifacts from sources | Yes |
| `docs/`               | Study-specific protocol notes, IRB packet, SOPs | Yes |
| `study.db`            | OLTP study database (built by `scripts/load.sh`) | No (gitignored) |
| `analytics.duckdb`    | OLAP analytical database (built by `scripts/refresh.sh`) | No (gitignored) |
| `exports/`            | Output directory for parquet / FHIR / report exports | No (gitignored) |

The principle: **YAML and library are sources of truth, in git. The
databases are runtime artifacts, regenerable from sources, not in git.**
Studies that contain real respondent data treat `study.db` like any
research database — back it up off-site (rsync, snapshots, encrypted)
but do not commit it to version control.

## Editing the instrument

Edit `instrument.yaml` directly. After changes, rebuild with `bash
scripts/load.sh`. quickq's loader detects whether the questionnaire's
`canonical_url` + `version` already exists in the database and updates
in place when no responses have been collected yet. Once responses
exist, bump the `version:` field in the YAML to author a new revision
rather than overwriting the instrument respondents already saw.

For the full YAML format reference (every field, all skip-logic
operators, scoring formulas, immutability rules), see
[Survey Authoring](https://quickq-io.github.io/quickq/authoring/).

## Going further

- [End-to-end walkthrough](https://quickq-io.github.io/quickq/tutorials/end-to-end/) — author, collect, analyze
- [Authoring deep-dive](https://quickq-io.github.io/quickq/tutorials/authoring/) — build a GAD-7 step by step
- [Question Types reference](https://quickq-io.github.io/quickq/reference/question-types/)
- [Analysis Recipes (Python)](https://quickq-io.github.io/quickq/reference/analysis-recipes/) and [(R)](https://quickq-io.github.io/quickq/reference/analysis-recipes-r/)

---

*This repository was scaffolded with `quickq new`.*
"""


_LOAD_SH = """\
#!/usr/bin/env bash
# Rebuild study.db from instrument.yaml. Idempotent: safe to re-run.
set -euo pipefail

DB_PATH="${1:-study.db}"

if [ -f "$DB_PATH" ]; then
    echo "Removing existing $DB_PATH ..."
    rm -f "$DB_PATH" "$DB_PATH-shm" "$DB_PATH-wal"
fi

quickq init "$DB_PATH"
quickq load instrument.yaml "$DB_PATH"

echo "Rebuilt $DB_PATH from instrument.yaml."
"""


_SEED_SH = """\
#!/usr/bin/env bash
# Generate synthetic responses for development / testing. Reproducible via --seed.
set -euo pipefail

DB_PATH="${1:-study.db}"
N="${2:-50}"

quickq seed "$DB_PATH" 1 --n "$N" --seed 1
echo "Seeded $N synthetic respondents into $DB_PATH."
"""


_REFRESH_SH = """\
#!/usr/bin/env bash
# Refresh the OLAP analytical database from study.db.
set -euo pipefail

DB_PATH="${1:-study.db}"
OLAP_PATH="${2:-analytics.duckdb}"

quickq refresh "$DB_PATH" "$OLAP_PATH"
echo "Refreshed $OLAP_PATH from $DB_PATH."
"""


_LIBRARY_README = """\
# Custom Library Extensions

This directory is for site-specific question banks that extend the
built-in quickq library. Drop YAML files here defining shared questions
or option_sets; reference them in `instrument.yaml` via
`library: <link_id>`.

Empty in a fresh study; populate as your study evolves and you find
yourself reusing the same questions across multiple instruments.

See the [library reference](https://quickq-io.github.io/quickq/reference/example-phq9/) for examples of library question definitions.
"""


_DOCS_README = """\
# Study Documentation

This directory is for study-specific human-readable documentation:

- Protocol summary
- IRB packet / consent forms
- Standard Operating Procedures (SOPs)
- Variable codebooks beyond what `quickq data-dict` produces
- Anything else your team or collaborators would benefit from having
  alongside the instrument source

Empty in a fresh study; add documents as your study takes shape.
"""


def scaffold(
    target: str | Path,
    *,
    name: str | None = None,
    from_yaml: str | Path | None = None,
    init_git: bool = True,
) -> ScaffoldResult:
    """
    Scaffold a quickq study repository at `target`.

    Args:
        target: Directory path to create. Must not already exist.
        name: Human-readable study name. Defaults to a Title-Cased version of
            the target directory's basename.
        from_yaml: If provided, copy this YAML file into the new repo as
            `instrument.yaml` instead of using the blank starter template.
        init_git: If True (default), run `git init` in the new directory
            after scaffolding.

    Returns:
        ScaffoldResult with the list of files created and whether git was
        initialized.

    Raises:
        ScaffoldError: if `target` exists or `from_yaml` is given but missing.
    """
    target = Path(target)
    if target.exists():
        raise ScaffoldError(
            f"{target} already exists. Choose a different path or remove the existing directory."
        )

    if from_yaml is not None:
        from_yaml = Path(from_yaml)
        if not from_yaml.exists():
            raise ScaffoldError(f"--from yaml file does not exist: {from_yaml}")

    # Derive a default name from the directory basename if not provided.
    slug = target.name
    display_name = name or _slug_to_name(slug)

    target.mkdir(parents=True)
    files_created: list[str] = []

    def _write(rel: str, content: str, *, executable: bool = False) -> None:
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if executable:
            path.chmod(0o755)
        files_created.append(rel)

    # README + .gitignore + instrument.yaml at root
    _write("README.md", _README.format(name=display_name))
    _write(".gitignore", _GITIGNORE)

    if from_yaml is not None:
        # Use the inherited YAML verbatim
        instrument_path = target / "instrument.yaml"
        instrument_path.write_text(from_yaml.read_text())
        files_created.append("instrument.yaml")
    else:
        _write(
            "instrument.yaml",
            _INSTRUMENT_YAML.format(name=display_name, slug=slug),
        )

    # scripts/
    _write("scripts/load.sh", _LOAD_SH, executable=True)
    _write("scripts/seed.sh", _SEED_SH, executable=True)
    _write("scripts/refresh.sh", _REFRESH_SH, executable=True)

    # library/ and docs/ as populated-with-README directories
    _write("library/README.md", _LIBRARY_README)
    _write("docs/README.md", _DOCS_README)

    # git init (best-effort; not fatal if git is missing)
    git_initialized = False
    if init_git and shutil.which("git") is not None:
        result = subprocess.run(
            ["git", "init", "--quiet"],
            cwd=target,
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            git_initialized = True

    return ScaffoldResult(
        target=target,
        files_created=files_created,
        git_initialized=git_initialized,
    )


def _slug_to_name(slug: str) -> str:
    """Title-case a slug for the human-readable display name.

    'gout-study'    -> 'Gout Study'
    'phq9_baseline' -> 'Phq9 Baseline'
    """
    parts = slug.replace("_", "-").split("-")
    return " ".join(p.capitalize() for p in parts if p)
