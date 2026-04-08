# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent builders for runtime and browser worker."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from typing import Any, Awaitable, Callable, Optional

import anyio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.context_engine import DialogueCompressorConfig
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

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
        tokens_threshold=50000,
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
        "Execute browser tasks step-by-step with Playwright MCP tools only.\n"
        "Before interacting, ensure page or selector readiness.\n"
        "Keep actions targeted and avoid unnecessary page snapshots.\n"
        "If actions repeatedly fail, stop and report the exact failing action.\n"
        "If you use browser_tabs, action MUST be one of: list, new, close, select.\n"
        "For specialized operations (file upload, drag-and-drop, coordinates, etc.), "
        "call browser_list_custom_actions to discover available actions and their params, "
        "then call browser_custom_action with the matching action name and params.\n"
        "Never call browser_custom_action with action='browser_task' or action='run_browser_task'. "
        "Do not launch nested browser tasks from the browser worker. "
        "If you cannot finish without recursion, return a JSON error object instead.\n"
        "IMPORTANT: Do NOT use browser_take_screenshot unless strictly necessary. "
        f"If a screenshot is needed, always save it under '{screenshot_subdir}/'. "
        "Use browser_run_code with: "
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
        "from the page or generated artifacts. If the task is incomplete or blocked, set ok=false and fill the "
        "progress fields so a continuation can resume with minimal repetition.\n"
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
    supports_ctx = "ctx" in params
    supports_tag = "tag" in params
    supports_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
    tool_timeout_s = _resolve_tool_timeout_s()

    async def execute_with_timeout(*args, **kwargs):
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
) -> ReActAgent:
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
    ensure_execute_signature_compat(agent, tool_result_observer=tool_result_observer)
    return agent

