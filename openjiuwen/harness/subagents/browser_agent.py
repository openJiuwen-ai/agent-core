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
DEFAULT_BROWSER_AGENT_MAX_OUTPUT_TOKENS = 8192
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
    "constraints. Refresh it when the intent or binding changes, not before every action. "
    "Set always includes op=set and objective, with no phase_version; that version belongs to verify. "
    "Bind only current phase_bindings controls; navigation-only cards remain source objects/URLs, not control IDs. "
    "If phase.handoff is present, the old bound fragment is historical: use the current interpretation "
    "turn to update its short objective and remaining operation/object bindings, not the old form. "
    "After restoring a selected page, keep the remaining work on that object; do not alternate navigation "
    "or scrolling merely to obtain another sample. Reuse source text or request a specific missing range. "
    "Optional read_queries (at most six resolved literals) lets Jev find text/controls such as "
    "[\"Guest rating Great\",\"Breakfast included\"]; this is not a selector or a fill-value binding. "
    "When you already know the wording of a missing control or text range, pass that literal with the "
    "same local phase update; do not leave it only in a long objective. On missing_identity, use the "
    "returned recovery_probe and bind the newly observed unique control, not a same-name guess. "
    "On an action-head uncertain_choice during complex recovery, use that existing LLM turn to prepare "
    "a remaining local objective with browser_phase set when helpful; do not repeat the full task or already "
    "completed navigation/search. Conditions are not required. Bound values name observed fields. "
    "Opening a candidate to inspect it is not selecting it for mutation. Before browsing/comparing candidates, "
    "set the current inspection objective once with allowed_operations=[read_text,find,snapshot,scroll,navigate]; "
    "leave target_ids empty until a current control is selected. The LLM may use ordinary tools to inspect options. "
    "After interpreting the requirements and selecting the object/variant, update the same phase to its observed "
    "operation control and required operation (e.g. click). Do not hand off a broad purchase goal between "
    "candidate navigation and inspection. Preserve > versus >=, budgets and the user's stopping point. "
    "In normal interpretation/recovery too, once you select an object, reuse browser_phase target_ids and "
    "allowed_operations for the remaining local work. If only reading remains, set allowed_operations to "
    "[read_text,find,extract_text,extract_value,snapshot] instead of leaving unrelated clicks as candidates. "
    "Update only when intent changes; "
    "do not add a phase call to each simple step. If source observations already answer the goal, answer directly. "
    "Bind the observed operation control, not just a matching object-title link: remove/edit/confirm needs its "
    "actual control. If missing, inspect nearby controls or use a native snapshot and an ordinary LLM action; "
    "do not leave all mutations available under a broad objective. "
    "After DOM rerendering, use fresh read bindings; a failed extract never asks you to repeat an ACKed fill. "
    "Keep the current page/object when handing back to Jev. An acknowledged action is not business success, "
    "but lack of visible feedback alone is not a reason to click it again: inspect or change the binding first. "
    "Reuse retained source text/cards even when their old bindings are stale; do not repeat reads just to "
    "refresh IDs. If freshness or coverage is insufficient, directly call a fixed reader or select a different "
    "range/object. No explanation schema is needed. Use browser_page_action read_text/find for page text, "
    "batch extract_text/extract_value for a current target, and viewport_only=true to read cards after scrolling. "
    "A capped full-page card probe is not the next viewport or the entire result list. "
    "Conditions are optional reader aids. Use verify only when the next decision needs a fresh observation. "
    "Do not toggle completed sorts. "
    "Use the original goal and source observations to judge completion; adapter gaps alone do not require more "
    "reads. Matching a search query does not prove sort order; product and shop ratings remain distinct. "
    "Cart readers are optional helpers, never a prerequisite to click or finish. Observe only what the actual "
    "user goal needs. An uncertain business effect must not be blindly replayed through another tool. "
    "Fixed reads and other known local UI actions remain available. Report genuinely unresolved work as partial.\n"
    "Preserve acknowledged steps and observed Added feedback in partial answers, without claiming verified SKU deltas. "
    "Treat parent search/source suggestions as preferences unless they are explicit user requirements. "
    "Prefer an available direct answer or inline widget; if absent or insufficient, inspect another suitable "
    "source while respecting the user's site constraints. Do not repeat a search to satisfy an invented widget rule. "
    "Preserve the chronology and scope of observations; a later page cannot rewrite earlier facts. "
    "An unexplained difference remains unknown, and a limited search cannot prove no suitable object exists. "
    "Distinguish source observations, execution limits and hypotheses. Empty blocker lists are not evidence "
    "that no obstacle exists. Do not infer a failure cause from coincident page text "
    "or contradict source restrictions.\n"
    "For a simple lookup, prefer a direct search-results URL when the engine and query are known. Use "
    "browser_probe_interactives for page controls and browser_probe_cards for repeated results or products. "
    "Use the compact PageState target_id with its generation_id directly; never reconstruct guessed CSS from "
    "a target ID. Navigate genuine detail hrefs directly; fragment links and state controls still need clicks. "
    "A targeted Probe returns query matches, not the global control index. Native snapshot/find remain valid "
    "fallbacks when Probe misses a control; snapshots are returned inline, with task-scoped recall for large output.\n"
    "If repeated navigation reaches the same obstacle, inspect retained facts and change recovery strategy; "
    "an ACK or redirect does not show progress. Do not reopen the same URL without a changed basis. Use an "
    "observed alternative, authorized human handoff or report the blocker; never bypass a challenge.\n"
    "Use browser_batch_interact for known deterministic steps or target-bound field extraction. "
    "wait/snapshot are browser_page_action operations, not batch steps; use generation_id with "
    "op=snapshot or op=wait,ms=500. First consume the action's returned URL/title, associated new tabs "
    "and selection state; if the result is already observed, continue without a wait. "
    "Prefer wait_for_selector with an observed target_id/ref, "
    "wait_for_text with text, or wait_for_url with an observed URL condition. URL waits also inspect this action's "
    "popups. Selector-dependent waits require a target; never invent one. If the expected change is unknown, "
    "inspect the current page and tabs instead of guessing a long wait condition. "
    "Do not invent URL parameters for same-page searches or assume a popup changes the old page URL. "
    "Never replay a successful click to fix a later wait. For an unknown pending change use one short "
    "page-action wait/read, not a guessed long condition. Use "
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
    "text once; it is not a tool, and must not repeat fields or evidence. "
    "Completion means reaching the user's authorized endpoint. If the user asked to stop before an external "
    "step they will perform, reaching that point is completed, not partial with that user step as next_action. "
    'Use <browser_progress>{"status":"completed","next_action":""}</browser_progress> once when complete; '
    "the prose and status must agree. Preserve all user constraints without substituting a different metric.\n"
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
    "恢复到已选页面后，剩余工作围绕该对象展开；不要为再次采样往返跳页或上下滚动，优先复用原文或指定缺失范围。"
    "set 必须带 op=set 和 objective，不带仅 verify 接受的 phase_version。target_ids 只用当前 phase_bindings "
    "控件；导航型卡片保留为来源对象/URL，不冒充当前控件绑定。"
    "phase.handoff 表示旧局部绑定已退出当前范围；在当前解释轮次更新简短 objective 和当前操作/对象绑定，"
    "不要沿用上一页表单。可选 read_queries 最多六个已解析文本词（如 Guest rating Great），供 Jev 定向 "
    "find/probe 控件；不是 selector、脚本或字段填写值。"
    "已经解析出缺失控件或文本的文案时，在同一次局部更新里带上 read_queries，不只放在长 objective 中。"
    "收到 missing_identity 时按 recovery_probe 重新观察并选择当前唯一控件，不按同名猜测重绑。"
    "打开候选进行检查不等于已选定对象执行写入。开始候选浏览/比较时，一次设置当前检查 objective，"
    "allowed_operations=[read_text,find,snapshot,scroll,navigate]，未选当前控件时 target_ids 留空；"
    "必要选项检查由 LLM 使用普通工具完成。LLM 解释约束并选定对象/规格后，再更新同一 phase，"
    "绑定实际操作控件和所需操作（如 click），不要在候选导航与检查之间仍交接整段购买目标。"
    "严格保留 > 与 >=、预算和用户停止点的原义。"
    "正常解释/恢复时也应交接已选对象：复用 target_ids 和 allowed_operations 限定剩余局部工作；"
    "只剩读取时可设 allowed_operations=[read_text,find,snapshot]，避免把无关点击继续作为候选。"
    "仅在意图或绑定改变时更新，不为每个简单步骤加 phase；来源观察足够回答时直接回答。"
    "target_ids 应绑定当前操作控件，而不只是同名对象链接；删除、编辑、确认需要各自的真实控件。"
    "缺控件时先读附近控件或原生 snapshot，再用普通 LLM 工具恢复，不要只给宽泛 objective 并放开全部修改操作。"
    "动作引发 DOM 重渲染后使用新观察的读取绑定，extract 失败不要求重做已 ACK 的填写。"
    "复杂恢复中遇到 action head 的 uncertain_choice，可在当前 LLM 轮次用 browser_phase set 交接剩余局部目标；"
    "不要重复整段任务或已完成的导航搜索，conditions 不必提供，bound_values 必须对应已观察字段。"
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
    "父委托中的查找路径和来源偏好若不是用户硬性要求，就按建议处理。可优先现成答案或内置卡片；"
    "缺失或不足时，在用户站点约束内查看其他合适来源，不为自行增加的组件要求反复搜索。"
    "保留观察的时间和范围，后来的页面不能改写先前事实；差异原因不明就说明未知，有限搜索不能证明对象不存在。"
    "区分页面观察、执行限制和原因推测。阻断列表为空不代表页面没有障碍；不要把同时出现的页面文字当作失败原因，"
    "也不要给出与来源限制冲突的建议。\n"
    "已知搜索引擎和关键词时，简单查询优先直接构造搜索结果 URL。页面控件使用 "
    "browser_probe_interactives，重复结果或商品使用 browser_probe_cards。直接使用 PageState 返回的 "
    "target_id 和 generation_id，禁止把 target_id 改写成猜测的 CSS。卡片包含 primary_link 或 href "
    "时优先导航真实详情 URL；片段链接和状态控件仍用点击。定向 Probe 仅返回本次匹配，不是全页索引。"
    "Probe 漏掉控件时用原生 snapshot/find 回退；快照内联返回，大结果可在任务内按需恢复。\n"
    "连续导航仍回到同一障碍时，使用已有观察调整恢复策略；ACK 或重定向不代表任务进展。"
    "依据未变时不要重开同一 URL，可选择已观察的其他路径、请求获授权的人工处理或如实报告阻挡，不绕过验证。\n"
    "只有两个及以上确定动作或同页多字段提取时使用 browser_batch_interact；单个动作使用原生基础工具。"
    "wait/snapshot 属于 browser_page_action，不可放入 batch steps；使用 generation_id 加 op=snapshot，"
    "或 op=wait,ms=500。先使用动作返回的 URL、标题、新标签和选择态；预期结果已经出现时直接继续，不另加等待。"
    "常用等待优先 wait_for_selector 加已观察 target_id/ref、wait_for_text 加 text，或 wait_for_url 加已知 URL 条件。"
    "URL 等待也检查本次动作产生的 popup。依赖 selector 的等待必须带目标，不能猜测；"
    "不清楚预期变化时读取当前页面和标签，而不是猜条件长等。后续等待失败不要重放成功的点击。"
    "同页搜索不猜 URL 参数，新标签也不要求旧页 URL 改变；未知的待更新状态可做一次短 wait/read，"
    "不猜条件长等。紧凑 Probe 不足时再使用 browser_snapshot；"
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
    "文本标签，不是工具调用，也不重写字段、证据或逐轮进度。完成指到达用户授权的指定终点；"
    "用户明确留给自己处理的后续步骤，不是代理未完成的 next_action。到达该终点时一次输出 "
    '<browser_progress>{"status":"completed","next_action":""}</browser_progress>，正文与状态保持一致。'
    "保留用户每项约束，不擅自换成平均值等另一口径。\n"
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

    # Use the existing provider request budget, never a second runtime timer.
    # Explicit caller/provider limits take precedence, including their native aliases.
    output_limits = [getattr(browser_model_config, key, None)
                     for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")]
    if all(value is None for value in output_limits):
        reasoning = getattr(browser_model_config, "reasoning", None)
        budget = (reasoning.get("budget_tokens", reasoning.get("max_tokens")) if isinstance(reasoning, dict)
                  else getattr(reasoning, "budget_tokens", None))
        requested = budget + 1024 if type(budget) is int and budget > 0 else 0
        browser_model_config.max_tokens = max(DEFAULT_BROWSER_AGENT_MAX_OUTPUT_TOKENS, requested)

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
