# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Categorized HITL ASK copy: title / summary for permission dialogs.

User-visible text is title + summary (+ remember hint). Internal rule ids stay
out of the message body. Titles name the matched risk; shell file IO summaries
use ``write`` / ``read`` / ``exec`` plus the path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.permission_engine.models import PermissionResult
from openjiuwen.harness.security.permission_engine.toolguard.tool_categories import (
    is_shell_tool,
    shell_tools_from_config,
)

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

_RULE_RISK_LABELS = {
    "shell_data_exfiltration": "文件外发",
    "shell_download_and_execute": "下载并执行",
    "shell_obfuscated_or_dynamic_execution": "动态或编码执行",
    "shell_reverse_shell_or_bind_shell": "反向或绑定 shell",
    "shell_privilege_escalation": "提权",
    "shell_fs_recursive_or_forced_delete": "递归或强制删除",
    "shell_ps_recursive_or_forced_delete": "递归或强制删除",
    "shell_registry_delete": "注册表删除",
    "shell_disk_partition_or_raw_device_write": "磁盘分区或裸设备写入",
    "shell_remote_execution_or_lateral_movement": "远程执行或横向移动",
    "shell_fork_bomb_or_resource_abuse": "资源滥用",
    "shell_system_shutdown_or_reboot": "关机或重启",
    "shell_chmod_world_writable": "权限放宽为全局可写",
    "shell_ld_preload_hijack": "LD_PRELOAD 劫持",
    "shell_clear_audit_history": "清除审计记录",
    "shell_disable_firewall": "关闭防火墙",
    "shell_docker_privileged": "Docker 特权运行",
}

_RULE_ID_RE = re.compile(r"(?:builtin|rules)\[([^\]]+)\]")
_FILE_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9]{1,15}$")
_ESCALATING = frozenset({"MEDIUM", "HIGH", "CRITICAL"})
_ACTION_RANK = {"write": 0, "exec": 1, "read": 2}


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
    permission_config: Mapping[str, Any] | None = None,
) -> PermissionAskPresentation:
    args = tool_args if isinstance(tool_args, dict) else {}
    name = (tool_name or "").strip() or "tool"
    category = _resolve_category(name, args, result, permission_config)
    title = _risk_title(result, category)
    summary = _summary_for_category(category, name, args, result, permission_config)

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


def _has_structure_complexity_rule(result: PermissionResult) -> bool:
    rule = (result.matched_rule or "").strip()
    return "too_complex" in rule or "parse_unavailable" in rule


def _command_line_summary(tool_name: str, tool_args: dict[str, Any]) -> str:
    from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
        canonicalize_shell_command_for_permission,
    )

    cmd = canonicalize_shell_command_for_permission(_command_text(tool_args))
    if cmd:
        return f"{tool_name}: {cmd}"
    return tool_name


def _summary_for_category(
    category: str,
    name: str,
    args: dict[str, Any],
    result: PermissionResult,
    permission_config: Mapping[str, Any] | None,
) -> str:
    if _has_structure_complexity_rule(result):
        return _command_line_summary(name, args)
    if category == "path":
        return _path_summary(name, args, result, permission_config)
    if category == "network":
        return _network_summary(args) or name
    if category == "finding":
        return (
            _shell_file_access_summary(
                name, args, result, permission_config, require_file_io=True,
            )
            or _finding_summary(name, args, result)
        )
    if category == "shell":
        return _shell_summary(name, args, result, permission_config)
    if category == "tool":
        return f"{name}（当前模式默认需确认）"
    return name


def _risk_title(result: PermissionResult, category: str) -> str:
    rule = (result.matched_rule or "").strip()
    for rid in _RULE_ID_RE.findall(rule):
        label = _RULE_RISK_LABELS.get(rid)
        if label:
            return f"检测到{label}，需要确认后才能执行"
    if "interpreter_sink" in rule:
        return "检测到管道汇入解释器，需要确认后才能执行"
    if "too_complex" in rule or "parse_unavailable" in rule:
        return "检测到命令结构过复杂，需要确认后才能执行"
    finding = _finding_risk_label(result)
    if finding:
        return f"检测到{finding}，需要确认后才能执行"
    if category == "path":
        return "检测到受保护的文件路径访问，需要确认后才能执行"
    if category == "network":
        return "检测到需确认的网络访问，需要确认后才能执行"
    if category == "finding":
        return "检测到风险命令结构，需要确认后才能执行"
    if category == "shell":
        return "检测到需确认的命令执行，需要确认后才能执行"
    if category == "tool":
        return "工具需要授权后才能使用"
    return "操作需要授权"


def _finding_risk_label(result: PermissionResult) -> str:
    for item in getattr(result, "findings", None) or []:
        sev = str(getattr(item, "severity", "") or "").strip().upper()
        if sev not in _ESCALATING:
            continue
        reason = str(getattr(item, "reason", "") or "").strip()
        label = _FINDING_LABELS.get(reason)
        if label:
            return label
    return ""


def _resolve_category(
    tool_name: str,
    tool_args: dict[str, Any],
    result: PermissionResult,
    permission_config: Mapping[str, Any] | None = None,
) -> str:
    rule = (result.matched_rule or "").strip()
    parts = [p.strip() for p in rule.split("|") if p.strip()] if rule else []

    if any("file_guard" in p for p in parts) or rule.startswith("file_guard"):
        return "path"
    if _is_network_category(rule, parts, tool_name):
        return "network"
    if _has_escalating_findings(getattr(result, "findings", None)):
        return "finding"
    if is_shell_tool(tool_name, shell_tools_from_config(permission_config)) and _command_text(tool_args):
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


def _workdir(tool_args: dict[str, Any]) -> Path:
    raw = tool_args.get("workdir")
    if isinstance(raw, str) and raw.strip():
        try:
            return Path(raw).resolve()
        except (OSError, RuntimeError):
            pass
    return Path(".").resolve()


def _path_summary(
    tool_name: str,
    tool_args: dict[str, Any],
    result: PermissionResult,
    permission_config: Mapping[str, Any] | None = None,
) -> str:
    if is_shell_tool(tool_name, shell_tools_from_config(permission_config)):
        extracted = _shell_file_access_summary(
            tool_name, tool_args, result, permission_config,
        )
        if extracted:
            return extracted
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


def _shell_summary(
    tool_name: str,
    tool_args: dict[str, Any],
    result: PermissionResult,
    permission_config: Mapping[str, Any] | None = None,
) -> str:
    extracted = _shell_file_access_summary(
        tool_name, tool_args, result, permission_config, require_file_io=True,
    )
    if extracted:
        return extracted
    return _command_line_summary(tool_name, tool_args)


def _shell_file_access_summary(
    tool_name: str,
    tool_args: dict[str, Any],
    result: PermissionResult,
    permission_config: Mapping[str, Any] | None = None,
    *,
    require_file_io: bool = False,
) -> str:
    if not is_shell_tool(tool_name, shell_tools_from_config(permission_config)):
        return ""
    cmd = _command_text(tool_args)
    if not cmd:
        return ""
    from openjiuwen.harness.security.permission_engine.fileguard.path_extract import (
        extract_shell_path_accesses,
    )

    try:
        accesses = extract_shell_path_accesses(
            cmd, _workdir(tool_args), include_cd_reads=False,
        )
    except (OSError, RuntimeError, ValueError):
        return ""
    if require_file_io:
        accesses = [
            item for item in accesses if _is_file_io_access(item[0], item[1])
        ]
    if not accesses:
        return ""
    picked = _pick_shell_access(accesses, result.external_paths or [])
    if picked is None:
        return ""
    path, action = picked
    return f"{action} {path}"


def _is_file_io_access(path: Path, action: str) -> bool:
    if action in {"write", "exec"}:
        return True
    if action != "read":
        return False
    text = str(path)
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    if text.startswith("/") and ":\\" in text:
        return False
    if "/" in path.suffix or "\\" in path.suffix:
        return False
    return True


def _looks_like_file_suffix(suffix: str) -> bool:
    """True for ``.txt`` / ``.docx``; false for POSIX artifacts like ``.jiuwenswarm\\agent``."""
    if not suffix or "/" in suffix or "\\" in suffix:
        return False
    return bool(_FILE_SUFFIX_RE.match(suffix))


def _pick_shell_access(
    accesses: list[tuple[Path, str]],
    external_paths: list[str],
) -> tuple[Path, str] | None:
    if not accesses:
        return None
    externals = [_path_key(item) for item in external_paths]
    candidates = accesses
    if externals:
        matched = [
            item for item in accesses if _path_key(item[0]) in externals
        ]
        if not matched:
            matched = [
                item
                for item in accesses
                if any(_same_path(item[0], raw) for raw in external_paths)
            ]
        if matched:
            candidates = matched
    ordered = sorted(
        candidates,
        key=lambda item: _ACTION_RANK.get(item[1], 9),
    )
    return ordered[0]


def _path_key(value: Path | str) -> str:
    text = str(value).replace("\\", "/").rstrip("/").casefold()
    return text


def _same_path(left: Path, right: str) -> bool:
    try:
        return left.resolve() == Path(right).resolve()
    except (OSError, RuntimeError):
        return _path_key(left) == _path_key(right)


def _finding_summary(tool_name: str, tool_args: dict[str, Any], result: PermissionResult) -> str:
    label = _finding_risk_label(result) or "风险命令行为"
    cmd = _command_text(tool_args)
    if cmd:
        return f"{label}: {cmd}"
    return f"{label} ({tool_name})"


__all__ = [
    "PermissionAskPresentation",
    "build_permission_ask_presentation",
    "render_ask_presentation_message",
]
