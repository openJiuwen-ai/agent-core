# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async web tools package: free search, paid search, and webpage fetch.

A fully asynchronous (aiohttp-based) reimplementation of the synchronous
``openjiuwen.harness.tools.web_tools`` module, kept self-contained so it does
not depend on the original module's private helpers.
"""

from __future__ import annotations

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.tools.web._common import is_free_search_enabled, is_paid_search_enabled
from openjiuwen.harness.tools.web.fetch_webpage import WebFetchWebpageTool
from openjiuwen.harness.tools.web.free_search import WebFreeSearchTool
from openjiuwen.harness.tools.web.paid_search import WebPaidSearchTool


def create_web_tools(
    *,
    language: str = "cn",
    agent_id: str | None = None,
    include_free_search: bool = True,
    include_paid_search: bool = True,
    include_fetch_webpage: bool = True,
) -> list[Tool]:
    """Create web tools, preferring configured paid search over free search."""
    tools: list[Tool] = []
    if include_paid_search and is_paid_search_enabled():
        tools.append(WebPaidSearchTool(language=language, agent_id=agent_id))
    if include_free_search and is_free_search_enabled():
        tools.append(WebFreeSearchTool(language=language, agent_id=agent_id))
    if include_fetch_webpage:
        tools.append(WebFetchWebpageTool(language=language, agent_id=agent_id))
    return tools


__all__ = [
    "WebFetchWebpageTool",
    "WebFreeSearchTool",
    "WebPaidSearchTool",
    "create_web_tools",
    "is_free_search_enabled",
    "is_paid_search_enabled",
]
