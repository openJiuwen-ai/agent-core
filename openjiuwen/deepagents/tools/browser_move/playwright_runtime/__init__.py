# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Playwright runtime package bootstrap."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any

# SRC_ROOT is the browser_move/ directory that contains controllers/, utils/, etc.
# Adding it to sys.path makes bare module imports (e.g. `from controllers.base import ...`)
# work both when running standalone (MCP server) and when loaded as part of the
# openjiuwen package.
_HERE = Path(__file__).resolve().parent
SRC_ROOT = _HERE.parent
# Walk up four levels (browser_move -> tools -> deepagents -> openjiuwen -> repo root)
REPO_ROOT = SRC_ROOT.parent.parent.parent.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.append(str(SRC_ROOT))
sys.modules.setdefault("playwright_runtime", sys.modules[__name__])

__all__ = [
    "REPO_ROOT",
    "SRC_ROOT",
    "build_browser_runtime_mcp_config",
    "browser_tools",
    "controller",
    "register_browser_runtime_mcp_server",
    "restart_local_browser_runtime_server",
    "service",
    "stop_local_browser_runtime_server",
]


def __getattr__(name: str) -> Any:
    if name == "controller":
        return import_module("openjiuwen.deepagents.tools.browser_move.controllers")
    if name == "browser_tools":
        return import_module("openjiuwen.deepagents.tools.browser_move.playwright_runtime.browser_tools")
    if name == "service":
        return import_module("openjiuwen.deepagents.tools.browser_move.playwright_runtime.service")
    if name in {
        "build_browser_runtime_mcp_config",
        "register_browser_runtime_mcp_server",
        "restart_local_browser_runtime_server",
        "stop_local_browser_runtime_server",
    }:
        module = import_module("openjiuwen.deepagents.tools.browser_move.playwright_runtime.browser_tools")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
