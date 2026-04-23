# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt section definitions and shared section name constants."""


class SectionName:
    """Centralized section name constants used by PromptSection and Rails."""

    IDENTITY = "identity"
    SAFETY = "safety"
    SKILLS = "skills"
    TOOLS = "tools"
    TODO = "todo"
    TASK_TOOL = "task_tool"
    TOOL_NAVIGATION = "tool_navigation"
    PROGRESSIVE_TOOL_RULES = "progressive_tool_rules"
    RUNTIME = "runtime"
    MEMORY = "memory"
    SESSION_TOOLS = "session_tools"
    MODE_INSTRUCTIONS = "mode_instructions"
    WORKSPACE = "workspace"
    HEARTBEAT = "heartbeat"
    CONTEXT = "context"
    EXTERNAL_MEMORY = "external_memory"
    COMPLETION_SIGNAL = "completion_signal"
    VERIFICATION_CONTRACT = "verification_contract"
