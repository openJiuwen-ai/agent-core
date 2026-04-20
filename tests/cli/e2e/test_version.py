"""E2E-01: ``openjiuwen --version`` outputs version number."""

from __future__ import annotations

import re

from tests.cli.e2e.conftest import run_cli


def test_version() -> None:
    """--version prints the version string and exits cleanly."""
    result = run_cli("--version", timeout=10)
    assert result.returncode == 0
    assert "openjiuwen" in result.stdout.lower()
    assert re.search(r"\d+\.\d+\.\d+", result.stdout)
    assert "Traceback" not in result.stderr
