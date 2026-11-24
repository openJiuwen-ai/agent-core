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
        "执行 Shell 命令并返回输出。\n"
        "\n"
        "工作目录在命令之间保持不变，但 Shell 状态（变量、函数、alias）不保留。"
        "Shell 环境从用户的 profile（bash 或 zsh）初始化。\n"
        "\n"
        "Windows 注意：`cmd`/PowerShell 自带 `mkdir` **不支持 `-p`**，不要在 cmd/PowerShell 中使用 `mkdir -p`。"
        "只有运行环境信息显示 Git Bash 或非 WSL stub 的 PATH bash 可用，并且实际使用 bash/Git Bash 时，POSIX `mkdir -p` 才适用。"
        "否则应使用 PowerShell `New-Item ... -Force` 或 cmd 逐级 `mkdir`。\n"
        "\n"
        "重要：避免使用本工具执行 `find`、`grep`、`cat`、`head`、`tail`、"
        "`sed`、`awk` 或 `echo` 命令，除非明确指示或确认专用工具无法完成任务。"
        "请使用对应的专用工具，以获得更好的体验：\n"
        "\n"
        " - 文件搜索：使用 glob 工具（不要用 find 或 ls）\n"
        " - 内容搜索：使用 grep 工具（不要用 grep 或 rg 命令）\n"
        " - 读取文件：使用 read_file 工具（不要用 cat/head/tail）\n"
        " - 编辑文件：使用 edit_file 工具（不要用 sed/awk）\n"
        " - 写入文件：使用 write_file 工具（不要用 echo > 或 cat <<EOF）\n"
        " - 输出文本：直接输出（不要用 echo/printf）\n"
        "虽然 bash 工具也能做到，但专用工具提供更好的用户体验，"
        "并且更方便审查和授权。\n"
        "\n"
        "# 使用说明\n"
        " - 创建文件或目录前，先用本工具执行 `ls` 确认父目录存在且位置正确\n"
        " - 路径包含空格时必须用双引号括起"
        "（例如 cd \"path with spaces/file.txt\"）\n"
        " - 尽量使用绝对路径维持当前工作目录，避免使用 `cd`；"
        "除非用户明确要求\n"
        " - 可通过 timeout 参数指定超时（秒），默认 300 秒，上限 3600 秒\n"
        " - 可将 run_in_background 设为 true 来后台运行命令。"
        "仅在不需要立即获取结果时使用，命令完成后会收到通知。"
        "使用该参数时无需在命令末尾加 `&`\n"
        " - 发出多条命令时：\n"
        "   - 独立命令：在同一消息中多次并行调用本工具。"
        "例如需要同时运行 \"git status\" 和 \"git diff\"，"
        "发送一条消息包含两次并行调用\n"
        "   - 依赖命令：在单次调用中用 `&&` 串联\n"
        "   - 仅在不关心前序命令是否失败时使用 `;`\n"
        "   - 不要用换行分隔命令（引号字符串内换行可以）\n"
        " - Git 命令规范：\n"
        "   - 优先创建新提交而非修改已有提交\n"
        "   - 执行破坏性操作（如 git reset --hard、git push --force、"
        "git checkout --）前，先考虑是否有更安全的替代方案，"
        "只在确实必要时使用\n"
        "   - 除非用户明确要求，不要跳过 hooks（--no-verify）"
        "或绕过签名（--no-gpg-sign）。hook 失败时应排查并修复根本原因\n"
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
        "   - 长时间运行的命令使用 `run_in_background: true`，"
        "无需 sleep 等待\n"
        "   - 不要在 sleep 循环中重试失败命令——排查根本原因\n"
        "   - 等待后台任务完成时会自动通知——不要轮询\n"
        "   - 如必须轮询外部进程，使用检查命令（如 `gh run view`）"
        "而非先 sleep\n"
        "   - 如必须 sleep，保持短时间（1-5 秒）"
    ),
    "en": (
        "Run commands under runtime shell/sequencing/Git rules. CWD persists, "
        "variables/functions/aliases do not; each shell loads its profile. Inspect parent before "
        "creating paths; quote spaces, prefer absolute paths. Clone repositories for source analysis."
        " Worktrees use project/.worktrees/<name>, base HEAD unless specified. Background work uses "
        "run_in_background, never trailing &; await notification without polling. Do not sleep before"
        " status checks; necessary sleeps 1–5 seconds. Prefer new commits over amend and safer "
        "alternatives to destructive Git commands. Never bypass hooks (--no-verify) or signing "
        "(--no-gpg-sign) without explicit user authorization; diagnose hook failures instead."
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


BASH_PARAMS: Dict[str, Dict[str, str]] = {
    "command": {
        "cn": "要执行的命令",
        "en": "The command to execute",
    },
    "timeout": {
        "cn": "可选超时时间（秒），默认 300，上限 3600。对于长时间运行的任务，建议适当增大该值以避免任务被提前中断",
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
            "设为 true 以后台运行命令。"
            "仅在不需要立即获取结果时使用，命令完成后会收到通知"
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
        "cn": "最大输出字符数，默认 20000：超出部分会被截断并写入临时文件（结果中会给出文件路径，可按需读取），防止超大输出撑爆上下文；显式传 0 表示不限制（谨慎使用）",
        "en": "Output cap (default 20000); overflow path returned. 0=unlimited; use carefully.",
    },
    "shell_type": {
        "cn": (
            "指定 Shell 类型，可选值：auto/cmd/powershell/bash/sh，默认 auto。cmd/PowerShell 不支持 `mkdir -p`；"
            "只有环境信息显示 Git Bash 或非 WSL stub 的 PATH bash 可用时，才对 POSIX 语法使用 auto/bash/sh。"
        ),
        "en": "Default auto. POSIX needs Git Bash/non-WSL-stub bash, never cmd/PowerShell.",
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
