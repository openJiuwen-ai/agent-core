# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pipeline A tool/command policy: builtin param rules > user rules; tool baseline beats defaults."""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
    canonicalize_shell_command_for_permission,
)
from openjiuwen.harness.security.permission_engine.toolguard.pattern_matchers import PathMatcher, match_wildcard
from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import (
    ShellAstParseResult,
    ShellSubcommand,
    parse_shell_for_permission,
)

logger = logging.getLogger(__name__)

_TIERED_PATH_MATCHER = PathMatcher()

_STRICT_ORDER = {PermissionLevel.DENY: 0, PermissionLevel.ASK: 1, PermissionLevel.ALLOW: 2}

# 规则内 tools 必须同类（与产品设计一致）
_SHELL_TOOLS = frozenset({
    "bash",
    "powershell",
    "core.powershell",
    "mcp_exec_command",
    "create_terminal",
})
_INTERPRETER_SINK_NAMES = frozenset({
    "bash", "sh", "zsh", "dash", "ash", "fish",
    "python", "python3", "pythonw", "py",
    "node", "nodejs",
    "ruby", "perl",
    "pwsh", "powershell",
    "cmd",
    "iex", "invoke-expression", "invoke-command",
})
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

_MR = "tiered_policy"
_APPROVAL_OVERRIDES_PREFIX = f"{_MR}:approval_overrides"


@dataclass(frozen=True)
class _TieredInvocationContext:
    builtin_rules: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    approval_overrides: list[dict[str, Any]]
    baseline_level: PermissionLevel | None
    baseline_rule: str | None
    defaults_cfg: dict[str, Any]


def _parse_level(value: str) -> PermissionLevel:
    v = (value or "").strip().lower()
    return PermissionLevel(v)


def strictest(*levels: PermissionLevel) -> PermissionLevel:
    if not levels:
        return PermissionLevel.ASK
    return min(levels, key=lambda p: _STRICT_ORDER[p])


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
        if not (isinstance(action, str) and action.strip()):
            logger.debug(
                "[PermissionEngine] permission.tiered_policy.rule_skipped "
                "id=%r reason=missing_action",
                rule.get("id"),
            )
            continue
        dec = _parse_level(action)
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


def _as_shell_guard_flag(value: Any, default: bool = True) -> bool:
    return value if isinstance(value, bool) else default


def _read_shell_guard(permission_config: Mapping[str, Any]) -> tuple[bool, bool]:
    raw = permission_config.get("shell_guard")
    if not isinstance(raw, dict):
        return True, True
    return (
        _as_shell_guard_flag(raw.get("unknown_structure")),
        _as_shell_guard_flag(raw.get("interpreter_sink")),
    )


def _shell_ast_floor(
        shell_parse: ShellAstParseResult | None,
        *,
        unknown_structure: bool,
) -> tuple[PermissionLevel | None, str | None]:
    if shell_parse is None or not unknown_structure:
        return None, None
    flags = shell_parse.flags
    if shell_parse.kind == "too_complex":
        reason = shell_parse.reason or "unsupported_complex_structure"
        return PermissionLevel.ASK, f"{_MR}:shell_ast:too_complex:{reason}"
    if shell_parse.kind == "parse_unavailable" and flags.has_risky_structure():
        reason = shell_parse.reason or "conservative_fallback"
        return PermissionLevel.ASK, f"{_MR}:shell_ast:parse_unavailable:{reason}"
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
        "builtin",
    )
    if any(lev == PermissionLevel.DENY for lev, _ in builtin_hits):
        return _finalize_hits(builtin_hits, "builtin")

    user_hits = _collect_param_rule_hits(
        ctx.rules,
        tool_name,
        tool_args,
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


def _evaluate_param_rules_only(
        tool_name: str,
        tool_args: dict[str, Any],
        ctx: _TieredInvocationContext,
) -> tuple[PermissionLevel, str] | None:
    builtin_hits = _collect_param_rule_hits(
        ctx.builtin_rules,
        tool_name,
        tool_args,
        "builtin",
    )
    if any(lev == PermissionLevel.DENY for lev, _ in builtin_hits):
        return _finalize_hits(builtin_hits, "builtin")

    user_hits = _collect_param_rule_hits(
        ctx.rules,
        tool_name,
        tool_args,
        "rules",
    )
    if any(lev == PermissionLevel.DENY for lev, _ in user_hits):
        return _finalize_hits(user_hits, "rules")
    if builtin_hits:
        return _finalize_hits(builtin_hits, "builtin")
    if user_hits:
        return _finalize_hits(user_hits, "rules")
    return None


def _sink_basename(subcommand: ShellSubcommand) -> str:
    token = ""
    argv = getattr(subcommand, "argv", ()) or ()
    if argv:
        token = str(argv[0])
    else:
        text = str(getattr(subcommand, "text", "") or "").strip()
        token = text.split()[0] if text else ""
    base = Path(token.replace("\\", "/")).name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def _has_pipeline(shell_parse: ShellAstParseResult) -> bool:
    if shell_parse.flags.has_pipeline:
        return True
    return any(
        "|" in (getattr(sc, "parent_operators", ()) or ())
        for sc in shell_parse.subcommands
    )


def _has_interpreter_sink(shell_parse: ShellAstParseResult) -> bool:
    if not _has_pipeline(shell_parse) or len(shell_parse.subcommands) < 2:
        return False
    return any(
        _sink_basename(sc) in _INTERPRETER_SINK_NAMES
        for sc in shell_parse.subcommands[1:]
    )


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
    - 内置规则必须已内联进 ``rules``（``layer: builtin``）；判定路径不再 load YAML。
    """
    tools_cfg = permission_config.get("tools") or {}
    if not isinstance(tools_cfg, dict):
        tools_cfg = {}

    defaults_cfg = permission_config.get("defaults") or {}
    if not isinstance(defaults_cfg, dict):
        defaults_cfg = {}

    rules = permission_config.get("rules") or []
    if not isinstance(rules, list):
        rules = []
    dict_rules = [r for r in rules if isinstance(r, dict)]
    builtin_rules = [r for r in dict_rules if r.get("layer") == "builtin"]
    user_rules = [r for r in dict_rules if r.get("layer") != "builtin"]
    approval_overrides = permission_config.get("approval_overrides") or []
    if not isinstance(approval_overrides, list):
        approval_overrides = []

    bl, bl_rule = _baseline_level(tools_cfg, tool_name)
    if bl == PermissionLevel.DENY:
        return PermissionLevel.DENY, bl_rule or f"{_MR}:tools.deny"

    check_unknown, check_sink = _read_shell_guard(permission_config)

    original_cmd = _command_text(tool_args)
    canon_cmd = canonicalize_shell_command_for_permission(original_cmd)
    canon_args = (
        _with_shell_command(tool_args, canon_cmd)
        if canon_cmd != original_cmd
        else tool_args
    )

    shell_parse: ShellAstParseResult | None = None
    if _tool_category(tool_name) == "shell":
        shell_parse = parse_shell_for_permission(canon_cmd)
    shell_floor, shell_floor_rule = _shell_ast_floor(
        shell_parse, unknown_structure=check_unknown,
    )
    invocation_ctx = _TieredInvocationContext(
        builtin_rules=builtin_rules,
        rules=user_rules,
        approval_overrides=approval_overrides,
        baseline_level=bl,
        baseline_rule=bl_rule,
        defaults_cfg=defaults_cfg,
    )

    override_hits = _collect_approval_override_hits(approval_overrides, tool_name, tool_args)
    if not override_hits and canon_args is not tool_args:
        override_hits = _collect_approval_override_hits(
            approval_overrides, tool_name, canon_args,
        )
    if override_hits:
        contributing = sorted(set(override_hits))
        return PermissionLevel.ALLOW, _APPROVAL_OVERRIDES_PREFIX + ":" + "+".join(contributing)

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

        permission, matched_rule = _aggregate_subcommand_results(subcommand_results)
        full_candidates: list[tuple[PermissionLevel, str]] = []
        orig_full = _evaluate_param_rules_only(tool_name, tool_args, invocation_ctx)
        if orig_full is not None:
            full_candidates.append(orig_full)
        if canon_args is not tool_args:
            canon_full = _evaluate_param_rules_only(tool_name, canon_args, invocation_ctx)
            if canon_full is not None:
                full_candidates.append(canon_full)
        if full_candidates:
            full_level = strictest(*(lvl for lvl, _ in full_candidates))
            full_rule = next(rule for lvl, rule in full_candidates if lvl == full_level)
            permission = strictest(permission, full_level)
            if permission == full_level:
                matched_rule = full_rule
        if check_sink and _has_interpreter_sink(shell_parse):
            permission = strictest(permission, PermissionLevel.ASK)
            if permission == PermissionLevel.ASK:
                sink_rule = f"{_MR}:shell_guard:interpreter_sink"
                matched_rule = (
                    f"{sink_rule}|{matched_rule}" if matched_rule else sink_rule
                )
        return _apply_shell_ast_floor(permission, matched_rule, shell_floor, shell_floor_rule)

    eval_args = canon_args if _tool_category(tool_name) == "shell" else tool_args
    result = _evaluate_single_invocation(
        tool_name,
        eval_args,
        invocation_ctx,
    )
    return _apply_shell_ast_floor(*result, shell_floor, shell_floor_rule)


def maybe_escalate_shell_operators(
        tool_name: str,
        tool_args: dict[str, Any],
        permission: PermissionLevel,
) -> PermissionLevel:
    """Legacy hook. Compound / operator ASK is decided in ``evaluate_tiered_policy``."""
    return permission


def matched_rule_uses_approval_override(matched_rule: str | None) -> bool:
    """当前结果是否来自 approval_overrides。"""
    if not isinstance(matched_rule, str):
        return False
    return matched_rule.startswith(_APPROVAL_OVERRIDES_PREFIX)
