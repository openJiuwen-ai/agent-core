# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill overlay 安全合成与请求级授权 Context。

tools / rules 合成与 ``SkillAuthorizationContext`` 与 0708 版本一致。

file_guard 轴已适配 dev-stable 的 native 配置形态（agent-core
``openjiuwen.harness.security.file_guard``）：0708 旧版合成直接合并
``file_guard.global``（path -> 轴）映射，dev-stable 裁决器消费的是
``file_guard.paths`` 条目列表（``{path, match, read, write, exec}``）。
本模块把 overlay 的 ``file_guard.global`` 声明**投影为 native prefix 条目**，
并保持 0708 的核心安全语义：

- deny 只增不改（任何声明都可收紧）；
- allow / ask 不可穿透 base 或同一 overlay 中同级/祖先路径的 deny；
- 新条目继承祖先 deny 轴（裁决端按单条最长前缀命中，缺轴会静默降级祖先 deny）。

glob 条目不参与祖先 deny 判定（无法按路径前缀比较）；但运行时裁决对 glob
命中与最长前缀命中取最严档，base 的 glob deny 不会被投影条目绕过。
"""

from __future__ import annotations

import contextvars
import copy
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Skill 声明可提升的原档位（``guard`` 为无 baseline 语义，等价于未放宽）。
_RAISABLE_TOOL_LEVELS = frozenset({"ask", "guard"})

_TOOL_LEVELS = frozenset({"allow", "ask", "deny"})
_FILE_GUARD_AXES = frozenset({"read", "write", "exec"})
_RULE_ACTIONS = frozenset({"allow", "deny"})
_RULE_SCOPES = frozenset({"exact", "head", "regex", "wildcard"})

#: native file_guard 裁决的严格度排序（与 agent-core ``_LEVEL_ORDER`` 对齐）。
_LEVEL_STRICTNESS = {"deny": 0, "ask": 1, "allow": 2}


# ---------- tools ----------


def _normalize_level(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    level = value.strip().lower()
    return level or None


def _base_tool_level(base: dict[str, Any], tool_name: str) -> str | None:
    """原档位：显式 ``tools.<name>`` 优先，未显式配置时继承 ``defaults``。"""
    tools = base.get("tools")
    if isinstance(tools, dict) and tool_name in tools:
        return _normalize_level(tools[tool_name])
    return _normalize_level(base.get("defaults"))


def _compose_tools(merged: dict[str, Any], base: dict[str, Any], overlay_tools: dict[Any, Any]) -> None:
    tools = merged.setdefault("tools", {})
    for tool_name, raw_level in overlay_tools.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            logger.warning("[skill_authorization] compose.tools.skip invalid tool name: %r", tool_name)
            continue
        overlay_level = _normalize_level(raw_level)
        if overlay_level not in _TOOL_LEVELS:
            logger.warning(
                "[skill_authorization] compose.tools.skip tool=%s invalid level=%r",
                tool_name, raw_level,
            )
            continue
        base_level = _base_tool_level(base, tool_name)
        if base_level == "deny":
            # 显式或默认 DENY 不可被任何声明改变。
            logger.info(
                "[skill_authorization] compose.tools.keep_deny tool=%s overlay=%s",
                tool_name, overlay_level,
            )
            continue
        if overlay_level == "allow":
            if base_level in _RAISABLE_TOOL_LEVELS:
                tools[tool_name] = "allow"
            elif base_level == "allow":
                continue
            else:
                # 原档位缺失 / 非法：不按 skill 声明放宽（fail-closed）。
                logger.warning(
                    "[skill_authorization] compose.tools.skip_raise tool=%s base_level=%r",
                    tool_name, base_level,
                )
        else:
            # ask / deny 直接收紧生效。
            tools[tool_name] = overlay_level


# ---------- rules ----------


def command_rule_fingerprint(rule: Any) -> tuple[str, str, str] | None:
    """返回命令规则的有效身份，忽略 ``id`` / ``description`` 等展示元数据。

    ``scope`` 缺失时使用 tiered policy 的同一推导口径，使 ``echo *`` 与显式
    ``scope=wildcard`` 被视为同一规则，同时保留真正的 scope 语义差异。
    """
    if not isinstance(rule, dict):
        return None
    pattern = rule.get("pattern")
    action = _normalize_level(rule.get("action"))
    if not isinstance(pattern, str) or not pattern.strip() or action not in _RULE_ACTIONS:
        return None
    normalized_pattern = pattern.strip()
    raw_scope = str(rule.get("scope") or "").strip().lower()
    if raw_scope in _RULE_SCOPES:
        scope = raw_scope
    elif normalized_pattern.lower().startswith("re:"):
        scope = "regex"
    elif normalized_pattern.endswith(" *"):
        scope = "wildcard"
    else:
        scope = "exact"
    # “总是允许”持久化会把单命令头模式写成 scope=head；
    # tiered policy 对同一形态的 wildcard 规则也会回退到命令头匹配。
    # 因此 `echo *` 的 head/wildcard 在实际裁决上等价，统一指纹避免重复授权。
    head_pattern = normalized_pattern[:-2].strip() if normalized_pattern.endswith(" *") else ""
    if scope in {"head", "wildcard"} and head_pattern and not any(
        char.isspace() for char in head_pattern
    ):
        scope = "head"
    return normalized_pattern, action, scope


def _compose_rules(merged: dict[str, Any], overlay_rules: list[Any]) -> None:
    base_rules = merged.get("rules")
    if not isinstance(base_rules, list):
        base_rules = []
        merged["rules"] = base_rules
    existing: set[tuple[str, str, str]] = set()
    for section_name in ("rules", "approval_overrides"):
        section = merged.get(section_name)
        if not isinstance(section, list):
            continue
        for existing_rule in section:
            fingerprint = command_rule_fingerprint(existing_rule)
            if fingerprint is not None:
                existing.add(fingerprint)
    for rule in overlay_rules:
        fingerprint = command_rule_fingerprint(rule)
        if fingerprint is None:
            logger.warning("[skill_authorization] compose.rules.skip malformed rule: %r", rule)
            continue
        if fingerprint in existing:
            logger.info(
                "[skill_authorization] compose.rules.skip_duplicate pattern=%s action=%s scope=%s",
                *fingerprint,
            )
            continue
        # allow / deny 均只追加；deny 不改变既有 deny、只能新增。
        base_rules.append(copy.deepcopy(rule))
        existing.add(fingerprint)


# ---------- file_guard.global → native paths 投影 ----------


def _normalize_guard_path(path: str) -> str:
    """合成期路径规范化：统一斜杠、去尾部斜杠（不触碰文件系统）。"""
    normalized = path.replace("\\", "/").strip()
    while len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def _is_same_or_ancestor(ancestor: str, path: str) -> bool:
    """``ancestor`` 是否为 ``path`` 的同级或祖先路径（均为规范化后的字符串）。"""
    if ancestor == path:
        return True
    if ancestor == "/":
        return path.startswith("/")
    return path.startswith(ancestor + "/")


def _collect_overlay_denies(overlay_global: dict[Any, Any]) -> set[tuple[str, str]]:
    """本 overlay 自声明的 ``(规范化路径, 轴)`` deny 集合（与遍历顺序无关）。

    同一 overlay 先 deny 父路径再 allow/ask 子路径时，子路径声明同样不得突破；
    该集合让祖先检查把 overlay 自加 deny 与 base deny 一视同仁。
    """
    denies: set[tuple[str, str]] = set()
    for raw_path, entry in overlay_global.items():
        if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(entry, dict):
            continue
        normalized = _normalize_guard_path(raw_path)
        for axis, raw_level in entry.items():
            if axis in _FILE_GUARD_AXES and _normalize_level(raw_level) == "deny":
                denies.add((normalized, axis))
    return denies


def _native_entry_path(entry: Any) -> str | None:
    """读取 native paths 条目（dict 或 ``FileGuardPathRule`` 风格对象）的规范化路径。"""
    raw = entry.get("path") if isinstance(entry, dict) else getattr(entry, "path", None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _normalize_guard_path(raw)


def _native_entry_match(entry: Any) -> str:
    raw = entry.get("match") if isinstance(entry, dict) else getattr(entry, "match", "prefix")
    return "glob" if raw == "glob" else "prefix"


def _native_entry_axis(entry: Any, axis: str) -> str | None:
    """读取 native paths 条目的轴档位；未配置的轴返回 ``None``。"""
    if isinstance(entry, dict):
        return _normalize_level(entry.get(axis))
    raw = getattr(entry, axis, None)
    return _normalize_level(getattr(raw, "value", raw))


def _axis_denied_by_ancestor(
    base_paths: list[Any],
    overlay_denies: set[tuple[str, str]],
    path: str,
    axis: str,
) -> bool:
    """base 或本 overlay 中同级/祖先路径是否已对该轴声明 ``deny``（不可被覆盖）。

    仅比较 prefix 条目；glob 条目无法按路径前缀比较，但运行时裁决对 glob
    命中取最严档，base 的 glob deny 不会被绕过。
    """
    for entry in base_paths:
        if _native_entry_match(entry) != "prefix":
            continue
        if _native_entry_axis(entry, axis) != "deny":
            continue
        base_path = _native_entry_path(entry)
        if base_path is not None and _is_same_or_ancestor(base_path, path):
            return True
    return any(
        deny_axis == axis and _is_same_or_ancestor(deny_path, path)
        for deny_path, deny_axis in overlay_denies
    )


def _find_prefix_entry_index(paths: list[Any], path: str) -> int | None:
    for index, item in enumerate(paths):
        if _native_entry_match(item) == "prefix" and _native_entry_path(item) == path:
            return index
    return None


def _compose_file_guard_global(
    merged: dict[str, Any],
    base: dict[str, Any],
    overlay_global: dict[Any, Any],
) -> None:
    """把 overlay 的 ``file_guard.global`` 投影为 native ``file_guard.paths`` 前缀条目。"""
    fg = merged.setdefault("file_guard", {})
    if not isinstance(fg, dict):
        fg = {}
        merged["file_guard"] = fg
    paths = fg.get("paths")
    if not isinstance(paths, list):
        paths = []
        fg["paths"] = paths

    base_fg = base.get("file_guard")
    base_paths = base_fg.get("paths") if isinstance(base_fg, dict) else None
    if not isinstance(base_paths, list):
        base_paths = []
    overlay_denies = _collect_overlay_denies(overlay_global)
    projected_any = False

    for raw_path, entry in overlay_global.items():
        if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(entry, dict):
            logger.warning(
                "[skill_authorization] compose.file_guard.skip path=%r entry=%r",
                raw_path, entry,
            )
            continue
        path = _normalize_guard_path(raw_path)
        existing_index = _find_prefix_entry_index(paths, path)
        existing_entry = paths[existing_index] if existing_index is not None else None
        if isinstance(existing_entry, dict):
            new_entry = dict(existing_entry)
        else:
            new_entry = {"path": path, "match": "prefix"}
            if existing_entry is not None:
                for axis in _FILE_GUARD_AXES:
                    level = _native_entry_axis(existing_entry, axis)
                    if level is not None:
                        new_entry[axis] = level
        new_entry["path"] = path
        new_entry["match"] = "prefix"
        for axis, raw_level in entry.items():
            if axis not in _FILE_GUARD_AXES:
                logger.warning(
                    "[skill_authorization] compose.file_guard.skip_axis path=%s axis=%r",
                    path, axis,
                )
                continue
            overlay_level = _normalize_level(raw_level)
            if overlay_level not in _TOOL_LEVELS:
                logger.warning(
                    "[skill_authorization] compose.file_guard.skip_axis path=%s axis=%s level=%r",
                    path, axis, raw_level,
                )
                continue
            base_level = _native_entry_axis(existing_entry, axis) if existing_entry is not None else None
            if overlay_level == "deny":
                # deny 直接收紧，只增不改（base 已是 deny 时为无害 no-op）。
                new_entry[axis] = "deny"
                continue
            # allow / ask：任何同级或祖先 deny 均不可突破（含本路径显式 deny、
            # 以及同一 overlay 自声明的祖先 deny）。
            if _axis_denied_by_ancestor(base_paths, overlay_denies, path, axis):
                logger.info(
                    "[skill_authorization] compose.file_guard.keep_deny path=%s axis=%s overlay=%s",
                    path, axis, overlay_level,
                )
                continue
            if overlay_level == "ask":
                if base_level == "allow":
                    new_entry[axis] = "ask"
                continue
            # overlay allow：仅提升 ask（缺省轴按 file_guard 语义视为 ask）。
            if base_level in (None, "ask"):
                new_entry[axis] = "allow"
        # 继承基线中同级或祖先路径的 deny 轴：裁决端按单条最长前缀条目取档、
        # 缺轴兜底为 defaults（通常 ask），新条目若不携带祖先 deny 轴会静默降级
        # 祖先 deny。
        for axis in _FILE_GUARD_AXES:
            if _normalize_level(new_entry.get(axis)) is not None:
                continue
            if _axis_denied_by_ancestor(base_paths, overlay_denies, path, axis):
                new_entry[axis] = "deny"
        if not any(_normalize_level(new_entry.get(axis)) is not None for axis in _FILE_GUARD_AXES):
            # 全部轴声明均被拦截 / 无有效轴：新建条目无意义（既有条目保持原样）。
            if existing_index is None:
                continue
        if existing_index is not None:
            paths[existing_index] = new_entry
        else:
            paths.append(new_entry)
        projected_any = True

    if projected_any and "enabled" not in fg:
        # overlay 投影了路径条目但 base 未显式开关时补齐开启，避免
        # normalize_path_guard_config 因缺省关闭而静默丢弃已批准的声明；
        # base 显式 enabled=false 时尊重原配置（fail-closed）。
        fg["enabled"] = True


def _match_glob(pattern: str, path_posix: str) -> bool:
    """简单 glob：支持 ``**`` / ``*`` / ``?``（与 agent-core native 口径一致）。"""
    i = 0
    out: list[str] = []
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    try:
        return bool(re.fullmatch("".join(out), path_posix))
    except re.error:
        return False


def _resolve_display_path(raw_path: str, workspace_root: Any) -> str | None:
    """把声明路径规范化为 posix 字符串（best-effort，不强制要求文件存在）。"""
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    try:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        p = Path(expanded)
        if not p.is_absolute() and workspace_root is not None:
            p = Path(workspace_root) / expanded
        if p.is_absolute():
            return p.resolve().as_posix()
        return p.as_posix()
    except (OSError, RuntimeError, ValueError):
        return _normalize_guard_path(raw)


def effective_file_guard_axis_level(
    paths: Any,
    raw_path: str,
    axis: str,
    *,
    defaults: Any = None,
    workspace_root: Any = None,
) -> str | None:
    """按 native 裁决语义计算 ``raw_path`` 在 ``axis`` 上的生效档位（allow/ask/deny）。

    与 agent-core ``FileGuardChecker._resolve_one`` 对齐：prefix 条目取最长
    前缀单条命中、glob 条目全部命中、多候选取最严（deny > ask > allow）、
    未命中回退 ``defaults``（轴缺省视为 ask）。条目未配置的轴按编译期缺省
    ask 处理。

    供审批差分展示使用，确保卡上的 before/after 与真实裁决一致；路径无法
    规范化时返回 ``None``，调用方应回退到简化口径。

    .. note:: 0708 旧版签名为 ``(global_map, raw_path, axis, *, workspace_root,
       rw_enabled)``，面向 legacy ``file_guard.global`` 映射；本版面向 native
       ``paths`` 条目列表，workspace 短路已由显式 ``file_guard.workspace``
       前缀规则表达，不再保留 ``rw_enabled`` 短路参数。
    """
    if axis not in _FILE_GUARD_AXES:
        return None
    if not isinstance(paths, (list, tuple)):
        return None
    path_posix = _resolve_display_path(raw_path, workspace_root)
    if path_posix is None:
        return None

    best_prefix: tuple[int, str] | None = None
    glob_levels: list[str] = []
    for entry in paths:
        entry_path = _native_entry_path(entry)
        if entry_path is None:
            continue
        # 未配置的轴在编译期缺省为 ask（对齐 agent-core ``_compile_path_entry``）。
        level = _native_entry_axis(entry, axis) or "ask"
        if _native_entry_match(entry) == "glob":
            if _match_glob(entry_path, path_posix):
                glob_levels.append(level)
            continue
        prefix = entry_path.rstrip("/")
        if path_posix == prefix or path_posix.startswith(prefix + "/"):
            if best_prefix is None or len(prefix) > best_prefix[0]:
                best_prefix = (len(prefix), level)

    candidates: list[str] = []
    if best_prefix is not None:
        candidates.append(best_prefix[1])
    candidates.extend(glob_levels)
    if candidates:
        return min(candidates, key=lambda lv: _LEVEL_STRICTNESS[lv])

    defaults_map: Any = defaults
    if isinstance(defaults_map, dict):
        return _normalize_level(defaults_map.get(axis)) or "ask"
    if defaults_map is not None:
        raw = getattr(defaults_map, axis, None)
        return _normalize_level(getattr(raw, "value", raw)) or "ask"
    return "ask"


# ---------- 合成入口 ----------


def compose_skill_permissions(
    base_effective: dict[str, Any],
    skill_overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    """把 Skill overlay 安全合成到当前生效权限配置上。

    输入均视为只读；返回新的合成配置。任何异常（含非法输入）返回
    ``base_effective`` 深拷贝（fail-closed，仅按原权限流程裁决）。
    """
    base = base_effective if isinstance(base_effective, dict) else {}
    if not skill_overlay or not isinstance(skill_overlay, dict):
        return copy.deepcopy(base)
    try:
        merged = copy.deepcopy(base)

        overlay_tools = skill_overlay.get("tools")
        if isinstance(overlay_tools, dict) and overlay_tools:
            _compose_tools(merged, base, overlay_tools)

        overlay_rules = skill_overlay.get("rules")
        if isinstance(overlay_rules, list) and overlay_rules:
            _compose_rules(merged, overlay_rules)

        overlay_fg = skill_overlay.get("file_guard")
        if isinstance(overlay_fg, dict):
            overlay_global = overlay_fg.get("global")
            if isinstance(overlay_global, dict) and overlay_global:
                _compose_file_guard_global(merged, base, overlay_global)

        return merged
    except Exception:  # noqa: BLE001 — 合成异常时仅使用原有权限（fail-closed）
        logger.warning(
            "[skill_authorization] compose.failed fallback=base_effective",
            exc_info=True,
        )
        return copy.deepcopy(base)


# ---------- 请求级授权 Context ----------


@dataclass(frozen=True)
class SkillAuthorizationContext:
    """请求级授权上下文：``PermissionEngine`` 据此查询当前作用域的 ``ACTIVE`` Grant。"""

    session_id: str
    agent_scope_id: str
    request_id: str = ""


_SKILL_AUTHORIZATION_CONTEXT: contextvars.ContextVar[SkillAuthorizationContext | None] = (
    contextvars.ContextVar("jiuwenswarm_skill_authorization_context", default=None)
)


def setup_skill_authorization_context(
    session_id: str | None,
    agent_scope_id: str | None,
    request_id: str | None = None,
) -> contextvars.Token:
    """在请求入口绑定授权 Context（finally 中用返回的 token reset）。"""
    ctx = SkillAuthorizationContext(
        session_id=(session_id or "").strip(),
        agent_scope_id=(agent_scope_id or "").strip(),
        request_id=(request_id or "").strip(),
    )
    return _SKILL_AUTHORIZATION_CONTEXT.set(ctx)


def reset_skill_authorization_context(token: contextvars.Token) -> None:
    _SKILL_AUTHORIZATION_CONTEXT.reset(token)


def get_skill_authorization_context() -> SkillAuthorizationContext | None:
    """读取当前授权 Context；缺失时调用方不得应用 Skill overlay。"""
    return _SKILL_AUTHORIZATION_CONTEXT.get()
