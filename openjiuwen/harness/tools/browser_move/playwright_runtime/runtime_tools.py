# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent Tool wrappers around BrowserAgentRuntime."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from .runtime import BrowserAgentRuntime

_ctx_parent_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_session_id",
    default="",
)
_ctx_parent_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_request_id",
    default="",
)

_CANCEL_DESC = (
    "Cancel an in-progress browser task by session_id. "
    "Optionally pass request_id to target a specific request within the session. "
    "Returns JSON with ok/session_id/request_id/error."
)
_CANCEL_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "description": "Session ID of the task to cancel"},
        "request_id": {"type": "string", "description": "Optional: specific request ID to cancel"},
    },
    "required": ["session_id"],
}

_CLEAR_CANCEL_DESC = (
    "Clear the cancellation flag for a browser session or request. "
    "Returns JSON with ok/session_id/request_id/error."
)
_CLEAR_CANCEL_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "description": "Session ID to clear"},
        "request_id": {"type": "string", "description": "Optional: specific request ID to clear"},
    },
    "required": ["session_id"],
}

_CUSTOM_ACTION_DESC = (
    "Run a registered custom browser action by name. "
    "Use for deterministic helpers such as drag-and-drop or coordinate resolution "
    "alongside the direct Playwright MCP browser tools. "
    "Call browser_list_custom_actions first to discover available actions and parameters. "
    "Aliases source/target and source_x/source_y/target_x/target_y are accepted."
)
_CUSTOM_ACTION_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "description": "Name of the custom action to run"},
        "session_id": {"type": "string", "description": "Session ID (optional)"},
        "request_id": {"type": "string", "description": "Request ID (optional)"},
        "params": {
            "type": "object",
            "description": "Extra key-value parameters forwarded to the action",
            "properties": {},
            "required": [],
        },
    },
    "required": ["action"],
}

_LIST_ACTIONS_DESC = (
    "List available custom browser actions and detailed parameter guidance "
    "for browser_custom_action."
)
_LIST_ACTIONS_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}

_RUNTIME_HEALTH_DESC = (
    "Return runtime readiness, heartbeat status, and selected provider/model configuration."
)
_RUNTIME_HEALTH_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}

_PROBE_INTERACTIVES_DESC = (
    "Return a compact list of visible, high-value interactive elements on the current page. "
    "Use this for page-level controls such as buttons, links, inputs, forms, navigation, login, "
    "pagination, menus, calendar dates, sort tabs, rating filters, and visible actions. "
    "Dynamic controls are returned as generation-scoped targets with region/kind semantics. "
    "The optional query filter is alias-aware for common "
    "search/input terms, including placeholders, aria labels, input type/name/id/class, and Chinese "
    "search text such as 搜索/关键词. Prefer max_items around 20-30 unless a larger inventory "
    "is needed. For product/search/listing card data, prefer browser_probe_cards first. "
    "The result includes compact PageState and generation-scoped target_id values that can be "
    "passed directly to browser_batch_interact without rebuilding CSS. It also includes "
    "role/text/region/kind plus match_count/visible/enabled/actionable. The model-facing "
    "result does not expose Probe-local ids or internal selectors."
)
_PROBE_INTERACTIVES_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_items": {
            "type": "integer",
            "description": "Maximum number of elements to return. Default 30, hard-capped at 40.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only return elements currently visible in the viewport. Default true.",
        },
        "query": {
            "type": "string",
            "description": "Optional text filter, e.g. 'cart', 'search', 'next', or 'login'.",
        },
    },
    "required": [],
}

_PROBE_CARDS_DESC = (
    "Return compact repeated card/listing structures from the current page. "
    "Use this first on product pages, marketplace pages, search-result pages, catalog pages, "
    "article-list pages, table/list-row result pages, or any page with repeated visible cards/listings. "
    "The result includes compact PageState with generation-scoped card/control target_id values, "
    "candidate card title, author/source, summary/snippet, price, rating, "
    "review count, availability, primary link, and visible controls, "
    "region/kind/result_index/is_ad semantics that distinguish main results, accounts, sidebars, "
    "hot searches, shops, chats, and product links, "
    "match_count/visible/enabled/clickable/generation_id, recurring structure signatures, and "
    "cache diagnostics. Navigate primary_link/href directly instead of clicking a card hint. "
    "If this returns the fields needed "
    "for the task, including article/search-result title/link/author/source/summary fields, "
    "use the compact card result directly instead of taking screenshots/snapshots or running "
    "broad DOM evaluation. Only evaluate again when a required field is missing."
)
_PROBE_CARDS_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_cards": {
            "type": "integer",
            "description": "Maximum number of cards to return. Default 12, hard-capped at 20.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only inspect cards visible in the current viewport. Default true.",
        },
        "include_buttons": {
            "type": "boolean",
            "description": "When true, include visible buttons/links inside each card. Default true.",
        },
        "query": {
            "type": "string",
            "description": "Optional text filter, e.g. 'mouse', 'book', 'laptop', or 'cart'.",
        },
    },
    "required": [],
}

_BATCH_INTERACT_DESC = (
    "Execute deterministic browser interactions in one standalone runtime tool call. "
    "Use after browser_probe_interactives/browser_probe_cards when a page-level flow has multiple "
    "known targets, such as three or more form fields, click+type+choose autocomplete, dropdown or "
    "date-picker selection, filter panels, search submit plus result wait, or compact extraction. "
    "This is a first-class helper like the probe tools; do not route this through browser_custom_action. "
    "Pass the current PageState generation_id and use target_id from probes for actions. "
    "Only read-only extraction and explicit wait operations accept a validated selector. "
    "Locator strategies are mutually exclusive. "
    "A one-step call is rewritten by the runtime to an equivalent official browser primitive when "
    "one exists, so it does not fail and require another model turn. Multi-step calls are preflighted "
    "before side effects. Results use status=completed/partial/failed and contain only compact step "
    "status, extracted fields, condition observations, metrics, and PageState."
)
_BATCH_INTERACT_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "description": (
                "Ordered browser steps to execute in one batch. Supported ops: click, fill, type, "
                "autocomplete, select_visible_text, press, select_option, set_checked, "
                "wait_for_selector, wait_for_text, wait_for_load_state, wait_for_url, "
                "wait_for_first_card_title, wait_for_sort_state, wait_for_result_count, "
                "wait_for_dom_text_change, wait_for_stable, wait_for_tab, extract_text, "
                "extract_value, screenshot."
            ),
            "minItems": 1,
            "maxItems": 25,
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "description": "Step operation name.",
                        "enum": [
                            "click",
                            "fill",
                            "type",
                            "autocomplete",
                            "select_visible_text",
                            "press",
                            "select_option",
                            "set_checked",
                            "wait_for_selector",
                            "wait_for_text",
                            "wait_for_load_state",
                            "wait_for_url",
                            "wait_for_first_card_title",
                            "wait_for_sort_state",
                            "wait_for_result_count",
                            "wait_for_dom_text_change",
                            "wait_for_stable",
                            "wait_for_tab",
                            "extract_text",
                            "extract_value",
                            "screenshot",
                        ],
                    },
                    "target_id": {
                        "type": "string",
                        "description": (
                            "Generation-scoped target_id returned by the current PageState/probe. "
                            "Preferred for actions and mutually exclusive with ref/selector/role/"
                            "label/placeholder/text/testid."
                        ),
                    },
                    "ref": {
                        "type": "string",
                        "description": (
                            "Native AX ref returned by browser_snapshot/browser_find in the current generation. "
                            "The runtime resolves it without model-generated CSS."
                        ),
                    },
                    "selector": {
                        "type": "string",
                        "description": (
                            "Current-generation selector_hint from a probe, or an explicit selector only "
                            "for a wait condition. "
                            "Do not combine with another locator strategy."
                        ),
                    },
                    "role": {
                        "type": "string",
                        "description": "ARIA role to locate, e.g. button/textbox/link.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Accessible name for role-based locating.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Label text for labeled form controls.",
                    },
                    "placeholder": {
                        "type": "string",
                        "description": "Placeholder text for form controls.",
                    },
                    "testid": {
                        "type": "string",
                        "description": "Exact data-testid value for a target.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Visible text for locate/click/wait_for_text.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to fill/type/select, or autocomplete query.",
                    },
                    "option_text": {
                        "type": "string",
                        "description": (
                            "Visible option text for autocomplete/select_visible_text/native select label."
                        ),
                    },
                    "choose_text": {
                        "type": "string",
                        "description": "Alias for option_text in autocomplete flows.",
                    },
                    "option_selector": {
                        "type": "string",
                        "description": "CSS selector for an autocomplete/dropdown option to choose.",
                    },
                    "choose_selector": {
                        "type": "string",
                        "description": "Alias for option_selector.",
                    },
                    "option_role": {
                        "type": "string",
                        "description": ("ARIA role for an autocomplete/dropdown option, e.g. option/menuitem."),
                    },
                    "choose_role": {
                        "type": "string",
                        "description": "Alias for option_role.",
                    },
                    "option_name": {
                        "type": "string",
                        "description": "Accessible name for option_role.",
                    },
                    "choose_name": {
                        "type": "string",
                        "description": "Alias for option_name.",
                    },
                    "option_value": {
                        "type": "string",
                        "description": "Native select option value. Alias for value when op=select_option.",
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "MCP/Playwright-style native select values list. Also accepted for single-select fields."
                        ),
                    },
                    "option_label": {
                        "type": "string",
                        "description": ("Native select visible label. Alias for option_text when op=select_option."),
                    },
                    "label_value": {
                        "type": "string",
                        "description": "Native select visible label alias.",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Native select option index.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Keyboard key for press, e.g. Enter or Escape.",
                    },
                    "checked": {
                        "type": "boolean",
                        "description": "Desired checked state for set_checked.",
                    },
                    "state": {
                        "type": "string",
                        "description": (
                            "Wait state for wait_for_selector/load_state, e.g. visible/attached/domcontentloaded."
                        ),
                    },
                    "option_target_id": {
                        "type": "string",
                        "description": "PageState target_id for an autocomplete/dropdown option.",
                    },
                    "option_ref": {
                        "type": "string",
                        "description": "Current-generation AX ref for an autocomplete/dropdown option.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Exact URL expected by wait_for_url.",
                    },
                    "expected_url": {
                        "type": "string",
                        "description": "Alias for the exact URL expected by wait_for_url.",
                    },
                    "url_contains": {
                        "type": "string",
                        "description": "URL substring expected by wait_for_url.",
                    },
                    "url_pattern": {
                        "type": "string",
                        "description": "Regular expression expected to match the current URL.",
                    },
                    "title_contains": {
                        "type": "string",
                        "description": "Title substring expected on a newly opened tab.",
                    },
                    "min_tabs": {
                        "type": "integer",
                        "description": (
                            "Minimum context tab count for wait_for_tab. By default the runtime waits "
                            "for one more tab than existed when the batch started."
                        ),
                    },
                    "activate": {
                        "type": "boolean",
                        "description": (
                            "For wait_for_tab, activate the matched new tab for subsequent steps. Default true."
                        ),
                    },
                    "expected_text": {
                        "type": "string",
                        "description": "Expected first-card title text.",
                    },
                    "attribute": {
                        "type": "string",
                        "description": "Attribute inspected by wait_for_sort_state; defaults to aria-sort.",
                    },
                    "expected_value": {
                        "type": "string",
                        "description": "Expected attribute or text value for a wait condition.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Exact result count expected.",
                    },
                    "min_count": {
                        "type": "integer",
                        "description": "Minimum result count expected.",
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum result count expected.",
                    },
                    "previous_text": {
                        "type": "string",
                        "description": "Previous DOM text used by wait_for_dom_text_change.",
                    },
                    "poll_interval_ms": {
                        "type": "integer",
                        "description": (
                            "Dynamic-condition polling interval, clamped to 50..1000ms; default 100ms. "
                            "Polling always shares the step's total timeout."
                        ),
                    },
                    "stable_ms": {
                        "type": "integer",
                        "description": "How long a matched value must remain unchanged.",
                    },
                    "field": {
                        "type": "string",
                        "description": "Structured output field name for extraction steps.",
                    },
                    "exact": {
                        "type": "boolean",
                        "description": "Use exact matching for role/label/text locators.",
                    },
                    "optional": {
                        "type": "boolean",
                        "description": "When true, failure records the step but continues.",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Optional per-step timeout override.",
                    },
                    "delay_ms": {
                        "type": "integer",
                        "description": "Optional typing delay in milliseconds.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters for extract_text.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Screenshot path for op=screenshot.",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "When true, screenshot the full page.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable purpose for logs/debugging.",
                    },
                },
                "required": ["op"],
            },
        },
        "timeout_ms": {
            "type": "integer",
            "description": ("Default per-step timeout in milliseconds. Default 2500, clamped to 250..30000."),
        },
        "generation_id": {
            "type": "string",
            "pattern": "^g[0-9]+$",
            "description": (
                "Current PageState generation_id returned by navigate, probe, snapshot, or the previous batch. "
                "Targets from older generations are rejected before browser execution."
            ),
        },
        "condition_timeout_ms": {
            "type": "integer",
            "description": (
                "Default timeout for condition waits in milliseconds. "
                "Default 10000, clamped to the action timeout..30000."
            ),
        },
        "continue_on_error": {
            "type": "boolean",
            "description": "When true, continue after failed steps and return per-step errors.",
        },
        "global_timeout_ms": {
            "type": "integer",
            "description": (
                "Hard timeout for the whole batch. Default is computed from step count, capped at "
                "120000; explicit values are capped at 180000."
            ),
        },
        "session_id": {
            "type": "string",
            "description": "Optional browser task session id.",
        },
        "request_id": {
            "type": "string",
            "description": "Optional browser task request id.",
        },
    },
    "required": ["steps", "generation_id"],
}


class BrowserCancelTool(Tool):
    """Cancel an in-progress browser task."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_cancel_run",
                description=_CANCEL_DESC,
                input_params=_CANCEL_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        await self._runtime.ensure_runtime_ready()
        session_id = inputs.get("session_id", "")
        request_id = inputs.get("request_id") or None
        try:
            result = await self._runtime.cancel_run(session_id=session_id, request_id=request_id)
            return ToolOutput(
                success=bool(result.get("ok", True)),
                data=result,
                error=result.get("error"),
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserClearCancelTool(Tool):
    """Clear a cancellation flag for a browser task."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_clear_cancel",
                description=_CLEAR_CANCEL_DESC,
                input_params=_CLEAR_CANCEL_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        await self._runtime.ensure_runtime_ready()
        session_id = inputs.get("session_id", "")
        request_id = inputs.get("request_id") or None
        try:
            result = await self._runtime.clear_cancel(session_id=session_id, request_id=request_id)
            return ToolOutput(
                success=bool(result.get("ok", True)),
                data=result,
                error=result.get("error"),
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserCustomActionTool(Tool):
    """Run a registered custom browser action."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_custom_action",
                description=_CUSTOM_ACTION_DESC,
                input_params=_CUSTOM_ACTION_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        action = inputs.get("action", "")
        session_id = (inputs.get("session_id") or "").strip() or _ctx_parent_session_id.get()
        request_id = (inputs.get("request_id") or "").strip() or _ctx_parent_request_id.get()
        params: Dict[str, Any] = inputs.get("params") or {}
        try:
            result = await self._runtime.run_custom_action(
                action=action,
                session_id=session_id,
                request_id=request_id,
                params=params,
            )
            return ToolOutput(
                success=bool(result.get("ok", True)),
                data=result,
                error=result.get("error"),
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserListActionsTool(Tool):
    """List available custom browser actions."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_list_custom_actions",
                description=_LIST_ACTIONS_DESC,
                input_params=_LIST_ACTIONS_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del inputs, kwargs
        try:
            data = await self._runtime.list_actions()
            return ToolOutput(success=True, data=data)
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserProbeInteractivesTool(Tool):
    """Compact visible-interactive-element probe."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_probe_interactives",
                description=_PROBE_INTERACTIVES_DESC,
                input_params=_PROBE_INTERACTIVES_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs

        try:
            max_items = int(inputs.get("max_items", 30))
        except (TypeError, ValueError):
            max_items = 30
        max_items = max(1, min(40, max_items))

        viewport_only_raw = inputs.get("viewport_only", True)
        if isinstance(viewport_only_raw, str):
            viewport_only = viewport_only_raw.strip().lower() not in {"0", "false", "no"}
        else:
            viewport_only = bool(viewport_only_raw)

        query = str(inputs.get("query") or "").strip()

        try:
            data = await self._runtime.probe_interactives(
                max_items=max_items,
                viewport_only=viewport_only,
                query=query,
            )
            return ToolOutput(
                success=bool(data.get("ok", True)),
                data=data,
                error=data.get("error"),
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserProbeCardsTool(Tool):
    """Compact repeated-card/listing probe."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_probe_cards",
                description=_PROBE_CARDS_DESC,
                input_params=_PROBE_CARDS_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs

        try:
            max_cards = int(inputs.get("max_cards", 12))
        except (TypeError, ValueError):
            max_cards = 12
        max_cards = max(1, min(20, max_cards))

        viewport_only_raw = inputs.get("viewport_only", True)
        if isinstance(viewport_only_raw, str):
            viewport_only = viewport_only_raw.strip().lower() not in {"0", "false", "no"}
        else:
            viewport_only = bool(viewport_only_raw)

        include_buttons_raw = inputs.get("include_buttons", True)
        if isinstance(include_buttons_raw, str):
            include_buttons = include_buttons_raw.strip().lower() not in {
                "0",
                "false",
                "no",
            }
        else:
            include_buttons = bool(include_buttons_raw)

        query = str(inputs.get("query") or "").strip()

        try:
            data = await self._runtime.probe_cards(
                max_cards=max_cards,
                viewport_only=viewport_only,
                include_buttons=include_buttons,
                query=query,
            )
            return ToolOutput(
                success=bool(data.get("ok", True)),
                data=data,
                error=data.get("error"),
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserBatchInteractTool(Tool):
    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_batch_interact",
                description=_BATCH_INTERACT_DESC,
                input_params=_BATCH_INTERACT_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs

        steps = inputs.get("steps")
        session_id = (inputs.get("session_id") or "").strip() or _ctx_parent_session_id.get()
        request_id = (inputs.get("request_id") or "").strip() or _ctx_parent_request_id.get()

        try:
            result = await self._runtime.batch_interact(
                steps=steps,
                generation_id=str(inputs.get("generation_id") or ""),
                timeout_ms=inputs.get("timeout_ms"),
                condition_timeout_ms=inputs.get("condition_timeout_ms"),
                wait_after_each_ms=inputs.get("wait_after_each_ms"),
                continue_on_error=bool(inputs.get("continue_on_error", False)),
                global_timeout_ms=inputs.get("global_timeout_ms"),
                session_id=session_id,
                request_id=request_id,
            )
            return ToolOutput(
                success=bool(result.get("ok", True)),
                data=result,
                error=result.get("error"),
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserRuntimeHealthTool(Tool):
    """Return runtime readiness and heartbeat metadata."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_runtime_health",
                description=_RUNTIME_HEALTH_DESC,
                input_params=_RUNTIME_HEALTH_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del inputs, kwargs
        try:
            data = await self._runtime.runtime_health()
            return ToolOutput(success=True, data=data)
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


def build_browser_runtime_tools(
    runtime: "BrowserAgentRuntime",
    language: str = "cn",
) -> List[Tool]:
    """Build browser helper tools backed by ``BrowserAgentRuntime``.

    By default this returns deterministic helper tools only. The browser subagent
    continues to use Playwright MCP primitive tools directly for low-level browser
    actions.
    """

    return [
        BrowserCancelTool(runtime, language),
        BrowserClearCancelTool(runtime, language),
        BrowserProbeInteractivesTool(runtime, language),
        BrowserProbeCardsTool(runtime, language),
        BrowserBatchInteractTool(runtime, language),
        BrowserCustomActionTool(runtime, language),
        BrowserListActionsTool(runtime, language),
        BrowserRuntimeHealthTool(runtime, language),
    ]
