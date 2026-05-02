"""
Project-level configuration for quickq.

Reads quickq.yml by searching upward from the given directory (default: cwd).
All settings can be overridden per-invocation via CLI flags.

Priority: CLI flag > quickq.yml > built-in default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class AuthoringConfig:
    strict_concepts: bool = True
    auto_concept: bool = False


@dataclass
class RenderConfig:
    format: str = "md"


@dataclass
class DataDictConfig:
    format: str = "markdown"


@dataclass
class QuickqConfig:
    authoring: AuthoringConfig = field(default_factory=AuthoringConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    data_dict: DataDictConfig = field(default_factory=DataDictConfig)


def load_config(search_path: Path | None = None) -> QuickqConfig:
    """
    Search upward from search_path for quickq.yml and return the merged config.
    Returns defaults if no config file is found.
    """
    config_file = _find_config(search_path or Path.cwd())
    if config_file is None:
        return QuickqConfig()
    raw = yaml.safe_load(config_file.read_text()) or {}
    return _parse(raw)


def _find_config(start: Path) -> Path | None:
    for directory in [start, *start.parents]:
        candidate = directory / "quickq.yml"
        if candidate.is_file():
            return candidate
    return None


def _parse(raw: dict) -> QuickqConfig:
    a = raw.get("authoring", {})
    r = raw.get("render", {})
    d = raw.get("data_dict", {})
    return QuickqConfig(
        authoring=AuthoringConfig(
            strict_concepts=bool(a.get("strict_concepts", True)),
            auto_concept=bool(a.get("auto_concept", False)),
        ),
        render=RenderConfig(
            format=str(r.get("format", "md")),
        ),
        data_dict=DataDictConfig(
            format=str(d.get("format", "markdown")),
        ),
    )
