# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Frozen compatibility port of core's legacy layered tool-permission policy.

This module is the byte-faithful core implementation that was temporarily hosted
in the security package; it now lives back in ``openjiuwen.harness.security``.
Downstream callers (jiuwenswarm flash rail, ``agent_teams.security.narrowing``)
keep the exact same decisions.

It is deliberately **not** merged with :mod:`agent_shield.tool_permission.toolguard.tiered`:
the two engines have different rule catalogs and different semantics for
``MEDIUM`` severity plus literal-argument evidence.  Semantic convergence is a
separate follow-up.

Only the hosting package changed; behaviour is unchanged.  Built-in rules are
embedded in the sibling :mod:`openjiuwen.harness.security.builtin_rules_compat`.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_shield.tool_permission.fileguard.unix_bash_literal import (
    LiteralCommand,
    parse_literal_shell,
)
from agent_shield.tool_permission.models import PermissionLevel
from agent_shield.tool_permission.toolguard.shell_ast import (
    ShellAstParseResult,
    parse_shell_for_permission,
)

from .builtin_rules_compat import BUILTIN_RULES
from .patterns import PathMatcher, match_wildcard

logger = logging.getLogger(__name__)


def match_literal_pattern(value: str, pattern: str) -> bool:
    """Wildcard matching only after the complete invocation was verified literal."""
    optional_arguments = pattern.endswith(" *")
    prefix = pattern[:-2] if optional_arguments else pattern
    escaped = re.escape(prefix).replace(r"\*", ".*").replace(r"\?", ".")
    if optional_arguments:
        escaped += "(?: .*)?"
    return re.fullmatch(escaped, value, flags=re.DOTALL) is not None


def pattern_binds_command(pattern: str, call: LiteralCommand) -> bool:
    """A new automatic allowance must identify the executable, not argument text."""
    pattern = pattern.strip()
    if pattern.lower().startswith("re:"):
        expression = pattern[3:].strip()
        prefix = "^" + re.escape(call.argv[0])
        if not any(expression.startswith(prefix + boundary) for boundary in (r"\s", r"\b", " ")):
            return False
        try:
            match = re.search(expression, call.text)
        except re.error:
            return False
        return match is not None and match.start() == 0 and match.end() > len(call.argv[0])
    head = pattern.split(maxsplit=1)[0] if pattern else ""
    return head == call.argv[0] and not any(char in head for char in "*?[")


_TIERED_PATH_MATCHER = PathMatcher()

_STRICT_ORDER = {PermissionLevel.DENY: 0, PermissionLevel.ASK: 1, PermissionLevel.ALLOW: 2}

# Shell operators that indicate command chaining / injection.
# If a command matches an allow pattern but also contains these operators,
# the permission is escalated from ALLOW -> ASK as a safety net.
_SHELL_OPERATORS_RE = re.compile(
    r"[;&|`<>]"  # ; & | ` < > (covers &&, ||, pipes, redirects, backticks)
    r"|\$[({]"  # $( or ${ -- command / variable substitution
    r"|\r?\n"  # newline injection
)

# 规则内 tools 必须同类（与产品设计一致）
_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal"})
_PATH_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "read_text_file",
        "write_text_file",
        "write",
        "read",
        "glob_file_search",
        "glob",
        "list_dir",
        "list_files",
        "grep",
        "search_replace",
    }
)
_NETWORK_TOOLS = frozenset({"mcp_fetch_webpage", "mcp_free_search", "mcp_paid_search"})

_PATH_ARG_KEYS = frozenset(
    {
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
    }
)

# Kept for symbol compatibility with the historical module.  Rules are embedded,
# so the mtime cache is no longer used.
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
    literal_arguments: bool = False


def _package_builtin_rules_path() -> Path:
    """Compat path of the embedded rules (core used a packaged YAML file)."""
    return Path(__file__).resolve().with_name("builtin_rules_compat.py")


def get_package_builtin_rules_path() -> Path:
    """兼容路径：内置规则现随 ``builtin_rules_compat.py`` 一起发布。

    core 旧实现返回 ``openjiuwen/harness/resources/builtin_rules.yaml``；该 YAML
    已随迁移移除，规则以 Python 数据嵌入，故这里返回嵌入模块自身的路径。
    """
    return _package_builtin_rules_path()


def _resolve_builtin_rules_yaml_path() -> Path | None:
    """兼容占位：不再加载包内 YAML（规则已静态嵌入）。"""
    return None


def get_builtin_security_rules() -> list[dict[str, Any]]:
    """内置安全规则列表（core 冻结版本，静态嵌入，规则内容与顺序不变）。"""
    return BUILTIN_RULES


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
    logger.warning(
        "[PermissionEngine] permission.tiered_policy.unknown_severity severity=%r fallback=HIGH",
        severity,
    )
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


def _shell_pattern_matches(pattern: str, command: str, *, literal_arguments: bool = False) -> bool:
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
            # 例如 YAML 双引号落盘后 `C:\Users` 变成非法 \U；add_dir 旧版
            # `posix|win` 第二支整段编译失败
            if "|" in expr:
                for part in expr.split("|"):
                    if _try_subexpr(part.strip()):
                        return True
            logger.warning(
                "[PermissionEngine] permission.tiered_policy.invalid_shell_regex expr=%r",
                expr,
            )
            return False
        return False
    glob_chars = frozenset("*?[")
    if any(ch in p for ch in glob_chars):
        return match_literal_pattern(command, p) if literal_arguments else match_wildcard(command, p)
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
            logger.warning(
                "[PermissionEngine] permission.tiered_policy.invalid_path_regex expr=%r",
                expr,
            )
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
    *,
    literal_arguments: bool = False,
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
        # 路径策略只认 Pipeline B（file_guard）；A 线跳过 path 类 rules
        if r_tools_s and _tool_category(r_tools_s[0]) == "path":
            logger.debug(
                "[PermissionEngine] permission.tiered_policy.rule_skipped id=%r reason=path_rules_moved_to_file_guard",
                rule.get("id"),
            )
            continue
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        if not tiered_policy_rule_matches(
            tool_name,
            pattern,
            tool_args,
            r_tools_s,
            literal_arguments=literal_arguments,
        ):
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
    *,
    literal_arguments: bool = False,
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
        if not tiered_policy_rule_matches(
            tool_name,
            pattern,
            tool_args,
            r_tools_s,
            literal_arguments=literal_arguments,
        ):
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
    *,
    literal_arguments: bool = False,
) -> bool:
    """单条 rule 是否对本次调用匹配（调用前已确认 tool_name in rule_tools）."""
    if not rule_tools:
        return False
    cat = _tool_category(rule_tools[0])
    if cat == "shell":
        return _shell_pattern_matches(pattern, _command_text(tool_args), literal_arguments=literal_arguments)
    if cat == "path":
        # 路径匹配已迁至 file_guard；A 线不再匹配 path
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
        literal_arguments=ctx.literal_arguments,
    )
    if any(lev == PermissionLevel.DENY for lev, _ in builtin_hits):
        return _finalize_hits(builtin_hits, "builtin")

    user_hits = _collect_param_rule_hits(
        ctx.rules,
        tool_name,
        tool_args,
        ctx.mode,
        "rules",
        literal_arguments=ctx.literal_arguments,
    )
    if any(lev == PermissionLevel.DENY for lev, _ in user_hits):
        return _finalize_hits(user_hits, "rules")

    override_hits = _collect_approval_override_hits(
        ctx.approval_overrides,
        tool_name,
        tool_args,
        literal_arguments=ctx.literal_arguments,
    )
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
    contributing = sorted(
        {f"{command}=>{matched_rule}" for command, permission, matched_rule in results if permission == final}
    )
    if not contributing:
        return final, f"{_MR}:shell_subcommands"
    return final, f"{_MR}:shell_subcommands:" + "+".join(contributing)


def _evaluate_tiered_policy_legacy(
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

    # Leaf split only — do not re-escalate after agent_shield / rule evaluation.
    if _tool_category(tool_name) == "shell" and shell_parse is not None and shell_parse.kind == "ok":
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

        return _aggregate_subcommand_results(subcommand_results)

    return _evaluate_single_invocation(
        tool_name,
        tool_args,
        invocation_ctx,
    )


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

    cmd = _command_text(tool_args)
    if cmd and _SHELL_OPERATORS_RE.search(cmd):
        return PermissionLevel.ASK
    return permission


def matched_rule_uses_approval_override(matched_rule: str | None) -> bool:
    """当前结果是否来自 approval_overrides。"""
    if not isinstance(matched_rule, str):
        return False
    return matched_rule.startswith(_APPROVAL_OVERRIDES_PREFIX)


def evaluate_tiered_policy(
    permission_config: Mapping[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[PermissionLevel, str]:
    """Evaluate policy while preserving the public two-item result contract."""
    permission, matched_rule, _ = _evaluate_policy_with_shell_evidence(permission_config, tool_name, tool_args)
    return permission, matched_rule


def _evaluate_policy_with_shell_evidence(
    permission_config: Mapping[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[PermissionLevel, str, bool]:
    """The bool is proof of explicit permission for every literal invocation."""
    shell_type = str(tool_args.get("shell_type") or "auto").lower()
    plan = (
        parse_literal_shell(_command_text(tool_args))
        if tool_name in _SHELL_TOOLS and shell_type in {"auto", "bash", "sh"}
        else None
    )
    if plan is None:
        return (*_evaluate_tiered_policy_legacy(permission_config, tool_name, tool_args), False)
    mode = str(permission_config.get("permission_mode") or "normal").strip().lower()
    if mode not in ("normal", "strict"):
        mode = "normal"
    tools = permission_config.get("tools") or {}
    baseline, baseline_rule = _baseline_level(tools if isinstance(tools, dict) else {}, tool_name)
    if baseline == PermissionLevel.DENY:
        return baseline, baseline_rule or "tools.deny", False
    rules = permission_config.get("rules")
    overrides = permission_config.get("approval_overrides")
    defaults = permission_config.get("defaults")
    ctx = _TieredInvocationContext(
        mode=mode,
        builtin_rules=get_builtin_security_rules(),
        rules=[r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else [],
        approval_overrides=[r for r in overrides if isinstance(r, dict)] if isinstance(overrides, list) else [],
        baseline_level=baseline,
        baseline_rule=baseline_rule,
        defaults_cfg=defaults if isinstance(defaults, dict) else {},
        literal_arguments=True,
    )
    # Original full-text restrictions (including heredoc body rules) cannot disappear.
    # Merge them with every invocation, so ASK can never hide another command's DENY.
    decisions = []
    for rules, namespace in ((ctx.builtin_rules, "builtin"), (ctx.rules, "rules")):
        hits = _collect_param_rule_hits(rules, tool_name, tool_args, mode, namespace)
        restrictive = [(level, label) for level, label in hits if level != PermissionLevel.ALLOW]
        if restrictive:
            decisions.append((_command_text(tool_args), *_finalize_hits(restrictive, namespace)))
    explicit = not decisions
    for call in plan.commands:
        args = _with_shell_command(tool_args, call.text)
        permission, matched = _evaluate_single_invocation(tool_name, args, ctx)
        decisions.append((call.text, permission, matched))
        explicit = explicit and _literal_explicit_allow(ctx, tool_name, args, call)
    permission, matched = _aggregate_subcommand_results(decisions)
    if permission != PermissionLevel.ALLOW or explicit:
        return permission, matched, permission == PermissionLevel.ALLOW and explicit
    return (*_evaluate_tiered_policy_legacy(permission_config, tool_name, tool_args), False)


def _literal_explicit_allow(
    ctx: _TieredInvocationContext,
    tool_name: str,
    args: dict[str, Any],
    call: LiteralCommand,
) -> bool:
    """Require a real rule naming the executable; baseline allow is insufficient."""
    for override in ctx.approval_overrides:
        pattern = override.get("pattern", "")
        if (
            isinstance(pattern, str)
            and pattern_binds_command(pattern, call)
            and _collect_approval_override_hits([override], tool_name, args, literal_arguments=True)
        ):
            return True
    if _collect_param_rule_hits(
        ctx.builtin_rules,
        tool_name,
        args,
        ctx.mode,
        "builtin",
        literal_arguments=True,
    ):
        return False
    for rule in ctx.rules:
        pattern = rule.get("pattern", "")
        if isinstance(pattern, str) and pattern_binds_command(pattern, call):
            hits = _collect_param_rule_hits([rule], tool_name, args, ctx.mode, "rules", literal_arguments=True)
            if hits and all(level == PermissionLevel.ALLOW for level, _ in hits):
                return True
    return False
