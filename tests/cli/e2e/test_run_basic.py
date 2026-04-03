"""E2E-02: Basic non-interactive ``openjiuwen run``."""

from __future__ import annotations

import pytest

from tests.cli.e2e.conftest import run_cli


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_run_basic() -> None:
    """``openjiuwen run "prompt"`` returns a meaningful answer."""
    result = run_cli(
        "run",
        "What is 2+2? Reply with just the number.",
    )
    assert result.returncode == 0
    assert "4" in result.stdout
    assert "Traceback" not in result.stderr
