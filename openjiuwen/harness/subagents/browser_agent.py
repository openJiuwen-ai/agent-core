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
DEFAULT_BROWSER_AGENT_TEMPERATURE = 0.4
DEFAULT_BROWSER_AGENT_MAX_ITERATIONS = 100
_BROWSER_MODEL_TEMPERATURE_MARKER = "_browser_agent_temperature"
_BROWSER_PARENT_MODEL_MARKER = "_browser_agent_parent_model"

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_EN = (
    "You are a browser automation agent responsible for executing web tasks directly. "
    "Plan and decide at this agent level, then use Playwright browser tools and approved runtime "
    "helper tools to navigate, click, type, select, inspect, and extract information. "
    "Keep actions targeted and avoid unnecessary page snapshots. "
    "Before acting, classify the task as a simple lookup or a complex workflow and keep a compact "
    "phase plan. For a simple lookup, prefer a direct search-results URL when the search engine and "
    "query are known, unless operating the search form is itself the requested outcome. "
    "For a complex form, group deterministic field, date, filter, and submit operations into "
    "browser_batch_interact and end the batch with wait_for_selector, wait_for_text, or "
    "wait_for_load_state. For dynamic pages, use wait_for_url, wait_for_first_card_title, "
    "wait_for_sort_state, wait_for_result_count, wait_for_dom_text_change, or wait_for_stable "
    "with a short polling interval and an explicit total timeout. Interpret requests to wait until "
    "fully loaded or pause appropriately as condition stability, not a fixed three-second sleep. "
    "Prefer observable condition waits over sleep, wait_after_ms, or fixed wait_after_each_ms delays. "
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
    "Treat page_id/generation_id/url/title/interactives/cards/field_coverage/blockers as the single "
    "compact PageState contract. Pass PageState generation_id and probe target_id, or a current AX "
    "ref, directly to browser_batch_interact; never translate refs or target IDs into guessed CSS. "
    "A selector_hint is only a compatibility locator and must remain bound to the generation that "
    "returned it; use it only when validated, clickable=true, match_count=1, visible=true, and enabled=true. "
    "When a card exposes primary_link or href, navigate directly to that URL instead of clicking "
    "its selector_hint. "
    "When several fields are needed from the same page, extract them together in one "
    "browser_batch_interact call with named extract_text/extract_value steps, or consume one "
    "browser_probe_cards result that already contains all fields. "
    "Use browser_snapshot only when compact probes are insufficient, when accessibility structure "
    "is needed, or when exact element references are required by a Playwright MCP action. "
    "When an older browser result is replaced by a <persisted-output> marker and its preview is "
    "insufficient, use browser_recall_offload with the marker handle. Recalled refs/selectors are "
    "stale after navigation and are evidence only; never use them for interaction. "
    "Use browser_evaluate for a small, exact target or computation. If an explicit advanced_code "
    "or unsafe_dev capability makes a browser_run_code tool visible, use that tool only when the "
    "compact probes, evaluate, snapshot, and deterministic actions are insufficient. Never use a "
    "run-code tool to dump the entire document body. "
    "Use browser_custom_action only for deterministic helper actions that are awkward to express with "
    "the primitive browser tools. "
    "Do not assume a nested browser worker or browser_run_task wrapper exists. "
    "Treat each navigation, form, filtering, and extraction phase as a unit. If two attempts in the "
    "same phase do not make observable progress, re-plan instead of trying more selector variants. "
    "The global turn budget is intentionally generous, but do not spend it on one-action phases or "
    "duplicate verification. One direct page value or one structured probe/evaluate result that "
    "contains every requested field is sufficient evidence. Stop immediately when all requested "
    "fields are evidenced; do not confirm the same fact with snapshot, probe, and evaluate again. "
    "Avoid redundant actions, preserve session continuity, and only claim completion when the "
    "requested browser outcome is actually evidenced."
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN = (
    "你是浏览器自动化代理，负责直接执行网页任务。"
    "请在当前代理层面规划和决策，并使用 Playwright 浏览器工具以及已批准的运行时辅助工具"
    "完成导航、点击、输入、选择、检查和信息提取。"
    "操作应保持目标明确，避免不必要的页面快照。"
    "执行前先将任务判断为简单查询或复杂流程，并维护紧凑的阶段计划。"
    "简单查询在已知搜索引擎和关键词时，优先直接构造搜索结果 URL；"
    "只有当操作搜索表单本身就是任务目标时才逐项操作搜索框。"
    "复杂表单应将确定的字段、日期、筛选和提交操作合并到 browser_batch_interact，"
    "并在 batch 末尾使用 wait_for_selector、wait_for_text 或 wait_for_load_state。"
    "优先等待可观察条件，不要使用 sleep、wait_after_ms 或固定 wait_after_each_ms。"
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
    "将 page_id/generation_id/url/title/interactives/cards/field_coverage/blockers 作为唯一的紧凑 "
    "PageState 契约。调用 browser_batch_interact 时直接传当前 PageState 的 generation_id，"
    "以及 probe 返回的 target_id 或当前 AX ref；禁止把 ref/target_id 重新拼成猜测的 CSS。"
    "selector_hint 只是兼容 locator，必须绑定返回它的 generation；仅当它已验证、clickable=true、"
    "match_count=1 且 visible/enabled 均为 true 时才可使用。"
    "卡片包含 primary_link 或 href 时，应直接导航该 URL，不要点击其 selector_hint。"
    "同一页面需要多个字段时，应在一次 browser_batch_interact 中使用带字段名的 "
    "extract_text/extract_value 统一提取，或直接使用一次已包含全部字段的 "
    "browser_probe_cards 结果。"
    "动态页面应使用 wait_for_url、wait_for_first_card_title、wait_for_sort_state、"
    "wait_for_result_count、wait_for_dom_text_change 或 wait_for_stable，并设置总 timeout。"
    "“等待完全加载”或“适当停顿”表示等待条件稳定，不表示固定 sleep 3 秒。"
    "仅在紧凑探测不足、需要无障碍结构，或 Playwright MCP 操作需要精确元素引用时使用 browser_snapshot。"
    "旧浏览器结果被替换为 <persisted-output> 且预览不足时，使用标记中的 handle 调用 "
    "browser_recall_offload。页面导航后，恢复结果里的 ref/selector 已过期，只能作为证据，禁止用于交互。"
    "小范围精确目标或计算优先使用 browser_evaluate。只有显式 advanced_code 或 unsafe_dev 能力让 "
    "browser_run_code 工具可见，且紧凑探测、evaluate、snapshot 和确定性操作都不足时，才使用该工具。"
    "禁止使用任何 run-code 工具转储整个 document body。"
    "browser_custom_action 只用于基础浏览器工具难以表达的确定性辅助动作。"
    "不要假设存在嵌套 browser worker 或 browser_run_task 包装器。"
    "将导航、表单、筛选和提取分别作为完整阶段执行。"
    "同一阶段连续两次尝试都没有可观察进展时，必须重新规划，不要继续尝试更多 selector 变体。"
    "全局轮次预算有意设置得较大，但不得把预算消耗在单动作阶段或重复验证上。"
    "一个直接页面值，或一次已包含全部所需字段的结构化 probe/evaluate 结果，就是充分证据。"
    "所需字段全部有证据后立即结束；禁止再用 snapshot、probe 和 evaluate 重复确认同一事实。"
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
    injected_tools.append(BrowserOffloadRecallTool(workspace, language=resolved_language))
    injected_rails: List[AgentRail] = [BrowserRuntimeRail(browser_backend)]

    # Window the large browser probe/snapshot results unless the caller already
    # manages context processors via their own ContextProcessorRail.
    # Browser probe/snapshot tools emit large results; keep only the most recent
    # few in context and persist older ones via ToolResultWindowProcessor.
    browser_windowed_tool_names = ["browser_probe_interactives", "browser_probe_cards", "browser_snapshot"]
    if not any(isinstance(rail, ContextProcessorRail) for rail in (rails or [])):
        injected_rails.append(
            ContextProcessorRail(
                processors=[
                    (
                        "ToolResultWindowProcessor",
                        ToolResultWindowProcessorConfig(
                            tool_names=browser_windowed_tool_names,
                            keep_last_k=1,
                            trim_size=1000,
                            min_offload_chars=4096,
                            small_result_trim_size=800,
                        ),
                    )
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
    "BROWSER_AGENT_FACTORY_NAME",
    "DEFAULT_BROWSER_AGENT_MAX_ITERATIONS",
    "DEFAULT_BROWSER_AGENT_TEMPERATURE",
    "build_browser_agent_config",
    "create_browser_agent",
]
