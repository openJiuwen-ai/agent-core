# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Categorized HITL ASK copy: title / summary for permission dialogs.

User-visible text is title + summary (+ remember hint). Internal rule ids stay
out of the message body. Titles name the matched risk. Command matches show the
command; file_guard matches use ``write`` / ``read`` / ``exec`` plus the path.

The wording itself comes from a :class:`PermissionPromptTexts`, which the host
supplies through ``ToolPermissionHost.prompt_texts``; the defaults reproduce the
wording this module used to inline. Only the categorization and the composition
live here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.permission_engine.models import PermissionResult
from openjiuwen.harness.security.permission_engine.prompt_texts import (
    DEFAULT_PERMISSION_PROMPT_TEXTS,
    PermissionPromptTexts,
)
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


@dataclass(frozen=True)
class _AskContext:
    """The two host-supplied inputs the composition needs, carried as one."""

    permission_config: Mapping[str, Any] | None
    texts: PermissionPromptTexts


def build_permission_ask_presentation(
    tool_name: str,
    tool_args: dict[str, Any] | None,
    result: PermissionResult,
    permission_config: Mapping[str, Any] | None = None,
    *,
    texts: PermissionPromptTexts | None = None,
) -> PermissionAskPresentation:
    ctx = _AskContext(
        permission_config=permission_config,
        texts=texts or DEFAULT_PERMISSION_PROMPT_TEXTS,
    )
    args = tool_args if isinstance(tool_args, dict) else {}
    name = (tool_name or "").strip() or "tool"
    category = _resolve_category(name, args, result, permission_config)
    title = _risk_title(result, category, ctx.texts)
    summary = _summary_for_category(category, name, args, result, ctx)

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
    ctx: _AskContext,
) -> str:
    if _has_structure_complexity_rule(result):
        return _command_line_summary(name, args)
    if category == "path":
        return _path_summary(name, args, result, ctx.permission_config)
    if category == "network":
        return _network_summary(args) or name
    if category == "finding":
        return (
            _shell_file_access_summary(
                name, args, result, ctx.permission_config, require_file_io=True,
            )
            or _finding_summary(name, args, result, ctx.texts)
        )
    if category == "shell":
        return _command_line_summary(name, args)
    if category == "tool":
        return ctx.texts.summary_tool.format(tool_name=name)
    return name


def _risk_title(
    result: PermissionResult,
    category: str,
    texts: PermissionPromptTexts,
) -> str:
    risk = _risk_name(result, category, texts)
    if risk:
        return texts.title_risk_detected.format(risk=risk)
    if category == "tool":
        return texts.title_tool
    return texts.title_generic


def _risk_name(
    result: PermissionResult,
    category: str,
    texts: PermissionPromptTexts,
) -> str:
    """Name the risk a title reports, or "" when no category names one."""
    rule = (result.matched_rule or "").strip()
    for rid in _RULE_ID_RE.findall(rule):
        label = texts.rule_risk_labels.get(rid)
        if label:
            return label
    if "interpreter_sink" in rule:
        return texts.risk_interpreter_sink
    if "too_complex" in rule or "parse_unavailable" in rule:
        return texts.finding_shell_too_complex
    finding = _finding_risk_label(result, texts)
    if finding:
        return finding
    return {
        "path": texts.risk_path,
        "network": texts.risk_network,
        "finding": texts.risk_finding,
        "shell": texts.risk_shell,
    }.get(category, "")


def _finding_risk_label(result: PermissionResult, texts: PermissionPromptTexts) -> str:
    for item in getattr(result, "findings", None) or []:
        sev = str(getattr(item, "severity", "") or "").strip().upper()
        if sev not in _ESCALATING:
            continue
        reason = str(getattr(item, "reason", "") or "").strip()
        label = _finding_label(texts, reason)
        if label:
            return label
    return ""


def _finding_label(texts: PermissionPromptTexts, reason: str) -> str:
    """The named risk for a finding reason, or "" for one with no name of its own."""
    return {
        "download_and_execute": texts.finding_download_and_execute,
        "dynamic_or_encoded_execution": texts.finding_dynamic_or_encoded_execution,
        "shell_risky_structure": texts.finding_shell_risky_structure,
        "shell_too_complex": texts.finding_shell_too_complex,
    }.get(reason, "")


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


def _finding_summary(
    tool_name: str,
    tool_args: dict[str, Any],
    result: PermissionResult,
    texts: PermissionPromptTexts,
) -> str:
    label = _finding_risk_label(result, texts) or texts.finding_other
    cmd = _command_text(tool_args)
    if cmd:
        return f"{label}: {cmd}"
    return f"{label} ({tool_name})"


__all__ = [
    "PermissionAskPresentation",
    "build_permission_ask_presentation",
    "render_ask_presentation_message",
]
