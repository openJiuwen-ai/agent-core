# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""模式匹配器 - 仅支持 wildcard 模式；含权限规则持久化.

wildcard 模式：
- * → .*  (零个或多个)
- ? → .   (恰好一个)
- 正则元字符转义
- " *" 结尾 → ( .*)? 便于 "ls *" 匹配 "ls" 或 "ls -la"
- 全串锚定 ^...$ 防注入
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


_SHELL_APPROVAL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal"})
_PATH_APPROVAL_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
})
_PATH_APPROVAL_KEYS = (
    "path", "file_path", "target_file", "file", "old_path", "new_path",
    "source_path", "dest_path", "directory", "dir",
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
    - 全串锚定 ^...$ 防止 "git status; rm -rf /" 匹配 "git status *"

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
    # 3. 全串锚定
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    try:
        return bool(re.match("^" + escaped + "$", val, flags))
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
    if not suggestions:
        return False
    overrides = permissions.get("approval_overrides")
    if not isinstance(overrides, list):
        overrides = []
        permissions["approval_overrides"] = overrides

    persisted_any = False
    for suggestion in suggestions:
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


def merge_external_directory_allow_into_permissions(
    permissions: PermissionsSection | dict[str, Any],
    paths: list[str],
) -> tuple[PermissionsSection, bool]:
    """在 ``permissions`` 副本上合并外部目录白名单；返回 ``(merged, wrote_any)``。"""
    if not paths:
        return cast(PermissionsSection, deepcopy(permissions)), False
    perms = cast(PermissionsSection, deepcopy(permissions))
    ext_cfg = perms.get("external_directory")
    if not isinstance(ext_cfg, dict):
        ext_cfg = {"*": "ask"}
        perms["external_directory"] = ext_cfg
    wrote = False
    for path_str in paths:
        path_norm = path_str.replace("\\", "/").rstrip("/")
        parent = str(Path(path_norm).parent).replace("\\", "/")
        key = parent if parent and parent != "." else path_norm
        if key not in ext_cfg or ext_cfg[key] != "allow":
            ext_cfg[key] = "allow"
            wrote = True
            logger.info(
                "[PermissionEngine] permission.merge.external path=%s action=allow",
                key,
            )
    return cast(PermissionsSection, perms), wrote


def merge_permission_allow_rule_into_permissions(
    permissions: PermissionsSection | dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[PermissionsSection, bool]:
    """在 ``permissions`` 副本上合并「始终允许」规则；返回 ``(merged, applied)``。

    ``applied`` 为假表示未写入任何变更（如 tiered 下当前非 ASK 或无安全 suggestion）。
    """
    from openjiuwen.harness.security.models import PermissionLevel
    from openjiuwen.harness.security.shell_ast import parse_shell_for_permission
    from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy

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
    if not _persist_tiered_approval_override_suggestions(perms, suggestions):
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
    """CLI ``command.add_dir``：全局信任目录子树。

    写入 ``permissions.external_directory``（以目录路径为前缀键），并追加
    ``approval_overrides``（路径类工具一条、shell 类工具一条），以便同时消除外部路径维度的 ASK
    与参数级 ASK。

    不更新内存中的引擎；新建 YAML 时可传 ``bootstrap_permissions``。``remember`` 由调用方忽略；本函数始终落盘。
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
            _PATH_TOOLS,
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

        ext_cfg = permissions.get("external_directory")
        if not isinstance(ext_cfg, dict):
            ext_cfg = {"*": "ask"}
            permissions["external_directory"] = ext_cfg
        ext_cfg[dir_norm] = "allow"
        logger.info(
            "[PermissionEngine] permission.persist.cli_add_dir.external.write path=%s action=allow",
            dir_norm,
        )

        path_pattern = "re:^" + re.escape(dir_norm) + r"(?:$|/)"
        posix = dir_norm
        # 仅用正斜杠路径；反斜杠写入 YAML 双引号后易被解析成 \U 等非法正则转义，匹配改由 tiered 对 command 做 \→/ 归一化
        shell_pattern = "re:" + rf".*{re.escape(posix)}.*"

        suffix = hashlib.sha256(dir_norm.encode("utf-8")).hexdigest()[:16]
        path_override_id = f"cli_trusted_path_{suffix}"
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

        path_tools = sorted(_PATH_TOOLS)
        if not _has_id(path_override_id):
            overrides.append({
                "id": path_override_id,
                "tools": path_tools,
                "match_type": "path",
                "pattern": path_pattern,
                "action": "allow",
            })
            logger.info(
                "[PermissionEngine] permission.persist.cli_add_dir.override.write target=path id=%s",
                path_override_id,
            )

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
            "path_pattern": path_pattern,
            "shell_pattern": shell_pattern,
            "tiered_overrides": True,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("[PermissionEngine] permission.persist.cli_add_dir.failed error=%s", e)
        return {"ok": False, "error": str(e)}
