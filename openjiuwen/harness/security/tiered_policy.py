# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""分层工具权限策略（tiered_policy）：内置参数规则 > 用户参数规则；整工具存在则不用默认。"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

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
_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal"})
_PATH_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
})
_NETWORK_TOOLS = frozenset({"mcp_fetch_webpage", "mcp_free_search", "mcp_paid_search"})

_PATH_ARG_KEYS = frozenset({
    "path", "file_path", "target_file", "file", "old_path", "new_path",
    "source_path", "dest_path", "directory", "dir",
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


def get_builtin_security_rules() -> list[dict[str, Any]]:
    """内置安全规则列表（进程内按路径+mtime 缓存）。

    仅加载包内 ``openjiuwen/harness/resources/builtin_rules.yaml``。
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
    if _BUILTIN_RULES_CACHE is not None:
        ck, mt, rules = _BUILTIN_RULES_CACHE
        if ck == key and mt == mtime:
            return rules
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rules = [r for r in (data.get("rules") or []) if isinstance(r, dict)]
    _BUILTIN_RULES_CACHE = (key, mtime, rules)
    return rules


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


def _command_text(tool_args: dict[str, Any]) -> str:
    return str(tool_args.get("command", "") or tool_args.get("cmd", "") or "").strip()


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
        if not isinstance(v, str) or not v.strip():
            continue
        if _tool_arg_value_looks_like_path(k, v):
            out.append(v.strip())
    return out


def _collect_param_rule_hits(
        rules: list[dict[str, Any]],
        tool_name: str,
        tool_args: dict[str, Any],
        mode: str,
        label_ns: str,
) -> list[tuple[PermissionLevel, str]]:
    """参数级规则命中列表 (level, label)；``label_ns`` 为 ``builtin`` 或 ``rules``。"""
    hits: list[tuple[PermissionLevel, str]] = []
    for rule in rules:
        if not isinstance(rule, dict):
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
    """用户审批后持久化的 allow override 命中列表。"""
    hits: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action = str(rule.get("action") or "").strip().lower()
        if action != "allow":
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
        for val in _iter_path_strings(tool_name, tool_args):
            if _path_pattern_matches(pattern, val):
                return True
        return False
    if cat == "network":
        # 产品设计：网络类暂仅整工具；参数规则不匹配
        return False
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
    builtin_hits = _collect_param_rule_hits(
        ctx.builtin_rules,
        tool_name,
        tool_args,
        ctx.mode,
        "builtin",
    )
    if any(lev == PermissionLevel.DENY for lev, _ in builtin_hits):
        return _finalize_hits(builtin_hits, "builtin")

    user_hits = _collect_param_rule_hits(
        ctx.rules,
        tool_name,
        tool_args,
        ctx.mode,
        "rules",
    )
    if any(lev == PermissionLevel.DENY for lev, _ in user_hits):
        return _finalize_hits(user_hits, "rules")

    override_hits = _collect_approval_override_hits(ctx.approval_overrides, tool_name, tool_args)
    if override_hits:
        contributing = sorted(set(override_hits))
        return PermissionLevel.ALLOW, _APPROVAL_OVERRIDES_PREFIX + ":" + "+".join(contributing)

    if builtin_hits:
        return _finalize_hits(builtin_hits, "builtin")

    if user_hits:
        return _finalize_hits(user_hits, "rules")

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
    contributing = sorted({
        f"{command}=>{matched_rule}"
        for command, permission, matched_rule in results
        if permission == final
    })
    if not contributing:
        return final, f"{_MR}:shell_subcommands"
    return final, f"{_MR}:shell_subcommands:" + "+".join(contributing)


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


def maybe_escalate_shell_operators(
        tool_name: str,
        tool_args: dict[str, Any],
        permission: PermissionLevel,
) -> PermissionLevel:
    """与旧版一致：命令含链式/注入元字符时 ALLOW→ASK."""
    if tool_name not in ("mcp_exec_command", "bash", "create_terminal"):
        return permission
    if permission != PermissionLevel.ALLOW:
        return permission
    from openjiuwen.harness.security.checker import _SHELL_OPERATORS_RE

    cmd = _command_text(tool_args)
    if cmd and _SHELL_OPERATORS_RE.search(cmd):
        return PermissionLevel.ASK
    return permission


def matched_rule_uses_approval_override(matched_rule: str | None) -> bool:
    """当前结果是否来自 approval_overrides。"""
    if not isinstance(matched_rule, str):
        return False
    return matched_rule.startswith(_APPROVAL_OVERRIDES_PREFIX)
