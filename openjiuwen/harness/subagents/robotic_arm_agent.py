# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for the robotic-arm (photo -> plan -> automatic execution) subagent.

Use ``factory_name="robotic_arm_agent"`` on :class:`SubAgentConfig` so
:class:`DeepAgent` dispatches to :func:`create_robotic_arm_agent`.
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
from openjiuwen.harness.tools.robotic_arm.arm_task_prompt import build_robotic_arm_system_prompt
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings
from openjiuwen.harness.tools.robotic_arm.plan_tools import build_plan_tools
from openjiuwen.harness.tools.robotic_arm.rails_factory import build_robotic_arm_rails

if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace

try:
    from openjiuwen.harness.prompts import resolve_language
except ImportError:

    def resolve_language(language: Optional[str] = None) -> str:  # type: ignore[misc]
        return language if language in {"cn", "en"} else "cn"

DEFAULT_ROBOTIC_ARM_DESCRIPTION_EN = (
    "Dedicated robotic-arm subagent: decomposes a manipulation goal into sub-tasks and grounds each "
    "step to a 2D point on a photo. The model only reports the plan; CV analysis, coordinate "
    "transform, trajectory computation, and the physical action run automatically outside the model."
)
DEFAULT_ROBOTIC_ARM_DESCRIPTION_CN = (
    "专用机械臂子代理：把操作目标拆解为子任务，并把每一步定位到照片上的 2D 坐标。"
    "模型只负责上报计划；CV 分析、坐标变换、轨迹计算和实际动作都在模型之外自动执行。"
)

DEFAULT_ROBOTIC_ARM_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_ROBOTIC_ARM_DESCRIPTION_CN,
    "en": DEFAULT_ROBOTIC_ARM_DESCRIPTION_EN,
}


def _resolve_runtime_settings(settings: Optional[RoboticArmRuntimeSettings]) -> RoboticArmRuntimeSettings:
    if settings is not None:
        return settings
    return RoboticArmRuntimeSettings.from_env()


def build_robotic_arm_agent_config(
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
    settings: Optional[RoboticArmRuntimeSettings] = None,
) -> SubAgentConfig:
    """Build a SubAgentConfig that materializes as :func:`create_robotic_arm_agent`.

    ``settings`` (see :class:`RoboticArmRuntimeSettings`) is where callers supply the
    ``step_executor`` pipeline a specific rig needs -- there is no generic default
    hardware to fall back to, unlike ``mobile_gui_agent``'s ``uiautomator2`` device.
    """
    resolved_language = resolve_language(language)
    resolved_settings = _resolve_runtime_settings(settings)
    default_prompt = build_robotic_arm_system_prompt(resolved_settings)
    return SubAgentConfig(
        agent_card=card
        or AgentCard(
            name="robotic_arm_agent",
            description=DEFAULT_ROBOTIC_ARM_DESCRIPTION.get(resolved_language, DEFAULT_ROBOTIC_ARM_DESCRIPTION["cn"]),
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
        factory_name="robotic_arm_agent",
        factory_kwargs={"settings": resolved_settings},
    )


def create_robotic_arm_agent(
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
    settings: Optional[RoboticArmRuntimeSettings] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create a DeepAgent wired with robotic-arm perception rails and the report_plan tool.

    Args:
        model: Pre-constructed Model instance (must support image inputs).
        settings: The ``step_executor`` pipeline -- see :class:`RoboticArmRuntimeSettings`.
            Required field (``step_executor`` or ``step_executor_model``) has no
            default and must be supplied by the caller for this specific rig.
        **config_kwargs: Extra fields forwarded to DeepAgentConfig.

    Returns:
        Configured DeepAgent instance ready for invoke()/stream().
    """
    resolved_language = resolve_language(language)
    resolved_settings = _resolve_runtime_settings(settings)

    final_card = card or AgentCard(
        name="robotic_arm_agent",
        description=DEFAULT_ROBOTIC_ARM_DESCRIPTION.get(resolved_language, DEFAULT_ROBOTIC_ARM_DESCRIPTION["cn"]),
    )
    default_prompt = build_robotic_arm_system_prompt(resolved_settings)
    final_prompt = system_prompt or default_prompt

    injected_tools = build_plan_tools()
    injected_rails = build_robotic_arm_rails(resolved_settings, model=model)
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
    "build_robotic_arm_agent_config",
    "create_robotic_arm_agent",
]
