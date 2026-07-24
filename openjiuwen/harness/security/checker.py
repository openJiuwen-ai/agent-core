# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""权限相关检查器。

工具级 allow/ask/deny 由 :func:`openjiuwen.harness.security.tiered_policy.evaluate_tiered_policy`
统一评估；路径防护由 :mod:`openjiuwen.harness.security.file_guard` 承担。

:class:`ExternalDirectoryChecker` 保留为 deprecated 薄封装，内部转调 FileGuard Legacy 投影。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.file_guard import build_file_guard_checker
from openjiuwen.harness.security.models import PermissionResult

logger = logging.getLogger(__name__)


# Shell operators that indicate command chaining / injection.
# If a command matches an allow pattern but also contains these operators,
# the permission is escalated from ALLOW → ASK as a safety net.
_SHELL_OPERATORS_RE = re.compile(
    r'[;&|`<>]'  # ; & | ` < > (covers &&, ||, pipes, redirects, backticks)
    r'|\$[({]'  # $( or ${ — command / variable substitution
    r'|\r?\n'  # newline injection
)
_COMMAND_EXEC_TOOLS = frozenset({"mcp_exec_command"})


class ExternalDirectoryChecker:
    """Deprecated：检查 workspace 外路径；内部转调 :class:`~file_guard.FileGuardChecker`。

    新代码请直接使用 :func:`openjiuwen.harness.security.file_guard.build_file_guard_checker`
    或 :class:`~file_guard.FileGuardChecker`。
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        workspace_root: Path | None = None,
        trusted_dirs: list[Path] | None = None,
    ):
        self.config = config
        self._workspace_root = workspace_root
        self._trusted_dirs = trusted_dirs or []
        self._checker = build_file_guard_checker(
            config,
            workspace_root=workspace_root,
            trusted_dirs=self._trusted_dirs,
        )

    def check_external_paths(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PermissionResult | None:
        """若路径层命中 ASK/DENY 则返回结果；否则返回 None。"""
        if self._checker is None:
            return None
        return self._checker.evaluate(tool_name, tool_args)
