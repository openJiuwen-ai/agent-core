# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""分层工具权限策略（tiered_policy）：内置参数规则 > 用户参数规则；整工具存在则不用默认。"""

from __future__ import annotations

import logging
import json
import re
import sys
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.harness.security.builtin_platforms import filter_entries_for_platform
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.patterns import PathMatcher, match_wildcard
from openjiuwen.harness.security.shell_ast import (
    ShellAstParseResult,
    parse_shell_for_permission,
)

logger = logging.getLogger(__name__)

_TIERED_PATH_MATCHER = PathMatcher()

_STRICT_ORDER = {PermissionLevel.DENY: 0, PermissionLevel.ASK: 1, PermissionLevel.ALLOW: 2}

# 规则内 tools 必须同类（与产品设计一致）
_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal", "powershell"})

_PATH_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
    "send_file_to_user",
})
_NETWORK_TOOLS = frozenset({"mcp_fetch_webpage", "mcp_free_search", "mcp_paid_search"})

_PATH_ARG_KEYS = frozenset({
    "path", "file_path", "target_file", "file", "old_path", "new_path",
    "source_path", "dest_path", "directory", "dir",
    "abs_file_path_list",
})

# (resolved_path_str, mtime, rules)；文件变更后 mtime 变化会重新加载
_BUILTIN_RULES_CACHE: tuple[str, float, list[dict[str, Any]]] | None = None

_MR = "tiered_policy"
_APPROVAL_OVERRIDES_PREFIX = f"{_MR}:approval_overrides"


@dataclass(frozen=True)
class _TieredInvocationContext:
    mode: str
    builtin_rules: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    approval_overrides: list[dict[str, Any]]
    baseline_level: PermissionLevel | None
    baseline_rule: str | None
    defaults_cfg: dict[str, Any]


def _package_builtin_rules_path() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / "builtin_rules.yaml"


def get_package_builtin_rules_path() -> Path:
    """包内 ``resources/builtin_rules.yaml`` 的绝对路径。

    不经过用户配置目录；供测试或需固定使用发行版内置规则文件的场景调用。
    """
    return _package_builtin_rules_path()


def _resolve_builtin_rules_yaml_path() -> Path | None:
    """仅使用包内 ``openjiuwen/harness/resources/builtin_rules.yaml``（不再查用户/环境目录）。"""
    pkg_path = _package_builtin_rules_path()
    if pkg_path.is_file():
        return pkg_path
    logger.warning(
        "[PermissionEngine] permission.tiered_policy.builtin_rules_missing package_path=%s",
        pkg_path,
    )
    return None


def get_builtin_security_rules(*, platform: str | None = None) -> list[dict[str, Any]]:
    """内置安全规则列表（进程内按路径+mtime 缓存全量，返回时按平台过滤）。

    仅加载包内 ``openjiuwen/harness/resources/builtin_rules.yaml``。
    ``platform`` 为 ``None`` 时使用当前 OS（``windows`` / ``unix``）。
    """
    global _BUILTIN_RULES_CACHE
    path = _resolve_builtin_rules_yaml_path()
    if path is None:
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    key = str(path.resolve())
    cached = _BUILTIN_RULES_CACHE
    if cached is None or cached[0] != key or cached[1] != mtime:
        rules = _read_builtin_security_rules(path)
        _BUILTIN_RULES_CACHE = (key, mtime, rules)
    else:
        rules = cached[2]
    return filter_entries_for_platform(rules, platform=platform)


def _read_builtin_security_rules(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [r for r in (data.get("rules") or []) if isinstance(r, dict)]


def _parse_level(value: str) -> PermissionLevel:
    v = (value or "").strip().lower()
    return PermissionLevel(v)


def strictest(*levels: PermissionLevel) -> PermissionLevel:
    if not levels:
        return PermissionLevel.ASK
    return min(levels, key=lambda p: _STRICT_ORDER[p])


def severity_to_decision(severity: str, permission_mode: str) -> PermissionLevel:
    sev = (severity or "").strip().upper()
    mode = (permission_mode or "normal").strip().lower()
    if mode not in ("normal", "strict"):
        mode = "normal"
    if sev == "LOW":
        return PermissionLevel.ALLOW
    if sev == "MEDIUM":
        return PermissionLevel.ASK if mode == "strict" else PermissionLevel.ALLOW
    if sev == "HIGH":
        return PermissionLevel.ASK
    if sev == "CRITICAL":
        return PermissionLevel.DENY if mode == "strict" else PermissionLevel.ASK
    logger.warning("[PermissionEngine] permission.tiered_policy.unknown_severity severity=%r fallback=HIGH", severity)
    return PermissionLevel.ASK


def _tool_category(tool_name: str) -> str | None:
    if tool_name in _SHELL_TOOLS:
        return "shell"
    if tool_name in _PATH_TOOLS:
        return "path"
    if tool_name in _NETWORK_TOOLS:
        return "network"
    return None


def rule_tools_category_consistent(tools: list[str]) -> bool:
    cats: set[str] = set()
    for t in tools:
        c = _tool_category(t)
        if c is None:
            return False
        cats.add(c)
        if len(cats) > 1:
            return False
    return bool(cats)


def _normalize_shell_whitespace(command: str) -> str:
    """匹配前空白归一化（压缩连续空白）。"""
    return re.sub(r"\s+", " ", (command or "").strip())


def _command_text(tool_args: dict[str, Any]) -> str:
    return _normalize_shell_whitespace(
        str(tool_args.get("command", "") or tool_args.get("cmd", "") or ""),
    )


def _shell_pattern_matches(pattern: str, command: str) -> bool:
    if not pattern or not command:
        return False
    p = pattern.strip()
    if p.lower().startswith("re:"):
        expr = p[3:].strip()
        flags = re.IGNORECASE if sys.platform == "win32" else 0
        norm = command.replace("\\", "/")

        def _try_subexpr(sub: str) -> bool:
            if not sub:
                return False
            try:
                if re.search(sub, command, flags):
                    return True
                if norm != command and re.search(sub, norm, flags):
                    return True
            except re.error:
                return False
            return False

        try:
            if re.search(expr, command, flags):
                return True
            if norm != command and re.search(expr, norm, flags):
                return True
        except re.error:
            # 例如 YAML 双引号落盘后 `C:\Users` 变成非法 \U；add_dir 旧版 `posix|win` 第二支整段编译失败
            if "|" in expr:
                for part in expr.split("|"):
                    if _try_subexpr(part.strip()):
                        return True
            logger.warning("[PermissionEngine] permission.tiered_policy.invalid_shell_regex expr=%r", expr)
            return False
        return False
    glob_chars = frozenset("*?[")
    if any(ch in p for ch in glob_chars):
        return match_wildcard(command, p)
    return command == p


def _path_pattern_matches(pattern: str, value: str) -> bool:
    if not pattern or not value:
        return False
    p = pattern.strip()
    if p.lower().startswith("re:"):
        expr = p[3:].strip()
        flags = re.IGNORECASE if sys.platform == "win32" else 0
        try:
            return bool(re.search(expr, value.replace("\\", "/"), flags))
        except re.error:
            logger.warning("[PermissionEngine] permission.tiered_policy.invalid_path_regex expr=%r", expr)
            return False
    return _TIERED_PATH_MATCHER.match_path(p, value)


def expand_path_arg_values(raw: Any) -> list[str]:
    """把路径参数展开为单个路径字符串（支持 JSON 数组与 list）。"""
    if raw is None:
        return []
    items: list[Any]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        if stripped[:1] == "[":
            try:
                parsed = json.loads(stripped)
            except (TypeError, ValueError):
                return [stripped]
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, str):
                items = [parsed]
            else:
                items = [stripped]
        else:
            items = [stripped]
    else:
        items = [raw]
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _tool_arg_value_looks_like_path(arg_key: str, value: str) -> bool:
    """是否把该参数值纳入路径类 pattern 匹配（已知名或形似路径）。"""
    if arg_key in _PATH_ARG_KEYS:
        return True
    if "/" in value or "\\" in value:
        return True
    return len(value) > 1 and value[1] == ":"


def _iter_path_strings(_tool_name: str, tool_args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k, v in tool_args.items():
        for item in expand_path_arg_values(v):
            if _tool_arg_value_looks_like_path(k, item):
                out.append(item)
    return out


def _rule_layer(rule: dict[str, Any]) -> str:
    layer = str(rule.get("_config_layer") or "").strip().lower()
    if layer in ("global", "user", "session", "builtin"):
        return layer
    # 未标记：视为 global（包内 builtin 走独立列表；裸 rules 偏组织底线）
    return "global"


def _collect_param_rule_hits(
        rules: list[dict[str, Any]],
        tool_name: str,
        tool_args: dict[str, Any],
        mode: str,
        label_ns: str,
        *,
        layers: frozenset[str] | None = None,
) -> list[tuple[PermissionLevel, str]]:
    """参数级规则命中列表 (level, label)；``label_ns`` 为 ``builtin`` 或 ``rules``。"""
    hits: list[tuple[PermissionLevel, str]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if layers is not None and _rule_layer(rule) not in layers:
            continue
        r_tools = rule.get("tools") or []
        if isinstance(r_tools, str):
            r_tools = [r_tools]
        if not isinstance(r_tools, list) or tool_name not in r_tools:
            continue
        r_tools_s = [str(x).strip() for x in r_tools if isinstance(x, str) and str(x).strip()]
        if not rule_tools_category_consistent(r_tools_s):
            logger.warning(
                "[PermissionEngine] permission.tiered_policy.rule_skipped "
                "id=%r reason=inconsistent_tool_category tools=%s",
                rule.get("id"),
                r_tools_s,
            )
            continue
        # 路径策略只认 Pipeline B（file_guard）；A 线跳过 path 类 rules
        if r_tools_s and _tool_category(r_tools_s[0]) == "path":
            logger.debug(
                "[PermissionEngine] permission.tiered_policy.rule_skipped "
                "id=%r reason=path_rules_moved_to_file_guard",
                rule.get("id"),
            )
            continue
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        if not tiered_policy_rule_matches(tool_name, pattern, tool_args, r_tools_s):
            continue
        action = rule.get("action")
        if isinstance(action, str) and action.strip():
            dec = _parse_level(action)
        else:
            sev = rule.get("severity", "HIGH")
            if not isinstance(sev, str):
                sev = "HIGH"
            dec = severity_to_decision(sev, mode)
        rid = rule.get("id", "")
        label = f"{label_ns}[{rid}]" if rid else f"{label_ns}[?]"
        hits.append((dec, label))
    return hits


def _collect_approval_override_hits(
        rules: list[dict[str, Any]],
        tool_name: str,
        tool_args: dict[str, Any],
) -> list[str]:
    """用户审批后持久化的 allow override 命中列表。

    仅 command（及非 path）类生效；``match_type: path`` / 路径工具 override 忽略，
    路径放行只认 ``file_guard``。
    """
    hits: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action = str(rule.get("action") or "").strip().lower()
        if action != "allow":
            continue
        match_type = str(rule.get("match_type") or "").strip().lower()
        if match_type == "path":
            logger.debug(
                "[PermissionEngine] permission.tiered_policy.override_skipped "
                "id=%r reason=path_overrides_moved_to_file_guard",
                rule.get("id"),
            )
            continue
        r_tools = rule.get("tools") or []
        if isinstance(r_tools, str):
            r_tools = [r_tools]
        if not isinstance(r_tools, list) or tool_name not in r_tools:
            continue
        r_tools_s = [str(x).strip() for x in r_tools if isinstance(x, str) and str(x).strip()]
        if not rule_tools_category_consistent(r_tools_s):
            logger.warning(
                "[PermissionEngine] permission.tiered_policy.override_skipped "
                "id=%r reason=inconsistent_tool_category tools=%s",
                rule.get("id"),
                r_tools_s,
            )
            continue
        if r_tools_s and _tool_category(r_tools_s[0]) == "path":
            logger.debug(
                "[PermissionEngine] permission.tiered_policy.override_skipped "
                "id=%r reason=path_overrides_moved_to_file_guard",
                rule.get("id"),
            )
            continue
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        if not tiered_policy_rule_matches(tool_name, pattern, tool_args, r_tools_s):
            continue
        rid = rule.get("id", "")
        label = f"approval_overrides[{rid}]" if rid else "approval_overrides[?]"
        hits.append(label)
    return hits


def tiered_policy_rule_matches(
        tool_name: str,
        pattern: str,
        tool_args: dict[str, Any],
        rule_tools: list[str],
) -> bool:
    """单条 rule 是否对本次调用匹配（调用前已确认 tool_name in rule_tools）."""
    if not rule_tools:
        return False
    cat = _tool_category(rule_tools[0])
    if cat == "shell":
        return _shell_pattern_matches(pattern, _command_text(tool_args))
    if cat == "path":
        # 路径匹配已迁至 file_guard；A 线不再匹配 path
        return False
    if cat == "network":
        from openjiuwen.harness.security.network_guard import network_url_text

        url = network_url_text(tool_args)
        if not url:
            return False
        from openjiuwen.harness.security.patterns import URLMatcher

        return URLMatcher().match_url(pattern, url)
    return False


def _baseline_level(tools_cfg: dict[str, Any], tool_name: str) -> tuple[PermissionLevel | None, str | None]:
    if tool_name not in tools_cfg:
        return None, None
    raw = tools_cfg[tool_name]
    if isinstance(raw, str):
        try:
            return _parse_level(raw), f"tools.{tool_name}"
        except ValueError:
            logger.warning(
                "[PermissionEngine] permission.tiered_policy.invalid_tool_level tool=%s value=%r",
                tool_name,
                raw,
            )
            return None, None
    if isinstance(raw, dict) and isinstance(raw.get("*"), str):
        try:
            logger.warning(
                "[PermissionEngine] permission.tiered_policy.tools_dict_non_scalar tool=%s using=asterisk_only",
                tool_name,
            )
            return _parse_level(raw["*"]), f"tools.{tool_name}.*"
        except ValueError:
            return None, None
    logger.warning(
        "[PermissionEngine] permission.tiered_policy.invalid_tool_baseline tool=%s reason=non_scalar_level",
        tool_name,
    )
    return None, None


def _finalize_hits(hits: list[tuple[PermissionLevel, str]], prefix: str) -> tuple[PermissionLevel, str]:
    if any(lev == PermissionLevel.DENY for lev, _ in hits):
        contributing = sorted({r for lev, r in hits if lev == PermissionLevel.DENY})
        return PermissionLevel.DENY, f"{_MR}:{prefix}:deny:" + "+".join(contributing)
    final = strictest(*(h[0] for h in hits))
    contributing = sorted({r for lev, r in hits if lev == final})
    matched = f"{_MR}:{prefix}:" + "+".join(contributing) if contributing else f"{_MR}:{prefix}"
    return final, matched


def _shell_ast_floor(
        shell_parse: ShellAstParseResult | None,
) -> tuple[PermissionLevel | None, str | None]:
    if shell_parse is None:
        return None, None
    flags = shell_parse.flags
    if shell_parse.kind == "too_complex":
        reason = shell_parse.reason or "unsupported_complex_structure"
        return PermissionLevel.ASK, f"{_MR}:shell_ast:too_complex:{reason}"
    if shell_parse.kind == "parse_unavailable" and flags.has_risky_structure():
        reason = shell_parse.reason or "conservative_fallback"
        return PermissionLevel.ASK, f"{_MR}:shell_ast:parse_unavailable:{reason}"
    if any((
            flags.has_input_redirection,
            flags.has_output_redirection,
            flags.has_command_substitution,
            flags.has_process_substitution,
            flags.has_heredoc,
    )):
        return PermissionLevel.ASK, f"{_MR}:shell_ast:structure_guard"
    return None, None


def _apply_shell_ast_floor(
        permission: PermissionLevel,
        matched_rule: str,
        shell_floor: PermissionLevel | None,
        shell_floor_rule: str | None,
) -> tuple[PermissionLevel, str]:
    if shell_floor is None:
        return permission, matched_rule
    final = strictest(permission, shell_floor)
    if final == permission:
        return permission, matched_rule
    if matched_rule and shell_floor_rule:
        return final, f"{shell_floor_rule}|{matched_rule}"
    return final, shell_floor_rule or matched_rule


def _with_shell_command(tool_args: dict[str, Any], command: str) -> dict[str, Any]:
    sub_args = dict(tool_args)
    if "command" in sub_args or "cmd" not in sub_args:
        sub_args["command"] = command
    if "cmd" in sub_args:
        sub_args["cmd"] = command
    return sub_args


def _evaluate_single_invocation(
        tool_name: str,
        tool_args: dict[str, Any],
        ctx: _TieredInvocationContext,
) -> tuple[PermissionLevel, str]:
    """评估顺序（P1）：Global DENY → … → Global ASK 底线 → User rules → overrides → tools/defaults。

    ``approval_overrides`` **不可**放宽 Global/builtin 底线。
    """
    builtin_hits = _collect_param_rule_hits(
        ctx.builtin_rules,
        tool_name,
        tool_args,
        ctx.mode,
        "builtin",
    )
    if any(lev == PermissionLevel.DENY for lev, _ in builtin_hits):
        return _finalize_hits(builtin_hits, "builtin")

    global_hits = _collect_param_rule_hits(
        ctx.rules,
        tool_name,
        tool_args,
        ctx.mode,
        "rules",
        layers=frozenset({"global"}),
    )
    if any(lev == PermissionLevel.DENY for lev, _ in global_hits):
        return _finalize_hits(global_hits, "rules")

    overlay_hits = _collect_param_rule_hits(
        ctx.rules,
        tool_name,
        tool_args,
        ctx.mode,
        "rules",
        layers=frozenset({"user", "session"}),
    )
    if any(lev == PermissionLevel.DENY for lev, _ in overlay_hits):
        return _finalize_hits(overlay_hits, "rules")

    # Global ASK 底线（builtin CRITICAL→ASK / Global rules ASK）优先于 approval_overrides
    if builtin_hits:
        return _finalize_hits(builtin_hits, "builtin")
    if global_hits:
        return _finalize_hits(global_hits, "rules")

    override_hits = _collect_approval_override_hits(ctx.approval_overrides, tool_name, tool_args)
    if override_hits:
        contributing = sorted(set(override_hits))
        return PermissionLevel.ALLOW, _APPROVAL_OVERRIDES_PREFIX + ":" + "+".join(contributing)

    if overlay_hits:
        return _finalize_hits(overlay_hits, "rules")

    if ctx.baseline_level is not None:
        return ctx.baseline_level, ctx.baseline_rule or f"{_MR}:tools"

    if "*" in ctx.defaults_cfg and isinstance(ctx.defaults_cfg["*"], str):
        try:
            dl = _parse_level(ctx.defaults_cfg["*"])
            return dl, f"{_MR}:defaults.*"
        except ValueError:
            logger.warning(
                "[PermissionEngine] permission.tiered_policy.invalid_default_level value=%r",
                ctx.defaults_cfg["*"],
            )

    return PermissionLevel.ASK, f"{_MR}:fallback(no_config)"


def _aggregate_subcommand_results(
        results: list[tuple[str, PermissionLevel, str]],
) -> tuple[PermissionLevel, str]:
    if not results:
        return PermissionLevel.ASK, f"{_MR}:shell_subcommands:fallback"
    if len(results) == 1:
        _, permission, matched_rule = results[0]
        return permission, matched_rule

    final = strictest(*(permission for _, permission, _ in results))
    # 各段均由 approval_overrides 放行时，matched_rule 归并为 overrides 前缀，
    # 以便 findings 升级跳过（与「会话记住分段」语义一致）。
    if (
        final == PermissionLevel.ALLOW
        and all(permission == PermissionLevel.ALLOW for _, permission, _ in results)
        and all(matched_rule_uses_approval_override(rule) for _, _, rule in results)
    ):
        contributing = sorted({
            rule[len(_APPROVAL_OVERRIDES_PREFIX) + 1:]
            if rule.startswith(_APPROVAL_OVERRIDES_PREFIX + ":")
            else rule
            for _, _, rule in results
        })
        return PermissionLevel.ALLOW, _APPROVAL_OVERRIDES_PREFIX + ":shell_subcommands:" + "+".join(
            contributing
        )

    contributing = sorted({
        f"{command}=>{matched_rule}"
        for command, permission, matched_rule in results
        if permission == final
    })
    if not contributing:
        return final, f"{_MR}:shell_subcommands"
    return final, f"{_MR}:shell_subcommands:" + "+".join(contributing)


def _evaluate_shell_full_command_floor(
        tool_name: str,
        tool_args: dict[str, Any],
        ctx: _TieredInvocationContext,
) -> tuple[PermissionLevel, str] | None:
    """整命令上的 builtin / Global 底线（如 ``curl | bash``），不匹配 approval_overrides。

    overrides / defaults 仍按子命令分段评估，与 suggestion 落盘一致。
    """
    builtin_hits = _collect_param_rule_hits(
        ctx.builtin_rules,
        tool_name,
        tool_args,
        ctx.mode,
        "builtin",
    )
    if any(lev == PermissionLevel.DENY for lev, _ in builtin_hits):
        return _finalize_hits(builtin_hits, "builtin")

    global_hits = _collect_param_rule_hits(
        ctx.rules,
        tool_name,
        tool_args,
        ctx.mode,
        "rules",
        layers=frozenset({"global"}),
    )
    if any(lev == PermissionLevel.DENY for lev, _ in global_hits):
        return _finalize_hits(global_hits, "rules")

    overlay_hits = _collect_param_rule_hits(
        ctx.rules,
        tool_name,
        tool_args,
        ctx.mode,
        "rules",
        layers=frozenset({"user", "session"}),
    )
    if any(lev == PermissionLevel.DENY for lev, _ in overlay_hits):
        return _finalize_hits(overlay_hits, "rules")

    if builtin_hits:
        return _finalize_hits(builtin_hits, "builtin")
    if global_hits:
        return _finalize_hits(global_hits, "rules")
    return None


def evaluate_tiered_policy(
        permission_config: Mapping[str, Any],
        tool_name: str,
        tool_args: dict[str, Any],
) -> tuple[PermissionLevel, str]:
    """返回 (最终权限, matched_rule 摘要).

    - 整工具 ``deny`` 优先于参数级放行。
    - 内置参数规则一旦命中则不再看用户 ``rules``。
    - 有参数级命中时结果仅来自该层（内置或用户）。
    - 无参数级命中时：仅有整工具则用整工具；否则仅用默认（整工具存在则忽略默认）。
    - Shell ``simple``：先整命令安全底线，再按子命令分段评估后聚合。
    """
    mode = str(permission_config.get("permission_mode") or "normal").strip().lower()
    if mode not in ("normal", "strict"):
        mode = "normal"

    tools_cfg = permission_config.get("tools") or {}
    if not isinstance(tools_cfg, dict):
        tools_cfg = {}

    defaults_cfg = permission_config.get("defaults") or {}
    if not isinstance(defaults_cfg, dict):
        defaults_cfg = {}

    rules = permission_config.get("rules") or []
    if not isinstance(rules, list):
        rules = []
    approval_overrides = permission_config.get("approval_overrides") or []
    if not isinstance(approval_overrides, list):
        approval_overrides = []

    bl, bl_rule = _baseline_level(tools_cfg, tool_name)
    if bl == PermissionLevel.DENY:
        return PermissionLevel.DENY, bl_rule or f"{_MR}:tools.deny"

    shell_parse: ShellAstParseResult | None = None
    if _tool_category(tool_name) == "shell":
        shell_parse = parse_shell_for_permission(_command_text(tool_args))
    shell_floor, shell_floor_rule = _shell_ast_floor(shell_parse)
    builtin_rules = get_builtin_security_rules()
    invocation_ctx = _TieredInvocationContext(
        mode=mode,
        builtin_rules=builtin_rules,
        rules=rules,
        approval_overrides=approval_overrides,
        baseline_level=bl,
        baseline_rule=bl_rule,
        defaults_cfg=defaults_cfg,
    )

    if _tool_category(tool_name) == "shell" and shell_parse is not None and shell_parse.kind == "simple":
        full_floor = _evaluate_shell_full_command_floor(tool_name, tool_args, invocation_ctx)
        if full_floor is not None:
            return _apply_shell_ast_floor(*full_floor, shell_floor, shell_floor_rule)

        subcommand_results: list[tuple[str, PermissionLevel, str]] = []
        for subcommand in shell_parse.subcommands:
            if not subcommand.text:
                continue
            sub_args = _with_shell_command(tool_args, subcommand.text)
            sub_permission, sub_rule = _evaluate_single_invocation(
                tool_name,
                sub_args,
                invocation_ctx,
            )
            subcommand_results.append((subcommand.text, sub_permission, sub_rule))
            if sub_permission == PermissionLevel.DENY:
                break

        aggregated = _aggregate_subcommand_results(subcommand_results)
        return _apply_shell_ast_floor(*aggregated, shell_floor, shell_floor_rule)

    result = _evaluate_single_invocation(
        tool_name,
        tool_args,
        invocation_ctx,
    )
    return _apply_shell_ast_floor(*result, shell_floor, shell_floor_rule)


def global_baseline_blocks_persist(
        permission_config: Mapping[str, Any],
        tool_name: str,
        tool_args: dict[str, Any],
) -> bool:
    """Global/builtin 底线（DENY 或 CRITICAL→ASK 等）命中时禁止 pattern 永久/会话放宽。"""
    mode = str(permission_config.get("permission_mode") or "normal").strip().lower()
    if mode not in ("normal", "strict"):
        mode = "normal"
    rules = permission_config.get("rules") or []
    if not isinstance(rules, list):
        rules = []
    builtin_hits = _collect_param_rule_hits(
        get_builtin_security_rules(),
        tool_name,
        tool_args,
        mode,
        "builtin",
    )
    if builtin_hits:
        return True
    global_hits = _collect_param_rule_hits(
        rules,
        tool_name,
        tool_args,
        mode,
        "rules",
        layers=frozenset({"global"}),
    )
    return bool(global_hits)


def maybe_escalate_shell_operators(
        tool_name: str,
        tool_args: dict[str, Any],
        permission: PermissionLevel,
) -> PermissionLevel:
    """重定向 / 命令替换等危险结构时 ALLOW→ASK；单纯 ``|`` / ``&&`` / ``;`` 不抬。

    与 findings 的 simple-compound vs risky-structure 分级、以及
    ``shell_subcommands`` 分段评估保持一致。
    """
    if tool_name not in ("mcp_exec_command", "bash", "create_terminal", "powershell"):
        return permission
    if permission != PermissionLevel.ALLOW:
        return permission

    cmd = _command_text(tool_args)
    if not cmd:
        return permission

    parsed = parse_shell_for_permission(cmd)
    if parsed.kind == "too_complex":
        return PermissionLevel.ASK
    flags = parsed.flags
    if any((
            flags.has_subshell,
            flags.has_command_group,
            flags.has_command_substitution,
            flags.has_process_substitution,
            flags.has_parameter_expansion,
            flags.has_heredoc,
            flags.has_input_redirection,
            flags.has_output_redirection,
    )):
        return PermissionLevel.ASK
    # 仅管道 / 复合算子：留给分段评估 + INFO findings，不在此抬 ASK
    return permission


def matched_rule_uses_approval_override(matched_rule: str | None) -> bool:
    """当前结果是否来自 approval_overrides。"""
    if not isinstance(matched_rule, str):
        return False
    return matched_rule.startswith(_APPROVAL_OVERRIDES_PREFIX)
