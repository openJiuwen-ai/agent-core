# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent builders for runtime and browser worker."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
from collections import deque
from typing import Any, Awaitable, Callable, Optional

import anyio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.context_engine import DialogueCompressorConfig
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig, ToolMessage
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.browser_move.playwright_runtime.semantic_routing import (
    compact_json,
    has_semantic_route,
    semantic_route_message,
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


def _tool_calls_list(tool_call: Any) -> list[Any]:
    return tool_call if isinstance(tool_call, list) else [tool_call]


def _normalized_browser_tool_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    known = (
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_wait_for",
        "browser_take_screenshot",
        "browser_run_code",
        "browser_run_code_unsafe",
        "browser_probe_interactives",
        "browser_probe_cards",
        "browser_probe_form_fields",
        "browser_fill_form_semantic",
        "browser_probe_dropdown",
        "browser_select_dropdown_option",
        "browser_probe_calendar",
        "browser_select_calendar_date",
        "browser_batch_interact",
    )

    for prefix in (
        "mcp_playwright-official_",
        "mcp_playwright_",
        "playwright-official_",
        "playwright_",
    ):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    for item in known:
        if raw == item or raw.endswith(f"_{item}") or raw.endswith(f".{item}"):
            return item
    return raw


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}



_STALE_FAILURE_PATTERNS = (
    "does not match any elements",
    "element is not attached",
    "element is not visible",
    "target closed",
    "locator.fill: timeout",
    "locator.click: timeout",
    "timeout 5000ms exceeded",
    "timeout exceeded",
    "stale",
)


def _stringify_compact(value: Any, *, limit: int = 4000) -> str:
    return compact_json(value, limit=limit)


def _result_text(results: Any) -> str:
    parts: list[str] = []
    items = results if isinstance(results, list) else [results]
    for item in items:
        current = item[0] if isinstance(item, tuple) and item else item
        content = getattr(current, "content", None)
        if content is not None:
            parts.append(str(content))
        else:
            parts.append(_stringify_compact(current, limit=2000))
    return "\n".join(parts).lower()[:6000]


def _looks_like_tool_failure(result_text: str) -> bool:
    if not result_text:
        return False
    if any(pattern in result_text for pattern in _STALE_FAILURE_PATTERNS):
        return True
    return '"ok": false' in result_text or '"success": false' in result_text or "tool_error" in result_text


def _mentions_semantic_field(args: dict[str, Any]) -> bool:
    return has_semantic_route(args)


def _iter_argument_strings(value: Any, parent_key: str = "") -> list[tuple[str, str]]:
    strings: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            strings.extend(_iter_argument_strings(child, str(key).lower()))
    elif isinstance(value, list):
        for child in value:
            strings.extend(_iter_argument_strings(child, parent_key))
    elif isinstance(value, str):
        strings.append((parent_key, value.strip()))
    return strings


def _tool_target_fingerprints(args: dict[str, Any]) -> set[str]:
    target_keys = {"target", "selector", "selector_hint", "element", "ref"}
    fingerprints: set[str] = set()
    for key, value in _iter_argument_strings(args):
        if not value:
            continue
        lower_value = value.lower()
        if key in target_keys:
            fingerprints.add(lower_value[:600])
        ref_matches = re.findall(r"\[ref=[^\]]+\]", value)
        fingerprints.update(match.lower() for match in ref_matches)
    return fingerprints


def _append_unique(history: deque[str], value: str) -> None:
    if not value:
        return
    try:
        history.remove(value)
    except ValueError:
        pass
    history.append(value)


class _BrowserMcpUsageLimiter:
    """Runtime gate that routes inefficient MCP loops into semantic helpers."""

    _semantic_tools = {
        "browser_probe_interactives",
        "browser_probe_cards",
        "browser_probe_form_fields",
        "browser_fill_form_semantic",
        "browser_probe_dropdown",
        "browser_select_dropdown_option",
        "browser_probe_calendar",
        "browser_select_calendar_date",
        "browser_batch_interact",
    }
    _limited_tools = {
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_wait_for",
        "browser_take_screenshot",
    }
    _raw_interaction_tools = {
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_wait_for",
        "browser_take_screenshot",
    }

    def __init__(self) -> None:
        raw_enabled = os.getenv("BROWSER_MCP_USAGE_LIMITS", "1").strip().lower()
        self.enabled = raw_enabled not in {"0", "false", "no", "off"}
        self.last_tool = ""
        self.last_signature = ""
        self.consecutive = 0
        self.raw_streak = 0
        self.raw_fail_streak = 0
        self.last_failed_signature = ""
        self.last_failure_semantic_context = False
        self.recent_failed_signatures: deque[str] = deque(maxlen=12)
        self.recent_failed_targets: deque[str] = deque(maxlen=20)
        self.recent_raw_failures: deque[bool] = deque(maxlen=8)

    def _limit_for(self, tool_name: str) -> int:
        defaults = {
            "browser_snapshot": 1,
            "browser_click": 2,
            "browser_type": 2,
            "browser_fill_form": 1,
            "browser_wait_for": 1,
            "browser_take_screenshot": 1,
        }
        env_key = f"BROWSER_MCP_LIMIT_{tool_name.upper()}"
        try:
            return max(0, int(os.getenv(env_key, defaults.get(tool_name, 2))))
        except (TypeError, ValueError):
            return defaults.get(tool_name, 2)

    def _raw_streak_limit(self) -> int:
        try:
            return max(1, int(os.getenv("BROWSER_MCP_RAW_STREAK_LIMIT", "5")))
        except (TypeError, ValueError):
            return 5

    def _signature(self, tool_name: str, args: dict[str, Any]) -> str:
        return f"{tool_name}:{_stringify_compact(args, limit=1200)}"

    def _semantic_route_message(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        prefer_form_for_field_entry: bool = False,
    ) -> str:
        return semantic_route_message(
            tool_name,
            args,
            prefer_form_for_field_entry=prefer_form_for_field_entry,
        )

    def blocked_reason(self, tool_call: Any) -> str | None:
        if not self.enabled:
            return None
        calls = _tool_calls_list(tool_call)
        if len(calls) != 1:
            return None

        current = calls[0]
        tool_name = _normalized_browser_tool_name(getattr(current, "name", ""))
        args = _parse_tool_args(getattr(current, "arguments", ""))
        signature = self._signature(tool_name, args)
        target_fingerprints = _tool_target_fingerprints(args)

        if tool_name in self._semantic_tools:
            return None

        if tool_name in {"browser_run_code", "browser_run_code_unsafe"}:
            code = str(args.get("code") or "").lower()
            broad_patterns = (
                "document.body.innertext",
                "document.body.textcontent",
                "document.documentelement.outerhtml",
                "queryselectorall('*')",
                'queryselectorall("*")',
            )
            if any(pattern in code for pattern in broad_patterns):
                return (
                    "mcp_usage_limited: broad browser_run_code DOM dumps are disabled. "
                    "Use browser_probe_interactives, browser_probe_cards, browser_probe_form_fields, "
                    "browser_probe_dropdown, browser_probe_calendar, or a narrow selector-specific script instead."
                )
            return None

        if tool_name not in self._limited_tools:
            return None

        if signature in self.recent_failed_signatures:
            return (
                f"mcp_usage_limited: the same failed {tool_name} target was retried. "
                "Do not retry stale refs/selectors. " + self._semantic_route_message(tool_name, args)
            )

        if target_fingerprints & set(self.recent_failed_targets):
            return (
                f"mcp_usage_limited: a recently failed {tool_name} target/ref was retried. "
                "Do not retry stale refs/selectors. " + self._semantic_route_message(tool_name, args)
            )

        if sum(self.recent_raw_failures) >= 2 or self.raw_fail_streak >= 2:
            return (
                "mcp_usage_limited: repeated raw Playwright interactions recently failed. "
                + self._semantic_route_message(
                    tool_name,
                    args,
                    prefer_form_for_field_entry=True,
                )
            )

        if self.last_failure_semantic_context and tool_name in {
            "browser_click",
            "browser_type",
            "browser_fill_form",
        }:
            return (
                "mcp_usage_limited: the last dropdown/calendar/form interaction failed. "
                + self._semantic_route_message(
                    tool_name,
                    args,
                    prefer_form_for_field_entry=True,
                )
            )

        next_count = self.consecutive + 1 if tool_name == self.last_tool else 1
        limit = self._limit_for(tool_name)
        if limit >= 0 and next_count > limit:
            return (
                f"mcp_usage_limited: repeated {tool_name} calls are limited to {limit} consecutive call(s). "
                + self._semantic_route_message(tool_name, args)
            )

        next_raw_streak = self.raw_streak + 1 if tool_name in self._raw_interaction_tools else 0
        if next_raw_streak > self._raw_streak_limit():
            return (
                f"mcp_usage_limited: raw browser primitive streak exceeded {self._raw_streak_limit()} calls. "
                "Use browser_probe_form_fields/browser_fill_form_semantic for forms, "
                "browser_select_dropdown_option for dropdowns, browser_select_calendar_date for calendars, "
                "or browser_batch_interact with verified selectors."
            )

        if tool_name == "browser_wait_for":
            duration = args.get("time") or args.get("timeout") or args.get("timeout_ms") or args.get("ms")
            try:
                duration_value = float(duration)
            except (TypeError, ValueError):
                duration_value = 0.0
            if duration_value > 6000:
                return (
                    "mcp_usage_limited: long generic waits are disabled. Use condition-based "
                    "wait_for_selector/wait_for_text in browser_batch_interact or a targeted probe."
                )

        return None

    def record_result(self, tool_call: Any, results: Any) -> None:
        calls = _tool_calls_list(tool_call)
        if len(calls) != 1:
            self.last_tool = ""
            self.last_signature = ""
            self.consecutive = 0
            self.raw_streak = 0
            return

        current = calls[0]
        tool_name = _normalized_browser_tool_name(getattr(current, "name", ""))
        args = _parse_tool_args(getattr(current, "arguments", ""))
        signature = self._signature(tool_name, args)
        target_fingerprints = _tool_target_fingerprints(args)

        if tool_name in self._semantic_tools:
            self.last_tool = ""
            self.last_signature = ""
            self.consecutive = 0
            self.raw_streak = 0
            self.raw_fail_streak = 0
            self.last_failed_signature = ""
            self.last_failure_semantic_context = False
            self.recent_failed_signatures.clear()
            self.recent_failed_targets.clear()
            self.recent_raw_failures.clear()
            return

        if tool_name == self.last_tool:
            self.consecutive += 1
        else:
            self.last_tool = tool_name
            self.consecutive = 1
        self.last_signature = signature

        if tool_name in self._raw_interaction_tools:
            self.raw_streak += 1
        else:
            self.raw_streak = 0

        failed = _looks_like_tool_failure(_result_text(results))
        semantic_context = _mentions_semantic_field(args)
        if failed and tool_name in self._raw_interaction_tools:
            self.raw_fail_streak += 1
            self.last_failed_signature = signature
            self.last_failure_semantic_context = semantic_context
            self.recent_raw_failures.append(True)
            _append_unique(self.recent_failed_signatures, signature)
            for fingerprint in target_fingerprints:
                _append_unique(self.recent_failed_targets, fingerprint)
        elif tool_name in self._raw_interaction_tools:
            self.raw_fail_streak = 0
            self.last_failure_semantic_context = False
            self.recent_raw_failures.append(False)
        else:
            self.raw_fail_streak = 0
            self.last_failure_semantic_context = False

def _blocked_tool_results(tool_call: Any, reason: str) -> list[tuple[Any, ToolMessage]]:
    results: list[tuple[Any, ToolMessage]] = []
    for call in _tool_calls_list(tool_call):
        payload = {
            "ok": False,
            "error": reason,
            "policy": "browser_mcp_usage_limiter",
            "tool_name": getattr(call, "name", ""),
        }
        results.append(
            (
                payload,
                ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False),
                    tool_call_id=getattr(call, "id", None),
                ),
            )
        )
    return results


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
        "autocomplete widgets, airport/city selectors, country/title fields, and passenger selectors. "
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
        "browser_click, browser_type, browser_wait_for, and browser_take_screenshot loops. "
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
            results = _blocked_tool_results(tool_call, blocked_reason)
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

