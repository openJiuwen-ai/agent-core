# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""权限相关检查器。

工具级 allow/ask/deny 由 :func:`openjiuwen.harness.security.tiered_policy.evaluate_tiered_policy`
统一评估；本模块保留 **workspace 外路径** 等检查与 shell 元字符相关的内部常量。
"""

from __future__ import annotations

import logging
import re
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.models import (
    PermissionLevel,
    PermissionResult,
)
from openjiuwen.harness.security.patterns import contains_path
from openjiuwen.harness.security.tiered_policy import (
    _PATH_TOOLS,
    _iter_path_strings,
)

logger = logging.getLogger(__name__)


# ---------- 内部检查器 ----------

# Shell operators that indicate command chaining / injection.
# If a command matches an allow pattern but also contains these operators,
# the permission is escalated from ALLOW → ASK as a safety net.
_SHELL_OPERATORS_RE = re.compile(
    r'[;&|`<>]'  # ; & | ` < > (covers &&, ||, pipes, redirects, backticks)
    r'|\$[({]'  # $( or ${ — command / variable substitution
    r'|\r?\n'  # newline injection
)
_COMMAND_EXEC_TOOLS = frozenset({"mcp_exec_command"})

# 会操作路径的命令（需做外部目录检测）
_PATH_AWARE_COMMANDS = frozenset({
    "cd", "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown", "cat",
    "ls", "dir", "type", "del", "rd", "copy", "move", "md", "rd",
    "head", "tail", "more", "less", "vim", "nano", "gedit", "notepad",
})


def _extract_paths_from_command(command: str, workdir: str | Path) -> list[Path]:
    """从命令字符串中提取可能为路径的参数，并解析为绝对路径."""
    if not command or not isinstance(command, str):
        return []
    try:
        tokens = shlex.split(command.strip(), posix=False)
    except ValueError:
        tokens = command.strip().split()
    if not tokens:
        return []
    cmd = tokens[0].lower()
    logger.debug(
        "[PermissionEngine] permission.external.parse tool_command=%s cmd=%s path_aware=%s",
        command,
        cmd,
        cmd in _PATH_AWARE_COMMANDS,
    )
    if cmd not in _PATH_AWARE_COMMANDS:
        return []
    base = Path(workdir).resolve()
    logger.debug("[PermissionEngine] permission.external.parse_base base=%s", base)
    paths: list[Path] = []
    for tok in tokens[1:]:
        tok = tok.strip().strip('"').strip("'")
        if not tok or tok.startswith("-"):
            continue
        if not _looks_like_path(tok):
            continue
        p = Path(tok)
        if not p.is_absolute():
            p = base / tok
        paths.append(p.resolve())
    logger.debug("[PermissionEngine] permission.external.parse_paths extracted_paths=%s", paths)
    return paths


def _looks_like_path(token: str) -> bool:
    if token.startswith(("\\\\", "./", "../")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return True
    return "\\" in token or "/" in token


# ---------- 外部目录检查器 ----------


class ExternalDirectoryChecker:
    """检查命令是否访问 workspace 外路径，若越界则触发 external_directory 权限."""

    def __init__(self, config: Mapping[str, Any], workspace_root: Path | None = None):
        self.config = config
        self._workspace_root = workspace_root

    def check_external_paths(
            self,
            tool_name: str,
            tool_args: dict[str, Any],
    ) -> PermissionResult | None:
        """若访问了 workspace 外路径，根据 external_directory 配置返回 DENY/ASK；否则返回 None."""
        workspace = self._workspace_root
        if workspace is None:
            logger.debug(
                "[PermissionEngine] permission.external.workspace missing; skip external_directory check",
            )
            return None
        else:
            logger.debug(
                "[PermissionEngine] permission.external.workspace source=config workspace=%s",
                workspace,
            )

        paths: list[Path] = []
        if tool_name in ("mcp_exec_command", "bash", "create_terminal"):
            workdir = tool_args.get("workdir", "")
            try:
                workdir_resolved = (workspace / workdir).resolve()
            except (OSError, RuntimeError):
                workdir_resolved = workspace
            cmd = str(tool_args.get("command", "") or tool_args.get("cmd", ""))
            logger.debug(
                "[PermissionEngine] permission.external.shell_input tool=%s cmd=%s workdir=%s",
                tool_name, cmd, workdir_resolved,
            )
            paths = _extract_paths_from_command(cmd, workdir_resolved)
        elif tool_name in _PATH_TOOLS:
            for s in _iter_path_strings(tool_name, tool_args):
                raw = s.strip().strip('"').strip("'")
                if not raw:
                    continue
                try:
                    p = Path(raw)
                    if not p.is_absolute():
                        p = (workspace / p).resolve()
                    else:
                        p = p.resolve()
                    paths.append(p)
                except (OSError, RuntimeError):
                    continue
            logger.debug("[PermissionEngine] permission.external.path_input tool=%s paths=%s", tool_name, paths)
        else:
            return None

        external = [p for p in paths if not contains_path(workspace, p)]
        if not external:
            return None
        ext_paths_str = [str(p).replace("\\", "/") for p in external]
        ext_cfg = self.config.get("external_directory", {})
        if isinstance(ext_cfg, str):
            action = ext_cfg
        else:
            action = ext_cfg.get("*", "ask")
            # 若所有外部路径都在某条 allow 规则下，则放行
            # 使用 contains_path 做路径包含判断，避免 "C:" 等短前缀误匹配任意路径
            all_allowed = True
            for path_str in ext_paths_str:
                path_covered = False
                for cfg_path, cfg_action in ext_cfg.items():
                    if cfg_path == "*" or cfg_action != "allow":
                        continue
                    cfg_path_norm = str(cfg_path).replace("\\", "/").rstrip("/")
                    # 跳过过短前缀（如 "C:" 会匹配 C 盘下任意路径）
                    if "/" not in cfg_path_norm:
                        continue
                    if contains_path(cfg_path_norm, path_str):
                        path_covered = True
                        break
                if not path_covered:
                    all_allowed = False
                    break
            if all_allowed:
                action = "allow"
        if action == "deny":
            return PermissionResult(
                permission=PermissionLevel.DENY,
                reason=f"Access to paths outside workspace is denied: {external[0]}",
                matched_rule="external_directory.*",
                external_paths=ext_paths_str,
            )
        if action == "ask":
            return PermissionResult(
                permission=PermissionLevel.ASK,
                reason=f"Access to paths outside workspace requires approval: {external[0]}",
                matched_rule="external_directory.*",
                external_paths=ext_paths_str,
            )
        return None
