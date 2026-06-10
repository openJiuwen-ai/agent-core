# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt template loader for skill document optimizer."""

from __future__ import annotations

from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def load_skill_opt_prompt(name: str) -> str:
    """Load a prompt template by name (without .md extension).

    Parameters
    ----------
    name : str
        Template name, e.g. ``"analyst_error"`` or ``"merge_final"``.

    Returns
    -------
    str
        The raw template content.

    Raises
    ------
    FileNotFoundError
        If the template does not exist.
    """
    path = _TEMPLATES_DIR / f"{name}.md"
    if not path.is_file():
        available = sorted(p.stem for p in _TEMPLATES_DIR.glob("*.md"))
        raise FileNotFoundError(
            f"Prompt template '{name}' not found. Available: {available}"
        )
    return path.read_text(encoding="utf-8")
