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
    "Query the runtime's shared, versioned page index for visible high-value interactive elements. "
    "Use this for page-level controls such as buttons, links, inputs, forms, navigation, login, "
    "pagination, menus, and visible actions. The optional query filter is alias-aware for common "
    "search/input terms, including Chinese text such as 搜索/关键词. Repeated-item controls include "
    "their containing group and item context. Pass scope_group_id and scope_item_index from a card "
    "result to retrieve controls for one repeated item without another DOM scan. Prefer max_items "
    "around 20-30. For listing data, use browser_probe_cards first."
)
_PROBE_INTERACTIVES_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_items": {
            "type": "integer",
            "description": "Maximum number of elements to return. Default 50, hard-capped at 100.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only return elements currently visible in the viewport. Default true.",
        },
        "query": {
            "type": "string",
            "description": "Optional text filter, including text from a containing repeated item.",
        },
        "scope_group_id": {
            "type": "string",
            "description": "Optional group_id returned by browser_probe_cards.",
        },
        "scope_item_index": {
            "type": "integer",
            "description": "Optional zero-based item index within scope_group_id.",
        },
    },
    "required": [],
}

_PROBE_CARDS_DESC = (
    "Query the shared page index for repeated card, row, article, product, or result-item groups. "
    "The probe detects duplicate sibling structures first, ranks groups, parses at most three "
    "representatives, compiles relative field paths, and applies that schema to the remaining items. "
    "Use it first on product, marketplace, search-result, catalog, article-list, table, and list pages. "
    "The default compact result includes the selected group, essential card fields, interaction hints, "
    "and page-index diagnostics. Use diagnostics_level='standard' for attempted groups or 'debug' for "
    "recurring signatures and cache selectors. Prefer the compact result over snapshots or DOM dumps."
)
_PROBE_CARDS_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_cards": {
            "type": "integer",
            "description": "Maximum number of cards to return. Default 20, hard-capped at 50.",
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
        "diagnostics_level": {
            "type": "string",
            "enum": ["compact", "standard", "debug"],
            "description": "Diagnostic detail level. Default compact.",
        },
    },
    "required": [],
}



_PROBE_FORM_FIELDS_DESC = (
    "Return compact visible form-field metadata before filling passenger/contact/profile forms. "
    "Use this before browser_batch_interact when field selectors are not already known, especially on "
    "booking, checkout, login, registration, passport, nationality, country, date-of-birth, and contact forms. "
    "The result includes label, placeholder, aria label, name/id/testid, type, value, required/disabled/readonly, "
    "selector_hint, bbox, and native select options without dumping the whole DOM."
)
_PROBE_FORM_FIELDS_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_fields": {
            "type": "integer",
            "description": "Maximum fields to return. Default 80, hard-capped at 160.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only return visible fields in the viewport. Default true.",
        },
        "query": {
            "type": "string",
            "description": "Optional field filter, e.g. 'passport', 'birth', 'nationality', 'contact'.",
        },
        "include_options": {
            "type": "boolean",
            "description": "When true, include visible options for native select elements. Default true.",
        },
    },
    "required": [],
}

_FILL_FORM_SEMANTIC_DESC = (
    "Fill visible form fields by semantic field names, using compact field matching inside the page. "
    "Use this after browser_probe_form_fields or when passenger/contact/profile fields are ordinary visible "
    "inputs but raw Playwright refs are stale. Pass a fields object such as "
    "{'given name': 'Alex', 'surname': 'Tan', 'email': 'test@example.com'}. "
    "For dropdown-style fields, use browser_select_dropdown_option instead."
)
_FILL_FORM_SEMANTIC_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "object",
            "description": "Mapping from semantic field name to value, e.g. {'given name': 'Alex'}.",
            "properties": {},
            "required": [],
        },
        "max_fields": {
            "type": "integer",
            "description": "Maximum visible fields to consider. Default 120, hard-capped at 200.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only consider fields visible in the viewport. Default true.",
        },
        "clear_existing": {
            "type": "boolean",
            "description": "When true, replace existing values. Default true.",
        },
    },
    "required": ["fields"],
}

_PROBE_DROPDOWN_DESC = (
    "Return compact visible options from an open dropdown, autocomplete, combobox, listbox, "
    "menu, or suggestion popup. Use immediately after focusing/typing into dropdown-style fields "
    "such as airports, countries, passenger titles, date-of-birth month/year, and search suggestions. "
    "Prefer this over browser_snapshot for dynamic dropdown overlays. The result includes option text, "
    "role, disabled/selected flags, bbox, selector_hint, and scoring against the optional query."
)
_PROBE_DROPDOWN_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_options": {
            "type": "integer",
            "description": "Maximum options to return. Default 30, hard-capped at 80.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only return options visible in the viewport. Default true.",
        },
        "query": {
            "type": "string",
            "description": "Optional option text/query filter, e.g. 'Kuala Lumpur' or 'Singapore'.",
        },
    },
    "required": [],
}

_SELECT_DROPDOWN_DESC = (
    "Atomically select one or more options from a dropdown/autocomplete/combobox. Use for dynamic option "
    "lists instead of repeated browser_click/browser_type turns. It supports native selects, Select2, "
    "custom comboboxes, and additive native multi-select widgets. Pass field_selector or field_label to identify "
    "the field, then query/option_text or option_texts for the choices. It returns selection and verification metadata."
)
_SELECT_DROPDOWN_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "field_selector": {
            "type": "string",
            "description": "Optional selector for the native select, input, combobox, or visible trigger.",
        },
        "field_label": {
            "type": "string",
            "description": "Optional visible field label when no stable selector is available.",
        },
        "query": {
            "type": "string",
            "description": "Text to type into the field before selecting, e.g. airport/city name.",
        },
        "option_text": {
            "type": "string",
            "description": "Desired visible option text. Defaults to query when omitted.",
        },
        "option_texts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of visible option texts for atomic native multi-select selection.",
        },
        "exact": {
            "type": "boolean",
            "description": "When true, require exact text match. Default false.",
        },
        "preserve_existing": {
            "type": "boolean",
            "description": "For native multi-selects, preserve existing selections. Default true.",
        },
        "selection_mode": {
            "type": "string",
            "enum": ["add", "replace"],
            "description": "For native multi-selects, add to or replace existing selections. Default add.",
        },
        "timeout_ms": {
            "type": "integer",
            "description": "Timeout for field interaction. Default 5000, hard-capped at 30000.",
        },
        "wait_after_type_ms": {
            "type": "integer",
            "description": "Wait after typing before selecting options. Default 250, max 5000.",
        },
    },
    "required": [],
}

_PROBE_CALENDAR_DESC = (
    "Return compact visible date-picker/calendar state: visible months and normalized day cells. "
    "Use after opening a calendar/date picker to avoid ambiguous clicks on bare day numbers such as '15'. "
    "The result includes ISO date, day/month/year, disabled/selected/outside-month flags, bbox, "
    "selector_hint, and month label. Prefer this over browser_snapshot for calendar overlays."
)
_PROBE_CALENDAR_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_days": {
            "type": "integer",
            "description": "Maximum day cells to return. Default 120, hard-capped at 240.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only return visible day cells. Default true.",
        },
        "query": {
            "type": "string",
            "description": "Optional task/date hint retained in result for traceability.",
        },
    },
    "required": [],
}

_SELECT_CALENDAR_DESC = (
    "Atomically set/select an exact ISO date from a date input or open calendar widget. "
    "Use this for travel/booking calendars and date-of-birth calendars instead of clicking month/day refs. "
    "It opens field_selector if provided, optionally tries direct input, navigates previous/next months, "
    "clicks the matching enabled date cell, closes the calendar overlay, and returns verification metadata."
)
_SELECT_CALENDAR_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "description": "Required target date in ISO form YYYY-MM-DD, e.g. 2026-07-15.",
        },
        "field_selector": {
            "type": "string",
            "description": "Optional selector for the date input/field to open before selection.",
        },
        "next_selector": {
            "type": "string",
            "description": "Optional stable selector for the calendar next-month button.",
        },
        "prev_selector": {
            "type": "string",
            "description": "Optional stable selector for the calendar previous-month button.",
        },
        "max_month_clicks": {
            "type": "integer",
            "description": "Maximum month navigation clicks. Default 18, hard-capped at 60.",
        },
        "timeout_ms": {
            "type": "integer",
            "description": "Timeout for opening the field. Default 5000, hard-capped at 30000.",
        },
        "try_direct_input": {
            "type": "boolean",
            "description": "When true, try setting a non-readonly input value before calendar clicking. Default true.",
        },
    },
    "required": ["date"],
}

_BATCH_INTERACT_DESC = (
    "Execute multiple deterministic browser interactions in one standalone runtime tool call. "
    "Use after browser_probe_interactives/browser_probe_cards when a page-level flow has multiple "
    "known targets, such as three or more form fields, click+type+choose autocomplete, dropdown or "
    "date-picker selection, filter panels, search submit plus result wait, or compact extraction. "
    "For a single dropdown/calendar field, prefer browser_select_dropdown_option or "
    "browser_select_calendar_date first. "
    "This is a first-class helper like the probe tools; do not route this through browser_custom_action. "
    "Do not use it for a single uncertain click; keep using browser_fill_form for ordinary visible "
    "text fields when that official tool is enough."
)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)

_BATCH_INTERACT_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "description": (
                "Ordered browser steps to execute in one batch. Supported ops: click, fill, type, "
                "autocomplete, select_visible_text, select_dropdown_option, select_calendar_date, press, "
                "select_option, set_checked, wait_for_selector, wait_for_text, wait_for_load_state, sleep, "
                "extract_value, screenshot."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "description": "Step operation name.",
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector, preferably selector_hint from probes.",
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
                            "Visible option text for autocomplete/select_visible_text/native select "
                            "label."
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
                        "description": (
                            "ARIA role for an autocomplete/dropdown option, e.g. option/menuitem."
                        ),
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
                            "MCP/Playwright-style native select values list. Also accepted for "
                            "single-select fields."
                        ),
                    },
                    "option_label": {
                        "type": "string",
                        "description": (
                            "Native select visible label. Alias for option_text when "
                            "op=select_option."
                        ),
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
                            "Wait state for wait_for_selector/load_state, e.g. "
                            "visible/attached/domcontentloaded."
                        ),
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
                    "wait_after_type_ms": {
                        "type": "integer",
                        "description": (
                            "Optional wait after autocomplete typing before choosing an option."
                        ),
                    },
                    "wait_after_ms": {
                        "type": "integer",
                        "description": "Optional wait after this step succeeds.",
                    },
                    "ms": {
                        "type": "integer",
                        "description": "Sleep duration for op=sleep.",
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
            "description": (
                "Default per-step timeout in milliseconds. Default 5000, clamped to 250..30000."
            ),
        },
        "wait_after_each_ms": {
            "type": "integer",
            "description": "Optional short wait after each successful step, clamped to 0..5000.",
        },
        "continue_on_error": {
            "type": "boolean",
            "description": "When true, continue after failed steps and return per-step errors.",
        },
        "global_timeout_ms": {
            "type": "integer",
            "description": (
                "Hard timeout for the whole batch. Default is computed from step count, capped at "
                "90000."
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
    "required": ["steps"],
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
            max_items = int(inputs.get("max_items", 50))
        except (TypeError, ValueError):
            max_items = 50
        max_items = max(1, min(100, max_items))

        viewport_only_raw = inputs.get("viewport_only", True)
        if isinstance(viewport_only_raw, str):
            viewport_only = viewport_only_raw.strip().lower() not in {"0", "false", "no"}
        else:
            viewport_only = bool(viewport_only_raw)

        query = str(inputs.get("query") or "").strip()
        scope_group_id = str(inputs.get("scope_group_id") or "").strip()
        scope_item_index_raw = inputs.get("scope_item_index")
        try:
            scope_item_index = (
                max(0, int(scope_item_index_raw))
                if scope_item_index_raw is not None
                else None
            )
        except (TypeError, ValueError):
            scope_item_index = None

        try:
            data = await self._runtime.probe_interactives(
                max_items=max_items,
                viewport_only=viewport_only,
                query=query,
                scope_group_id=scope_group_id,
                scope_item_index=scope_item_index,
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
            max_cards = int(inputs.get("max_cards", 20))
        except (TypeError, ValueError):
            max_cards = 20
        max_cards = max(1, min(50, max_cards))

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
        diagnostics_level = str(
            inputs.get("diagnostics_level") or "compact"
        ).strip().lower()
        if diagnostics_level not in {"compact", "standard", "debug"}:
            diagnostics_level = "compact"

        try:
            data = await self._runtime.probe_cards(
                max_cards=max_cards,
                viewport_only=viewport_only,
                include_buttons=include_buttons,
                query=query,
                diagnostics_level=diagnostics_level,
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




class BrowserProbeFormFieldsTool(Tool):
    """Compact visible form-field probe."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_probe_form_fields",
                description=_PROBE_FORM_FIELDS_DESC,
                input_params=_PROBE_FORM_FIELDS_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        max_fields = inputs.get("max_fields", 80)
        try:
            max_fields_int = max(1, min(160, int(max_fields)))
        except (TypeError, ValueError):
            max_fields_int = 80
        viewport_only = _coerce_bool(inputs.get("viewport_only"), True)
        include_options = _coerce_bool(inputs.get("include_options"), True)

        try:
            result = await self._runtime.probe_form_fields(
                max_fields=max_fields_int,
                viewport_only=viewport_only,
                query=str(inputs.get("query") or ""),
                include_options=include_options,
            )
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None



class BrowserFillFormSemanticTool(Tool):
    """Fill visible form fields using semantic field names."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_fill_form_semantic",
                description=_FILL_FORM_SEMANTIC_DESC,
                input_params=_FILL_FORM_SEMANTIC_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        fields = inputs.get("fields")
        if not isinstance(fields, dict) or not fields:
            return ToolOutput(success=False, error="fields must be a non-empty object")
        max_fields = inputs.get("max_fields", 120)
        try:
            max_fields_int = max(1, min(200, int(max_fields)))
        except (TypeError, ValueError):
            max_fields_int = 120

        try:
            result = await self._runtime.fill_form_semantic(
                fields=fields,
                max_fields=max_fields_int,
                viewport_only=_coerce_bool(inputs.get("viewport_only"), True),
                clear_existing=_coerce_bool(inputs.get("clear_existing"), True),
            )
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserProbeDropdownTool(Tool):
    """Compact visible-dropdown probe."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_probe_dropdown",
                description=_PROBE_DROPDOWN_DESC,
                input_params=_PROBE_DROPDOWN_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        max_options = inputs.get("max_options", 30)
        try:
            max_options_int = max(1, min(80, int(max_options)))
        except (TypeError, ValueError):
            max_options_int = 30
        viewport_only = inputs.get("viewport_only", True)
        if isinstance(viewport_only, str):
            viewport_only = viewport_only.strip().lower() not in {"0", "false", "no"}

        try:
            result = await self._runtime.probe_dropdown(
                max_options=max_options_int,
                viewport_only=bool(viewport_only),
                query=str(inputs.get("query") or ""),
            )
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserSelectDropdownOptionTool(Tool):
    """Select a dropdown/autocomplete option atomically."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_select_dropdown_option",
                description=_SELECT_DROPDOWN_DESC,
                input_params=_SELECT_DROPDOWN_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        try:
            result = await self._runtime.select_dropdown_option(
                field_selector=str(inputs.get("field_selector") or ""),
                field_label=str(inputs.get("field_label") or ""),
                query=str(inputs.get("query") or ""),
                option_text=str(inputs.get("option_text") or ""),
                option_texts=[str(item) for item in inputs.get("option_texts", []) if str(item).strip()]
                if isinstance(inputs.get("option_texts"), list)
                else None,
                exact=_coerce_bool(inputs.get("exact"), False),
                preserve_existing=_coerce_bool(inputs.get("preserve_existing"), True),
                selection_mode=str(inputs.get("selection_mode") or "add"),
                timeout_ms=inputs.get("timeout_ms", 5000),
                wait_after_type_ms=inputs.get("wait_after_type_ms", 250),
            )
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserProbeCalendarTool(Tool):
    """Compact visible-calendar probe."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_probe_calendar",
                description=_PROBE_CALENDAR_DESC,
                input_params=_PROBE_CALENDAR_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        max_days = inputs.get("max_days", 120)
        try:
            max_days_int = max(1, min(240, int(max_days)))
        except (TypeError, ValueError):
            max_days_int = 120
        viewport_only = inputs.get("viewport_only", True)
        if isinstance(viewport_only, str):
            viewport_only = viewport_only.strip().lower() not in {"0", "false", "no"}

        try:
            result = await self._runtime.probe_calendar(
                max_days=max_days_int,
                viewport_only=bool(viewport_only),
                query=str(inputs.get("query") or ""),
            )
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserSelectCalendarDateTool(Tool):
    """Select an exact calendar date atomically."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        del language
        super().__init__(
            ToolCard(
                name="browser_select_calendar_date",
                description=_SELECT_CALENDAR_DESC,
                input_params=_SELECT_CALENDAR_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        date = str(inputs.get("date") or "").strip()
        if not date:
            return ToolOutput(success=False, error="date is required")
        try:
            result = await self._runtime.select_calendar_date(
                date=date,
                field_selector=str(inputs.get("field_selector") or ""),
                next_selector=str(inputs.get("next_selector") or ""),
                prev_selector=str(inputs.get("prev_selector") or ""),
                max_month_clicks=inputs.get("max_month_clicks", 18),
                timeout_ms=inputs.get("timeout_ms", 5000),
                try_direct_input=_coerce_bool(inputs.get("try_direct_input"), True),
            )
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
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
                timeout_ms=inputs.get("timeout_ms"),
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
        BrowserProbeFormFieldsTool(runtime, language),
        BrowserFillFormSemanticTool(runtime, language),
        BrowserProbeDropdownTool(runtime, language),
        BrowserSelectDropdownOptionTool(runtime, language),
        BrowserProbeCalendarTool(runtime, language),
        BrowserSelectCalendarDateTool(runtime, language),
        BrowserBatchInteractTool(runtime, language),
        BrowserCustomActionTool(runtime, language),
        BrowserListActionsTool(runtime, language),
        BrowserRuntimeHealthTool(runtime, language),
    ]
