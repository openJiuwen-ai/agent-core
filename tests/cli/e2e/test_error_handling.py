"""E2E-14: API error handling."""

from __future__ import annotations

import os
import tempfile

import pytest

from tests.cli.e2e.conftest import E2E_ENV, run_cli


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_invalid_api_key() -> None:
    """Invalid API key produces empty output or error message."""
    # Use isolated env so CWD/.env and ~/.openjiuwen/.env don't interfere
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("OPENJIUWEN_")
    }
    env["PYTHONPATH"] = E2E_ENV.get("PYTHONPATH", "")
    env["OPENJIUWEN_API_KEY"] = "invalid_key_xyz"
    env["OPENJIUWEN_API_BASE"] = E2E_ENV["OPENJIUWEN_API_BASE"]
    env["OPENJIUWEN_MODEL"] = E2E_ENV["OPENJIUWEN_MODEL"]
    env["OPENJIUWEN_PROVIDER"] = E2E_ENV["OPENJIUWEN_PROVIDER"]
    env["HOME"] = "/tmp/openjiuwen_test_invalid_key"
    with tempfile.TemporaryDirectory() as tmpdir:
        result = run_cli("run", "hello", env=env, cwd=tmpdir)
    # With invalid key the SDK either:
    #   - returns empty stdout (error swallowed internally), or
    #   - surfaces an error keyword in output
    combined = (result.stderr + result.stdout).lower()
    has_error_kw = any(
        kw in combined
        for kw in [
            "401", "unauthorized", "error",
            "failed", "no output",
        ]
    )
    has_empty_output = len(result.stdout.strip()) == 0
    assert has_error_kw or has_empty_output, (
        f"Expected error or empty output, got: {result.stdout[:200]}"
    )


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_no_api_key() -> None:
    """Missing API key -> non-zero exit, helpful message."""
    # Build a clean env: no OPENJIUWEN_* vars, redirect HOME,
    # and use a temp cwd so CWD/.env is not found either.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("OPENJIUWEN_")
    }
    env["PYTHONPATH"] = E2E_ENV.get("PYTHONPATH", "")
    env["HOME"] = "/tmp/openjiuwen_test_no_key"
    with tempfile.TemporaryDirectory() as tmpdir:
        result = run_cli(
            "run", "hello", env=env, timeout=15, cwd=tmpdir
        )
    assert result.returncode != 0
    combined = (result.stderr + result.stdout).lower()
    assert "api key" in combined
