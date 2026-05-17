# coding: utf-8
"""Packaging metadata regression tests for the OpenJiuWen CLI."""

from __future__ import annotations

import tomllib
from pathlib import Path


def _load_pyproject() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_cli_project_has_build_system() -> None:
    """Editable installs need explicit build metadata for CLI scripts."""
    pyproject = _load_pyproject()

    assert pyproject["build-system"] == {
        "requires": ["setuptools>=61"],
        "build-backend": "setuptools.build_meta",
    }


def test_cli_console_script_points_to_harness_cli() -> None:
    """The published openjiuwen command should launch harness CLI."""
    pyproject = _load_pyproject()

    assert pyproject["project"]["scripts"]["openjiuwen"] == (
        "openjiuwen.harness.cli.cli:cli"
    )
