# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool description section package for DeepAgent.

Centralizes all bilingual tool descriptions and provides the
``build_tools_section`` factory used by Rails at runtime.

All built-in tools register via ``ToolMetadataProvider`` implementations.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)
from openjiuwen.harness.prompts.tools.ask_user import (
    AskUserMetadataProvider,
)
from openjiuwen.harness.prompts.tools.bash import (
    BashMetadataProvider,
)
from openjiuwen.harness.prompts.tools.powershell import (
    PowerShellMetadataProvider,
)
from openjiuwen.harness.prompts.tools.audio import (
    AudioMetadataMetadataProvider,
    AudioQuestionAnsweringMetadataProvider,
    AudioTranscriptionMetadataProvider,
)
from openjiuwen.harness.prompts.tools.code import (
    CodeMetadataProvider,
)
from openjiuwen.harness.prompts.tools.cron import (
    CronMetadataProvider,
)
from openjiuwen.harness.prompts.tools.filesystem import (
    ReadFileMetadataProvider,
    WriteFileMetadataProvider,
    EditFileMetadataProvider,
    GlobMetadataProvider,
    ListDirMetadataProvider,
    GrepMetadataProvider,
)
from openjiuwen.harness.prompts.tools.list_skill import (
    ListSkillMetadataProvider,
)
from openjiuwen.harness.prompts.tools.load_tools import (
    LoadToolsMetadataProvider,
)
from openjiuwen.harness.prompts.tools.search_tools import (
    SearchToolsMetadataProvider,
)
from openjiuwen.harness.prompts.tools.session_tools import (
    SessionsListMetadataProvider,
    SessionsSpawnMetadataProvider,
    SessionsCancelMetadataProvider,
)
from openjiuwen.harness.prompts.tools.skill_tool import (
    SkillToolMetadataProvider,
)
from openjiuwen.harness.prompts.tools.todo import (
    TodoCreateMetadataProvider,
    TodoListMetadataProvider,
    TodoModifyMetadataProvider,
    TodoGetMetadataProvider,
)
from openjiuwen.harness.prompts.tools.video_understanding import (
    VideoUnderstandingMetadataProvider,
)
from openjiuwen.harness.prompts.tools.vision import (
    ImageOCRMetadataProvider,
    VisualQuestionAnsweringMetadataProvider,
)
from openjiuwen.harness.prompts.tools.lsp_tool import (
    LspToolMetadataProvider,
)
from openjiuwen.harness.prompts.tools.task_tool import (
    TaskMetadataProvider,
)
from openjiuwen.harness.prompts.tools.web_tools import (
    FreeSearchMetadataProvider,
    PaidSearchMetadataProvider,
    FetchWebpageMetadataProvider,
)
from openjiuwen.harness.prompts.tools.agent_mode import (
    SwitchModeMetadataProvider,
    EnterPlanModeMetadataProvider,
    ExitPlanModeMetadataProvider,
)
from openjiuwen.harness.prompts.tools.mcp import (
    ListMcpResourcesMetadataProvider,
    ReadMcpResourceMetadataProvider,
)

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
_PROVIDERS: List[ToolMetadataProvider] = [
    AskUserMetadataProvider(),
    BashMetadataProvider(),
    PowerShellMetadataProvider(),
    AudioTranscriptionMetadataProvider(),
    AudioQuestionAnsweringMetadataProvider(),
    AudioMetadataMetadataProvider(),
    CodeMetadataProvider(),
    CronMetadataProvider(),
    ReadFileMetadataProvider(),
    WriteFileMetadataProvider(),
    EditFileMetadataProvider(),
    GlobMetadataProvider(),
    ListDirMetadataProvider(),
    GrepMetadataProvider(),
    ListSkillMetadataProvider(),
    SearchToolsMetadataProvider(),
    LoadToolsMetadataProvider(),
    SessionsListMetadataProvider(),
    SessionsSpawnMetadataProvider(),
    SessionsCancelMetadataProvider(),
    SkillToolMetadataProvider(),
    TodoCreateMetadataProvider(),
    TodoListMetadataProvider(),
    TodoModifyMetadataProvider(),
    TodoGetMetadataProvider(),
    ImageOCRMetadataProvider(),
    VisualQuestionAnsweringMetadataProvider(),
    VideoUnderstandingMetadataProvider(),
    TaskMetadataProvider(),
    LspToolMetadataProvider(),
    FreeSearchMetadataProvider(),
    PaidSearchMetadataProvider(),
    FetchWebpageMetadataProvider(),
    SwitchModeMetadataProvider(),
    EnterPlanModeMetadataProvider(),
    ExitPlanModeMetadataProvider(),
    ListMcpResourcesMetadataProvider(),
    ReadMcpResourceMetadataProvider(),
]

_REGISTRY: Dict[str, ToolMetadataProvider] = {
    p.get_name(): p for p in _PROVIDERS
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_tool_description(name: str, language: str = "cn") -> str:
    """查找工具描述。内置工具找不到时抛 KeyError（fail-fast）。"""
    provider = _REGISTRY.get(name)
    if provider is None:
        raise KeyError(
            f"Tool '{name}' not registered. "
            f"Available: {sorted(_REGISTRY.keys())}"
        )
    return provider.get_description(language)


def get_tool_input_params(
    name: str, language: str = "cn"
) -> Dict[str, Any]:
    """查找工具参数 schema。内置工具找不到时抛 KeyError。"""
    provider = _REGISTRY.get(name)
    if provider is None:
        raise KeyError(
            f"Tool '{name}' not registered. "
            f"Available: {sorted(_REGISTRY.keys())}"
        )
    return provider.get_input_params(language)


def build_tool_card(
    name: str,
    tool_id: str,
    language: str = "cn",
    format_args: Optional[Dict[str, str]] = None,
    agent_id: Optional[str] = None,
) -> ToolCard:
    """统一建卡函数。工具类不再自己拼 ToolCard。

    Args:
        name: 工具名称。
        tool_id: 工具 ID 前缀。
        language: 语言（'cn' 或 'en'）。
        format_args: 可选的格式化参数字典，用于填充描述中的占位符。
        agent_id: 可选的 agent ID，用于生成唯一的工具 ID。
            如果提供，最终的 tool_id 为 f"{tool_id}_{agent_id}"；
            如果不提供，使用 uuid 生成唯一后缀。

    Returns:
        配置好的 ToolCard 实例。
    """
    description = get_tool_description(name, language)

    # 如果提供了格式化参数，填充描述中的占位符
    if format_args:
        description = description.format(**format_args)

    final_tool_id = f"{tool_id}_{agent_id}" if agent_id else f"{tool_id}_{uuid.uuid4().hex}"

    return ToolCard(
        id=final_tool_id,
        name=name,
        description=description,
        input_params=get_tool_input_params(name, language),
    )


def register_tool_provider(
    provider: ToolMetadataProvider,
) -> None:
    """运行时注册新的工具 provider（供 rail 动态添加工具用）。

    注册时自动执行 validate()。
    """
    provider.validate()
    _REGISTRY[provider.get_name()] = provider


def validate_all_tool_providers() -> None:
    """校验所有已注册 provider 的双语完整性。

    可在启动或测试中调用。
    """
    for provider in _REGISTRY.values():
        provider.validate()


# ---------------------------------------------------------------------------
# build_tools_section factory (unchanged API)
# ---------------------------------------------------------------------------
def build_tools_section(
    tool_descriptions: Optional[Dict[str, str]] = None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build a dynamic tools section from tool descriptions.

    This is NOT registered in STATIC_SECTION_BUILDERS — it is called
    by Rails at runtime to inject tool descriptions into the prompt.

    Returns None if no tool descriptions are provided.
    """
    if not tool_descriptions:
        return None

    header = {"cn": "## 可用工具", "en": "## Available Tools"}
    lines = [header.get(language, header["cn"])]
    for name, desc in tool_descriptions.items():
        lines.append(f"- **{name}**: {desc}")

    content = "\n".join(lines)
    return PromptSection(
        name=SectionName.TOOLS,
        content={language: content},
        priority=40,
    )
