# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Bash (shell) tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "执行 Shell 命令。",
    "en": "Execute shell commands.",
}

BASH_PARAMS: Dict[str, Dict[str, str]] = {
    "command": {
        "cn": "要执行的 Shell 命令",
        "en": "Shell command to execute",
    },
    "timeout": {
        "cn": "超时时间（秒），默认 30，上限 300",
        "en": "Timeout in seconds, default 30, max 300",
    },
    "workdir": {
        "cn": "执行目录（相对或绝对路径），默认为工作区根目录；不能越出工作区沙箱",
        "en": "Working directory (relative or absolute path), defaults to workspace root; cannot escape sandbox",
    },
    "background": {
        "cn": "是否后台运行，默认 false；设为 true 时立即返回 PID，适合启动服务进程",
        "en": "Run in background (default false); returns PID immediately when true, useful for starting servers",
    },
    "max_output_chars": {
        "cn": "最大输出字符数，默认 8000（上限 20000），防止超大输出撑爆上下文",
        "en": "Max output characters, default 8000 (max 20000), prevents oversized output from flooding context",
    },
    "shell_type": {
        "cn": "指定 Shell 类型，可选值：auto/cmd/powershell/bash/sh，默认 auto（自动检测）",
        "en": "Shell to use: auto/cmd/powershell/bash/sh, default auto (auto-detect)",
    },
}


def get_bash_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for bash tool input_params."""
    p = BASH_PARAMS
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": p["command"][lang]},
            "timeout": {"type": "integer", "description": p["timeout"][lang]},
            "workdir": {"type": "string", "description": p["workdir"][lang]},
            "background": {"type": "boolean", "description": p["background"][lang]},
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
    """Bash 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "bash"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_bash_input_params(language)
