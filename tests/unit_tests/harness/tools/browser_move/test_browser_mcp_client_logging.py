#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_summarizer():
    repo_root = Path(__file__).resolve().parents[5]
    module_path = repo_root / "openjiuwen" / "harness" / "tools" / "browser_move" / "clients" / "logging_utils.py"
    spec = importlib.util.spec_from_file_location("browser_move_client_logging_utils", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.summarize_tool_arguments_for_log


summarize_tool_arguments_for_log = _load_summarizer()


def test_stdio_argument_summary_redacts_browser_run_code_values() -> None:
    summary = summarize_tool_arguments_for_log(
        "browser_run_code_unsafe",
        {
            "code": "async (page) => { await page.fill('#email', 'secret@example.com'); }",
            "timeout_ms": 5000,
        },
    )

    assert summary["kind"] == "code_execution"
    assert summary["code_redacted"] is True
    assert summary["code_length"] > 0
    assert "secret@example.com" not in str(summary)
    assert "page.fill" not in str(summary)


def test_stdio_argument_summary_redacts_batch_step_values() -> None:
    summary = summarize_tool_arguments_for_log(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "fill", "selector": "#firstName", "value": "Logging"},
                {"op": "fill", "selector": "#email", "value": "log.user@example.com"},
                {"op": "click", "label": "Female"},
            ],
            "continue_on_error": True,
        },
    )

    assert summary["kind"] == "browser_batch_interact"
    assert summary["step_count"] == 3
    assert summary["op_counts"] == {"click": 1, "fill": 2}
    assert summary["steps_preview"][0]["value_keys_redacted"] == ["value"]
    assert "Logging" not in str(summary)
    assert "log.user@example.com" not in str(summary)


def test_stdio_argument_summary_sanitizes_navigation_query() -> None:
    summary = summarize_tool_arguments_for_log(
        "mcp_playwright-official_browser_navigate",
        {"url": "https://example.test/form?token=secret", "timeout_ms": 3000},
    )

    assert summary["kind"] == "navigation"
    assert summary["url"] == "https://example.test/form?<redacted>"
    assert "secret" not in str(summary)
