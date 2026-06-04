# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness prompt section 构建。"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import List

from openjiuwen.core.single_agent.prompts.builder import PromptSection


_IDENTITY_PATH = Path(__file__).parent / "identity.md"


def _load_identity() -> str:
    """加载 identity.md 内容。"""
    return _IDENTITY_PATH.read_text(encoding="utf-8")


def _resolve_shell_name() -> str:
    """Return a human-readable shell name for the current environment."""
    if sys.platform == "win32":
        # Check if Git Bash is available
        git_bash = Path(
            os.environ.get("PROGRAMFILES", "")
        ) / "Git" / "bin" / "bash.exe"
        if git_bash.is_file():
            return "Git Bash / cmd.exe"
        return "cmd.exe"
    if sys.platform == "darwin":
        return "zsh (default) / bash"
    return "bash"


def _platform_adaptation_section() -> PromptSection:
    """Build a section that tells the agent about the current OS platform."""
    os_type = sys.platform  # win32 | linux | darwin
    shell_name = _resolve_shell_name()
    os_version = platform.platform()

    cn = (
        "# 运行环境\n\n"
        f"- 当前运行平台：`{os_type}`\n"
        f"- Shell：{shell_name}\n"
        f"- OS 版本：{os_version}\n\n"
        "## 平台命令差异（仅在必须使用 shell 时参考）\n\n"
        "以下命令差异仅适用于测试、构建、git、包管理、运行脚本等必须调用 shell 的场景。"
        "文件读取、编辑、搜索仍应优先使用专用工具。\n\n"
        "| 操作 | Windows (`win32`/`win64`) | Linux/macOS (`linux`/`darwin`) |\n"
        "|------|---------------------------|-------------------------------|\n"
        "| 创建目录 | `mkdir folder` 或 PowerShell "
        "`New-Item -ItemType Directory -Path folder` "
        "| `mkdir -p folder` |\n"
        "| 删除文件 | `del file.txt` 或 PowerShell `Remove-Item file.txt` | `rm file.txt` |\n"
        "| 删除目录 | `rmdir /s /q folder` 或 PowerShell `Remove-Item -Recurse folder` | `rm -rf folder` |\n"
        "| 查找文件 | `dir /s pattern` 或 PowerShell "
        "`Get-ChildItem -Recurse -Filter pattern` "
        "| `find . -name pattern` |\n"
        "| 环境变量 | `%VAR%` (cmd) / `$VAR` (Git Bash) | `$VAR` |\n"
        "| PATH 分隔 | `;` | `:` |\n"
        "| 命令串联 | `&&`（条件）或 `&`（无条件） | `&&`（条件）或 `;`（无条件） |\n\n"
        "**特别注意**：Windows 的 `mkdir` 不支持 `-p` 参数！"
        "在 Windows 上使用 `mkdir -p folder` 会错误创建名为 `-p` 的目录。"
        "如需创建嵌套目录，请使用 PowerShell "
        "`New-Item -ItemType Directory -Path \"parent/child\" -Force`，"
        "或使用 cmd 分步创建 `mkdir parent && mkdir parent\\child`。\n\n"
        "## Python 跨平台代码规范\n\n"
        "- 路径构建：始终使用 `pathlib.Path`，不要用字符串拼接 `/` 或 `\\`\n"
        "- PATH 拼接：`os.environ` 和 `env` 字典中的 PATH 必须用 `os.pathsep` 拼接，不要硬编码 `:` 或 `;`\n"
        "- venv 路径：Windows 为 `.venv\\Scripts\\python.exe`，Linux/macOS 为 `.venv/bin/python`\n"
        "- 临时目录：使用 `tempfile.gettempdir()`，不要假设 `/tmp` 存在\n"
        "- subprocess：`asyncio.create_subprocess_exec` 传 `env` 参数时确保 PATH 用 `os.pathsep` 拼接\n\n"
        "## 局部验证\n\n"
        "局部验证只检查本次任务变更的文件，禁止全量扫描。"
        "调用检查工具时优先使用 `python -m ruff check <files>`，"
        "不要直接调用 `ruff` CLI 命令，避免跨平台 PATH 问题。"
    )
    en = (
        "# Environment\n\n"
        f"- Current platform: `{os_type}`\n"
        f"- Shell: {shell_name}\n"
        f"- OS Version: {os_version}\n\n"
        "## Platform Command Differences (only when shell is required)\n\n"
        "The following command differences apply only to scenarios where shell execution is required "
        "(testing, builds, git, package management, running scripts). "
        "File reading, editing, and searching should still prefer dedicated tools.\n\n"
        "| Operation | Windows (`win32`/`win64`) | Linux/macOS (`linux`/`darwin`) |\n"
        "|-----------|---------------------------|-------------------------------|\n"
        "| Create directory | `mkdir folder` or PowerShell "
        "`New-Item -ItemType Directory -Path folder` "
        "| `mkdir -p folder` |\n"
        "| Delete file | `del file.txt` or PowerShell `Remove-Item file.txt` | `rm file.txt` |\n"
        "| Delete directory | `rmdir /s /q folder` or PowerShell `Remove-Item -Recurse folder` | `rm -rf folder` |\n"
        "| Find file | `dir /s pattern` or PowerShell "
        "`Get-ChildItem -Recurse -Filter pattern` "
        "| `find . -name pattern` |\n"
        "| Env variable | `%VAR%` (cmd) / `$VAR` (Git Bash) | `$VAR` |\n"
        "| PATH separator | `;` | `:` |\n"
        "| Command chaining | `&&` (conditional) or `&` (unconditional) |"
        " `&&` (conditional) or `;` (unconditional) |\n\n"
        "**WARNING**: Windows `mkdir` does NOT support the `-p` flag! "
        "Using `mkdir -p folder` on Windows will incorrectly create a directory named `-p`. "
        "To create nested directories on Windows, use either PowerShell "
        "`New-Item -ItemType Directory -Path \"parent/child\" -Force` "
        "or cmd with step-by-step creation `mkdir parent && mkdir parent\\\\child`.\n\n"
        "## Python Cross-Platform Code Rules\n\n"
        "- Path construction: Always use `pathlib.Path`; never concatenate `/` or `\\` strings\n"
        "- PATH joining: Use `os.pathsep` in `os.environ` and `env` dicts; never hardcode `:` or `;`\n"
        "- venv path: Windows is `.venv\\Scripts\\python.exe`; Linux/macOS is `.venv/bin/python`\n"
        "- Temp directory: Use `tempfile.gettempdir()`; do not assume `/tmp` exists\n"
        "- subprocess: When passing `env` to `asyncio.create_subprocess_exec`, ensure PATH uses `os.pathsep`\n\n"
        "## Local Verification\n\n"
        "Local verification only checks files changed by the current task; full-repo scans are forbidden. "
        "Use `python -m ruff check <files>` to invoke check tools,"
        " not direct CLI calls like `ruff`, to avoid cross-platform PATH issues."
    )

    return PromptSection(
        name="auto_harness_platform_adaptation",
        content={"cn": cn, "en": en},
        priority=89,
    )


def build_auto_harness_sections(
    *,
    ci_gate_rules: str = "",
    wisdom: str = "",
) -> List[PromptSection]:
    """构建 Auto Harness Agent 的 prompt sections。

    Args:
        ci_gate_rules: CI 门控规则文本（来自 ci_gate.yaml）。
        wisdom: 经验库合成的活跃上下文。

    Returns:
        PromptSection 列表，注入到 SystemPromptBuilder。
    """
    sections: List[PromptSection] = []

    # Identity section（最高优先级）
    identity_text = _load_identity()
    sections.append(PromptSection(
        name="auto_harness_identity",
        content={"cn": identity_text, "en": identity_text},
        priority=10,
    ))

    # Platform adaptation（紧跟 identity，优先级高于 CI gate）
    sections.append(_platform_adaptation_section())

    # CI Gate 规则
    if ci_gate_rules:
        sections.append(PromptSection(
            name="auto_harness_ci_gate",
            content={
                "cn": f"## CI 门控规则\n\n{ci_gate_rules}",
                "en": f"## CI Gate Rules\n\n{ci_gate_rules}",
            },
            priority=20,
        ))

    # 经验库活跃上下文
    if wisdom:
        sections.append(PromptSection(
            name="auto_harness_wisdom",
            content={
                "cn": f"## 经验库\n\n{wisdom}",
                "en": f"## Experience Library\n\n{wisdom}",
            },
            priority=30,
        ))

    return sections