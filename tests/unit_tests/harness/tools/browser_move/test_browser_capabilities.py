# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for browser capability resolution."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.harness.subagents.browser_agent import create_browser_agent
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    ADVANCED_CODE_BROWSER_TOOL_NAMES,
    CORE_BROWSER_TOOL_NAMES,
    DEVTOOLS_BROWSER_TOOL_NAMES,
    NETWORK_BROWSER_TOOL_NAMES,
    PDF_BROWSER_TOOL_NAMES,
    UNSAFE_DEV_BROWSER_TOOL_NAMES,
    VISION_BROWSER_TOOL_NAMES,
    resolve_browser_capabilities,
)


def test_core_only_selection_exposes_exactly_core_tools() -> None:
    resolved = resolve_browser_capabilities([])

    assert resolved.requested_names == ()
    assert resolved.selected_names == ("core",)
    assert resolved.rejected_names == ()
    assert resolved.allowed_tool_names == CORE_BROWSER_TOOL_NAMES
    assert len(CORE_BROWSER_TOOL_NAMES) == 19
    assert "browser_run_code" not in CORE_BROWSER_TOOL_NAMES
    assert "browser_run_code_unsafe" not in CORE_BROWSER_TOOL_NAMES
    assert "browser_console_messages" not in CORE_BROWSER_TOOL_NAMES
    assert "browser_network_requests" not in CORE_BROWSER_TOOL_NAMES
    assert "browser_resize" not in CORE_BROWSER_TOOL_NAMES


def test_diagnostic_tools_are_available_through_existing_optional_categories() -> None:
    assert "browser_console_messages" in DEVTOOLS_BROWSER_TOOL_NAMES
    assert "browser_resize" in DEVTOOLS_BROWSER_TOOL_NAMES
    assert "browser_network_request" in NETWORK_BROWSER_TOOL_NAMES
    assert "browser_network_requests" in NETWORK_BROWSER_TOOL_NAMES


def test_advanced_code_exposes_only_safe_run_code_variant() -> None:
    resolved = resolve_browser_capabilities(["advanced_code"])

    assert resolved.selected_names == ("core", "advanced_code")
    assert resolved.allowed_tool_names == (
        CORE_BROWSER_TOOL_NAMES + ADVANCED_CODE_BROWSER_TOOL_NAMES
    )
    assert "browser_run_code" in resolved.allowed_tool_names
    assert "browser_run_code_unsafe" not in resolved.allowed_tool_names


def test_unsafe_dev_replaces_advanced_code_when_both_are_requested() -> None:
    resolved = resolve_browser_capabilities(["advanced_code", "unsafe_dev"])

    assert resolved.requested_names == ("advanced_code", "unsafe_dev")
    assert resolved.selected_names == ("core", "unsafe_dev")
    assert resolved.allowed_tool_names == (
        CORE_BROWSER_TOOL_NAMES + UNSAFE_DEV_BROWSER_TOOL_NAMES
    )
    assert "browser_run_code" not in resolved.allowed_tool_names
    assert "browser_run_code_unsafe" in resolved.allowed_tool_names


def test_pdf_selection_adds_pdf_tools_to_core() -> None:
    resolved = resolve_browser_capabilities(["pdf"])

    assert resolved.selected_names == ("core", "pdf")
    assert resolved.allowed_tool_names == CORE_BROWSER_TOOL_NAMES + PDF_BROWSER_TOOL_NAMES
    assert not set(VISION_BROWSER_TOOL_NAMES).intersection(resolved.allowed_tool_names)


def test_multiple_categories_preserve_requested_order() -> None:
    resolved = resolve_browser_capabilities(["vision", "pdf"])

    assert resolved.requested_names == ("vision", "pdf")
    assert resolved.selected_names == ("core", "vision", "pdf")
    assert resolved.allowed_tool_names == (
        CORE_BROWSER_TOOL_NAMES + VISION_BROWSER_TOOL_NAMES + PDF_BROWSER_TOOL_NAMES
    )


def test_duplicate_categories_are_deduplicated_stably() -> None:
    resolved = resolve_browser_capabilities(["pdf", "pdf", "vision", "pdf"])

    assert resolved.requested_names == ("pdf", "vision")
    assert resolved.selected_names == ("core", "pdf", "vision")
    assert len(resolved.allowed_tool_names) == len(set(resolved.allowed_tool_names))


def test_unknown_category_is_rejected_by_resolver_and_browser_factory() -> None:
    resolved = resolve_browser_capabilities(["unknown", "pdf"])

    assert resolved.selected_names == ("core", "pdf")
    assert resolved.rejected_names == ("unknown",)

    with pytest.raises(ValueError, match="Unsupported browser capabilities: unknown"):
        create_browser_agent(
            MagicMock(spec=Model),
            browser_capabilities=["unknown"],
        )
