# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Categorized HITL ASK copy: title / summary for permission dialogs.

User-visible text is title + summary (+ remember hint). Internal rule ids stay
out of the message body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen.harness.security.permission_engine.models import PermissionResult

_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal", "powershell"})
_FETCH_TOOLS = frozenset({"mcp_fetch_webpage", "fetch_webpage", "web_fetch_webpage"})

_PATH_ACTION = {
    "write_file": "write",
    "write_text_file": "write",
    "write": "write",
    "edit_file": "edit",
    "search_replace": "edit",
    "read_file": "read",
    "read_text_file": "read",
    "read": "read",
    "list_dir": "list",
    "list_files": "list",
    "glob_file_search": "list",
    "glob": "list",
}

_PATH_ARG_KEYS = (
    "path",
    "file_path",
    "target_file",
    "file",
    "old_path",
    "new_path",
    "source_path",
    "dest_path",
    "directory",
    "dir",
)

_FINDING_LABELS = {
    "download_and_execute": "下载并执行",
    "dynamic_or_encoded_execution": "动态或编码执行",
    "shell_risky_structure": "含重定向或命令替换等结构",
    "shell_too_complex": "命令结构过复杂",
}

_ESCALATING = frozenset({"MEDIUM", "HIGH", "CRITICAL"})


@dataclass(frozen=True)
class PermissionAskPresentation:
    category: str
    title: str
    summary: str
    details: str = ""


def build_permission_ask_presentation(
    tool_name: str,
    tool_args: dict[str, Any] | None,
    result: PermissionResult,
) -> PermissionAskPresentation:
    args = tool_args if isinstance(tool_args, dict) else {}
    name = (tool_name or "").strip() or "tool"
    category = _resolve_category(name, args, result)

    if category == "path":
        title = "检测到受保护的文件路径访问"
        summary = _path_summary(name, args, result)
    elif category == "network":
        title = "检测到需确认的网络访问"
        summary = _network_summary(args) or name
    elif category == "finding":
        title = "检测到风险命令结构"
        summary = _finding_summary(name, args, result)
    elif category == "shell":
        title = "检测到需确认的命令执行"
        summary = _shell_summary(name, args)
    elif category == "tool":
        title = "工具需要授权后才能使用"
        summary = f"{name}（当前模式默认需确认）"
    else:
        title = "操作需要授权"
        summary = name

    return PermissionAskPresentation(
        category=category,
        title=title,
        summary=summary,
        details="",
    )


def render_ask_presentation_message(
    presentation: PermissionAskPresentation,
    *,
    always_allow_hint: str = "",
) -> str:
    """Message body: summary first (collapsed UI), then optional remember hint."""
    parts = [presentation.summary.strip(), ""]
    if presentation.details.strip():
        parts.append(presentation.details.strip())
    hint = (always_allow_hint or "").strip()
    if hint:
        parts.append("")
        parts.append(hint)
    return "\n".join(parts).rstrip() + "\n"


def _resolve_category(
    tool_name: str,
    tool_args: dict[str, Any],
    result: PermissionResult,
) -> str:
    rule = (result.matched_rule or "").strip()
    parts = [p.strip() for p in rule.split("|") if p.strip()] if rule else []

    if any("file_guard" in p for p in parts) or rule.startswith("file_guard"):
        return "path"
    if _is_network_category(rule, parts, tool_name):
        return "network"
    if _has_escalating_findings(getattr(result, "findings", None)):
        return "finding"
    if tool_name in _SHELL_TOOLS and _command_text(tool_args):
        return "shell"
    if parts or rule:
        return "tool"
    return "generic"


def _is_network_category(rule: str, parts: list[str], tool_name: str) -> bool:
    if any("net_guard" in p for p in parts):
        return True
    if any("network_guard" in p for p in parts):
        return True
    if rule.startswith("net_guard") or rule.startswith("network_guard"):
        return True
    return tool_name in _FETCH_TOOLS


def _has_escalating_findings(findings: list[Any] | None) -> bool:
    if not findings:
        return False
    for item in findings:
        sev = str(getattr(item, "severity", "") or "").strip().upper()
        if sev in _ESCALATING:
            return True
    return False


def _command_text(tool_args: dict[str, Any]) -> str:
    return str(tool_args.get("command", "") or tool_args.get("cmd", "") or "")


def _path_summary(tool_name: str, tool_args: dict[str, Any], result: PermissionResult) -> str:
    action = _PATH_ACTION.get(tool_name, tool_name)
    path = ""
    external = result.external_paths or []
    if external:
        path = str(external[0])
    if not path:
        path = _first_path_arg(tool_args)
    if path:
        return f"{action} {path}"
    return action


def _first_path_arg(tool_args: dict[str, Any]) -> str:
    for key in _PATH_ARG_KEYS:
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    for val in tool_args.values():
        if not isinstance(val, str) or not val.strip():
            continue
        if "/" in val or "\\" in val:
            return val.strip()
    return ""


def _network_summary(tool_args: dict[str, Any]) -> str:
    from openjiuwen.harness.security.permission_engine.netguard.net_guard import extract_fetch_url

    url = extract_fetch_url(tool_args)
    if url:
        return url
    for key in ("url", "uri", "host", "endpoint"):
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _shell_summary(tool_name: str, tool_args: dict[str, Any]) -> str:
    from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
        canonicalize_shell_command_for_permission,
    )

    cmd = canonicalize_shell_command_for_permission(_command_text(tool_args))
    if cmd:
        return f"{tool_name}: {cmd}"
    return tool_name


def _finding_summary(tool_name: str, tool_args: dict[str, Any], result: PermissionResult) -> str:
    label = "风险命令行为"
    for item in getattr(result, "findings", None) or []:
        sev = str(getattr(item, "severity", "") or "").strip().upper()
        if sev not in _ESCALATING:
            continue
        reason = str(getattr(item, "reason", "") or "").strip()
        label = _FINDING_LABELS.get(reason, label)
        break
    cmd = _command_text(tool_args)
    if cmd:
        return f"{label}: {cmd}"
    return f"{label} ({tool_name})"


__all__ = [
    "PermissionAskPresentation",
    "build_permission_ask_presentation",
    "render_ask_presentation_message",
]
