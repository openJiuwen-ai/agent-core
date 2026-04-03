"""Shared constants and helpers for CLI E2E tests.

All E2E tests run real LLM calls and are skipped by default.
Set ``OPENJIUWEN_E2E=1`` to enable them.
"""

from __future__ import annotations

import os
import subprocess
import sys

# ── API configuration ─────────────────────────────��───────────────────
API_KEY = os.getenv("OPENJIUWEN_API_KEY", "")
API_BASE = os.getenv(
    "OPENJIUWEN_API_BASE", "https://api.openai.com/v1"
)
MODEL = os.getenv("OPENJIUWEN_MODEL", "gpt-4o")
PROVIDER = os.getenv("OPENJIUWEN_PROVIDER", "OpenAI")

# Timeout for a single LLM-backed test (seconds)
LLM_TIMEOUT = 120

# Python executable
PYTHON = sys.executable

# Base command for running the CLI via ``python -m``
CLI_CMD = [PYTHON, "-m", "openjiuwen.harness.cli"]

# Project root (for PYTHONPATH when cwd changes)
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

# Environment with API credentials injected
E2E_ENV: dict[str, str] = {
    **os.environ,
    "OPENJIUWEN_API_KEY": API_KEY,
    "OPENJIUWEN_API_BASE": API_BASE,
    "OPENJIUWEN_MODEL": MODEL,
    "OPENJIUWEN_PROVIDER": PROVIDER,
    "PYTHONPATH": PROJECT_ROOT,
}


def run_cli(
    *args: str,
    input: str | None = None,
    timeout: int = LLM_TIMEOUT,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a subprocess and return the result.

    Args:
        *args: Extra CLI arguments (e.g. ``"run"``, ``"--version"``).
        input: stdin content (for pipe mode).
        timeout: Max seconds to wait.
        env: Environment dict (defaults to :data:`E2E_ENV`).
        cwd: Working directory.

    Returns:
        :class:`subprocess.CompletedProcess` with captured stdout/stderr.
    """
    return subprocess.run(
        [*CLI_CMD, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input,
        env=env or E2E_ENV,
        cwd=cwd,
    )
