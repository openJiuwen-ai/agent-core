"""E2E-05: Pipe mode via stdin."""

from __future__ import annotations

import pytest

from tests.cli.e2e.conftest import run_cli


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_run_pipe_mode() -> None:
    """``openjiuwen run -`` reads prompt from stdin."""
    result = run_cli(
        "run",
        "-",
        input="What is 3+3? Reply with just the number.",
    )
    assert result.returncode == 0
    assert "6" in result.stdout


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_run_auto_stdin_detection() -> None:
    """``openjiuwen run`` with piped stdin auto-detects."""
    result = run_cli(
        "run",
        input="What is 3+3? Reply with just the number.",
    )
    assert result.returncode == 0
    assert "6" in result.stdout
