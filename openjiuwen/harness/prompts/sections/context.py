# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context prompt section for DeepAgent - reads config files and daily memory."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from zoneinfo import ZoneInfo

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.prompts.workspace_content.workspace_header import (
    CONTEXT_HEADER,
    CONTEXT_FILE_TITLES,
    DAILY_MEMORY_TITLE,
    CONTEXT_FILES,
)


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
        workspace_root: str,
        file_key: str,
) -> str | None:
    """Read a single context file using sys_operation.

    Args:
        sys_operation: SysOperation instance.
        workspace_root: Root path of the workspace.
        file_key: File identifier (e.g. "AGENT.md").

    Returns:
        File content string, or None if file doesn't exist or read fails.
    """
    if sys_operation is None:
        return None

    full_path = f"{workspace_root}/{file_key}" if workspace_root else file_key

    result = await sys_operation.fs().read_file(full_path)
    if result.code == 0 and result.data:
        return result.data.content

    return None


async def _read_daily_memory(
        sys_operation,
        workspace_root: str,
        timezone: Optional[str] = None,
) -> str | None:
    """Read today's daily memory file.

    Args:
        sys_operation: SysOperation instance.
        workspace_root: Root path of the workspace.
        timezone: IANA timezone name for date formatting (e.g. 'Asia/Shanghai', 'UTC').
                  Defaults to 'Asia/Shanghai' if None.

    Returns:
        Daily memory content string, or None if file doesn't exist.
    """
    if sys_operation is None:
        return None

    tz = timezone or "Asia/Shanghai"
    date = _format_date(tz)
    file_path = f"memory/daily_memory/{date}.md"
    full_path = f"{workspace_root}/{file_path}" if workspace_root else file_path

    result = await sys_operation.fs().read_file(full_path)
    if result.code == 0 and result.data:
        return result.data.content

    return None


async def _build_context_content(
        sys_operation,
        workspace_root: str,
        language: str = "cn",
        extra_content: Optional[str] = None,
        timezone: Optional[str] = None,
) -> str:
    """Build the complete context file contents section.

    Args:
        sys_operation: SysOperation instance.
        workspace_root: Root path of the workspace.
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
        content = await _read_context_file(sys_operation, workspace_root, file_key)
        if content is None:
            continue
        title = titles.get(file_key, f"## {file_key}")
        parts.append(f"{title}\n\n{content}\n\n")

    daily_content = await _read_daily_memory(sys_operation, workspace_root, timezone)
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

    workspace_root = str(getattr(workspace, "root_path", "") or "")

    content = await _build_context_content(
        sys_operation, workspace_root, language, extra_content=tools_content, timezone=timezone
    )

    return PromptSection(
        name="context",
        content={language: content},
        priority=96,
    )


def build_tools_content(
        ability_manager,
        language: str = "cn",
) -> Optional[str]:
    """Build tools list content string for embedding in context section.

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

    header = "## 可用工具\n" if language == "cn" else "## Available Tools\n"
    lines = [header]
    for name, desc in tool_descriptions.items():
        lines.append(f"- **{name}**: {desc}")

    return "\n".join(lines) + "\n"
