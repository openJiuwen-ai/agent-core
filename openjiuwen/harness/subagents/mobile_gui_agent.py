# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for the mobile GUI (VLM grounding) subagent.

Use ``factory_name="mobile_gui_agent"`` on :class:`SubAgentConfig` so
:class:`DeepAgent` dispatches to :func:`create_mobile_gui_agent`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.schema.config import SubAgentConfig

from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.rails_factory import (
    build_mobile_gui_rails,
    resolve_mobile_skill_root,
)
from openjiuwen.harness.tools.mobile_gui.runtime_tools import build_mobile_gui_tool_instances
from openjiuwen.harness.tools.mobile_gui.tiktoken_multimodal_patch import (
    apply_tiktoken_counter_multimodal_patch,
)
from openjiuwen.harness.tools.mobile_gui.vlm_grounding_prompt import (
    build_vlm_grounding_system_prompt,
)

try:
    from openjiuwen.harness.prompts import resolve_language
except ImportError:
    def resolve_language(language: Optional[str] = None) -> str:  # type: ignore[misc]
        return language if language in {"cn", "en"} else "cn"


if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace

DEFAULT_MOBILE_GUI_DESCRIPTION_EN = (
    "Dedicated mobile subagent for Android GUI automation using VLM coordinate grounding."
)
DEFAULT_MOBILE_GUI_DESCRIPTION_CN = "专用移动子代理，基于 VLM 坐标 grounding 操控 Android GUI。"

DEFAULT_MOBILE_GUI_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_MOBILE_GUI_DESCRIPTION_CN,
    "en": DEFAULT_MOBILE_GUI_DESCRIPTION_EN,
}


def _resolve_runtime_settings(
    model: Model | None,
    settings: Optional[MobileGuiRuntimeSettings],
) -> MobileGuiRuntimeSettings:
    if settings is not None:
        return settings
    return MobileGuiRuntimeSettings.from_env()


def build_mobile_gui_agent_config(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 30,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[MobileGuiRuntimeSettings] = None,
) -> SubAgentConfig:
    """Build a SubAgentConfig that materializes as :func:`create_mobile_gui_agent`."""
    resolved_language = resolve_language(language)
    resolved_settings = _resolve_runtime_settings(model, settings)
    default_prompt = build_vlm_grounding_system_prompt(resolved_settings)
    return SubAgentConfig(
        agent_card=card or AgentCard(
            name="mobile_gui_agent",
            description=DEFAULT_MOBILE_GUI_DESCRIPTION.get(
                resolved_language,
                DEFAULT_MOBILE_GUI_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=system_prompt or default_prompt,
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=model,
        rails=rails,
        skills=skills,
        backend=backend,
        workspace=workspace,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        factory_name="mobile_gui_agent",
        factory_kwargs={"settings": resolved_settings},
    )


def create_mobile_gui_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 30,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[MobileGuiRuntimeSettings] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create a DeepAgent with Android VLM grounding tools and rails."""
    apply_tiktoken_counter_multimodal_patch()
    resolved_language = resolve_language(language)
    resolved_settings = _resolve_runtime_settings(model, settings)

    final_card = card or AgentCard(
        name="mobile_gui_agent",
        description=DEFAULT_MOBILE_GUI_DESCRIPTION.get(
            resolved_language,
            DEFAULT_MOBILE_GUI_DESCRIPTION["cn"],
        ),
    )
    default_prompt = build_vlm_grounding_system_prompt(resolved_settings)
    final_prompt = system_prompt or default_prompt

    skill_root = resolve_mobile_skill_root(workspace)
    injected_tools = build_mobile_gui_tool_instances(resolved_settings)
    injected_rails = build_mobile_gui_rails(
        resolved_settings,
        skill_root=skill_root,
        model=model,
    )
    final_tools: List[Tool | ToolCard] = list(tools or []) + injected_tools
    final_rails: List[AgentRail] = list(rails or []) + injected_rails

    extra_config: Dict[str, Any] = dict(config_kwargs)
    if extra_config.get("context_engine_config") is None:
        extra_config["context_engine_config"] = ContextEngineConfig(
            max_context_message_num=resolved_settings.context_max_message_num,
            default_window_round_num=resolved_settings.context_default_window_round_num,
        )

    return create_deep_agent(
        model=model,
        card=final_card,
        system_prompt=final_prompt,
        tools=final_tools,
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
        **extra_config,
    )


__all__ = [
    "build_mobile_gui_agent_config",
    "create_mobile_gui_agent",
]
