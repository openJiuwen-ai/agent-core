# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""模式匹配与「始终允许」规则合并；含权限规则持久化.

「始终允许」的纯内存 merge 逻辑位于本包 :mod:`.approval_rules`；
本模块保留 core 的匹配器与“落盘”职责（写 agent YAML / CLI 信任目录）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from openjiuwen.harness.security.approval_rules import (
    merge_external_directory_allow_into_permissions as merge_external_directory_allow_into_permissions,
)
from openjiuwen.harness.security.approval_rules import (
    merge_file_guard_access_allows as merge_file_guard_access_allows,
)
from openjiuwen.harness.security.approval_rules import (
    merge_file_guard_path_rule as merge_file_guard_path_rule,
)
from openjiuwen.harness.security.approval_rules import (
    merge_permission_allow_rule_into_permissions as merge_permission_allow_rule_into_permissions,
)
from openjiuwen.harness.security.models import PermissionsSection
from openjiuwen.harness.security.suggestions import PermissionSuggestion as PermissionSuggestion
from openjiuwen.harness.security.suggestions import (
    build_permission_suggestions as build_permission_suggestions,
)

# Restrictive character class: Unicode letters, digits, underscores (including
# CJK) plus common argument/path characters; shell metacharacters are excluded.
_WILDCARD_CHARS = r'[-\w \._/:"\'%+]'


def match_wildcard(value: str, pattern: str) -> bool:
    """Return whether the whole ``value`` matches the wildcard ``pattern``."""
    if not pattern or not value:
        return False
    val = value.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    to_escape = set(".+^${}()|[]\\")
    escaped = "".join("\\" + c if c in to_escape else c for c in pat)
    escaped = escaped.replace("?", _WILDCARD_CHARS)
    if escaped.endswith(" *"):
        escaped = escaped[:-2] + "( " + _WILDCARD_CHARS + "*)?"
    else:
        escaped = escaped.replace("*", _WILDCARD_CHARS + "*")
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    try:
        return bool(re.fullmatch(escaped, val, flags))
    except re.error:
        return False


class PatternMatcher:
    """Wildcard matcher supporting only ``*`` and ``?``."""

    @staticmethod
    def match(pattern: str, value: str) -> bool:
        if not pattern or not value:
            return False
        return match_wildcard(value, pattern)

    def match_any(self, patterns: list[str], value: str) -> bool:
        return any(self.match(pattern, value) for pattern in patterns)


class PathMatcher:
    """Match file paths, falling back to parent-directory prefixes."""

    def __init__(self) -> None:
        self._pm = PatternMatcher()

    def match_path(self, pattern: str, path: str | Path) -> bool:
        normalized_path = str(path).replace("\\", "/")
        normalized_pattern = pattern.replace("\\", "/")
        if self._pm.match(normalized_pattern, normalized_path):
            return True
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
        return any(self.match_path(pattern, path) for pattern in patterns)


class URLMatcher:
    """Match URLs by full URL, hostname, netloc or base URL."""

    def __init__(self) -> None:
        self._pm = PatternMatcher()

    def match_url(self, pattern: str, url: str) -> bool:
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
        return any(self.match_url(pattern, url) for pattern in patterns)


class CommandMatcher:
    """Match shell command text with whole-string wildcard anchoring."""

    def __init__(self) -> None:
        self._pm = PatternMatcher()

    def match_command(self, pattern: str, command: str) -> bool:
        if not command:
            return False
        return self._pm.match(pattern, command)

    def match_command_any(self, patterns: list[str], command: str) -> bool:
        return any(self.match_command(pattern, command) for pattern in patterns)


def build_command_allow_pattern(cmd: str) -> str:
    """Build a wildcard pattern matching a complete command."""
    return cmd.strip() + " *"


def contains_path(parent: str | Path, child: str | Path) -> bool:
    """Return whether ``child`` is under ``parent`` (path-traversal safe)."""
    try:
        rel = os.path.relpath(Path(child).resolve(), Path(parent).resolve())
        return not rel.startswith("..") and rel != ".."
    except (ValueError, OSError):
        return False


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

        data = _load_agent_config_for_persist(cfg_path, fallback_permissions=bootstrap_permissions)
        if data is None:
            return {
                "ok": False,
                "error": (
                    "cannot bootstrap yaml (missing file; pass bootstrap_permissions with non-empty permissions dict)"
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
            "[PermissionEngine] permission.persist.cli_add_dir.file_guard path=%s read=allow write=allow exec=ask",
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
            overrides.append(
                {
                    "id": shell_override_id,
                    "tools": shell_tools,
                    "match_type": "command",
                    "pattern": shell_pattern,
                    "action": "allow",
                }
            )
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
