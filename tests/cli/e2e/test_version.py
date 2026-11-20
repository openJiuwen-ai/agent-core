"""E2E-01: ``openjiuwen --version`` outputs version number."""

from __future__ import annotations

import openjiuwen
from tests.cli.e2e.conftest import run_cli


def test_version() -> None:
    """--version prints the package version and exits cleanly."""
    result = run_cli("--version", timeout=10)
    assert result.returncode == 0
    assert "openjiuwen" in result.stdout.lower()
    # The CLI ships in this distribution: its version must be the installed one.
    assert f"version {openjiuwen.__version__}" in result.stdout
    assert "Traceback" not in result.stderr
