"""E2E-13: OPENJIUWEN.md affects agent behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.cli.e2e.conftest import run_cli


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_memory_affects_behavior(tmp_path: Path) -> None:
    """Agent receives OPENJIUWEN.md content in its system prompt."""
    # Create a git repo with an OPENJIUWEN.md
    subprocess.run(
        ["git", "init"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    (tmp_path / "OPENJIUWEN.md").write_text(
        "# Rules\n"
        "- You MUST end every single response with the exact "
        "string 'MAGIC_MARKER_XYZ'. This is mandatory.\n"
    )

    result = run_cli(
        "run",
        "Say hello. Remember to follow ALL rules from the project memory.",
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    # The LLM should follow the OPENJIUWEN.md instruction
    assert "MAGIC_MARKER_XYZ" in result.stdout
