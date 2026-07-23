# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent builders for runtime and browser worker."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from typing import Any, Awaitable, Callable, Iterable, Optional

import anyio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.context_engine import DialogueCompressorConfig
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.browser_move.playwright_runtime.mcp_usage_limiter import (
    BrowserMcpUsageLimiter as _BrowserMcpUsageLimiter,
    normalized_browser_tool_name as _normalized_browser_tool_name,
    tool_calls_list as _tool_calls_list,
)

ToolResultObserver = Callable[[str, Any], Awaitable[None]]


def _build_dialogue_compressor_config(
    provider: str,
    api_key: str,
    api_base: str,
    model_name: str,
) -> Optional[DialogueCompressorConfig]:
    compressor_model = (model_name or "").strip()
    if not compressor_model:
        logger.warning("DialogueCompressor disabled: model_name is empty.")
        return None
    return DialogueCompressorConfig(
        messages_to_keep=10,
        keep_last_round=True,
        model_client=ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
            verify_ssl=False,
        ),
        # Use alias field `model` for compatibility with strict alias parsing.
        model=ModelRequestConfig(model=compressor_model),
    )


def _resolve_tool_timeout_s(default_s: float = 180.0) -> float:
    raw = (
        os.getenv("PLAYWRIGHT_TOOL_TIMEOUT_S")
        or os.getenv("PLAYWRIGHT_MCP_TIMEOUT_S")
        or os.getenv("BROWSER_TIMEOUT_S")
        or str(default_s)
    )
    try:
        parsed = float(raw)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return default_s


def _resolve_sampling_value(
    keys: tuple[str, ...],
    *,
    default: float,
    min_value: float,
    max_value: float,
) -> float:
    for key in keys:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if min_value <= value <= max_value:
            return value
    return default


def _resolve_sampling_params(
    *,
    temperature_keys: tuple[str, ...],
    top_p_keys: tuple[str, ...],
    default_temperature: float,
    default_top_p: float,
) -> tuple[float, float]:
    temperature = _resolve_sampling_value(
        temperature_keys,
        default=default_temperature,
        min_value=0.0,
        max_value=2.0,
    )
    top_p = _resolve_sampling_value(
        top_p_keys,
        default=default_top_p,
        min_value=0.0,
        max_value=1.0,
    )
    return temperature, top_p


def _format_tool_names(tool_call: Any) -> str:
    if isinstance(tool_call, list):
        names = [getattr(item, "name", "") for item in tool_call]
        names = [name for name in names if name]
        return ", ".join(names) if names else "<unknown>"
    name = getattr(tool_call, "name", "")
    return name or "<unknown>"


def _looks_like_tool_call(value: Any) -> bool:
    if isinstance(value, list):
        return all(hasattr(item, "name") for item in value)
    return hasattr(value, "name")


def _drop_none_tool_arguments(tool_call: Any) -> None:
    calls = tool_call if isinstance(tool_call, list) else [tool_call]
    for current_call in calls:
        raw_arguments = getattr(current_call, "arguments", None)
        if not isinstance(raw_arguments, str):
            continue
        try:
            parsed_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            continue

        cleaned_arguments = SchemaUtils.remove_none_values(parsed_arguments)
        if cleaned_arguments is None:
            cleaned_arguments = {}
        if cleaned_arguments != parsed_arguments:
            current_call.arguments = json.dumps(cleaned_arguments, ensure_ascii=False)


def _normalize_execute_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, Any, Any, Any, dict[str, Any]]:
    """Accept both legacy and current ability_manager.execute call shapes."""
    ctx = kwargs.get("ctx")
    tool_call = kwargs.get("tool_call")
    session = kwargs.get("session")
    tag = kwargs.get("tag")
    extra_kwargs = {k: v for k, v in kwargs.items() if k not in {"ctx", "tool_call", "session", "tag"}}
    positional = list(args)

    if tool_call is None and positional:
        if len(positional) >= 3 and not _looks_like_tool_call(positional[0]):
            if ctx is None:
                ctx = positional.pop(0)
        elif len(positional) >= 4 and ctx is None:
            ctx = positional.pop(0)

    if tool_call is None and positional:
        tool_call = positional.pop(0)
    if session is None and positional:
        session = positional.pop(0)
    if tag is None and positional:
        tag = positional.pop(0)

    return ctx, tool_call, session, tag, extra_kwargs


def build_browser_worker_system_prompt(
    screenshot_subdir: str = "screenshots",
    artifacts_subdir: str = "artifacts",
) -> str:
    return (
        "You are a browser worker agent.\n"
        "Execute browser tasks step-by-step with Playwright MCP tools and approved runtime helper tools only.\n"
        "Before interacting, ensure page or selector readiness.\n"
        "Keep actions targeted and avoid unnecessary page snapshots.\n"
        "Before broad page snapshots, full-body scans, or generic DOM scraping, "
        "choose the smallest compact probe that matches the task. "
        "Use browser_probe_interactives for buttons, links, inputs, forms, navigation controls, "
        "login controls, pagination controls, menus, and other visible interactive elements. "
        "When using browser_probe_interactives only for page-level controls, prefer max_items around 20-30 "
        "unless the task explicitly requires a larger inventory. "
        "On product pages, marketplace pages, search-result pages, catalog pages, article-list pages, "
        "or any page with repeated visible cards/listings, call browser_probe_cards before broad extraction. "
        "Use browser_probe_cards to identify compact repeated structures such as product cards, result cards, "
        "book cards, article cards, listing rows, title/author/source/summary, "
        "title/price/rating/review/availability fields, primary links, "
        "visible buttons, bounding boxes, selector hints, and recurring structure signatures. "
        "For product/listing/item-data tasks, prefer browser_probe_cards first; call browser_probe_interactives "
        "only if you also need page-level navigation, filters, forms, or controls outside the cards. "
        "If browser_probe_cards returns the fields needed for the task, including article/result-row "
        "title, link, author/source, or summary fields, use that compact result as the evidence "
        "for extraction and final reporting instead of taking screenshots, snapshots, or running extra DOM scans. "
        "After a successful card probe, call browser_run_code/browser_run_code_unsafe only for a clearly missing "
        "required field or a specific selector-based action; do not repeat broad evaluation "
        "just to re-read the same cards. Prefer selector_hint values from compact probes when they are relevant. "
        "Before filling passenger/contact/checkout forms with guessed selectors, "
        "call browser_probe_form_fields to get verified selector_hint values. "
        "After probing fields, prefer browser_fill_form_semantic for ordinary visible text fields, or "
        "browser_batch_interact with verified selector_hint values for multi-step form sequences. "
        "Use browser_probe_dropdown and browser_select_dropdown_option for dropdowns, comboboxes, "
        "Select2/custom multi-select widgets, airport/city selectors, country/title fields, and passenger "
        "selectors. Pass field_label when a custom widget has no stable input selector. For native multi-selects, "
        "use option_texts for an atomic set or preserve_existing=true when adding options sequentially. "
        "After opening or typing into a dropdown, do not keep clicking stale refs; probe or select by option text. "
        "Use browser_probe_calendar and browser_select_calendar_date for calendars and date pickers. "
        "Always target exact ISO dates by year, month, and day; do not click a bare day number such as '15' "
        "unless the month/year are verified. "
        "browser_batch_interact is a standalone runtime helper like browser_probe_interactives and "
        "browser_probe_cards, not a browser_custom_action wrapper. Use it directly. "
        "For multi-field forms with several known controls, search flows with autocomplete, dropdown/date-picker "
        "flows, filter panels, or any short sequence where two or more next click/type/wait/extract steps are "
        "already known, call browser_batch_interact before falling back to repeated "
        "browser_click/browser_type/browser_wait_for turns. "
        "Do not force browser_batch_interact for a single uncertain click, one simple text field, or a page state "
        "that still needs inspection. Use selector_hint values from probes as batch step selectors. "
        "Use autocomplete steps for type-then-choose widgets, and condition-based wait_for_selector/wait_for_text "
        "steps instead of fixed browser_wait_for sleeps. Do not split click+type+wait, click+wait+click, "
        "date open+choose, or search submit+result wait into separate ReAct turns unless browser_batch_interact "
        "failed or the next target is genuinely unknown. "
        "Keep using browser_fill_form for ordinary visible text fields when that official tool is enough. "
        "If browser_batch_interact fails because selectors are missing, call browser_probe_form_fields "
        "before another batch attempt. Do not use snapshot [ref=...] targets for passenger/contact form filling. "
        "Raw Playwright MCP primitives are constrained by a usage policy: avoid repeated browser_snapshot, "
        "browser_click, browser_type, browser_wait_for, browser_evaluate, browser_run_code, and "
        "browser_take_screenshot loops. "
        "If a low-level primitive is blocked, switch to the matching semantic helper rather than retrying. "
        "Never retry the same stale [ref=...] target after it fails. "
        "Use browser_snapshot only when the compact probes are insufficient, when accessibility structure is needed, "
        "or when you need exact element references required by a Playwright MCP action. "
        "Use browser_run_code_unsafe or browser_run_code only when you already know the exact selector/computation, "
        "or when the compact probes and browser_snapshot are insufficient. "
        "Do not use browser_run_code_unsafe or browser_run_code to dump the entire document body "
        " unless all compact approaches fail.\nIf actions repeatedly fail, stop and report the exact failing action.\n"
        "If you use browser_tabs, action MUST be one of: list, new, close, select.\n"
        "For specialized operations (file upload, drag-and-drop, coordinates, etc.), "
        "call browser_list_custom_actions to discover available actions and their params, "
        "then call browser_custom_action with the matching action name and params.\n"
        "Never call browser_custom_action with action='browser_task' or action='run_browser_task'. "
        "Do not launch nested browser tasks from the browser worker. "
        "If you cannot finish without recursion, return a JSON error object instead.\n"
        "IMPORTANT: Do NOT use browser_take_screenshot unless strictly necessary. "
        f"If a screenshot is needed, always save it under '{screenshot_subdir}/'. "
        "Use browser_run_code_unsafe or browser_run_code with: "
        f"async (page) => {{ await page.screenshot({{ path: '{screenshot_subdir}/screenshot.png' }}); "
        f"return '{screenshot_subdir}/screenshot.png'; }}\n"
        f"If you produce any output files (reports, notes, summaries, markdown, text files, etc.), "
        f"write them to the '{artifacts_subdir}/' directory relative to the working directory. "
        "Never write output files to the project root or any other location.\n"
        "Final output MUST be a single JSON object with keys:\n"
        "ok (boolean), final (string), page (object with url and title), "
        "screenshot (string|null), error (string|null).\n"
        "Also include status (completed|partial|blocked|failed) whenever possible. "
        "If the task is not fully complete, include progress as an object with "
        "completed_steps, remaining_steps, next_step, completion_evidence, and missing_requirements.\n"
        "Set ok=true only when the exact user-visible goal is fully satisfied and you can cite concrete evidence "
        "from the page, compact probe results, browser snapshots, screenshots, or generated artifacts. "
        "If the task is incomplete or blocked, set ok=false and fill the progress fields so a continuation can "
        "resume with minimal repetition.\n"
        "Return JSON only, even on failures. "
        "Do not output markdown, code fences, or plain text outside the JSON object."
    )


async def _notify_tool_results(
    tool_call: Any,
    results: Any,
    observer: ToolResultObserver,
) -> None:
    calls = tool_call if isinstance(tool_call, list) else [tool_call]
    result_items = results if isinstance(results, list) else []
    for idx, current_call in enumerate(calls):
        if idx >= len(result_items):
            break
        current_result = result_items[idx]
        tool_result = current_result[0] if isinstance(current_result, tuple) and current_result else current_result
        tool_name = getattr(current_call, "name", "") or "<tool>"
        await observer(tool_name, tool_result)


def ensure_execute_signature_compat(
    agent: ReActAgent,
    *,
    tool_result_observer: ToolResultObserver | None = None,
) -> None:
    """Wrap ability_manager.execute with signature compatibility and timeout."""
    execute_fn = getattr(agent.ability_manager, "execute", None)
    if execute_fn is None:
        return
    if getattr(execute_fn, "_playwright_timeout_wrapped", False):
        return

    try:
        params = inspect.signature(execute_fn).parameters
    except (TypeError, ValueError):
        return

    original_execute = execute_fn
    usage_limiter = _BrowserMcpUsageLimiter()
    logger.info(
        "[BROWSER_MCP_LIMIT] installed enabled=%s raw_streak_limit=%s",
        usage_limiter.enabled,
        usage_limiter.raw_streak_limit,
    )
    supports_ctx = "ctx" in params
    supports_tag = "tag" in params
    supports_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
    tool_timeout_s = _resolve_tool_timeout_s()
    runtime_marker_emitted = False

    async def execute_with_timeout(*args, **kwargs):
        nonlocal runtime_marker_emitted
        if not runtime_marker_emitted:
            runtime_marker_emitted = True
            logger.info(
                "[BROWSER_MCP_LIMIT] runtime_active enabled=%s raw_streak_limit=%s",
                usage_limiter.enabled,
                usage_limiter.raw_streak_limit,
            )
        ctx, tool_call, session, tag, extra_kwargs = _normalize_execute_call(args, kwargs)
        tool_names = _format_tool_names(tool_call)
        _drop_none_tool_arguments(tool_call)
        call_kwargs: dict[str, Any] = {}
        if supports_ctx:
            call_kwargs["ctx"] = ctx
        if "tool_call" in params:
            call_kwargs["tool_call"] = tool_call
        if "session" in params:
            call_kwargs["session"] = session
        if supports_tag:
            call_kwargs["tag"] = tag
        if supports_kwargs:
            call_kwargs.update(extra_kwargs)
        blocked_reason = usage_limiter.blocked_reason(tool_call)
        if blocked_reason:
            normalized_names = ",".join(
                _normalized_browser_tool_name(getattr(call, "name", "")) for call in _tool_calls_list(tool_call)
            )
            logger.warning(
                "[BROWSER_MCP_LIMIT] blocked tool=%s normalized=%s reason=%s",
                tool_names,
                normalized_names,
                blocked_reason,
            )
            results = usage_limiter.blocked_tool_results(tool_call, blocked_reason)
            if tool_result_observer is not None:
                await _notify_tool_results(tool_call, results, tool_result_observer)
            return results
        try:
            with anyio.fail_after(tool_timeout_s):
                results = await original_execute(**call_kwargs)
        except TimeoutError as exc:
            logger.error(
                f"Tool execution timed out after {tool_timeout_s:.1f}s; tools={tool_names}"
            )
            raise RuntimeError(
                f"tool_execution_timeout: tools={tool_names}, timeout_s={tool_timeout_s:.1f}"
            ) from exc
        usage_limiter.record_result(tool_call, results)
        if tool_result_observer is not None:
            await _notify_tool_results(tool_call, results, tool_result_observer)
        return results

    agent.ability_manager.execute = execute_with_timeout
    setattr(agent.ability_manager.execute, "_playwright_timeout_wrapped", True)


# pylint: disable=too-many-arguments
def build_browser_worker_agent(
    provider: str,
    api_key: str,
    *,
    api_base: str,
    model_name: str,
    mcp_cfg: McpServerConfig,
    max_steps: int,
    screenshot_subdir: str = "screenshots",
    artifacts_subdir: str = "artifacts",
    tool_result_observer: ToolResultObserver | None = None,
    allowed_tool_names: Optional[Iterable[str]] = None,
) -> ReActAgent:
    resolved_allowed_tool_names = (
        None
        if allowed_tool_names is None
        else tuple(dict.fromkeys(allowed_tool_names))
    )
    logger.info(
        "Playwright worker received tool allowlist: %s",
        resolved_allowed_tool_names,
    )
    screenshot_subdir = (
        (screenshot_subdir or "screenshots").strip().replace("\\", "/").strip("/") or "screenshots"
    )
    artifacts_subdir = (
        (artifacts_subdir or "artifacts").strip().replace("\\", "/").strip("/") or "artifacts"
    )
    card = AgentCard(
        id="agent.playwright.browser_worker",
        name="playwright_browser_worker",
        description="Browser worker that executes web tasks using Playwright MCP tools.",
        input_params={},
    )
    config = (
        ReActAgentConfig()
        .configure_model_client(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
        )
        .configure_max_iterations(max_steps)
        .configure_prompt_template(
            [
                {
                    "role": "system",
                    "content": build_browser_worker_system_prompt(screenshot_subdir, artifacts_subdir),
                }
            ]
        )
    )
    worker_temperature, worker_top_p = _resolve_sampling_params(
        temperature_keys=("BROWSER_WORKER_TEMPERATURE", "BROWSER_MODEL_TEMPERATURE", "MODEL_TEMPERATURE"),
        top_p_keys=("BROWSER_WORKER_TOP_P", "BROWSER_MODEL_TOP_P", "MODEL_TOP_P"),
        default_temperature=0.2,
        default_top_p=0.1,
    )
    if config.model_config_obj is not None:
        config.model_config_obj.temperature = worker_temperature
        config.model_config_obj.top_p = worker_top_p
    agent = ReActAgent(card=card).configure(config)
    agent.ability_manager.add(mcp_cfg)
    if resolved_allowed_tool_names is not None:
        agent.ability_manager.set_mcp_tool_allowlist(
            mcp_cfg,
            resolved_allowed_tool_names,
        )
    ensure_execute_signature_compat(agent, tool_result_observer=tool_result_observer)
    return agent
