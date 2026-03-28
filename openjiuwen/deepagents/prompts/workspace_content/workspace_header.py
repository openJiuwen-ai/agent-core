# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual text constants for workspace and context sections."""
from __future__ import annotations

from typing import Dict

# ---------------------------------------------------------------------------
# Workspace header
# ---------------------------------------------------------------------------
WORKSPACE_HEADER_CN = "## 工作空间\n\n### 目录结构\n\n"
WORKSPACE_HEADER_EN = "## Workspace\n\n### directory structure\n\n"

WORKSPACE_HEADER: Dict[str, str] = {
    "cn": WORKSPACE_HEADER_CN,
    "en": WORKSPACE_HEADER_EN,
}


# ---------------------------------------------------------------------------
# Context header
# ---------------------------------------------------------------------------
CONTEXT_HEADER_CN = "## Context\n\n### 文件内容\n\n"
CONTEXT_HEADER_EN = "## Context\n\n### File Contents\n\n"

CONTEXT_HEADER: Dict[str, str] = {
    "cn": CONTEXT_HEADER_CN,
    "en": CONTEXT_HEADER_EN,
}


# ---------------------------------------------------------------------------
# Context file titles
# ---------------------------------------------------------------------------
CONTEXT_FILE_TITLES_CN: Dict[str, str] = {
    "Agent.md": "## Agent.md - 智能体配置",
    "Soul.md": "## Soul.md - 灵魂与价值观",
    "HeartBeat.md": "## HeartBeat.md - 心跳任务",
    "User.md": "## User.md - 用户信息",
    "Identity.md": "## Identity.md - 身份凭证",
    "Memory.md": "## Memory.md - 长期记忆",
}

CONTEXT_FILE_TITLES_EN: Dict[str, str] = {
    "Agent.md": "## Agent.md - Agent Configuration",
    "Soul.md": "## Soul.md - Soul & Values",
    "HeartBeat.md": "## HeartBeat.md - Heartbeat Tasks",
    "User.md": "## User.md - User Information",
    "Identity.md": "## Identity.md - Identity Credentials",
    "Memory.md": "## Memory.md - Long-term Memory",
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
    "Agent.md": "智能体配置",
    "Soul.md": "灵魂与价值观",
    "HeartBeat.md": "心跳任务",
    "User.md": "用户信息",
    "Identity.md": "身份凭证",
    "Memory.md": "长期记忆",
    "memory": "记忆核心模块",
    "daily_memory": "每日结构化记忆",
    "todo": "待办事项",
    "messages": "消息历史",
    "skills": "技能库",
    "agents": "子智能体",
    "user": "用户数据",
}

DIRECTORY_DESCRIPTIONS_EN: Dict[str, str] = {
    "Agent.md": "Agent configuration",
    "Soul.md": "Soul & values",
    "HeartBeat.md": "Heartbeat tasks",
    "User.md": "User information",
    "Identity.md": "Identity credentials",
    "Memory.md": "Long-term memory",
    "memory": "Memory core module",
    "daily_memory": "Daily structured memory",
    "todo": "Todo items",
    "messages": "Message history",
    "skills": "Skills library",
    "agents": "Sub-agents",
    "user": "User data",
}

DIRECTORY_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "cn": DIRECTORY_DESCRIPTIONS_CN,
    "en": DIRECTORY_DESCRIPTIONS_EN,
}


# ---------------------------------------------------------------------------
# Fixed context files
# ---------------------------------------------------------------------------
CONTEXT_FILES = [
    "Agent.md",
    "Soul.md",
    "HeartBeat.md",
    "User.md",
    "Identity.md",
    "Memory.md",
]
