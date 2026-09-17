# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-injected tool categories for permission matching.

Engine only understands category ``shell``. The concrete tool-name list is
injected by the host (compose) as ``permissions.categories.shell``. When the
host is absent, a package default is used.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_DEFAULT_SHELL_TOOLS = frozenset({
    "bash",
    "powershell",
    "core.powershell",
    "mcp_exec_command",
    "create_terminal",
})
_CATEGORY_SHELL = "shell"

__all__ = [
    "shell_tools_from_config",
    "is_shell_tool",
    "_DEFAULT_SHELL_TOOLS",
    "_CATEGORY_SHELL",
]


def shell_tools_from_config(permission_config: Mapping[str, Any] | None) -> frozenset[str]:
    """Resolve ``categories.shell`` from an effective permissions dict.

    Missing / empty ``categories.shell`` falls back to :data:`_DEFAULT_SHELL_TOOLS`.
    """
    raw = (permission_config or {}).get("categories")
    names = raw.get("shell") if isinstance(raw, dict) else None
    if isinstance(names, list) and any(isinstance(n, str) and n.strip() for n in names):
        return frozenset(n.strip() for n in names if isinstance(n, str) and n.strip())
    return _DEFAULT_SHELL_TOOLS


def is_shell_tool(tool_name: str, shell_tools: Iterable[str] | None = None) -> bool:
    """Whether ``tool_name`` is in the shell-tool set.

    ``shell_tools`` is the resolved name list (typically
    ``shell_tools_from_config(cfg)``). ``None`` uses the package default.
    """
    names = _DEFAULT_SHELL_TOOLS if shell_tools is None else frozenset(shell_tools)
    return (tool_name or "") in names
