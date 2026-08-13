# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""模式匹配器 - 仅支持 wildcard 模式；含权限规则持久化.

wildcard 模式：
- * → .*  (零个或多个)
- ? → .   (恰好一个)
- 正则元字符转义
- " *" 结尾 → ( .*)? 便于 "ls *" 匹配 "ls" 或 "ls -la"
- 全串匹配防注入
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


from urllib.parse import urlparse
import yaml
from openjiuwen.harness.security.models import PermissionsSection
from openjiuwen.harness.security.suggestions import (
    PermissionSuggestion,
    build_permission_suggestions,
)

logger = logging.getLogger(__name__)


def _resolve_agent_config_yaml_path(explicit: Path | None) -> Path | None:
    """解析落盘用的 agent 配置文件路径。

    仅使用显式 ``config_yaml_path``（如 ``ToolPermissionHost.permission_yaml_path`` 传入
    ``write_permissions_section_to_agent_config_yaml`` / ``persist_cli_trusted_directory``）。
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


_SHELL_APPROVAL_TOOLS = frozenset({
    "bash", "mcp_exec_command", "create_terminal", "powershell",
})
_PATH_APPROVAL_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
    "send_file_to_user",
})
_PATH_APPROVAL_KEYS = (
    "path", "file_path", "target_file", "file", "old_path", "new_path",
    "source_path", "dest_path", "directory", "dir",
    "abs_file_path_list",
)


@dataclass(frozen=True)
class _ApprovalOverrideSignature:
    tool_name: str
    tools: list[str]
    match_type: str
    existing_match_type: str | None
    pattern: str
    existing_pattern: str | None
    existing_action: str


# 限制性字符类：仅允许命令参数和路径常见字符，排除 ; | & ` < > $ 等 shell 元字符防注入
# - 置于开头避免被解析为范围
_WILDCARD_CHARS = r'[-a-zA-Z0-9 \._/:"\']'


def match_wildcard(value: str, pattern: str) -> bool:
    """通配符匹配.

    - * → 限制性字符类* (排除 shell 元字符，防命令拼接)
    - ? → 限制性字符类 (恰好一个)
    - 正则元字符转义
    - " *" 结尾 → ( 字符类*)? 使 "ls *" 可匹配 "ls" 或 "ls -la"
    - 全串匹配防止 "git status; rm -rf /" 匹配 "git status *"

    Args:
        value: 被匹配字符串（来自工具输入）
        pattern: 通配符模式（来自配置，可信）

    Returns:
        是否匹配
    """
    if not pattern or not value:
        return False
    val = value.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    # 1. 转义正则特殊字符（* 和 ? 保留，后续单独处理）
    to_escape = set(".+^${}()|[]\\")
    escaped = "".join("\\" + c if c in to_escape else c for c in pat)
    # 2. 先替换 ?（必须在 * 之前，否则会误替换 ")? " 中的 ?）
    escaped = escaped.replace("?", _WILDCARD_CHARS)
    # 3. * → 限制性字符类*
    if escaped.endswith(" *"):
        escaped = escaped[:-2] + "( " + _WILDCARD_CHARS + "*)?"
    else:
        escaped = escaped.replace("*", _WILDCARD_CHARS + "*")
    # 3. 全串匹配
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    try:
        return bool(re.fullmatch(escaped, val, flags))
    except re.error:
        return False




class PatternMatcher:
    """模式匹配器 - 仅支持 wildcard 模式 (*, ?)."""

    @staticmethod
    def match(pattern: str, value: str) -> bool:
        if not pattern or not value:
            return False
        return match_wildcard(value, pattern)

    def match_any(self, patterns: list[str], value: str) -> bool:
        """匹配任意一个模式."""
        return any(self.match(p, value) for p in patterns)


class PathMatcher:
    """路径匹配器."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_path(self, pattern: str, path: str | Path) -> bool:
        """匹配文件路径 (规范化分隔符后再比较)."""
        normalized_path = str(path).replace("\\", "/")
        normalized_pattern = pattern.replace("\\", "/")

        if self._pm.match(normalized_pattern, normalized_path):
            return True

        # 尝试匹配父目录层级
        path_obj = Path(str(path))
        for parent in path_obj.parents:
            parent_str = str(parent).replace("\\", "/")
            if self._pm.match(normalized_pattern, parent_str):
                return True
            if self._pm.match(normalized_pattern, parent_str + "/"):
                return True
            if self._pm.match(normalized_pattern, parent_str + "/*"):
                return True
        return False

    def match_path_any(self, patterns: list[str], path: str | Path) -> bool:
        return any(self.match_path(p, path) for p in patterns)


class URLMatcher:
    """URL 匹配器."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_url(self, pattern: str, url: str) -> bool:
        """匹配 URL (支持 hostname、netloc、full URL)."""
        if not url:
            return False
        if self._pm.match(pattern, url):
            return True
        try:
            parsed = urlparse(url)
            if self._pm.match(pattern, parsed.hostname or ""):
                return True
            if self._pm.match(pattern, parsed.netloc):
                return True
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            if self._pm.match(pattern, base_url):
                return True
            if self._pm.match(pattern, base_url + "/*"):
                return True
        except Exception:
            return False
        return False

    def match_url_any(self, patterns: list[str], url: str) -> bool:
        return any(self.match_url(p, url) for p in patterns)


class CommandMatcher:
    """命令匹配器 - 仅支持 wildcard，全串锚定防注入."""

    def __init__(self):
        self._pm = PatternMatcher()

    def match_command(self, pattern: str, command: str) -> bool:
        """匹配命令字符串 (wildcard 模式，全串锚定)."""
        if not command:
            return False
        return self._pm.match(pattern, command)

    def match_command_any(self, patterns: list[str], command: str) -> bool:
        return any(self.match_command(p, command) for p in patterns)


def build_command_allow_pattern(cmd: str) -> str:
    """构建匹配完整命令的通配符模式.

    Examples:
        "start chrome"   → start chrome *
        "npm install"    → npm install *
        "ls"             → ls *
    """
    return cmd.strip() + " *"


def contains_path(parent: str | Path, child: str | Path) -> bool:
    """子路径是否在父路径下（含路径穿越防护）.
    """
    try:
        rel = os.path.relpath(Path(child).resolve(), Path(parent).resolve())
        return not rel.startswith("..") and rel != ".."
    except (ValueError, OSError):
        return False


# ---------- 权限规则持久化 ----------


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


def _append_allow_tool(perms: dict[str, Any], tool_name: str) -> None:
    name = str(tool_name).strip()
    allow = [t for t in (perms.get("allow_tools") or []) if isinstance(t, str)]
    ask = [t for t in (perms.get("ask_tools") or []) if isinstance(t, str)]
    if name in ask:
        ask = [t for t in ask if t != name]
        if ask:
            perms["ask_tools"] = ask
        else:
            perms.pop("ask_tools", None)
    if name not in allow:
        allow.append(name)
    perms["allow_tools"] = allow
    added = [t for t in (perms.get("_allow_tools_added") or []) if isinstance(t, str)]
    if name not in added:
        added.append(name)
    perms["_allow_tools_added"] = added
    tools = perms.get("tools")
    if isinstance(tools, dict):
        tools = dict(tools)
        tools[name] = "allow"
        perms["tools"] = tools


def _can_persist_whole_tool_allow(
    perms: dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> bool:
    from openjiuwen.harness.security.models import PermissionLevel
    from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy

    deny = {t for t in (perms.get("deny_tools") or []) if isinstance(t, str)}
    if tool_name in deny:
        return False
    level, _ = evaluate_tiered_policy(perms, tool_name, tool_args)
    return level == PermissionLevel.ASK


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
    """在 ``permissions`` 副本上合并一条 ``file_guard.paths`` 规则；返回 ``(merged, wrote_any)``。

    用于 ``/add-dir`` / HITL「总是允许」路径类决策。``exec_`` 默认 ``ask``（目录信任 ≠ 默认可执行）。
    同 path 已存在时按轴向 allow 抬升合并，避免后一次 read-only 覆盖掉已有 write allow。
    """
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
    """按 ``(path, action)`` 写入 ``file_guard.paths``（HITL「总是允许」主路径）。

    - 使用触达路径本身，**不上卷父目录**（``ls dir`` → 信任 ``dir``，不是 ``dir`` 的父级）
    - 轴权限按 action：``read`` → 仅 read allow；``write`` → read+write allow；``exec`` → read+exec allow
    - 成功写入时在副本上设置 ``_file_guard_paths_added``，供 Host 只落盘增量 paths
    """
    if not accesses:
        return cast(PermissionsSection, deepcopy(permissions)), False
    perms = cast(PermissionsSection, deepcopy(permissions))
    wrote = False
    added: list[dict[str, Any]] = []
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
        if not did:
            continue
        wrote = True
        fg = perms.get("file_guard") if isinstance(perms.get("file_guard"), dict) else {}
        paths = fg.get("paths") if isinstance(fg, dict) else None
        if isinstance(paths, list):
            for entry in reversed(paths):
                if not isinstance(entry, dict):
                    continue
                existing_path = str(entry.get("path") or "").replace("\\", "/").rstrip("/")
                if existing_path == path_norm:
                    added.append(deepcopy(entry))
                    break
    if added:
        prior = perms.get("_file_guard_paths_added")
        merged_added: list[dict[str, Any]] = []
        if isinstance(prior, list):
            merged_added.extend(deepcopy(x) for x in prior if isinstance(x, dict))
        by_path = {
            str(e.get("path") or "").replace("\\", "/").rstrip("/"): e
            for e in merged_added
            if isinstance(e, dict)
        }
        for entry in added:
            key = str(entry.get("path") or "").replace("\\", "/").rstrip("/")
            if not key:
                continue
            by_path[key] = entry
        perms["_file_guard_paths_added"] = list(by_path.values())  # type: ignore[typeddict-unknown-key]
    return cast(PermissionsSection, perms), wrote


def merge_external_directory_allow_into_permissions(
    permissions: PermissionsSection | dict[str, Any],
    paths: list[str],
    *,
    actions: list[str] | None = None,
) -> tuple[PermissionsSection, bool]:
    """Deprecated：请改用 :func:`merge_file_guard_access_allows`。

    写入触达路径本身（不上卷父目录）；缺省按 **read** 轴 allow（write/exec 保持 ask），
    避免 ``ls`` 类读操作过度授权 write。可通过 ``actions`` 与 ``paths`` 对齐传入动作。
    """
    if not paths:
        return cast(PermissionsSection, deepcopy(permissions)), False
    access_list: list[tuple[str, str]] = []
    for i, path_str in enumerate(paths):
        act = "read"
        if actions is not None and i < len(actions) and actions[i]:
            act = str(actions[i])
        access_list.append((path_str, act))
    return merge_file_guard_access_allows(permissions, access_list)


def can_persist_pattern_allow(
    permissions: PermissionsSection | dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> bool:
    """是否允许将本次调用持久化为 pattern 级 allow。

    Global DENY / Global CRITICAL（及 builtin 底线 ASK/DENY）不可被 User/Session 永久允许放宽。
    """
    from openjiuwen.harness.security.models import PermissionLevel
    from openjiuwen.harness.security.tiered_policy import (
        global_baseline_blocks_persist,
    )

    return not global_baseline_blocks_persist(
        cast(dict[str, Any], permissions),
        tool_name,
        tool_args,
    )


def merge_permission_allow_rule_into_permissions(
    permissions: PermissionsSection | dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[PermissionsSection, bool]:
    """在 ``permissions`` 副本上合并「始终允许」规则；返回 ``(merged, applied)``。

    优先写入 command 等非 path 的 pattern 级 ``approval_overrides``。

    - **Shell 工具**（bash / powershell 等）：只写命令级 overrides，**永不**回退
      ``allow_tools``（避免「记住一条命令」变成整工具永久放行）。
      若 tiered 因 defaults 为 ALLOW、但引擎曾因 findings 抬到 ASK 而触发 HITL，
      仍允许在有安全 suggestion 时落盘 pattern。
    - **非 Shell**：无安全 non-path suggestion 且当前为 ASK 时，可回退 ``allow_tools``
      （Strict 下 path/杂项工具的 mode defaults ask）。
    path 细则仍可由 rail 侧并行合并 ``file_guard.paths``。
    """
    from openjiuwen.harness.security.models import PermissionLevel
    from openjiuwen.harness.security.shell_ast import parse_shell_for_permission
    from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy

    perms = cast(PermissionsSection, deepcopy(permissions))
    is_shell = tool_name in _SHELL_APPROVAL_TOOLS

    current_permission, _matched_rule = evaluate_tiered_policy(
        perms, tool_name, tool_args,
    )
    if current_permission == PermissionLevel.DENY:
        logger.warning(
            "[PermissionEngine] permission.merge.skip tool=%s reason=current_permission_deny",
            tool_name,
        )
        return cast(PermissionsSection, perms), False

    if not can_persist_pattern_allow(perms, tool_name, tool_args):
        logger.warning(
            "[PermissionEngine] permission.merge.skip tool=%s reason=global_baseline_blocks_persist",
            tool_name,
        )
        return cast(PermissionsSection, perms), False

    shell_ast_result = None
    if is_shell:
        shell_ast_result = parse_shell_for_permission(
            str(tool_args.get("command", "") or tool_args.get("cmd", "") or "").strip()
        )
    suggestions = build_permission_suggestions(
        tool_name,
        tool_args,
        shell_ast_result=shell_ast_result,
    )
    # path 类 suggestion 不写 approval_overrides（路径细则由 rail 侧 file_guard 落盘）。
    non_path = [s for s in suggestions if str(s.match_type or "").lower() != "path"]

    if non_path:
        # Shell：即便 tiered 为 ALLOW（findings 才抬 ASK），HITL 记住仍应落命令 pattern。
        if (
            current_permission != PermissionLevel.ASK
            and not is_shell
        ):
            logger.warning(
                "[PermissionEngine] permission.merge.skip tool=%s "
                "reason=current_permission_not_ask current=%s",
                tool_name,
                current_permission.value,
            )
            return cast(PermissionsSection, perms), False
        if _persist_tiered_approval_override_suggestions(perms, non_path):
            logger.info(
                "[PermissionEngine] permission.merge.ok tool=%s target=approval_overrides",
                tool_name,
            )
            return cast(PermissionsSection, perms), True

    if is_shell:
        logger.warning(
            "[PermissionEngine] permission.merge.skip tool=%s "
            "reason=shell_no_safe_suggestion_no_allow_tools_fallback",
            tool_name,
        )
        return cast(PermissionsSection, perms), False

    if current_permission != PermissionLevel.ASK:
        logger.warning(
            "[PermissionEngine] permission.merge.skip tool=%s reason=current_permission_not_ask current=%s",
            tool_name,
            current_permission.value,
        )
        return cast(PermissionsSection, perms), False

    # 非 shell：无安全 pattern 时回退整工具 allow_tools（todo_list 等）。
    if _can_persist_whole_tool_allow(perms, tool_name, tool_args):
        _append_allow_tool(perms, tool_name)
        logger.info(
            "[PermissionEngine] permission.merge.ok tool=%s target=allow_tools",
            tool_name,
        )
        return cast(PermissionsSection, perms), True
    logger.warning(
        "[PermissionEngine] permission.merge.skip tool=%s reason=no_safe_suggestion",
        tool_name,
    )
    return cast(PermissionsSection, perms), False


def persist_cli_trusted_directory(
    raw_path: str,
    *,
    config_yaml_path: Path | None = None,
    bootstrap_permissions: PermissionsSection | None = None,
) -> dict[str, Any]:
    """CLI ``command.add_dir``：全局信任目录子树。

    写入 ``permissions.file_guard.paths``（``read/write: allow``，``exec: ask``），
    并追加 shell 类 ``approval_overrides``（命令维，消除 A 层路径文本 ASK）。
    **不再**写入 path 类 approval_overrides，也**不再**写入 ``external_directory`` 具名键。

    不更新内存中的引擎；新建 YAML 时可传 ``bootstrap_permissions``。
    """
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
        from openjiuwen.harness.security.tiered_policy import (
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
        # 写回同一 dict 树（merge 返回副本）
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
