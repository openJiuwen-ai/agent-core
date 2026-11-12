# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Merge "always allow" decisions into a ``permissions`` mapping.

These functions are the pure in-memory half of allow-always persistence.  File
I/O (writing the agent YAML) stays in the caller/harness layer.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from agent_shield.tool_permission.toolguard.shell_ast import parse_shell_for_permission

from .models import PermissionLevel
from .suggestions import PermissionSuggestion, build_permission_suggestions

logger = logging.getLogger(__name__)

_SHELL_APPROVAL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal"})


class PermissionsSection(TypedDict, total=False):
    """The subset of the agent ``permissions:`` section used by these helpers."""

    enabled: bool
    schema: str
    defaults: dict[str, Any]
    tools: dict[str, Any]
    rules: list[dict[str, Any]]
    approval_overrides: list[Any]
    file_guard: dict[str, Any]
    external_directory: dict[str, str]


@dataclass(frozen=True)
class _ApprovalOverrideSignature:
    tool_name: str
    tools: list[str]
    match_type: str
    existing_match_type: str | None
    pattern: str
    existing_pattern: str | None
    existing_action: str


def _evaluate_current_permission(
    permissions: PermissionsSection | dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> PermissionLevel:
    """Evaluate the current decision through core's synchronous compatibility path.

    The allow-always merge only proceeds when the current decision is ASK.
    """
    from .tiered_policy import evaluate_tiered_policy

    return evaluate_tiered_policy(permissions, tool_name, tool_args)[0]


def _persist_tiered_approval_override_suggestions(
    permissions: PermissionsSection,
    suggestions: list[PermissionSuggestion],
) -> bool:
    """Write command-class approval overrides; path suggestions are ignored."""
    if not suggestions:
        return False
    overrides = permissions.get("approval_overrides")
    if not isinstance(overrides, list):
        overrides = []
        permissions["approval_overrides"] = overrides

    persisted_any = False
    for suggestion in suggestions:
        if str(suggestion.match_type or "").strip().lower() == "path":
            logger.info(
                "[PermissionEngine] permission.persist.skip reason=path_suggestion_use_file_guard pattern=%s",
                suggestion.pattern,
            )
            continue
        for tool_name in suggestion.tools:
            if _ensure_single_allow_override(
                overrides,
                tool_name=tool_name,
                match_type=suggestion.match_type,
                pattern=suggestion.pattern,
                action=suggestion.action,
            ):
                persisted_any = True
    return persisted_any


def _ensure_single_allow_override(
    overrides: list[Any],
    *,
    tool_name: str,
    match_type: str,
    pattern: str,
    action: str,
) -> bool:
    for existing in overrides:
        if not isinstance(existing, dict):
            continue
        tools = existing.get("tools") or []
        if isinstance(tools, str):
            tools = [tools]
        existing_match_type = existing.get("match_type")
        existing_pattern = existing.get("pattern")
        existing_action = str(existing.get("action") or "").strip().lower()
        signature = _ApprovalOverrideSignature(
            tool_name=tool_name,
            tools=tools,
            match_type=match_type,
            existing_match_type=existing_match_type,
            pattern=pattern,
            existing_pattern=existing_pattern,
            existing_action=existing_action,
        )
        if _is_same_allow_override(signature):
            logger.info(
                "[PermissionEngine] permission.persist.skip tool=%s reason=approval_override_exists "
                "match_type=%s pattern=%s",
                tool_name,
                match_type,
                pattern,
            )
            return True

    overrides.append(
        {
            "id": _build_approval_override_id(tool_name, match_type, pattern),
            "tools": [tool_name],
            "match_type": match_type,
            "pattern": pattern,
            "action": action,
        }
    )
    return True


def _is_same_allow_override(signature: _ApprovalOverrideSignature) -> bool:
    if signature.tool_name not in signature.tools:
        return False
    if signature.existing_match_type != signature.match_type:
        return False
    if signature.existing_pattern != signature.pattern:
        return False
    return signature.existing_action == "allow"


def _build_approval_override_id(tool_name: str, match_type: str, pattern: str) -> str:
    raw = f"user_allow_{tool_name}_{match_type}_{pattern}"
    collapsed = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    if not collapsed:
        return "user_allow_override"
    return collapsed[:120]


def _persist_tiered_tool_allow(
    permissions: PermissionsSection,
    tool_name: str,
) -> bool:
    """Persist whole-tool allow for tools without safe parameter suggestions."""
    if not tool_name:
        return False
    tools = permissions.get("tools")
    if not isinstance(tools, dict):
        tools = {}
        permissions["tools"] = tools
    if tools.get(tool_name) == "allow":
        return False
    tools[tool_name] = "allow"
    return True


def _axes_for_file_guard_action(action: str) -> tuple[str, str, str]:
    """HITL "always allow" writes the axes implied by the current path action."""
    act = (action or "read").strip().lower()
    if act == "write":
        return "allow", "allow", "ask"
    if act == "exec":
        return "allow", "ask", "allow"
    return "allow", "ask", "ask"


def _escalate_axis_toward_allow(old: Any, new: str) -> str:
    """Merge a same-path rule: only raise towards allow, never downgrade."""
    if new == "allow":
        return "allow"
    if old in ("allow", "ask", "deny"):
        return str(old)
    return new


def merge_file_guard_path_rule(
    permissions: PermissionsSection | dict[str, Any],
    path: str,
    *,
    read: str = "allow",
    write: str = "allow",
    exec_: str = "ask",
    match: str = "prefix",
) -> tuple[PermissionsSection, bool]:
    """Merge one ``file_guard.paths`` rule into a copy of ``permissions``.

    Used by ``/add-dir`` and HITL "always allow" path decisions.  ``exec_``
    defaults to ``ask`` (trusting a directory does not imply executing in it).
    An existing same-path rule is raised towards allow per axis so a later
    read-only decision cannot overwrite an earlier write allow.

    Returns:
        ``(merged_permissions, wrote_any)``.
    """
    path_norm = path.replace("\\", "/").rstrip("/")
    if not path_norm:
        return cast(PermissionsSection, deepcopy(permissions)), False

    perms = cast(PermissionsSection, deepcopy(permissions))
    file_guard = perms.get("file_guard")
    if not isinstance(file_guard, dict):
        file_guard = {}
        perms["file_guard"] = file_guard
    file_guard["enabled"] = True
    paths = file_guard.get("paths")
    if not isinstance(paths, list):
        paths = []
        file_guard["paths"] = paths

    for i, existing in enumerate(paths):
        if not isinstance(existing, dict):
            continue
        existing_path = str(existing.get("path") or "").replace("\\", "/").rstrip("/")
        if existing_path != path_norm:
            continue
        merged_read = _escalate_axis_toward_allow(existing.get("read"), read)
        merged_write = _escalate_axis_toward_allow(existing.get("write"), write)
        merged_exec = _escalate_axis_toward_allow(existing.get("exec"), exec_)
        merged_match = existing.get("match", "prefix") or match
        unchanged = (
            existing.get("read") == merged_read
            and existing.get("write") == merged_write
            and existing.get("exec") == merged_exec
            and existing.get("match", "prefix") == merged_match
        )
        if unchanged:
            return cast(PermissionsSection, perms), False
        paths[i] = {
            **existing,
            "path": path_norm,
            "read": merged_read,
            "write": merged_write,
            "exec": merged_exec,
            "match": merged_match,
        }
        logger.info(
            "[PermissionEngine] permission.merge.file_guard path=%s read=%s write=%s exec=%s",
            path_norm,
            merged_read,
            merged_write,
            merged_exec,
        )
        return cast(PermissionsSection, perms), True

    paths.append(
        {
            "path": path_norm,
            "read": read,
            "write": write,
            "exec": exec_,
            "match": match,
        }
    )
    logger.info(
        "[PermissionEngine] permission.merge.file_guard path=%s read=%s write=%s exec=%s",
        path_norm,
        read,
        write,
        exec_,
    )
    return cast(PermissionsSection, perms), True


def merge_file_guard_access_allows(
    permissions: PermissionsSection | dict[str, Any],
    accesses: list[tuple[str, str]],
) -> tuple[PermissionsSection, bool]:
    """Write ``(path, action)`` pairs into ``file_guard.paths``.

    - uses the touched path itself, never its parent directory
    - axes follow the action: ``read`` -> read allow; ``write`` -> read+write
      allow; ``exec`` -> read+exec allow
    """
    if not accesses:
        return cast(PermissionsSection, deepcopy(permissions)), False
    perms = cast(PermissionsSection, deepcopy(permissions))
    wrote = False
    for path_str, action in accesses:
        if not isinstance(path_str, str) or not path_str.strip():
            continue
        path_norm = path_str.replace("\\", "/").rstrip("/")
        if not path_norm:
            continue
        read, write, exec_ = _axes_for_file_guard_action(action)
        perms, did_write = merge_file_guard_path_rule(
            perms,
            path_norm,
            read=read,
            write=write,
            exec_=exec_,
        )
        wrote = wrote or did_write
    return cast(PermissionsSection, perms), wrote


def merge_external_directory_allow_into_permissions(
    permissions: PermissionsSection | dict[str, Any],
    paths: list[str],
    *,
    actions: list[str] | None = None,
) -> tuple[PermissionsSection, bool]:
    """Deprecated alias for :func:`merge_file_guard_access_allows`.

    Writes the touched path itself (never its parent) and defaults to the
    ``read`` axis, so a read-only operation does not grant write.  ``actions``
    may be aligned positionally with ``paths``.
    """
    if not paths:
        return cast(PermissionsSection, deepcopy(permissions)), False
    access_list: list[tuple[str, str]] = []
    for i, path_str in enumerate(paths):
        action = "read"
        if actions is not None and i < len(actions) and actions[i]:
            action = str(actions[i])
        access_list.append((path_str, action))
    return merge_file_guard_access_allows(permissions, access_list)


def merge_permission_allow_rule_into_permissions(
    permissions: PermissionsSection | dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[PermissionsSection, bool]:
    """Merge an "always allow" rule into a copy of ``permissions``.

    ``applied`` is false when nothing changed (e.g. the current decision is not
    ASK, or there is no safe suggestion).
    """
    perms = cast(PermissionsSection, deepcopy(permissions))
    current_permission = _evaluate_current_permission(perms, tool_name, tool_args)
    if current_permission != PermissionLevel.ASK:
        logger.warning(
            "[PermissionEngine] permission.merge.skip tool=%s reason=current_permission_not_ask current=%s",
            tool_name,
            current_permission.value,
        )
        return cast(PermissionsSection, perms), False
    shell_ast_result = None
    if tool_name in _SHELL_APPROVAL_TOOLS:
        shell_ast_result = parse_shell_for_permission(
            str(tool_args.get("command", "") or tool_args.get("cmd", "") or "").strip()
        )
    suggestions = build_permission_suggestions(
        tool_name,
        tool_args,
        shell_ast_result=shell_ast_result,
    )
    # Path suggestions are not written to approval_overrides (path details are
    # persisted by the rail's file_guard); a path tool currently at ASK is
    # raised to whole-tool allow instead.
    non_path = [suggestion for suggestion in suggestions if str(suggestion.match_type or "").lower() != "path"]
    if not _persist_tiered_approval_override_suggestions(perms, non_path):
        if tool_name not in _SHELL_APPROVAL_TOOLS:
            if _persist_tiered_tool_allow(perms, tool_name):
                logger.info(
                    "[PermissionEngine] permission.merge.ok tool=%s target=tools",
                    tool_name,
                )
                return cast(PermissionsSection, perms), True
        logger.warning(
            "[PermissionEngine] permission.merge.skip tool=%s reason=no_safe_suggestion",
            tool_name,
        )
        return cast(PermissionsSection, perms), False
    logger.info(
        "[PermissionEngine] permission.merge.ok tool=%s target=approval_overrides",
        tool_name,
    )
    return cast(PermissionsSection, perms), True


__all__ = [
    "PermissionsSection",
    "merge_external_directory_allow_into_permissions",
    "merge_file_guard_access_allows",
    "merge_file_guard_path_rule",
    "merge_permission_allow_rule_into_permissions",
]
