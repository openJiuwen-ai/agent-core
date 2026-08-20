# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from importlib import import_module
from typing import Any

from openjiuwen.harness.prompts.tools.lsp_tool import LspToolMetadataProvider
from openjiuwen.harness.tools.agent_mode_tools import (
    EnterPlanModeTool,
    ExitPlanModeTool,
    SwitchModeTool,
    generate_word_slug,
    get_or_create_plan_slug,
    resolve_plan_file_path,
)
from openjiuwen.harness.tools.ask_user import AskUserTool
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code import CodeTool
from openjiuwen.harness.tools.compression_recall import CompressionRecallTool
from openjiuwen.harness.tools.cron import (
    CronToolContext,
    create_cron_tools,
)
from openjiuwen.harness.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from openjiuwen.harness.tools.lsp_tool import LspTool
from openjiuwen.harness.tools.multimodal import (
    AudioMetadataTool,
    AudioQuestionAnsweringTool,
    AudioTranscriptionTool,
    ImageOCRTool,
    VideoUnderstandingTool,
    VisualQuestionAnsweringTool,
    create_audio_tools,
    create_vision_tools,
)
from openjiuwen.harness.tools.shell import BashTool, PowerShellTool
from openjiuwen.harness.tools.skills import ListSkillTool, SkillTool
from openjiuwen.harness.tools.subagent import (
    SESSION_SPAWN_TASK_TYPE,
    SessionsCancelTool,
    SessionsListTool,
    SessionsSpawnTool,
    SessionTaskRow,
    SessionToolkit,
    TaskTool,
    build_session_tools,
    create_task_tool,
)
from openjiuwen.harness.tools.todo import (
    TodoCreateTool,
    TodoGetTool,
    TodoListTool,
    TodoModifyTool,
    TodoTool,
    create_todos_tool,
)
from openjiuwen.harness.tools.tool_discovery import ToolSearchTool
from openjiuwen.harness.tools.web import (
    WebFetchWebpageTool,
    WebFreeSearchTool,
    WebPaidSearchTool,
    create_web_tools,
    is_free_search_enabled,
    is_paid_search_enabled,
)
from openjiuwen.harness.tools.worktree import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    WorktreeConfig,
    WorktreeManager,
)

__all__ = [
    "AudioMetadataTool",
    "AudioQuestionAnsweringTool",
    "AudioTranscriptionTool",
    "BashTool",
    "PowerShellTool",
    "CodeTool",
    "CompressionRecallTool",
    "CronToolContext",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "GlobTool",
    "GrepTool",
    "create_cron_tools",
    "ToolSearchTool",
    "ImageOCRTool",
    "ListDirTool",
    "ListSkillTool",
    "ReadFileTool",
    "SkillTool",
    "TodoCreateTool",
    "TodoListTool",
    "TodoModifyTool",
    "TodoGetTool",
    "TodoTool",
    "ToolOutput",
    "VisualQuestionAnsweringTool",
    "VideoUnderstandingTool",
    "WebFetchWebpageTool",
    "WebFreeSearchTool",
    "WebPaidSearchTool",
    "create_web_tools",
    "is_free_search_enabled",
    "is_paid_search_enabled",
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
    "SESSION_SPAWN_TASK_TYPE",
    "SessionTaskRow",
    "SessionToolkit",
    "SessionsListTool",
    "SessionsSpawnTool",
    "SessionsCancelTool",
    "build_session_tools",
    "TaskTool",
    "create_task_tool",
    "AskUserTool",
    "EnterWorktreeTool",
    "ExitWorktreeTool",
    "WorktreeConfig",
    "WorktreeManager",
]


_LAZY_SUBMODULES = {
    "multimodal": "openjiuwen.harness.tools.multimodal",
    "web": "openjiuwen.harness.tools.web",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_SUBMODULES:
        module = import_module(_LAZY_SUBMODULES[name])
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
