# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import importlib
from typing import TYPE_CHECKING

__all__ = [
    "AudioMetadataTool",
    "AudioQuestionAnsweringTool",
    "AudioTranscriptionTool",
    "BashTool",
    "PowerShellTool",
    "CodeTool",
    "CronToolContext",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "GlobTool",
    "GrepTool",
    "create_cron_tools",
    "SearchToolsTool",
    "LoadToolsTool",
    "ImageOCRTool",
    "ListDirTool",
    "ListSkillTool",
    "LoadToolsTool",
    "ReadFileTool",
    "SearchToolsTool",
    "SkillTool",
    "SkillCompleteTool",
    "TodoCreateTool",
    "TodoListTool",
    "TodoModifyTool",
    "TodoTool",
    "ToolOutput",
    "VisualQuestionAnsweringTool",
    "WebFetchWebpageTool",
    "WebFreeSearchTool",
    "WebPaidSearchTool",
    "create_web_tools",
    "is_free_search_enabled",
    "WriteFileTool",
    "LspTool",
    "LspToolMetadataProvider",
    "create_audio_tools",
    "create_todos_tool",
    "create_vision_tools",
    "EnterPlanModeTool",
    "ExitPlanModeTool",
    "SwitchModeTool",
    "generate_word_slug",
    "get_or_create_plan_slug",
    "resolve_plan_file_path",
]

_LAZY_EXPORTS = {
    "AudioMetadataTool": "openjiuwen.harness.tools.audio",
    "AudioQuestionAnsweringTool": "openjiuwen.harness.tools.audio",
    "AudioTranscriptionTool": "openjiuwen.harness.tools.audio",
    "create_audio_tools": "openjiuwen.harness.tools.audio",
    "ToolOutput": "openjiuwen.harness.tools.base_tool",
    "CodeTool": "openjiuwen.harness.tools.code",
    "CronToolContext": "openjiuwen.harness.tools.cron",
    "create_cron_tools": "openjiuwen.harness.tools.cron",
    "EditFileTool": "openjiuwen.harness.tools.filesystem",
    "GlobTool": "openjiuwen.harness.tools.filesystem",
    "GrepTool": "openjiuwen.harness.tools.filesystem",
    "ListDirTool": "openjiuwen.harness.tools.filesystem",
    "ReadFileTool": "openjiuwen.harness.tools.filesystem",
    "WriteFileTool": "openjiuwen.harness.tools.filesystem",
    "ListSkillTool": "openjiuwen.harness.tools.list_skill",
    "LoadToolsTool": "openjiuwen.harness.tools.load_tools",
    "SearchToolsTool": "openjiuwen.harness.tools.search_tools",
    "SkillTool": "openjiuwen.harness.tools.skill_tool",
    "SkillCompleteTool": "openjiuwen.harness.tools.skill_complete_tool",
    "BashTool": "openjiuwen.harness.tools.bash",
    "PowerShellTool": "openjiuwen.harness.tools.powershell",
    "TodoCreateTool": "openjiuwen.harness.tools.todo",
    "TodoListTool": "openjiuwen.harness.tools.todo",
    "TodoModifyTool": "openjiuwen.harness.tools.todo",
    "TodoTool": "openjiuwen.harness.tools.todo",
    "create_todos_tool": "openjiuwen.harness.tools.todo",
    "ImageOCRTool": "openjiuwen.harness.tools.vision",
    "VisualQuestionAnsweringTool": "openjiuwen.harness.tools.vision",
    "create_vision_tools": "openjiuwen.harness.tools.vision",
    "WebFetchWebpageTool": "openjiuwen.harness.tools.web_tools",
    "WebFreeSearchTool": "openjiuwen.harness.tools.web_tools",
    "WebPaidSearchTool": "openjiuwen.harness.tools.web_tools",
    "create_web_tools": "openjiuwen.harness.tools.web_tools",
    "is_free_search_enabled": "openjiuwen.harness.tools.web_tools",
    "SwitchModeTool": "openjiuwen.harness.tools.agent_mode_tools",
    "EnterPlanModeTool": "openjiuwen.harness.tools.agent_mode_tools",
    "ExitPlanModeTool": "openjiuwen.harness.tools.agent_mode_tools",
    "generate_word_slug": "openjiuwen.harness.tools.agent_mode_tools",
    "get_or_create_plan_slug": "openjiuwen.harness.tools.agent_mode_tools",
    "resolve_plan_file_path": "openjiuwen.harness.tools.agent_mode_tools",
    "LspTool": "openjiuwen.harness.tools.lsp_tool",
    "LspToolMetadataProvider": "openjiuwen.harness.prompts.sections.tools.lsp_tool",
}


def __getattr__(name: str):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from openjiuwen.harness.prompts.sections.tools.lsp_tool import LspToolMetadataProvider
    from openjiuwen.harness.tools.agent_mode_tools import (
        EnterPlanModeTool,
        ExitPlanModeTool,
        SwitchModeTool,
        generate_word_slug,
        get_or_create_plan_slug,
        resolve_plan_file_path,
    )
    from openjiuwen.harness.tools.audio import (
        AudioMetadataTool,
        AudioQuestionAnsweringTool,
        AudioTranscriptionTool,
        create_audio_tools,
    )
    from openjiuwen.harness.tools.base_tool import ToolOutput
    from openjiuwen.harness.tools.bash import BashTool
    from openjiuwen.harness.tools.code import CodeTool
    from openjiuwen.harness.tools.cron import CronToolContext, create_cron_tools
    from openjiuwen.harness.tools.filesystem import (
        EditFileTool,
        GlobTool,
        GrepTool,
        ListDirTool,
        ReadFileTool,
        WriteFileTool,
    )
    from openjiuwen.harness.tools.list_skill import ListSkillTool
    from openjiuwen.harness.tools.load_tools import LoadToolsTool
    from openjiuwen.harness.tools.lsp_tool import LspTool
    from openjiuwen.harness.tools.powershell import PowerShellTool
    from openjiuwen.harness.tools.search_tools import SearchToolsTool
    from openjiuwen.harness.tools.skill_complete_tool import SkillCompleteTool
    from openjiuwen.harness.tools.skill_tool import SkillTool
    from openjiuwen.harness.tools.todo import (
        TodoCreateTool,
        TodoListTool,
        TodoModifyTool,
        TodoTool,
        create_todos_tool,
    )
    from openjiuwen.harness.tools.vision import ImageOCRTool, VisualQuestionAnsweringTool, create_vision_tools
    from openjiuwen.harness.tools.web_tools import (
        WebFetchWebpageTool,
        WebFreeSearchTool,
        WebPaidSearchTool,
        create_web_tools,
        is_free_search_enabled,
    )
