# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Bash (shell) tool.

Prompt design and parameter schema are derived from Claude Code's
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
        "执行 Shell 命令并返回输出。\n"
        "\n"
        "Shell 环境从用户的 profile（bash 或 zsh）初始化。\n"
        "\n"
        "Windows 注意：`cmd`/PowerShell 自带 `mkdir` **不支持 `-p`**，不要在 cmd/PowerShell 中使用 `mkdir -p`。"
        "只有运行环境信息显示 Git Bash 或非 WSL stub 的 PATH bash 可用，并且实际使用 bash/Git Bash 时，POSIX `mkdir -p` 才适用。"
        "否则应使用 PowerShell `New-Item ... -Force` 或 cmd 逐级 `mkdir`。\n"
        "\n"
        " - 输出文本：直接输出（不要用 echo/printf）\n"
        "\n"
        "# 使用说明\n"
        " - 创建文件或目录前，先用本工具执行 `ls` 确认父目录存在且位置正确\n"
        " - 路径包含空格时必须用双引号括起"
        "（例如 cd \"path with spaces/file.txt\"）\n"
        " - 尽量使用绝对路径维持当前工作目录，避免使用 `cd`；"
        "除非用户明确要求\n"
        " - 可通过 timeout 参数指定超时（秒），默认 1800 秒，上限 3600 秒\n"
        " - Git 命令规范：\n"
        "   - hook 失败时应排查并修复根本原因\n"
        "   - 用户分享代码仓 URL 让你「看看这个仓库」或「分析一下」时，"
        "首选动作是 `git clone <url> <本地路径>`；克隆完再用 read_file/grep/glob "
        "等专用工具读源码，比抓取仓库主页能拿到的信息完整得多\n"
        "   - 用 `git worktree add` 创建 worktree 时，目标路径必须位于"
        "当前项目目录下的 `.worktrees/<name>`，例如 "
        "`git worktree add .worktrees/feature-x -b feature-x HEAD`。"
        "严禁使用 `../` 或项目目录之外的路径作为 worktree 目标——"
        "项目同级目录属于越界，会污染项目外部空间。"
        "base 分支未指定时用当前分支（`HEAD`），不要臆测 `main`。\n"
        " - 避免不必要的 `sleep` 命令：\n"
        "   - 能立即执行的命令之间不要 sleep\n"
        "   - 不要在 sleep 循环中重试失败命令——排查根本原因\n"
        "   - 如必须轮询外部进程，使用检查命令（如 `gh run view`）"
        "而非先 sleep\n"
        "   - 如必须 sleep，保持短时间（1-5 秒）"
    ),
    "en": (
        "Executes a given bash command and returns its output.\n"
        "\n"
        "The shell environment "
        "is initialized from the user's profile (bash or zsh).\n"
        "\n"
        "Windows note: `cmd`/PowerShell `mkdir` **does not support `-p`**; do not use "
        "`mkdir -p` in cmd/PowerShell. POSIX `mkdir -p` is appropriate only when "
        "the runtime environment information shows Git Bash or a non-WSL-stub PATH bash "
        "is available and you are actually using bash/Git Bash. Otherwise, use "
        "PowerShell `New-Item ... -Force` or create each level with cmd `mkdir`.\n"
        "\n"
        " - Communication: Output text directly (NOT echo/printf)\n"
        "\n"
        "# Instructions\n"
        " - If your command will create new directories or files, first "
        "use this tool to run `ls` to verify the parent directory exists "
        "and is the correct location.\n"
        " - Always quote file paths that contain spaces with double "
        "quotes in your command "
        "(e.g., cd \"path with spaces/file.txt\").\n"
        " - Try to maintain your current working directory throughout the "
        "session by using absolute paths and avoiding usage of `cd`. "
        "You may use `cd` if the user explicitly requests it.\n"
        " - You may specify an optional timeout in seconds (up to 3600s / 60 minutes). "
        "By default, your command will timeout after 1800s.\n"
        " - For git commands:\n"
        "   - If a hook fails, investigate and fix the underlying issue.\n"
        "   - When a user shares a repo URL and asks you to 'look at' "
        "or 'analyze' it, the natural first step is "
        "`git clone <url> <local_path>`; after cloning, use "
        "read_file/grep/glob on the working tree - it gives you far "
        "more than the rendered repository page would.\n"
        "   - When creating a worktree with `git worktree add`, the "
        "target path MUST live under `.worktrees/<name>` inside the "
        "current project directory, e.g. "
        "`git worktree add .worktrees/feature-x -b feature-x HEAD`. "
        "NEVER use `../` or any path outside the current project "
        "directory as the worktree target; the project's sibling "
        "directory is out of bounds and pollutes space outside the "
        "project. When the base branch is unspecified, use the current "
        "branch (`HEAD`); do not assume `main`.\n"
        " - Avoid unnecessary `sleep` commands:\n"
        "   - Do not sleep between commands that can run immediately "
        "-- just run them.\n"
        "   - Do not retry failing commands in a sleep loop -- diagnose "
        "the root cause.\n"
        "   - If you must poll an external process, use a check command "
        "(e.g. `gh run view`) rather than sleeping first.\n"
        "   - If you must sleep, keep the duration short (1-5 seconds) "
        "to avoid blocking the user."
    ),
}

# ── parameter descriptions ──────────────────────────────────

_DESCRIPTION_PARAM_CN = (
    "用简洁的主动语态描述该命令的作用。"
    "不要在描述中使用 \"复杂\" 或 \"风险\" 等词——直接描述它做什么。\n"
    "\n"
    "对于简单命令（git、npm、常用 CLI 工具），保持简短（5-10 个字）：\n"
    "- ls → \"列出当前目录文件\"\n"
    "- git status → \"显示工作区状态\"\n"
    "- npm install → \"安装项目依赖\"\n"
    "\n"
    "对于不易一眼看懂的命令（管道命令、冷门参数等），"
    "补充足够上下文说明其用途：\n"
    "- find . -name \"*.tmp\" -exec rm {} \\; → "
    "\"递归查找并删除所有 .tmp 文件\"\n"
    "- git reset --hard origin/main → "
    "\"丢弃所有本地更改，与远程 main 对齐\"\n"
    "- curl -s url | jq '.data[]' → "
    "\"从 URL 获取 JSON 并提取 data 数组元素\""
)

_DESCRIPTION_PARAM_EN = (
    "Clear, concise description of what this command does in active "
    "voice. Never use words like \"complex\" or \"risk\" in the "
    "description - just describe what it does.\n"
    "\n"
    "For simple commands (git, npm, standard CLI tools), keep it brief "
    "(5-10 words):\n"
    "- ls -> \"List files in current directory\"\n"
    "- git status -> \"Show working tree status\"\n"
    "- npm install -> \"Install package dependencies\"\n"
    "\n"
    "For commands that are harder to parse at a glance (piped commands, "
    "obscure flags, etc.), add enough context to clarify what it does:\n"
    "- find . -name \"*.tmp\" -exec rm {} \\; -> "
    "\"Find and delete all .tmp files recursively\"\n"
    "- git reset --hard origin/main -> "
    "\"Discard all local changes and match remote main\"\n"
    "- curl -s url | jq '.data[]' -> "
    "\"Fetch JSON from URL and extract data array elements\""
)

BASH_PARAMS: Dict[str, Dict[str, str]] = {
    "command": {
        "cn": "要执行的命令",
        "en": "The command to execute",
    },
    "timeout": {
        "cn": "可选超时时间（秒），默认 1800，上限 3600。对于长时间运行的任务，建议适当增大该值以避免任务被提前中断",
        "en": "Optional timeout in seconds, default 1800, max 3600. For long-running tasks, it is recommended to "
              "increase this value to avoid premature termination"
    },
    "description": {
        "cn": _DESCRIPTION_PARAM_CN,
        "en": _DESCRIPTION_PARAM_EN,
    },
    "workdir": {
        "cn": "执行目录（相对或绝对路径），默认为工作区根目录；不能越出工作区沙箱",
        "en": (
            "Working directory (relative or absolute path), defaults to "
            "workspace root; cannot escape workspace sandbox"
        ),
    },
    "max_output_chars": {
        "cn": "最大输出字符数，默认 20000：超出部分会被截断并写入临时文件（结果中会给出文件路径，可按需读取），防止超大输出撑爆上下文；显式传 0 表示不限制（谨慎使用）",
        "en": (
            "Max output characters; defaults to 20000: output beyond this is truncated and the full "
            "content is written to a temp file (its path is returned in the result for on-demand reading), "
            "preventing oversized output from flooding context. Pass 0 explicitly to disable the limit (use with care)"
        ),
    },
    "shell_type": {
        "cn": (
            "指定 Shell 类型，可选值：auto/cmd/powershell/bash/sh，默认 auto。cmd/PowerShell 不支持 `mkdir -p`；"
            "只有环境信息显示 Git Bash 或非 WSL stub 的 PATH bash 可用时，才对 POSIX 语法使用 auto/bash/sh。"
        ),
        "en": (
            "Shell to use: auto/cmd/powershell/bash/sh, default auto. cmd/PowerShell do not support `mkdir -p`; "
            "use auto/bash/sh for POSIX syntax only when the environment information shows Git Bash or a "
            "non-WSL-stub PATH bash is available."
        ),
    },
}


def get_bash_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for bash tool input_params.

    Property order follows Claude Code convention: core params first
    (command, timeout, description), then project-specific params
    (workdir, max_output_chars, shell_type).
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
