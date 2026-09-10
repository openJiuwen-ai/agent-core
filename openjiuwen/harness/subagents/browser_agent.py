# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for the browser subagent."""

from __future__ import annotations

import copy
import dataclasses
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ToolResultWindowProcessorConfig
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context_processor import (
    BrowserWorkingContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.offload_recall import BrowserOffloadRecallTool
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserInstanceConfig,
    RuntimeSettings,
    build_browser_guardrails,
    build_playwright_mcp_config,
    build_runtime_settings,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    DEFAULT_BROWSER_CAPABILITIES,
    resolve_browser_capabilities,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
    BrowserRuntimeRail,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    build_browser_runtime_tools,
)

try:
    from openjiuwen.harness.prompts import resolve_language
except ImportError:

    def resolve_language(language: Optional[str] = None) -> str:  # type: ignore[misc]
        return language if language in {"cn", "en"} else "cn"


if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace


BROWSER_AGENT_FACTORY_NAME = "browser_agent"
# Agent checkpoints are namespaced by AgentCard.id inside a conversation.
# Keep the default stable so reconstructing this subagent can restore its
# Session-backed working context on a same-conversation follow-up.
BROWSER_AGENT_CARD_ID = "openjiuwen.browser_agent"
DEFAULT_BROWSER_AGENT_TEMPERATURE = 0.4
DEFAULT_BROWSER_AGENT_MAX_ITERATIONS = 100
_BROWSER_MODEL_TEMPERATURE_MARKER = "_browser_agent_temperature"
_BROWSER_PARENT_MODEL_MARKER = "_browser_agent_parent_model"

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_EN = (
    "You are a browser automation agent responsible for executing web tasks directly. "
    "Choose the strategy at this agent level and use the available Playwright and runtime tools; "
    "the runtime validates targets and outcomes but does not replace your task judgment.\n"
    "Every model call includes one runtime-maintained <browser_working_context> followed by the latest "
    "<browser_state>. Requirements, evidence, blockers, status, and runtime directive are authoritative. "
    "A fresh browser capture occurs initially and after a recognized page mutation; otherwise the cached "
    "observation is reused. When the runtime directive requires replanning, change the strategy materially.\n"
    "For a simple lookup, prefer a direct search-results URL when the engine and query are known. Use "
    "browser_probe_interactives for page controls and browser_probe_cards for repeated results or products. "
    "Use the compact PageState target_id with its generation_id directly; never reconstruct guessed CSS from "
    "a target ID. When a card has primary_link or href, navigate directly to that URL.\n"
    "Use browser_batch_interact when two or more deterministic actions or same-page field extractions are already "
    "known. Use a primitive for one uncertain action. Prefer observable condition waits over fixed sleep. Use "
    "browser_snapshot only when compact probes are insufficient, and browser_evaluate only for a small exact "
    "target or computation. If an older result has a <persisted-output> marker, recall it only when its preview "
    "does not contain the needed evidence; recalled targets are not executable after navigation.\n"
    "Record requested values under the canonical requirement fields and use unknown for an inspected missing "
    "value. One trustworthy page value or structured result is enough; do not verify the same fact with multiple "
    "tools. Stop immediately when the requested outcome is evidenced. The runtime determines final status, so "
    "return a concise natural-language result rather than another progress object.\n"
    "If an optional capability makes a browser_run_code tool visible, use it only when deterministic tools are "
    "insufficient, and never dump the full document. Preserve session continuity and report a concrete blocker "
    "when the available browser state or tools cannot complete the task."
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN = (
    "你是浏览器自动化代理，负责直接完成网页任务。请在当前代理层决定策略并使用可见的 Playwright "
    "和 runtime 工具；runtime 负责验证目标和结果，但不代替你的任务判断。\n"
    "每次模型调用都会依次提供 runtime 维护的 <browser_working_context> 和最新 <browser_state>。"
    "其中的请求字段、证据、阻断项、状态和 runtime 指令是权威信息。系统仅在初始调用和已识别的"
    "页面变更后重新观察；runtime 要求重新规划时，应实质改变策略。\n"
    "已知搜索引擎和关键词时，简单查询优先直接构造搜索结果 URL。页面控件使用 "
    "browser_probe_interactives，重复结果或商品使用 browser_probe_cards。直接使用 PageState 返回的 "
    "target_id 和 generation_id，禁止把 target_id 改写成猜测的 CSS。卡片包含 primary_link 或 href "
    "时直接导航该 URL。\n"
    "只有两个及以上确定动作或同页多字段提取时使用 browser_batch_interact；单个不确定动作使用基础工具。"
    "优先等待可观察条件，不使用固定 sleep。紧凑 Probe 不足时再使用 browser_snapshot；"
    "browser_evaluate 仅用于小范围精确目标或计算。旧结果出现 <persisted-output> 且预览不足时才恢复；"
    "导航后恢复内容中的目标不可继续操作。\n"
    "按 browser_working_context 中的规范字段记录证据；字段已检查但缺失时使用 unknown。"
    "一个可信页面值或结构化结果已经足够，不要用多个工具重复验证同一事实。请求结果有证据后立即结束。"
    "最终状态由 runtime 决定，只返回简洁自然语言结果，不再维护第二份进度对象。\n"
    "只有可选能力明确暴露 browser_run_code 时才使用，并且仅限确定性工具不足的情况；禁止转储完整页面。"
    "保持浏览器会话连续；现有页面或工具确实无法完成时，报告具体 blocker。"
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN,
    "en": DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_EN,
}

DEFAULT_BROWSER_AGENT_DESCRIPTION_EN = (
    "Dedicated browser subagent that directly controls the browser with Playwright MCP tools."
)
DEFAULT_BROWSER_AGENT_DESCRIPTION_CN = "专用浏览器子代理，直接使用 Playwright MCP 工具执行网页任务。"
DEFAULT_BROWSER_AGENT_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_BROWSER_AGENT_DESCRIPTION_CN,
    "en": DEFAULT_BROWSER_AGENT_DESCRIPTION_EN,
}


def _coerce_browser_instance(
    browser_instance: Optional[BrowserInstanceConfig | Dict[str, Any]],
    browser_key: Optional[str],
) -> Optional[BrowserInstanceConfig]:
    """Normalize the per-instance browser config from a model, dict, or bare key.

    A dict form is accepted so the teams manifest can carry browser identity as
    serializable ``factory_kwargs`` across the spawn wire boundary.
    """
    if isinstance(browser_instance, BrowserInstanceConfig):
        return browser_instance
    if isinstance(browser_instance, dict):
        allowed = {f.name for f in dataclasses.fields(BrowserInstanceConfig)}
        return BrowserInstanceConfig(**{k: v for k, v in browser_instance.items() if k in allowed})
    if browser_key:
        return BrowserInstanceConfig(key=str(browser_key))
    return None


def _resolve_runtime_settings(
    model: Model,
    settings: Optional[RuntimeSettings],
    instance: Optional[BrowserInstanceConfig] = None,
) -> RuntimeSettings:
    if settings is not None:
        return settings
    if model.model_client_config is not None:
        cc = model.model_client_config
        request_model_name = ""
        if model.model_config is not None:
            request_model_name = (
                getattr(model.model_config, "model", None) or getattr(model.model_config, "model_name", None) or ""
            )
        return RuntimeSettings(
            provider=cc.client_provider,
            api_key=cc.api_key,
            api_base=cc.api_base or "",
            model_name=request_model_name,
            mcp_cfg=build_playwright_mcp_config(instance),
            guardrails=build_browser_guardrails(),
            instance=instance,
        )
    return build_runtime_settings(instance)


def _browser_model_with_temperature(model: Model, temperature: float) -> Model:
    """Copy a parent model descriptor and override sampling for browser work."""
    resolved_temperature = max(0.0, min(float(temperature), 2.0))
    if getattr(model, _BROWSER_MODEL_TEMPERATURE_MARKER, None) == resolved_temperature:
        return model
    parent_model = getattr(model, _BROWSER_PARENT_MODEL_MARKER, model)

    model_config = getattr(model, "model_config", None)
    if isinstance(model_config, ModelRequestConfig):
        browser_model_config = model_config.model_copy(
            deep=True,
            update={"temperature": resolved_temperature},
        )
    elif model_config is None:
        browser_model_config = ModelRequestConfig(temperature=resolved_temperature)
    else:
        browser_model_config = copy.copy(model_config)
        setattr(browser_model_config, "temperature", resolved_temperature)

    model_client_config = getattr(model, "model_client_config", None)
    if issubclass(type(model), Model) and model_client_config is not None:
        browser_model = Model(
            model_client_config=model_client_config,
            model_config=browser_model_config,
        )
        setattr(browser_model, _BROWSER_MODEL_TEMPERATURE_MARKER, resolved_temperature)
        setattr(browser_model, _BROWSER_PARENT_MODEL_MARKER, parent_model)
        return browser_model

    # Lightweight test doubles and compatibility model descriptors may not be
    # constructible as a concrete Model. Preserve their shape without mutating
    # the parent object.
    browser_model = copy.copy(model)
    browser_model.model_config = browser_model_config
    setattr(browser_model, _BROWSER_MODEL_TEMPERATURE_MARKER, resolved_temperature)
    setattr(browser_model, _BROWSER_PARENT_MODEL_MARKER, parent_model)
    return browser_model


def build_browser_agent_config(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = DEFAULT_BROWSER_AGENT_MAX_ITERATIONS,
    temperature: float = DEFAULT_BROWSER_AGENT_TEMPERATURE,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[RuntimeSettings] = None,
    browser_key: Optional[str] = None,
    browser_instance: Optional[BrowserInstanceConfig | Dict[str, Any]] = None,
) -> SubAgentConfig:
    """Build a SubAgentConfig that materializes as create_browser_agent()."""
    resolved_language = resolve_language(language)
    instance = _coerce_browser_instance(browser_instance, browser_key)
    browser_model = _browser_model_with_temperature(model, temperature)
    resolved_settings = _resolve_runtime_settings(browser_model, settings, instance)
    return SubAgentConfig(
        agent_card=card
        or AgentCard(
            id=BROWSER_AGENT_CARD_ID,
            name="browser_agent",
            description=DEFAULT_BROWSER_AGENT_DESCRIPTION.get(
                resolved_language,
                DEFAULT_BROWSER_AGENT_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=system_prompt
        or DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT.get(
            resolved_language,
            DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["cn"],
        ),
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=browser_model,
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
    max_iterations: int = DEFAULT_BROWSER_AGENT_MAX_ITERATIONS,
    temperature: float = DEFAULT_BROWSER_AGENT_TEMPERATURE,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[RuntimeSettings] = None,
    browser_key: Optional[str] = None,
    browser_instance: Optional[BrowserInstanceConfig | Dict[str, Any]] = None,
    browser_capabilities: Optional[List[str]] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create the browser subagent with task-scoped capability context.

    ``browser_capabilities`` is resolved against the trusted capability
    catalog here. Core is always applied, including when the caller omits the
    optional capability list.
    """
    if browser_capabilities is not None and (
        not isinstance(browser_capabilities, list)
        or not all(isinstance(capability, str) for capability in browser_capabilities)
    ):
        raise ValueError("browser_capabilities must be a list of strings")

    resolved_capabilities = resolve_browser_capabilities(browser_capabilities)
    if resolved_capabilities.rejected_names:
        rejected = ", ".join(resolved_capabilities.rejected_names)
        available = ", ".join(capability.name for capability in DEFAULT_BROWSER_CAPABILITIES)
        raise ValueError(f"Unsupported browser capabilities: {rejected}. Available capabilities: {available}")

    logger.info(
        "Resolved browser capabilities: requested=%s, selected=%s, allowed_tools=%s",
        resolved_capabilities.requested_names,
        resolved_capabilities.selected_names,
        resolved_capabilities.allowed_tool_names,
    )

    resolved_language = resolve_language(language)
    instance = _coerce_browser_instance(browser_instance, browser_key)
    browser_model = _browser_model_with_temperature(model, temperature)
    resolved_settings = _resolve_runtime_settings(browser_model, settings, instance)

    final_card = card or AgentCard(
        id=BROWSER_AGENT_CARD_ID,
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

    runtime_kwargs: Dict[str, Any] = {
        "provider": resolved_settings.provider,
        "api_key": resolved_settings.api_key,
        "api_base": resolved_settings.api_base,
        "model_name": resolved_settings.model_name,
        "mcp_cfg": resolved_settings.mcp_cfg,
        "guardrails": resolved_settings.guardrails,
        "instance": resolved_settings.instance,
        "allowed_tool_names": resolved_capabilities.allowed_tool_names,
    }
    browser_backend = BrowserAgentRuntime(**runtime_kwargs)
    injected_tools = build_browser_runtime_tools(browser_backend, language=resolved_language)
    working_context_config = BrowserWorkingContextProcessorConfig(
        language=resolved_language,
        runtime_projection_only=True,
    )
    injected_rails: List[AgentRail] = [
        BrowserRuntimeRail(browser_backend),
    ]
    injected_tools.append(BrowserOffloadRecallTool(workspace, language=resolved_language))

    browser_state_processor = (
        "BrowserStateContextProcessor",
        BrowserStateContextProcessorConfig(provider=browser_backend),
    )
    browser_working_context_processor = (
        "BrowserWorkingContextProcessor",
        working_context_config,
    )
    browser_windowed_tool_names = [
        "browser_probe_interactives",
        "browser_probe_cards",
        "browser_snapshot",
        "browser_find",
        "browser_evaluate",
    ]
    browser_tool_result_window_processor = (
        "ToolResultWindowProcessor",
        ToolResultWindowProcessorConfig(
            tool_names=browser_windowed_tool_names,
            keep_last_k=1,
            trim_size=1000,
            min_offload_chars=4096,
            small_result_trim_size=800,
        ),
    )
    caller_context_rails = [rail for rail in (rails or []) if isinstance(rail, ContextProcessorRail)]
    if caller_context_rails:
        for context_rail in caller_context_rails:
            context_rail.add_processors(
                [
                    browser_tool_result_window_processor,
                    browser_state_processor,
                    browser_working_context_processor,
                ]
            )
    else:
        injected_rails.append(
            ContextProcessorRail(
                processors=[
                    browser_tool_result_window_processor,
                    browser_state_processor,
                    browser_working_context_processor,
                ],
                preset=False,
            )
        )

    final_tools = list(tools or []) + injected_tools
    final_mcps = list(mcps or [])
    final_rails = list(rails or []) + injected_rails

    agent = create_deep_agent(
        model=browser_model,
        card=final_card,
        system_prompt=final_prompt,
        tools=final_tools,
        mcps=final_mcps,
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
    agent.register_task_resource_cleanup(
        browser_backend.release_task_resources,
        prepare=browser_backend.acquire_task_resources,
    )
    return agent


__all__ = [
    "BROWSER_AGENT_CARD_ID",
    "BROWSER_AGENT_FACTORY_NAME",
    "DEFAULT_BROWSER_AGENT_MAX_ITERATIONS",
    "DEFAULT_BROWSER_AGENT_TEMPERATURE",
    "build_browser_agent_config",
    "create_browser_agent",
]
