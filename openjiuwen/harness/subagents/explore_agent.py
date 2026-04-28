# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for Explore subagents."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.workspace.workspace import Workspace

# Tool card names registered by :class:`SysOperationRail` (see unit tests).
_EXPLORE_TOOL_BASH = "bash"
_EXPLORE_TOOL_GLOB = "glob"
_EXPLORE_TOOL_GREP = "grep"
_EXPLORE_TOOL_LIST_FILES = "list_files"
_EXPLORE_TOOL_READ_FILE = "read_file"


def _build_explore_system_prompt(
    *,
    language: Optional[str] = None,
) -> str:
    """Build the default Explore subagent system prompt (module-internal)."""
    resolved = resolve_language(language)
    if resolved == "cn":
        return _build_explore_system_prompt_cn()
    return _build_explore_system_prompt_en()


def _build_explore_system_prompt_en() -> str:
    glob_guidance = f"- Use `{_EXPLORE_TOOL_GLOB}` for broad file pattern matching"
    grep_guidance = f"- Use `{_EXPLORE_TOOL_GREP}` for searching file contents with regex"
    bash_ro = (
        f"- Use `{_EXPLORE_TOOL_BASH}` only for read-only shell inspection "
        f"(e.g. `ls`, `git status`, `git log`, `git diff`, `cat`, `head`, `tail`); "
        f"do not run commits, pushes, installs, or any command that mutates state"
    )

    return f"""You are a codebase navigation specialist operating on behalf of a host coding agent.
Your sole purpose is to locate, read, and report on existing code and nothing more.

=== IMPORTANT: READ-ONLY OPERATION ===
You must not alter the repository in any way. The following actions are forbidden:
- Writing or creating files (no Write, touch, or equivalent)
- Editing existing files (no Edit or in-place modification)
- Removing files (no rm or delete)
- Relocating or duplicating files (no mv or cp)
- Producing temporary files (including under /tmp or any scratch directory)
- Writing to disk via shell redirection (>, >>) or heredoc constructs
- Executing any command that leaves persistent side-effects on the system

You have no write-capable tools. Any attempt to modify files will simply fail.

Core capabilities:
- Locating files quickly using glob patterns
- Extracting relevant lines using regex-based content search
- Reading and interpreting file contents in depth

Tool usage guidelines:
{glob_guidance}
{grep_guidance}
- Use `{_EXPLORE_TOOL_READ_FILE}` to read a file when its path is already known
- Use `{_EXPLORE_TOOL_LIST_FILES}` to inspect directory layout when a targeted glob is unnecessary
{bash_ro}
- Calibrate search depth to the thoroughness level the caller requests (e.g. quick / medium / very thorough)
- Deliver findings as a plain text reply. Do not write output to any file

Performance expectations:
- Prioritize speed: plan your searches deliberately to minimise unnecessary tool calls
- Issue independent grep and read operations in parallel whenever possible

Return a clear, concise summary of your findings once the search is complete."""


def _build_explore_system_prompt_cn() -> str:
    glob_guidance = f"- 使用 `{_EXPLORE_TOOL_GLOB}` 做广泛的文件模式匹配"
    grep_guidance = f"- 使用 `{_EXPLORE_TOOL_GREP}` 用正则搜索文件内容"
    bash_ro = (
        f"- 仅将 `{_EXPLORE_TOOL_BASH}` 用于只读 shell 检查（如 ls、git status、git log、git diff、"
        f"cat、head、tail）；不要执行提交、推送、安装或任何会改变状态的命令"
    )

    return f"""你是宿主编程代理的代码库导航专家，职责是在现有代码中定位、读取并汇报信息。

=== 重要：仅限只读操作 ===
严禁以任何方式修改代码库，以下行为一律禁止：
- 新建文件（不得使用 write、touch 或任何创建文件的手段）
- 修改已有文件（不得执行编辑或原地替换操作）
- 删除文件（不得执行 rm 或等效命令）
- 移动或复制文件（不得执行 mv 或 cp）
- 在任意位置（包括 /tmp 或临时目录）生成临时文件
- 通过 shell 重定向（>、>>）或 heredoc 向磁盘写入内容
- 执行任何对系统产生持久副作用的命令

你没有写入类工具，任何试图修改文件的操作都会直接失败。

核心能力：
- 使用 glob 模式快速定位文件
- 借助正则表达式进行内容搜索
- 深入阅读并理解文件内容

工具使用指引：
{glob_guidance}
{grep_guidance}
- 已知具体路径时，使用 `{_EXPLORE_TOOL_READ_FILE}` 读取文件
- 需要了解目录结构且无需全量 glob 时，使用 `{_EXPLORE_TOOL_LIST_FILES}`
{bash_ro}
- 根据调用方指定的详尽程度（如 quick / medium / very thorough）调整搜索深度
- 以普通文本消息直接回复结果，不得将输出写入任何文件

性能要求：
- 以速度为优先，有针对性地规划搜索步骤，减少不必要的工具调用
- 凡相互独立的 grep 与读文件操作，尽量并行发起

搜索完成后，请以简洁清晰的方式汇报发现。"""


DEFAULT_EXPLORE_AGENT_SYSTEM_PROMPT_EN = _build_explore_system_prompt(language="en")
DEFAULT_EXPLORE_AGENT_SYSTEM_PROMPT_CN = _build_explore_system_prompt(language="cn")

DEFAULT_EXPLORE_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": DEFAULT_EXPLORE_AGENT_SYSTEM_PROMPT_CN,
    "en": DEFAULT_EXPLORE_AGENT_SYSTEM_PROMPT_EN,
}

DEFAULT_EXPLORE_AGENT_DESCRIPTION_EN = (
    "Codebase navigation agent optimised for speed. Invoke when you need to locate files by glob "
    'pattern (e.g. "src/components/**/*.tsx"), search source code for specific terms '
    '(e.g. "API endpoints"), or answer structural questions about a repository '
    '(e.g. "how do API endpoints work?"). Pass a thoroughness hint when calling: '
    '"quick" for a focused lookup, "medium" for a broader sweep, '
    'or "very thorough" for exhaustive analysis across multiple paths and naming conventions.'
)

DEFAULT_EXPLORE_AGENT_DESCRIPTION_CN = (
    "以速度为优先的代码库导航子代理：按 glob 模式定位文件（如 src/components/**/*.tsx）、"
    "按关键词检索源码（如 API 端点），或回答代码库结构性问题。"
    "调用时请传入详尽程度提示：quick 表示聚焦查找，medium 表示较宽范围扫描，"
    "very thorough 表示跨多路径与多种命名习惯的全面分析。"
)

DEFAULT_EXPLORE_AGENT_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_EXPLORE_AGENT_DESCRIPTION_CN,
    "en": DEFAULT_EXPLORE_AGENT_DESCRIPTION_EN,
}


def build_explore_agent_config(
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    model: Optional[Model] = None,
    rails: Optional[List[AgentRail]] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    workspace: Optional[str | Workspace] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    search_via_bash: bool = False,
) -> SubAgentConfig:
    """Build a SubAgentConfig for the built-in Explore subagent."""
    resolved_language = resolve_language(language)

    return SubAgentConfig(
        agent_card=card or AgentCard(
            name="explore_agent",
            description=DEFAULT_EXPLORE_AGENT_DESCRIPTION.get(
                resolved_language,
                DEFAULT_EXPLORE_AGENT_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=system_prompt
        or _build_explore_system_prompt(language=resolved_language),
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=model,
        rails=rails if rails is not None else [SysOperationRail()],
        skills=skills,
        backend=backend,
        workspace=workspace,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        restrict_to_work_dir=False
    )


def create_explore_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    search_via_bash: bool = False,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create and configure a predefined Explore subagent instance."""
    resolved_language = resolve_language(language)

    final_card = card or AgentCard(
        name="explore_agent",
        description=DEFAULT_EXPLORE_AGENT_DESCRIPTION.get(
            resolved_language,
            DEFAULT_EXPLORE_AGENT_DESCRIPTION["cn"],
        ),
    )
    final_prompt = system_prompt or _build_explore_system_prompt(
        language=resolved_language,
    )
    final_rails = rails if rails is not None else [SysOperationRail()]

    return create_deep_agent(
        model=model,
        card=final_card,
        system_prompt=final_prompt,
        tools=tools,
        mcps=mcps,
        subagents=subagents,
        rails=final_rails,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        **config_kwargs,
    )


__all__ = [
    "build_explore_agent_config",
    "create_explore_agent",
]
