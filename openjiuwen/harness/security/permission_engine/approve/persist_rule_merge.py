# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HITL persist-rule merge. Product YAML I/O still goes through Host when present."""

from __future__ import annotations

import hashlib
import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from openjiuwen.harness.security.permission_engine.approve.persist_rule_suggestions import (
    PermissionSuggestion,
    build_permission_suggestions,
)
from openjiuwen.harness.security.permission_engine.models import PermissionsSection

logger = logging.getLogger(__name__)

_SHELL_APPROVAL_TOOLS = frozenset({
    "bash", "powershell", "mcp_exec_command", "create_terminal",
})


def _resolve_agent_config_yaml_path(explicit: Path | None) -> Path | None:
    """解析落盘用的 agent 配置文件路径。

    仅使用显式 ``config_yaml_path``（如 ``ToolPermissionHost.permission_yaml_path``）。
    未提供则无法解析。不读取环境变量，避免与宿主注入路径混用。
    """
    if explicit is None:
        return None
    p = Path(explicit).expanduser().resolve()
    if p.is_file():
        return p
    try:
        if p.parent.is_dir():
            return p
    except OSError:
        return None
    return None


def _load_agent_config_root(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_agent_config_root(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def _load_agent_config_for_persist(
    cfg_path: Path,
    *,
    fallback_permissions: PermissionsSection | None = None,
) -> dict[str, Any] | None:
    """加载整份 agent YAML；若文件尚不存在，则用 ``fallback_permissions`` 生成仅含 ``permissions`` 的草稿。"""
    if cfg_path.is_file():
        return _load_agent_config_root(cfg_path)

    if not isinstance(fallback_permissions, dict) or not fallback_permissions:
        logger.warning(
            "[PermissionEngine] permission.persist.abort reason=new_yaml_requires_fallback_permissions path=%s",
            cfg_path,
        )
        return None
    return {"permissions": deepcopy(fallback_permissions)}


@dataclass(frozen=True)
class _ApprovalOverrideSignature:
    tool_name: str
    tools: list[str]
    match_type: str
    existing_match_type: str | None
    pattern: str
    existing_pattern: str | None
    existing_action: str


def _persist_tiered_approval_override_suggestions(
    permissions: PermissionsSection,
    suggestions: list[PermissionSuggestion],
) -> bool:
    """写入 command 类 approval_overrides；path 类忽略（路径只写 file_guard）。"""
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
                "[PermissionEngine] permission.persist.skip reason=path_suggestion_use_file_guard "
                "pattern=%s",
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

    overrides.append({
        "id": _build_approval_override_id(tool_name, match_type, pattern),
        "tools": [tool_name],
        "match_type": match_type,
        "pattern": pattern,
        "action": action,
    })
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


def write_permissions_section_to_agent_config_yaml(
    config_yaml_path: Path | None,
    permissions: PermissionsSection | dict[str, Any],
) -> bool:
    """将 ``permissions`` 整段写入 agent YAML（保留其它顶层键；文件不存在则新建仅含 permissions 的根）。"""
    cfg_path = _resolve_agent_config_yaml_path(config_yaml_path)
    if cfg_path is None:
        logger.warning(
            "[PermissionEngine] permission.write_yaml.abort reason=no_config_yaml_path",
        )
        return False
    try:
        if cfg_path.is_file():
            data = _load_agent_config_root(cfg_path)
        else:
            data = {}
        data["permissions"] = deepcopy(permissions)
        _save_agent_config_root(cfg_path, data)
        logger.info(
            "[PermissionEngine] permission.write_yaml.ok path=%s",
            cfg_path,
        )
        return True
    except Exception:
        logger.error(
            "[PermissionEngine] permission.write_yaml.failed path=%s",
            cfg_path,
            exc_info=True,
        )
        return False


def _axes_for_file_guard_action(action: str) -> tuple[str, str, str]:
    """HITL「总是允许」按当时 path action 落盘，避免 read 场景写开放 write。"""
    act = (action or "read").strip().lower()
    if act == "write":
        return "allow", "allow", "ask"
    if act == "exec":
        return "allow", "ask", "allow"
    return "allow", "ask", "ask"


def _escalate_axis_toward_allow(old: Any, new: str) -> str:
    """合并同 path 规则：仅在 new=allow 时抬升，不降级已有 allow/deny。"""
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
    """在 ``permissions`` 副本上合并一条 ``file_guard.paths`` 规则；返回 ``(merged, wrote_any)``。"""
    path_norm = path.replace("\\", "/").rstrip("/")
    if not path_norm:
        return cast(PermissionsSection, deepcopy(permissions)), False

    perms = cast(PermissionsSection, deepcopy(permissions))
    fg = perms.get("file_guard")
    if not isinstance(fg, dict):
        fg = {}
        perms["file_guard"] = fg  # type: ignore[typeddict-unknown-key]
    fg["enabled"] = True
    paths = fg.get("paths")
    if not isinstance(paths, list):
        paths = []
        fg["paths"] = paths

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
            path_norm, merged_read, merged_write, merged_exec,
        )
        return cast(PermissionsSection, perms), True

    entry = {
        "path": path_norm,
        "read": read,
        "write": write,
        "exec": exec_,
        "match": match,
    }
    paths.append(entry)
    logger.info(
        "[PermissionEngine] permission.merge.file_guard path=%s read=%s write=%s exec=%s",
        path_norm, read, write, exec_,
    )
    return cast(PermissionsSection, perms), True


def merge_file_guard_access_allows(
    permissions: PermissionsSection | dict[str, Any],
    accesses: list[tuple[str, str]],
) -> tuple[PermissionsSection, bool]:
    """按 ``(path, action)`` 写入 ``file_guard.paths``（HITL「总是允许」主路径）。"""
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
        perms, did = merge_file_guard_path_rule(
            perms, path_norm, read=read, write=write, exec_=exec_,
        )
        wrote = wrote or did
    return cast(PermissionsSection, perms), wrote


def merge_external_directory_allow_into_permissions(
    permissions: PermissionsSection | dict[str, Any],
    paths: list[str],
    *,
    actions: list[str] | None = None,
) -> tuple[PermissionsSection, bool]:
    """Deprecated：请改用 :func:`merge_file_guard_access_allows`。"""
    if not paths:
        return cast(PermissionsSection, deepcopy(permissions)), False
    access_list: list[tuple[str, str]] = []
    for i, path_str in enumerate(paths):
        act = "read"
        if actions is not None and i < len(actions) and actions[i]:
            act = str(actions[i])
        access_list.append((path_str, act))
    return merge_file_guard_access_allows(permissions, access_list)


def merge_permission_allow_rule_into_permissions(
    permissions: PermissionsSection | dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[PermissionsSection, bool]:
    """在 ``permissions`` 副本上合并「始终允许」规则；返回 ``(merged, applied)``。"""
    from openjiuwen.harness.security.permission_engine.models import PermissionLevel
    from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import parse_shell_for_permission
    from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import evaluate_tiered_policy

    perms = cast(PermissionsSection, deepcopy(permissions))
    current_permission, _matched_rule = evaluate_tiered_policy(
        perms, tool_name, tool_args,
    )
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
    non_path = [s for s in suggestions if str(s.match_type or "").lower() != "path"]
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


def persist_cli_trusted_directory(
    raw_path: str,
    *,
    config_yaml_path: Path | None = None,
    bootstrap_permissions: PermissionsSection | None = None,
) -> dict[str, Any]:
    """CLI ``command.add_dir``：全局信任目录子树。"""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return {"ok": False, "error": "path is empty"}

    try:
        resolved = Path(raw_path.strip()).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as e:
        return {"ok": False, "error": f"invalid path: {e}"}

    dir_norm = resolved.as_posix().rstrip("/")
    if not dir_norm:
        return {"ok": False, "error": "path resolves to empty"}

    try:
        from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import (
            _SHELL_TOOLS,
        )

        cfg_path = _resolve_agent_config_yaml_path(config_yaml_path)
        if cfg_path is None:
            return {"ok": False, "error": "no agent config yaml path (pass config_yaml_path)"}

        data = _load_agent_config_for_persist(
            cfg_path, fallback_permissions=bootstrap_permissions
        )
        if data is None:
            return {
                "ok": False,
                "error": (
                    "cannot bootstrap yaml (missing file; pass bootstrap_permissions with "
                    "non-empty permissions dict)"
                ),
            }
        permissions = data.get("permissions")
        if permissions is None:
            permissions = {}
            data["permissions"] = permissions

        merged, _wrote = merge_file_guard_path_rule(
            cast(PermissionsSection, permissions),
            dir_norm,
            read="allow",
            write="allow",
            exec_="ask",
        )
        data["permissions"] = merged
        permissions = merged

        logger.info(
            "[PermissionEngine] permission.persist.cli_add_dir.file_guard path=%s "
            "read=allow write=allow exec=ask",
            dir_norm,
        )

        posix = dir_norm
        shell_pattern = "re:" + rf".*{re.escape(posix)}.*"

        suffix = hashlib.sha256(dir_norm.encode("utf-8")).hexdigest()[:16]
        shell_override_id = f"cli_trusted_shell_{suffix}"

        overrides = permissions.get("approval_overrides")
        if not isinstance(overrides, list):
            overrides = []
            permissions["approval_overrides"] = overrides

        def _has_id(oid: str) -> bool:
            for r in overrides:
                if isinstance(r, dict) and r.get("id") == oid:
                    return True
            return False

        shell_tools = sorted(_SHELL_TOOLS)
        if not _has_id(shell_override_id):
            overrides.append({
                "id": shell_override_id,
                "tools": shell_tools,
                "match_type": "command",
                "pattern": shell_pattern,
                "action": "allow",
            })
            logger.info(
                "[PermissionEngine] permission.persist.cli_add_dir.override.write target=shell id=%s",
                shell_override_id,
            )

        _save_agent_config_root(cfg_path, data)
        return {
            "ok": True,
            "normalized": dir_norm,
            "shell_pattern": shell_pattern,
            "file_guard": True,
            "tiered_overrides": True,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("[PermissionEngine] permission.persist.cli_add_dir.failed error=%s", e)
        return {"ok": False, "error": str(e)}


__all__ = [
    "merge_external_directory_allow_into_permissions",
    "merge_file_guard_access_allows",
    "merge_file_guard_path_rule",
    "merge_permission_allow_rule_into_permissions",
    "persist_cli_trusted_directory",
    "write_permissions_section_to_agent_config_yaml",
]
