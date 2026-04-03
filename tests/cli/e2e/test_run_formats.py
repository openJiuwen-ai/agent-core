"""E2E-03 / E2E-04: ``--output-format json`` and ``stream-json``."""

from __future__ import annotations

import json

import pytest

from tests.cli.e2e.conftest import run_cli


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_run_json_format() -> None:
    """``-f json`` produces a valid JSON object with result."""
    result = run_cli(
        "run", "-f", "json", "What is 2+2?"
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert "result" in data and len(data["result"]) > 0
    assert isinstance(data["chunks"], int) and data["chunks"] > 0
    assert "model" in data


@pytest.mark.skip(reason="E2E test requires real LLM API credentials")
def test_run_stream_json_format() -> None:
    """``-f stream-json`` outputs valid JSONL lines."""
    result = run_cli(
        "run", "-f", "stream-json", "Say hello"
    )
    assert result.returncode == 0
    lines = [
        line
        for line in result.stdout.strip().split("\n")
        if line.strip()
    ]
    assert len(lines) >= 1

    valid_types = {
        "llm_output",
        "llm_reasoning",
        "answer",
        "message",
        "__interaction__",
        "controller_output",
    }
    has_content = False
    for line in lines:
        data = json.loads(line)
        assert "type" in data
        assert "index" in data
        assert data["type"] in valid_types
        if data["type"] in ("llm_output", "answer"):
            has_content = True
    assert has_content
