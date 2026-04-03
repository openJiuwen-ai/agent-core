"""E2E-07: Agent tool calls (Bash / File / Grep)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.cli.e2e.conftest import run_cli


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_tool_bash(tmp_path: Path) -> None:
    """Agent executes BashTool and returns output."""
    result = run_cli(
        "run",
        "Run 'echo hello_from_test' and tell me the output",
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "hello_from_test" in result.stdout


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_tool_read_file(tmp_path: Path) -> None:
    """Agent reads a file and returns its content."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("line1\nline2\nline3\n")

    result = run_cli(
        "run",
        f"Read the file {test_file} and show its contents",
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "line1" in result.stdout
    assert "line2" in result.stdout


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_tool_grep(tmp_path: Path) -> None:
    """Agent uses GrepTool to find code patterns."""
    (tmp_path / "code.py").write_text(
        "def hello():\n    return 'world'\n"
    )

    result = run_cli(
        "run",
        "Search for 'def hello' in this directory",
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "code.py" in result.stdout
