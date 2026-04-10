# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual text constants for workspace and context sections."""
from __future__ import annotations

from typing import Dict

# ---------------------------------------------------------------------------
# Workspace header
# ---------------------------------------------------------------------------
WORKSPACE_HEADER_CN = "# 工作空间\n\n"
WORKSPACE_HEADER_EN = "# Workspace\n\n"

WORKSPACE_HEADER: Dict[str, str] = {
    "cn": WORKSPACE_HEADER_CN,
    "en": WORKSPACE_HEADER_EN,
}


# ---------------------------------------------------------------------------
# Context header
# ---------------------------------------------------------------------------
CONTEXT_HEADER_CN = "# 项目上下文\n\n以下文件已加载到上下文中，无需再次读取。\n\n"
CONTEXT_HEADER_EN = (
    "# Project Context\n\n"
    "The following files are already loaded into context, so you do not need to "
    "read them again.\n\n"
)

CONTEXT_HEADER: Dict[str, str] = {
    "cn": CONTEXT_HEADER_CN,
    "en": CONTEXT_HEADER_EN,
}


# ---------------------------------------------------------------------------
# Context file titles
# ---------------------------------------------------------------------------
CONTEXT_FILE_TITLES_CN: Dict[str, str] = {
    "AGENT.md": "## AGENT.md - 智能体配置",
    "SOUL.md": "## SOUL.md - 灵魂与价值观",
    "HEARTBEAT.md": "## HEARTBEAT.md - 心跳任务",
    "USER.md": "## USER.md - 用户信息",
    "IDENTITY.md": "## IDENTITY.md - 身份凭证",
    "MEMORY.md": "## MEMORY.md - 长期记忆",
}

CONTEXT_FILE_TITLES_EN: Dict[str, str] = {
    "AGENT.md": "## AGENT.md - Agent Configuration",
    "SOUL.md": "## SOUL.md - Soul & Values",
    "HEARTBEAT.md": "## HEARTBEAT.md - Heartbeat Tasks",
    "USER.md": "## USER.md - User Information",
    "IDENTITY.md": "## IDENTITY.md - Identity Credentials",
    "MEMORY.md": "## MEMORY.md - Long-term Memory",
}

CONTEXT_FILE_TITLES: Dict[str, Dict[str, str]] = {
    "cn": CONTEXT_FILE_TITLES_CN,
    "en": CONTEXT_FILE_TITLES_EN,
}


# ---------------------------------------------------------------------------
# Daily memory titles
# ---------------------------------------------------------------------------
DAILY_MEMORY_TITLE_CN = "## daily_memory/{date} - 今日记忆"
DAILY_MEMORY_TITLE_EN = "## daily_memory/{date} - Today's Memory"

DAILY_MEMORY_TITLE: Dict[str, str] = {
    "cn": DAILY_MEMORY_TITLE_CN,
    "en": DAILY_MEMORY_TITLE_EN,
}

# ---------------------------------------------------------------------------
# Directory/file descriptions
# ---------------------------------------------------------------------------
DIRECTORY_DESCRIPTIONS_CN: Dict[str, str] = {
    "AGENT.md": "智能体配置",
    "SOUL.md": "灵魂与价值观",
    "HEARTBEAT.md": "心跳任务",
    "USER.md": "用户信息",
    "IDENTITY.md": "身份凭证",
    "MEMORY.md": "长期记忆",
    "memory": "记忆核心模块",
    "daily_memory": "每日结构化记忆",
    "todo": "待办事项",
    "messages": "消息历史",
    "skills": "技能库",
    "agents": "子智能体",
}

DIRECTORY_DESCRIPTIONS_EN: Dict[str, str] = {
    "AGENT.md": "Agent configuration",
    "SOUL.md": "Soul & values",
    "HEARTBEAT.md": "Heartbeat tasks",
    "USER.md": "User information",
    "IDENTITY.md": "Identity credentials",
    "MEMORY.md": "Long-term memory",
    "memory": "Memory core module",
    "daily_memory": "Daily structured memory",
    "todo": "Todo items",
    "messages": "Message history",
    "skills": "Skills library",
    "agents": "Sub-agents",
}

DIRECTORY_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "cn": DIRECTORY_DESCRIPTIONS_CN,
    "en": DIRECTORY_DESCRIPTIONS_EN,
}


# ---------------------------------------------------------------------------
# Fixed context files
# ---------------------------------------------------------------------------
CONTEXT_FILES = [
    "AGENT.md",
    "SOUL.md",
    "HEARTBEAT.md",
    "USER.md",
    "IDENTITY.md",
]
