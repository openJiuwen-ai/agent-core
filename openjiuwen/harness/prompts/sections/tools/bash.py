# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Bash (shell) tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "执行 Shell 命令并返回输出。\n\n"
        "工作目录在命令之间保持不变，但 Shell 状态不保留。\n\n"
        "重要：避免使用本工具执行 `find`、`grep`、`cat`、`head`、`tail`、"
        "`sed`、`awk` 或 `echo` 命令，除非明确指示或确认专用工具无法完成任务。"
        "请使用对应的专用工具，以获得更好的体验：\n"
        " - 文件搜索：使用 glob 工具（不要用 find 或 ls）\n"
        " - 内容搜索：使用 grep 工具（不要用 grep 或 rg 命令）\n"
        " - 读取文件：使用 read_file 工具（不要用 cat/head/tail）\n"
        " - 编辑文件：使用 edit_file 工具（不要用 sed/awk）\n"
        " - 写入文件：使用 write_file 工具（不要用 echo > 或 cat <<EOF）\n"
        " - 输出文本：直接输出（不要用 echo/printf）\n"
        "虽然 bash 工具也能做到，但专用工具提供更好的用户体验，"
        "并且更方便审查和授权。\n\n"
        "# 使用说明\n"
        " - 创建文件或目录前，先用本工具执行 `ls` 确认父目录存在且位置正确\n"
        " - 路径包含空格时必须用双引号括起（例如 cd \"path with spaces/file.txt\"）\n"
        " - 尽量使用绝对路径维持当前工作目录，避免使用 `cd`；除非用户明确要求\n"
        " - 可通过 timeout 参数指定超时（秒），默认 30 秒，上限 300 秒\n"
        " - 可将 background 设为 true 来后台运行命令。"
        "仅在不需要立即获取结果时使用，命令完成后会收到通知。"
        "使用该参数时无需在命令末尾加 `&`\n"
        " - 发出多条命令时：\n"
        "   - 独立命令：在同一消息中多次并行调用本工具。"
        "例如需要同时运行 \"git status\" 和 \"git diff\"，发送一条消息包含两次并行调用\n"
        "   - 依赖命令：在单次调用中用 `&&` 串联\n"
        "   - 仅在不关心前序命令是否失败时使用 `;`\n"
        "   - 不要用换行分隔命令（引号字符串内换行可以）\n"
        " - Git 命令规范：\n"
        "   - 优先创建新提交而非修改已有提交\n"
        "   - 执行破坏性操作（如 git reset --hard、git push --force、git checkout --）前，"
        "先考虑是否有更安全的替代方案，只在确实必要时使用\n"
        "   - 除非用户明确要求，不要跳过 hooks（--no-verify）"
        "或绕过签名（--no-gpg-sign）。hook 失败时应排查并修复根本原因\n"
        " - 避免不必要的 `sleep` 命令：\n"
        "   - 能立即执行的命令之间不要 sleep\n"
        "   - 长时间运行的命令使用 `background: true`，无需 sleep 等待\n"
        "   - 不要在 sleep 循环中重试失败命令——排查根本原因\n"
        "   - 等待后台任务完成时会自动通知——不要轮询\n"
        "   - 如必须轮询外部进程，使用检查命令（如 `gh run view`）而非先 sleep\n"
        "   - 如必须 sleep，保持短时间（1-5 秒）"
    ),
    "en": (
        "Execute a given bash command and return its output.\n\n"
        "The working directory persists between commands, but shell state does not.\n\n"
        "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, "
        "`tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed "
        "or after you have verified that a dedicated tool cannot accomplish your task. "
        "Instead, use the appropriate dedicated tool as this will provide a much "
        "better experience for the user:\n"
        " - File search: Use glob tool (NOT find or ls)\n"
        " - Content search: Use grep tool (NOT grep or rg)\n"
        " - Read files: Use read_file tool (NOT cat/head/tail)\n"
        " - Edit files: Use edit_file tool (NOT sed/awk)\n"
        " - Write files: Use write_file tool (NOT echo >/cat <<EOF)\n"
        " - Communication: Output text directly (NOT echo/printf)\n"
        "While the bash tool can do similar things, it is better to use the "
        "built-in tools as they provide a better user experience and make it "
        "easier to review tool calls and give permission.\n\n"
        "# Instructions\n"
        " - If your command will create new directories or files, first use "
        "this tool to run `ls` to verify the parent directory exists and is "
        "the correct location.\n"
        " - Always quote file paths that contain spaces with double quotes "
        "in your command (e.g., cd \"path with spaces/file.txt\")\n"
        " - Try to maintain your current working directory throughout the "
        "session by using absolute paths and avoiding usage of `cd`. "
        "You may use `cd` if the user explicitly requests it.\n"
        " - You may specify an optional timeout in seconds (up to 300s / "
        "5 minutes). By default, your command will timeout after 30s.\n"
        " - You can use the `background` parameter to run the command in "
        "the background. Only use this if you don't need the result "
        "immediately and are OK being notified when the command completes "
        "later. You do not need to use '&' at the end of the command when "
        "using this parameter.\n"
        " - When issuing multiple commands:\n"
        "   - If the commands are independent and can run in parallel, make "
        "multiple bash tool calls in a single message. Example: if you need "
        "to run \"git status\" and \"git diff\", send a single message with "
        "two bash tool calls in parallel.\n"
        "   - If the commands depend on each other and must run sequentially, "
        "use a single bash call with '&&' to chain them together.\n"
        "   - Use ';' only when you need to run commands sequentially but "
        "don't care if earlier commands fail.\n"
        "   - DO NOT use newlines to separate commands (newlines are ok in "
        "quoted strings).\n"
        " - For git commands:\n"
        "   - Prefer to create a new commit rather than amending an existing "
        "commit.\n"
        "   - Before running destructive operations (e.g., git reset --hard, "
        "git push --force, git checkout --), consider whether there is a "
        "safer alternative that achieves the same goal. Only use destructive "
        "operations when they are truly the best approach.\n"
        "   - Never skip hooks (--no-verify) or bypass signing "
        "(--no-gpg-sign) unless the user has explicitly asked for it. "
        "If a hook fails, investigate and fix the underlying issue.\n"
        " - Avoid unnecessary `sleep` commands:\n"
        "   - Do not sleep between commands that can run immediately — "
        "just run them.\n"
        "   - If your command is long running and you would like to be "
        "notified when it finishes — use `background: true`. No sleep "
        "needed.\n"
        "   - Do not retry failing commands in a sleep loop — diagnose "
        "the root cause.\n"
        "   - If waiting for a background task you started with "
        "`background: true`, you will be notified when it completes — "
        "do not poll.\n"
        "   - If you must poll an external process, use a check command "
        "(e.g. `gh run view`) rather than sleeping first.\n"
        "   - If you must sleep, keep the duration short (1-5 seconds) "
        "to avoid blocking the user."
    ),
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
    "description": {
        "cn": "命令描述（可选），用于日志和审计",
        "en": "Optional command description for logging and audit trail",
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
            "description": {"type": "string", "description": p["description"][lang]},
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
