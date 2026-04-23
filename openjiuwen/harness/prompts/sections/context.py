# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context prompt section for DeepAgent - reads config files and daily memory."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional
import re

from zoneinfo import ZoneInfo

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.prompts.workspace_content.workspace_header import (
    CONTEXT_HEADER,
    CONTEXT_FILE_TITLES,
    DAILY_MEMORY_TITLE,
    CONTEXT_FILES,
)
from openjiuwen.harness.workspace.workspace import WorkspaceNode
from openjiuwen.harness.prompts.sections import SectionName

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection


# ---------------------------------------------------------------------------
# Template detection
# ---------------------------------------------------------------------------

# Marker phrases found only in unfilled workspace templates.
_TEMPLATE_MARKERS = (
    "此处应保存的内容",
    "What should be saved here",
    "在你们的第一次对话中填写",
    "Fill this in during your first",
    "在这里添加你需要",
    "Add your periodic tasks here",
)


def _is_unfilled_template(content: str, max_template_len: int = 500) -> bool:
    """Return True if *content* looks like an unfilled workspace template.

    Rules (applied in order):
    1. Files longer than *max_template_len* are never considered templates.
    2. After stripping HTML comments, if nothing remains → template.
    3. If any ``_TEMPLATE_MARKERS`` phrase appears in the raw content → template.
    4. After also stripping Markdown headings, if nothing remains → template.
    """
    if len(content) > max_template_len:
        return False
    text = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL).strip()
    if not text:
        return True
    for marker in _TEMPLATE_MARKERS:
        if marker in content:
            return True
    no_headings = re.sub(r'^#{1,6}\s+.*$', '', text, flags=re.MULTILINE).strip()
    return not no_headings


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _format_date(timezone: str = "Asia/Shanghai") -> str:
    """Format current date string with timezone.

    Args:
        timezone: IANA timezone name (e.g. 'Asia/Shanghai', 'UTC').
                  Defaults to 'Asia/Shanghai'.

    Returns:
        Date string in 'YYYY-MM-DD' format.
    """
    tz = ZoneInfo(timezone)
    return datetime.now(tz).strftime("%Y-%m-%d")


async def _read_context_file(
        sys_operation,
        workspace,
        file_key: str,
) -> str | None:
    """Read a single context file using sys_operation.

    Args:
        sys_operation: SysOperation instance.
        workspace: Workspace instance.
        file_key: File identifier (e.g. "AGENT.md").

    Returns:
        File content string, or None if file doesn't exist or read fails.
    """
    if sys_operation is None:
        return None

    full_path: Path | None
    if file_key == WorkspaceNode.MEMORY_MD.value:
        memory_dir = workspace.get_node_path(WorkspaceNode.MEMORY)
        full_path = memory_dir / WorkspaceNode.MEMORY_MD.value if memory_dir else None
    else:
        full_path = workspace.get_node_path(file_key)

    if full_path is None:
        return None

    result = await sys_operation.fs().read_file(str(full_path))
    if result.code == 0 and result.data:
        content = result.data.content
        if content and not _is_unfilled_template(content):
            return content

    return None


async def _read_daily_memory(
        sys_operation,
        workspace,
        timezone: Optional[str] = None,
) -> str | None:
    """Read today's daily memory file only when today's file exists.

    Args:
        sys_operation: SysOperation instance.
        workspace: Workspace instance.
        timezone: IANA timezone name for date formatting (e.g. 'Asia/Shanghai', 'UTC').
                  Defaults to 'Asia/Shanghai' if None.

    Returns:
        Daily memory content string, or None if today's file doesn't exist.
    """
    if sys_operation is None:
        return None

    memory_dir = workspace.get_node_path(WorkspaceNode.MEMORY)
    if memory_dir is None:
        return None

    tz = timezone or "Asia/Shanghai"
    date = _format_date(tz)
    daily_memory_dir = memory_dir / WorkspaceNode.DAILY_MEMORY.value
    list_result = await sys_operation.fs().list_files(path=str(daily_memory_dir))
    if list_result.code != 0 or not list_result.data or not list_result.data.list_items:
        return None

    today_file = f"{date}.md"
    if not any(item.name == today_file for item in list_result.data.list_items):
        return None

    full_path = daily_memory_dir / f"{date}.md"

    result = await sys_operation.fs().read_file(str(full_path))
    if result.code == 0 and result.data:
        return result.data.content

    return None


async def _build_context_content(
        sys_operation,
        workspace,
        language: str = "cn",
        extra_content: Optional[str] = None,
        timezone: Optional[str] = None,
) -> str:
    """Build the complete context file contents section.

    Args:
        sys_operation: SysOperation instance.
        workspace: Workspace instance.
        language: 'cn' or 'en'.
        extra_content: Optional content to append at the end (e.g. tools list).
        timezone: IANA timezone name for date formatting (e.g. 'Asia/Shanghai', 'UTC').
                  Uses local timezone if None.

    Returns:
        Formatted context content string.
    """
    header = CONTEXT_HEADER.get(language, CONTEXT_HEADER["cn"])
    titles = CONTEXT_FILE_TITLES.get(language, CONTEXT_FILE_TITLES["cn"])
    daily_title_tpl = DAILY_MEMORY_TITLE.get(language, DAILY_MEMORY_TITLE["cn"])

    parts = [header]

    for file_key in CONTEXT_FILES:
        content = await _read_context_file(sys_operation, workspace, file_key)
        if content is None:
            continue
        title = titles.get(file_key, f"## {file_key}")
        parts.append(f"{title}\n\n{content}\n\n")

    if language == "cn":
        parts.append("[以下文件仅在有实际内容时注入，空文件跳过]\n\n")
    else:
        parts.append(
            "[The following files are injected only when they contain real content; "
            "empty files are skipped]\n\n"
        )

    daily_content = await _read_daily_memory(sys_operation, workspace, timezone)
    if daily_content:
        date = _format_date(timezone or "Asia/Shanghai")
        title = daily_title_tpl.format(date=date)
        parts.append(f"{title}\n\n{daily_content}\n\n")

    if extra_content:
        parts.append(extra_content)

    return "".join(parts)


async def build_context_section(
        sys_operation,
        workspace,
        language: str = "cn",
        tools_content: Optional[str] = None,
        timezone: Optional[str] = None,
) -> Optional["PromptSection"]:
    """Build a PromptSection for context files.

    Args:
        sys_operation: SysOperation instance.
        workspace: Workspace object with root_path attribute.
        language: 'cn' or 'en'.
        tools_content: Optional pre-rendered tools content string for the given language.
        timezone: IANA timezone name for date formatting (e.g. 'Asia/Shanghai', 'UTC').
                  Uses local timezone if None.

    Returns:
        A PromptSection instance with context content, or None if workspace is None.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    if workspace is None:
        return None

    content = await _build_context_content(
        sys_operation, workspace, language, extra_content=tools_content, timezone=timezone
    )

    return PromptSection(
        name=SectionName.CONTEXT,
        content={language: content},
        priority=80,
    )


def _extract_task_tool_agent_lines(
        description: str,
        language: str = "cn",
) -> list[str]:
    """Extract available sub-agent lines from task_tool's tool description."""
    if not description:
        return []

    if language == "cn":
        marker = "可用代理类型及对应工具："
        stop_marker = "重要："
    else:
        marker = "Available agent types and the tools they have access to:"
        stop_marker = "Important:"

    if marker not in description:
        return []

    body = description.split(marker, 1)[1]
    if stop_marker in body:
        body = body.split(stop_marker, 1)[0]

    lines = []
    for raw_line in body.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lines.append(line if line.startswith("- ") else f"- {line}")
    return lines


def build_tools_content(
        ability_manager,
        language: str = "cn",
) -> Optional[str]:
    """Build tools list content string.

    Args:
        ability_manager: AbilityManager instance (or None).
        language: 'cn' or 'en'.

    Returns:
        Formatted tools content string, or None if no tools available.
    """
    if ability_manager is None:
        return None

    tool_descriptions = {}
    for ability in ability_manager.list():
        if isinstance(ability, ToolCard) and ability.name and ability.description:
            tool_descriptions[ability.name] = ability.description

    if not tool_descriptions:
        return None

    hidden_tools = {
        "cron_list_jobs",
        "cron_get_job",
        "cron_create_job",
        "cron_update_job",
        "cron_delete_job",
        "cron_toggle_job",
        "cron_preview_job",
    }

    grouped_labels = [
        (
            ("read_file", "write_file", "edit_file"),
            "read_file / write_file / edit_file" if language == "cn" else "read_file / write_file / edit_file",
            "文件读写编辑" if language == "cn" else "Read, write, and edit files",
        ),
        (
            ("glob", "list_files", "grep"),
            "glob / list_files / grep",
            "文件搜索" if language == "cn" else "Search files and file contents",
        ),
    ]
    memory_group = (
        ("memory_search", "memory_get", "write_memory", "edit_memory", "read_memory"),
        "memory_search / memory_get / write_memory / edit_memory / read_memory",
        "记忆系统" if language == "cn" else "Memory system",
    )

    summary_overrides_cn = {
        "paid_search": "付费联网搜索（配置 API 时优先使用）",
        "free_search": "免费搜索（DuckDuckGo 等）",
        "fetch_webpage": "抓取网页文本内容",
        "image_ocr": "读取图片中的文字",
        "visual_question_answering": "理解图片内容并回答问题",
        "audio_transcription": "转写音频文件",
        "audio_question_answering": "理解音频内容并回答",
        "audio_metadata": "识别音频时长和歌曲信息",
        "video_understanding": "分析视频内容",
        "session_new": "创建多个协程任务（子 agent 异步运行）",
        "session_cancel": "取消正在运行的协程",
        "session_list": "查看所有协程状态",
        "cron": "管理定时任务与提醒",
        "bash": "执行 Shell 命令",
        "code": "执行 Python 或 JavaScript 代码",
        "list_skill": "列出可用技能",
        "task_tool": "启动临时子代理处理复杂任务",
    }
    summary_overrides_en = {
        "paid_search": "Paid web search (preferred when configured)",
        "free_search": "Free web search",
        "fetch_webpage": "Fetch webpage text",
        "image_ocr": "Read text from images",
        "visual_question_answering": "Understand images and answer questions",
        "audio_transcription": "Transcribe audio",
        "audio_question_answering": "Understand audio and answer questions",
        "audio_metadata": "Identify audio duration and song metadata",
        "video_understanding": "Analyze video content",
        "session_new": "Create async sub-agent sessions",
        "session_cancel": "Cancel a running sub-agent session",
        "session_list": "List sub-agent session status",
        "cron": "Manage scheduled jobs and reminders",
        "bash": "Run shell commands",
        "code": "Run Python or JavaScript code",
        "list_skill": "List available skills",
        "task_tool": "Launch a temporary sub-agent for complex work",
    }
    summary_overrides = summary_overrides_cn if language == "cn" else summary_overrides_en

    def _tool_summary(name: str) -> str:
        """Return concise tool summary with safe dict access."""
        return summary_overrides.get(name, tool_descriptions.get(name, "").strip())

    header = "# 可用工具" if language == "cn" else "# Available Tools"
    lines = [header, ""]
    rendered_names: set[str] = set()

    preferred_order = [
        "paid_search",
        "free_search",
        "fetch_webpage",
        "image_ocr",
        "visual_question_answering",
        "audio_transcription",
        "audio_question_answering",
        "audio_metadata",
        "video_understanding",
        "session_new",
        "session_cancel",
        "session_list",
        "cron",
    ]

    for name in preferred_order:
        if name in tool_descriptions and name not in hidden_tools:
            lines.append(f"- {name}: {_tool_summary(name)}")
            rendered_names.add(name)

    for group_names, label, summary in grouped_labels:
        existing = [name for name in group_names if name in tool_descriptions and name not in hidden_tools]
        if len(existing) == len(group_names):
            lines.append(f"- {label}: {summary}")
            rendered_names.update(existing)
        else:
            for name in existing:
                lines.append(f"- {name}: {_tool_summary(name)}")
                rendered_names.add(name)

    for name in ("bash", "code"):
        if name in tool_descriptions and name not in hidden_tools and name not in rendered_names:
            lines.append(f"- {name}: {_tool_summary(name)}")
            rendered_names.add(name)

    if "list_skill" in tool_descriptions and "list_skill" not in rendered_names:
        lines.append(f"- list_skill: {_tool_summary('list_skill')}")
        rendered_names.add("list_skill")

    group_names, label, summary = memory_group
    existing = [name for name in group_names if name in tool_descriptions and name not in hidden_tools]
    if len(existing) == len(group_names):
        lines.append(f"- {label}: {summary}")
        rendered_names.update(existing)
    else:
        for name in existing:
            lines.append(f"- {name}: {_tool_summary(name)}")
            rendered_names.add(name)

    if "task_tool" in tool_descriptions and "task_tool" not in rendered_names:
        lines.append(f"- task_tool: {_tool_summary('task_tool')}")
        rendered_names.add("task_tool")

    if "bash" in rendered_names:
        if language == "cn":
            lines.extend(
                [
                    "",
                    "## bash 使用原则",
                    "",
                    "- 优先使用专用工具完成文件搜索、内容搜索、读取、编辑和写入，不要用 bash 替代 `glob` / `grep` / "
                    "`read_file` / `edit_file` / `write_file`",
                    "- 独立命令尽量并行调用；多步依赖命令才在单次调用里用 `&&` 串联，仅在不关心前序失败时才用 `;`",
                    "- 长时间运行命令使用 `background: true`，不要用 `sleep` 轮询等待",
                    "- 尽量使用绝对路径并避免频繁 `cd`；路径包含空格时使用双引号",
                    "- 执行破坏性 Git 操作前先考虑更安全的替代方案",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "## bash Guidelines",
                    "",
                    "- Prefer dedicated tools for file search, content search, reading, editing, and writing "
                    "instead of using bash as a substitute for `glob` / `grep` / `read_file` / `edit_file` / "
                    "`write_file`",
                    "- Run independent commands in parallel; only chain dependent commands with `&&`, and "
                    "use `;` only when earlier failures do not matter",
                    "- Use `background: true` for long-running commands instead of polling with `sleep`",
                    "- Prefer absolute paths and avoid frequent `cd`; quote paths with spaces using double quotes",
                    "- Consider safer alternatives before destructive Git operations",
                ]
            )

    if "task_tool" in rendered_names:
        if language == "cn":
            lines.extend(
                [
                    "",
                    "## task_tool 使用原则",
                    "",
                    "- 任务复杂、多步骤、可独立执行时使用",
                    "- 独立任务尽量并行执行",
                    "- 简单任务直接执行，不使用子代理",
                ]
            )
            agent_lines = _extract_task_tool_agent_lines(
                tool_descriptions.get("task_tool", ""),
                language,
            )
            if agent_lines:
                lines.extend(["", "可用代理类型：", *agent_lines])
        else:
            lines.extend(
                [
                    "",
                    "## task_tool Guidelines",
                    "",
                    "- Use it for complex, multi-step, independent tasks",
                    "- Run independent tasks in parallel when possible",
                    "- Execute simple tasks directly without spawning a sub-agent",
                ]
            )
            agent_lines = _extract_task_tool_agent_lines(
                tool_descriptions.get("task_tool", ""),
                language,
            )
            if agent_lines:
                lines.extend(["", "Available agent types:", *agent_lines])

    for name, desc in tool_descriptions.items():
        if name in rendered_names or name in hidden_tools:
            continue
        compact_desc = desc.strip().splitlines()[0]
        lines.append(f"- {name}: {summary_overrides.get(name, compact_desc)}")

    return "\n".join(lines) + "\n"


def build_tools_section(
        ability_manager,
        language: str = "cn",
) -> Optional["PromptSection"]:
    """Build an independent PromptSection for tools (P:30).

    Args:
        ability_manager: AbilityManager instance (or None).
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance, or None if no tools available.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    content = build_tools_content(ability_manager, language)
    if not content:
        return None

    return PromptSection(
        name=SectionName.TOOLS,
        content={language: content},
        priority=30,
    )
