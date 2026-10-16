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
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    DEFAULT_BROWSER_CAPABILITIES,
    resolve_browser_capabilities,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context_processor import (
    BrowserWorkingContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserInstanceConfig,
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
    "the runtime validates execution targets and records outcomes; business judgment belongs to you.\n"
    "Every model call includes one runtime-maintained <browser_working_context> followed by the latest "
    "<browser_state>. Execution receipts describe dispatch and ACK; judge business completion from the original "
    "goal and source observations. Optional phase conditions and counters do not add user requirements. "
    "A fresh browser capture occurs initially and after a recognized page mutation; otherwise the cached "
    "observation is reused. When the runtime directive requires replanning, change the strategy materially.\n"
    "browser_phase is optional: use set with objective when a local intent or observed field binding helps "
    "handover; ordinary actions need no phase configuration. For a complex goal, prepare only the current "
    "executable fragment once: a short objective, resolved values bound to observed objects, and known completion "
    "conditions. Refresh it when the intent or binding changes, not before every action. "
    "Keep the current page/object when handing back to Jev. An acknowledged action is not business success, "
    "but lack of visible feedback alone is not a reason to click it again: inspect or change the binding first. "
    "Prefer browser_page_action read_text/find for fixed text reads; the same observations support both models. "
    "Conditions are optional reader aids. Use verify only when the next decision needs a fresh observation. "
    "Do not toggle completed sorts. "
    "Use the original goal and source observations to judge completion; adapter gaps alone do not require more "
    "reads. Matching a search query does not prove sort order; product and shop ratings remain distinct. "
    "Cart readers are optional helpers, never a prerequisite to click or finish. Observe only what the actual "
    "user goal needs. An uncertain business effect must not be blindly replayed through another tool. "
    "Fixed reads and other known local UI actions remain available. Report genuinely unresolved work as partial.\n"
    "Preserve acknowledged steps and observed Added feedback in partial answers, without claiming verified SKU deltas. "
    "Distinguish source observations, execution limits and hypotheses. Empty blocker lists are not evidence "
    "that no obstacle exists. Do not infer a failure cause from coincident page text "
    "or contradict source restrictions.\n"
    "For a simple lookup, prefer a direct search-results URL when the engine and query are known. Use "
    "browser_probe_interactives for page controls and browser_probe_cards for repeated results or products. "
    "Use the compact PageState target_id with its generation_id directly; never reconstruct guessed CSS from "
    "a target ID. Navigate genuine detail hrefs directly; fragment links and state controls still need clicks. "
    "A targeted Probe returns query matches, not the global control index. Native snapshot/find remain valid "
    "fallbacks when Probe misses a control; snapshots are returned inline, with task-scoped recall for large output.\n"
    "Use browser_batch_interact when two or more deterministic actions or same-page field extractions are already "
    "known. Use a native primitive for a single action. Prefer wait_for_selector with an observed target_id/ref, "
    "wait_for_text with text, or wait_for_url with an observed URL condition. URL waits also inspect this action's "
    "popups. Selector-dependent waits require a target; never invent one. If the expected change is unknown, "
    "inspect the current page and tabs instead of guessing a long wait condition. "
    "Never replay a successful click to fix a later wait. Use "
    "observable condition waits instead of fixed sleeps. Use "
    "browser_snapshot only when compact probes are insufficient, and browser_evaluate only for a small exact "
    "target or computation. If an older result has a <persisted-output> marker, recall it only when its preview "
    "does not contain the needed evidence; recalled targets are not executable after navigation.\n"
    "Read source text directly; structured extraction is optional unless the caller requests a fixed output shape. "
    "Keep the source and object of each observation; do not reread merely to populate field slots. "
    "For answer lookups, AI/knowledge/weather panels are usable with source and any displayed date; no traditional "
    "widget or independent cross-check is required. They do not count as natural search results or replace "
    "a requested detail visit. Do not add reverse conversions, dates/weekdays, or verification not requested. "
    "ad_status=unknown means no positive ad marker was detected, not proof that a result is non-advertising. "
    "Keep video current_part versus collection duration and hotel stars versus guest ratings distinct. "
    "When extracting duration, include scope when the page identifies it; "
    "do not infer part duration from a collection. "
    "Report genuinely unavailable values as unknown. One trustworthy page value or structured result is enough; "
    "do not verify the same fact with multiple "
    "tools. Decide completion against the user's actual goal using this task's observations, not slot coverage. "
    "Return a concise answer that distinguishes confirmed observations from uncertain interpretations. "
    "Runtime preserves execution "
    "failures, source identity, cancellation and deadlines. If genuinely unfinished, use the existing "
    '<browser_progress>{"status":"partial","next_action":"remaining user goal"}</browser_progress> '
    "text once; it is not a tool, and must not repeat fields or evidence.\n"
    "If an optional capability makes a browser_run_code tool visible, use it only when deterministic tools are "
    "insufficient, and never dump the full document. Preserve session continuity and report a concrete blocker "
    "when the available browser state or tools cannot complete the task."
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN = (
    "你是浏览器自动化代理，负责直接完成网页任务。请在当前代理层决定策略并使用可见的 Playwright "
    "和 runtime 工具；runtime 校验执行目标并记录回执，业务完成由你判断。\n"
    "每次模型调用都会依次提供 runtime 维护的 <browser_working_context> 和最新 <browser_state>。"
    "执行回执说明派发与 ACK；业务完成应结合原始目标和来源观察判断。可选阶段条件和计数不增加用户要求。"
    "系统仅在初始调用和已识别的"
    "页面变更后重新观察；runtime 要求重新规划时，应实质改变策略。\n"
    "browser_phase 是可选入口：需要交接局部目标或已观察字段值时，用 set 提供 objective 即可；"
    "复杂目标只为当前可执行片段一次准备简短 objective、已解析值及观察对象绑定、已知完成条件；"
    "交回 Jev 时保留当前页面和对象。工具 ACK 不等于业务成功，但缺少反馈也不等于可以再次点击；先读取诊断或调整绑定。"
    "意图或绑定变化时再更新，不要每个动作前重设。固定文本读取优先 browser_page_action 的 read_text/find，"
    "结果供两种模型共享。"
    "普通动作不需要阶段配置；条件只是可选读取辅助，下一步确需新观察时才主动 verify。"
    "不切回已完成的排序，部分 Batch 只继续未完成步骤。"
    "按原始目标与实际观察判断完成，不为填满适配器字段而重复读取。查询词匹配不证明排序，"
    "商品评分和店铺评分不能混用。购物车 reader 是可选辅助，不是点击或收尾前置条件；"
    "只核对用户目标真正需要的事实。真实未知业务效果禁止换工具盲重放，固定读取及其他已知局部 UI "
    "仍可继续。确有未完成工作时报告 partial。\n"
    "partial 答复仍保留已确认步骤和 Added 等已观察反馈，但反馈不等于 SKU 增量通过。"
    "区分页面观察、执行限制和原因推测。阻断列表为空不代表页面没有障碍；不要把同时出现的页面文字当作失败原因，"
    "也不要给出与来源限制冲突的建议。\n"
    "已知搜索引擎和关键词时，简单查询优先直接构造搜索结果 URL。页面控件使用 "
    "browser_probe_interactives，重复结果或商品使用 browser_probe_cards。直接使用 PageState 返回的 "
    "target_id 和 generation_id，禁止把 target_id 改写成猜测的 CSS。卡片包含 primary_link 或 href "
    "时优先导航真实详情 URL；片段链接和状态控件仍用点击。定向 Probe 仅返回本次匹配，不是全页索引。"
    "Probe 漏掉控件时用原生 snapshot/find 回退；快照内联返回，大结果可在任务内按需恢复。\n"
    "只有两个及以上确定动作或同页多字段提取时使用 browser_batch_interact；单个动作使用原生基础工具。"
    "常用等待优先 wait_for_selector 加已观察 target_id/ref、wait_for_text 加 text，或 wait_for_url 加已知 URL 条件。"
    "URL 等待也检查本次动作产生的 popup。依赖 selector 的等待必须带目标，不能猜测；"
    "不清楚预期变化时读取当前页面和标签，而不是猜条件长等。后续等待失败不要重放成功的点击。"
    "优先等待可观察条件，不使用固定 sleep。紧凑 Probe 不足时再使用 browser_snapshot；"
    "browser_evaluate 仅用于小范围精确目标或计算。旧结果出现 <persisted-output> 且预览不足时才恢复；"
    "导航后恢复内容中的目标不可继续操作。\n"
    "可直接使用页面原文；只有调用方要求固定输出格式时才必须结构化提取。保留每份观察的来源和对象，"
    "不要为填字段槽而重读；确实不可获取的值如实说明未知。"
    "普通问答可直接读 AI/知识/天气答案卡并注明来源及页面已显示的日期，不强求传统组件；AI 卡不能充当自然结果序号"
    "或代替用户要求的详情访问。不自行增加换向汇率、日期星期或独立交叉验证。"
    "ad_status=unknown 表示未识别到明确广告标记，不是非广告证明。"
    "区分分集与合集时长、酒店星级与住客评分；时长有明确页面依据时标注 scope=current_part/collection，"
    "不能用合集总时长代替当前分集。"
    "一个可信页面值或结构化结果已经足够，不要用多个工具重复验证同一事实。请求结果有证据后立即结束。"
    "根据原始用户目标和本任务观察判断是否完成，不以推断 slot 是否填满为准。不要仅为填字段重复操作。"
    "runtime 保留真实执行失败、来源归属、取消和期限约束。若确有未完成目标，可在最终回答使用现有 "
    '<browser_progress>{"status":"partial","next_action":"未完成的用户目标"}</browser_progress> '
    "文本标签，不是工具调用，也不重写字段、证据或逐轮进度。\n"
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
    if model_client_config is not None:
        model_client_config = copy.deepcopy(model_client_config)
        configured = getattr(model_client_config, "stream_first_chunk_timeout", None)
        first_chunk_limit = min(90.0, configured) if isinstance(configured, (int, float)) and configured > 0 else 90.0
        model_client_config.stream_first_chunk_timeout = first_chunk_limit
    if issubclass(type(model), Model) and model_client_config is not None:
        browser_model = Model(
            model_client_config=model_client_config,
            model_config=browser_model_config,
        )
        setattr(browser_model, _BROWSER_MODEL_TEMPERATURE_MARKER, resolved_temperature)
        setattr(browser_model, _BROWSER_PARENT_MODEL_MARKER, parent_model)
        setattr(browser_model, "_browser_first_chunk_limit", first_chunk_limit)
        return browser_model

    # Lightweight test doubles and compatibility model descriptors may not be
    # constructible as a concrete Model. Preserve their shape without mutating
    # the parent object.
    browser_model = copy.copy(model)
    browser_model.model_config = browser_model_config
    if model_client_config is not None:
        browser_model.model_client_config = model_client_config
        setattr(browser_model, "_browser_first_chunk_limit", first_chunk_limit)
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
    browser_backend.llm_model = browser_model
    from openjiuwen.harness.tools.browser_move.decision.policy_model import BrowserPolicyModel

    decision_policy = BrowserPolicyModel(browser_model, resolved_settings.decision, browser_backend)
    browser_model = decision_policy
    browser_backend.decision_policy = decision_policy
    injected_tools = build_browser_runtime_tools(browser_backend, language=resolved_language)
    working_context_config = BrowserWorkingContextProcessorConfig(
        language=resolved_language,
        runtime_projection_only=True,
    )
    recall_tool = BrowserOffloadRecallTool(workspace, language=resolved_language)
    injected_rails: List[AgentRail] = [BrowserRuntimeRail(browser_backend, recall_tool=recall_tool)]
    injected_tools.append(recall_tool)

    browser_state_processor = (
        "BrowserStateContextProcessor",
        BrowserStateContextProcessorConfig(provider=browser_backend, decision_policy=decision_policy),
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
            keep_last_k=2,
            trim_size=1000,
            min_offload_chars=1000,
            small_result_trim_size=1000,
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

    config_kwargs["parallel_tool_calls"] = False
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
    if decision_policy is not None:
        agent.register_task_resource_cleanup(decision_policy.release_task_resources)
    return agent


__all__ = [
    "BROWSER_AGENT_CARD_ID",
    "BROWSER_AGENT_FACTORY_NAME",
    "DEFAULT_BROWSER_AGENT_MAX_ITERATIONS",
    "DEFAULT_BROWSER_AGENT_TEMPERATURE",
    "build_browser_agent_config",
    "create_browser_agent",
]
