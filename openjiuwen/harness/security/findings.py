# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""命令行为 findings（不含路径目标检测）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.shell_ast import parse_shell_for_permission
from openjiuwen.harness.security.tiered_policy import (
    _SHELL_TOOLS,
    _normalize_shell_whitespace,
    strictest,
)


@dataclass(frozen=True)
class GuardFinding:
    severity: str  # INFO | LOW | MEDIUM | HIGH | CRITICAL
    reason: str
    rule_id: str | None = None


_CURL_PIPE_SHELL = re.compile(
    r"(?i)(curl|wget|fetch|ftp)\b[^;&|]*\|\s*(bash|sh|zsh|dash|ash|source)\b"
    r"|(iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^;&|]*\|\s*(iex|Invoke-Expression)\b"
)
_EVAL_OR_ENCODED = re.compile(
    r"(?i)(\beval\s+|base64\s+(-d|--decode)\b|Invoke-Expression|\biex\b|-EncodedCommand\b)"
)


def scan_shell_findings(command: str) -> list[GuardFinding]:
    """扫描 shell 命令行为信号；**不做**敏感路径字符串拦截。"""
    text = _normalize_shell_whitespace(command)
    if not text:
        return []

    findings: list[GuardFinding] = []
    parsed = parse_shell_for_permission(text)
    if parsed.kind == "too_complex":
        findings.append(
            GuardFinding(
                severity="MEDIUM",
                reason="shell_too_complex",
                rule_id="finding_shell_too_complex",
            )
        )
    elif parsed.flags.has_risky_structure():
        flags = parsed.flags
        # 仅管道 / && / ; 等简单复合：展示用 INFO，不参与升级 ASK
        # （与 shell_subcommands 分段评估一致；危险组合另有 CRITICAL/HIGH）。
        has_heavy = any((
            flags.has_subshell,
            flags.has_command_group,
            flags.has_command_substitution,
            flags.has_process_substitution,
            flags.has_parameter_expansion,
            flags.has_heredoc,
            flags.has_input_redirection,
            flags.has_output_redirection,
        ))
        if has_heavy:
            findings.append(
                GuardFinding(
                    severity="MEDIUM",
                    reason="shell_risky_structure",
                    rule_id="finding_shell_risky_structure",
                )
            )
        elif flags.has_pipeline or flags.has_compound_operators:
            findings.append(
                GuardFinding(
                    severity="INFO",
                    reason="shell_simple_compound",
                    rule_id="finding_shell_simple_compound",
                )
            )

    if _CURL_PIPE_SHELL.search(text):
        findings.append(
            GuardFinding(
                severity="CRITICAL",
                reason="download_and_execute",
                rule_id="finding_curl_pipe_shell",
            )
        )
    if _EVAL_OR_ENCODED.search(text):
        findings.append(
            GuardFinding(
                severity="HIGH",
                reason="dynamic_or_encoded_execution",
                rule_id="finding_eval_or_encoded",
            )
        )
    return findings


def findings_for_tool_call(tool_name: str, tool_args: dict[str, Any]) -> list[GuardFinding]:
    if tool_name not in _SHELL_TOOLS:
        return []
    cmd = str(tool_args.get("command", "") or tool_args.get("cmd", "") or "")
    return scan_shell_findings(cmd)


def escalate_with_findings(
    permission: PermissionLevel,
    findings: list[GuardFinding],
    *,
    mode: str,
) -> PermissionLevel:
    """findings 可升级 ASK，不可放宽 DENY；不另起并行决策引擎。

    INFO 仅展示、不升级。Auto：MEDIUM+；Strict：LOW+（不含 INFO）。
    """
    if permission == PermissionLevel.DENY or not findings:
        return permission
    mode_l = (mode or "auto").strip().lower()
    severities = {f.severity.upper() for f in findings}
    if mode_l == "full_access":
        # 主要展示；底线仍由 builtin/engine 负责
        return permission
    if mode_l == "strict":
        if severities & {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            return strictest(permission, PermissionLevel.ASK)
        return permission
    # auto：MEDIUM+ 可升级 ASK（INFO/LOW 仅展示）
    if severities & {"CRITICAL", "HIGH", "MEDIUM"}:
        return strictest(permission, PermissionLevel.ASK)
    return permission


__all__ = [
    "GuardFinding",
    "escalate_with_findings",
    "findings_for_tool_call",
    "scan_shell_findings",
]
