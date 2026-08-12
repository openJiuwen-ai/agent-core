#!/usr/bin/env python
# coding: utf-8
"""Shared fixtures for BrowserMove tests."""

import pytest

import openjiuwen.core.runner.resources_manager.tool_manager as tool_manager_module
from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr
from openjiuwen.harness.tools.browser_move.playwright_runtime import browser_tools


@pytest.fixture(autouse=True)
def isolated_browser_runtime_client_patch():
    """Restore client factory globals changed by BrowserAgentRuntime."""
    missing = object()
    original_create_client = ToolMgr.__dict__["_create_client"]
    original_stdio_client = getattr(tool_manager_module, "StdioClient", missing)
    original_streamable_client = getattr(tool_manager_module, "StreamableHttpClient", missing)
    original_patched = browser_tools._OPENJIUWEN_CLIENTS_PATCHED
    original_registry = dict(browser_tools._client_registry)

    yield

    ToolMgr._create_client = original_create_client
    for name, original in (
        ("StdioClient", original_stdio_client),
        ("StreamableHttpClient", original_streamable_client),
    ):
        if original is missing:
            if hasattr(tool_manager_module, name):
                delattr(tool_manager_module, name)
        else:
            setattr(tool_manager_module, name, original)
    browser_tools._OPENJIUWEN_CLIENTS_PATCHED = original_patched
    browser_tools._client_registry.clear()
    browser_tools._client_registry.update(original_registry)
