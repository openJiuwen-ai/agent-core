# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for the browser subagent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.factory import create_deep_agent
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail
from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.config import (
    RuntimeSettings,
    build_browser_guardrails,
    build_playwright_mcp_config,
    build_runtime_settings,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
    BrowserRuntimeRail,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.runtime_tools import (
    build_browser_runtime_tools,
)

try:
    from openjiuwen.deepagents.prompts import resolve_language
except ImportError:
    def resolve_language(language: Optional[str] = None) -> str:  # type: ignore[misc]
        return language if language in {"cn", "en"} else "cn"

if TYPE_CHECKING:
    from openjiuwen.deepagents.workspace.workspace import Workspace


BROWSER_AGENT_FACTORY_NAME = "browser_agent"

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_EN = (
    "You are a browser automation agent responsible for executing web tasks. "
    "Use browser tools to navigate, interact with, and extract information from websites. "
    "Prefer one comprehensive browser task call per user request. "
    "Report results factually and concisely."
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN = (
    "你是浏览器自动化代理，负责执行网页任务。"
    "请使用浏览器工具完成导航、交互和信息提取。"
    "每次请求优先发起一次完整的浏览器任务调用。"
    "请如实、简洁地汇报结果。"
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN,
    "en": DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_EN,
}

DEFAULT_BROWSER_AGENT_DESCRIPTION_EN = (
    "Dedicated browser subagent that executes web tasks with Playwright runtime tools."
)
DEFAULT_BROWSER_AGENT_DESCRIPTION_CN = "专用浏览器子代理，使用 Playwright runtime 工具执行网页任务。"
DEFAULT_BROWSER_AGENT_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_BROWSER_AGENT_DESCRIPTION_CN,
    "en": DEFAULT_BROWSER_AGENT_DESCRIPTION_EN,
}


def _resolve_runtime_settings(
    model: Model,
    settings: Optional[RuntimeSettings],
) -> RuntimeSettings:
    if settings is not None:
        return settings
    if model.model_client_config is not None:
        cc = model.model_client_config
        request_model_name = ""
        if model.model_config is not None:
            request_model_name = (
                getattr(model.model_config, "model", None)
                or getattr(model.model_config, "model_name", None)
                or ""
            )
        return RuntimeSettings(
            provider=cc.client_provider,
            api_key=cc.api_key,
            api_base=cc.api_base or "",
            model_name=request_model_name,
            mcp_cfg=build_playwright_mcp_config(),
            guardrails=build_browser_guardrails(),
        )
    return build_runtime_settings()


def build_browser_agent_config(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[RuntimeSettings] = None,
) -> SubAgentConfig:
    """Build a SubAgentConfig that materializes as create_browser_agent()."""
    resolved_language = resolve_language(language)
    resolved_settings = _resolve_runtime_settings(model, settings)
    return SubAgentConfig(
        agent_card=card or AgentCard(
            name="browser_agent",
            description=DEFAULT_BROWSER_AGENT_DESCRIPTION.get(
                resolved_language,
                DEFAULT_BROWSER_AGENT_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=system_prompt or DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT.get(
            resolved_language,
            DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["cn"],
        ),
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
        factory_name=BROWSER_AGENT_FACTORY_NAME,
        factory_kwargs={"settings": resolved_settings},
    )


def create_browser_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[RuntimeSettings] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create the browser subagent itself with runtime tools registered directly."""
    resolved_language = resolve_language(language)
    resolved_settings = _resolve_runtime_settings(model, settings)

    final_card = card or AgentCard(
        name="browser_agent",
        description=DEFAULT_BROWSER_AGENT_DESCRIPTION.get(
            resolved_language,
            DEFAULT_BROWSER_AGENT_DESCRIPTION["cn"],
        ),
    )
    final_prompt = system_prompt or DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT.get(
        resolved_language,
        DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["cn"],
    )

    browser_backend = BrowserAgentRuntime(
        provider=resolved_settings.provider,
        api_key=resolved_settings.api_key,
        api_base=resolved_settings.api_base,
        model_name=resolved_settings.model_name,
        mcp_cfg=resolved_settings.mcp_cfg,
        guardrails=resolved_settings.guardrails,
    )
    injected_tools = build_browser_runtime_tools(browser_backend, language=resolved_language)
    injected_rails = [BrowserRuntimeRail(browser_backend)]
    final_tools = list(tools or []) + injected_tools
    final_rails = list(rails or [FileSystemRail()]) + injected_rails

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
        **config_kwargs,
    )


__all__ = [
    "BROWSER_AGENT_FACTORY_NAME",
    "build_browser_agent_config",
    "create_browser_agent",
]
