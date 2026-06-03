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
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    RuntimeSettings,
    build_browser_guardrails,
    build_playwright_mcp_config,
    build_runtime_settings,
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

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_EN = (
    "You are a browser automation agent responsible for executing web tasks directly. "
    "Plan and decide at this agent level, then use Playwright browser tools and approved runtime "
    "helper tools to navigate, click, type, select, inspect, and extract information. "
    "Keep actions targeted and avoid unnecessary page snapshots. "
    "Before broad page snapshots, full-body scans, or generic DOM scraping, choose the smallest "
    "compact probe that matches the task. "
    "Use browser_probe_interactives for buttons, links, inputs, forms, navigation controls, "
    "login controls, pagination controls, menus, and other visible interactive elements. "
    "When using browser_probe_interactives only for page-level controls, prefer max_items around "
    "20-30 unless the task explicitly requires a larger inventory. "
    "On product pages, marketplace pages, search-result pages, catalog pages, article-list pages, "
    "or any page with repeated visible cards/listings, call browser_probe_cards before broad "
    "extraction. "
    "Use browser_probe_cards to identify compact repeated structures such as product cards, result "
    "cards, book cards, article cards, listing rows, title/price/rating/review/availability fields, "
    "primary links, visible buttons, bounding boxes, selector hints, and recurring structure "
    "signatures. "
    "For product/listing/item-data tasks, prefer browser_probe_cards first; call "
    "browser_probe_interactives only if you also need page-level navigation, filters, forms, or "
    "controls outside the cards. "
    "Prefer selector_hint values from compact probes when they are relevant. "
    "Use browser_snapshot only when compact probes are insufficient, when accessibility structure "
    "is needed, or when exact element references are required by a Playwright MCP action. "
    "Use browser_run_code_unsafe or browser_run_code only when you already know the exact "
    "selector/computation, or when the compact probes and browser_snapshot are insufficient. "
    "Do not use browser_run_code_unsafe or browser_run_code to dump the entire document body unless "
    "all compact approaches fail. "
    "Use browser_custom_action only for deterministic helper actions that are awkward to express with "
    "the primitive browser tools. "
    "Do not assume a nested browser worker or browser_run_task wrapper exists. "
    "Avoid redundant actions, preserve session continuity, and only claim completion when the "
    "requested browser outcome is actually evidenced."
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN = (
    "你是浏览器自动化代理，负责直接执行网页任务。"
    "请在当前代理层面规划和决策，并使用 Playwright 浏览器工具以及已批准的运行时辅助工具"
    "完成导航、点击、输入、选择、检查和信息提取。"
    "操作应保持目标明确，避免不必要的页面快照。"
    "在使用大范围页面快照、全页面扫描或通用 DOM 抓取前，优先选择与任务匹配的最小紧凑探测工具。"
    "需要按钮、链接、输入框、表单、导航控件、登录控件、分页控件、菜单或其他可见交互元素时，"
    "优先使用 browser_probe_interactives；如果只是查找页面级控件，max_items 通常保持在 20-30 左右，"
    "除非任务明确需要更大的元素清单。"
    "在商品页、市场页、搜索结果页、目录页、文章列表页，或任何包含重复可见卡片/列表项的页面上，"
    "应先调用 browser_probe_cards，再进行大范围提取。"
    "使用 browser_probe_cards 识别商品卡片、结果卡片、图书卡片、文章卡片、列表行、"
    "标题/价格/评分/评论数/库存字段、主链接、可见按钮、边界框、selector_hint 和重复结构特征。"
    "对于商品、列表或条目数据任务，优先使用 browser_probe_cards；只有在还需要卡片外的页面级导航、"
    "筛选器、表单或控件时，再调用 browser_probe_interactives。"
    "相关时优先使用紧凑探测返回的 selector_hint。"
    "仅在紧凑探测不足、需要无障碍结构，或 Playwright MCP 操作需要精确元素引用时使用 browser_snapshot。"
    "仅在已经知道精确 selector/计算逻辑，或紧凑探测和 browser_snapshot 都不足时，"
    "使用 browser_run_code_unsafe 或 browser_run_code。"
    "除非所有紧凑方式都失败，不要使用 browser_run_code_unsafe 或 browser_run_code 转储整个 document body。"
    "browser_custom_action 只用于基础浏览器工具难以表达的确定性辅助动作。"
    "不要假设存在嵌套 browser worker 或 browser_run_task 包装器。"
    "避免重复动作，保持会话连续性；只有当网页上的具体证据证明任务已完成时，才声明完成。"
    "请如实、简洁地汇报结果。"
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
    max_iterations: int = 25,
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
    max_iterations: int = 25,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[RuntimeSettings] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create the browser subagent with direct browser MCP tools and helper tools."""
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
    final_mcps = list(mcps or [])
    final_rails = list(rails or []) + injected_rails

    return create_deep_agent(
        model=model,
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


__all__ = [
    "BROWSER_AGENT_FACTORY_NAME",
    "build_browser_agent_config",
    "create_browser_agent",
]
