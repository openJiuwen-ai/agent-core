# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for browser capability resolution."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.harness.subagents.browser_agent import create_browser_agent
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    CORE_BROWSER_TOOL_NAMES,
    PDF_BROWSER_TOOL_NAMES,
    VISION_BROWSER_TOOL_NAMES,
    resolve_browser_capabilities,
)


def test_core_only_selection_exposes_exactly_core_tools() -> None:
    resolved = resolve_browser_capabilities([])

    assert resolved.requested_names == ()
    assert resolved.selected_names == ("core",)
    assert resolved.rejected_names == ()
    assert resolved.allowed_tool_names == CORE_BROWSER_TOOL_NAMES


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
