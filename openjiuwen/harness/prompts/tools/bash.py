# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Bash (shell) tool.

Prompt design and parameter schema fully aligned with Claude Code's
BashTool (see ``claude-code/src/tools/BashTool/prompt.ts`` and
``BashTool.tsx`` for the reference implementation).
"""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

# ── tool description (injected as the tool-level system prompt) ──


DESCRIPTION: Dict[str, str] = {
    "cn": (
        "在会话工作目录执行 Shell 命令并返回输出。每次调用独立加载 Shell profile，变量、函数和别名不会保留。"
        "用 workdir 指定目录。独立命令可并行，依赖命令在同一次调用中用 && 串联。"
        "后台任务用 run_in_background，命令末尾无需加 &，等待完成通知即可。超限输出可按返回路径读取。"
        "文件读写与搜索优先用专用工具。删除或移动前核对路径，按当前审批规则处理。"
        "Git 优先新提交和安全操作，未经明确授权不绕过 hooks 或签名。"
        "源码分析先克隆，worktree 建在项目的 .worktrees/<name>，默认基线为 HEAD。"
    ),
    "en": (
        "Run shell commands in the session working directory. Each call starts a fresh shell with"
        " its profile; variables, functions and aliases do not persist. Set workdir for a "
        "specific directory. Run independent commands in parallel and join dependent commands "
        "with && in one call. For background work use run_in_background without trailing & and "
        "await notification. Read clipped output from the returned path. Prefer dedicated tools "
        "for file I/O and search. Check paths before moving/deleting and "
        "follow approval rules. Prefer new Git commits and safer operations; do not bypass hooks "
        "or signing without explicit authorization. Clone for source analysis. Use "
        "project/.worktrees/<name> with HEAD as the default base."
    ),
}

# ── parameter descriptions ──────────────────────────────────

_DESCRIPTION_PARAM_CN = (
    "用主动语态简述命令作用。简单命令约 5–10 字，复杂管道或生僻参数说明实际效果。"
)


BASH_PARAMS: Dict[str, Dict[str, str]] = {
    "command": {
        "cn": "要执行的命令",
        "en": "The command to execute",
    },
    "timeout": {
        "cn": "超时秒数，默认 300，上限 3600。",
        "en": "Seconds: default 300, max 3600; increase for long work."
    },
    "description": {
        "cn": _DESCRIPTION_PARAM_CN,
        "en": (
            "Brief active-voice purpose: 5–10 words for simple commands, extra context for opaque "
            "commands."
        ),
    },
    "run_in_background": {
        "cn": (
            "仅在无需立即获取结果时设为 true。完成后通知。"
        ),
        "en": (
            "Only when output is not needed immediately and later completion notification is "
            "acceptable."
        ),
    },
    "workdir": {
        "cn": "执行目录（相对或绝对路径），默认为工作区根目录；不能越出工作区沙箱",
        "en": (
            "Working directory (relative or absolute path), defaults to "
            "workspace root; cannot escape workspace sandbox"
        ),
    },
    "max_output_chars": {
        "cn": "输出字符上限，默认 20000。超限时返回完整输出文件的路径，0 表示不限。",
        "en": "Output cap (default 20000); overflow path returned. 0=unlimited; use carefully.",
    },
    "shell_type": {
        "cn": (
            "默认 auto。Windows 上仅在实际使用 Git Bash 或非 WSL stub 的 Bash 时使用 POSIX 语法，cmd/PowerShell 不支持 "
            "mkdir -p。"
        ),
        "en": (
            "Default auto. On Windows, use POSIX syntax only with Git Bash or non-WSL-stub Bash, "
            "never cmd or PowerShell."
        ),
    },
}


def get_bash_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for bash tool input_params.

    Property order follows Claude Code convention: core params first
    (command, timeout, description, run_in_background), then
    project-specific params (workdir, max_output_chars, shell_type).
    """
    p = BASH_PARAMS
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": p["command"][lang]},
            "timeout": {
                "type": "integer", "description": p["timeout"][lang]},
            "description": {"type": "string", "description": p["description"][lang]},
            "run_in_background": {"type": "boolean", "description": p["run_in_background"][lang]},
            "workdir": {"type": "string", "description": p["workdir"][lang]},
            "max_output_chars": {"type": "integer", "description": p["max_output_chars"][lang]},
            "shell_type": {
                "type": "string",
                "enum": ["auto", "cmd", "powershell", "bash", "sh"],
                "description": p["shell_type"][lang],
            },
        },
        "required": ["command"],
    }


class BashMetadataProvider(ToolMetadataProvider):
    """Bash tool metadata provider."""

    def get_name(self) -> str:
        return "bash"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_bash_input_params(language)
