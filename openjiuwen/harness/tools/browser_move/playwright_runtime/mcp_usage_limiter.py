# coding: utf-8

"""Shared browser MCP usage policy for ReAct and DeepAgent execution paths."""

from __future__ import annotations

import json
import os
import re
from collections import deque
from typing import Any

from openjiuwen.core.foundation.llm import ToolMessage

from .semantic_routing import compact_json, semantic_route_message

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

_SEMANTIC_FAILURE_TOOLS = {
    "browser_fill_form_semantic",
    "browser_select_dropdown_option",
    "browser_select_calendar_date",
    "browser_batch_interact",
}


def normalized_browser_tool_name(name: str) -> str:
    """Normalize official Playwright aliases to stable browser tool names."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    known = (
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_select_option",
        "browser_wait_for",
        "browser_take_screenshot",
        "browser_evaluate",
        "browser_run_code",
        "browser_run_code_unsafe",
        "browser_press_key",
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


def tool_calls_list(tool_call: Any) -> list[Any]:
    """Return one or many framework tool calls as a list."""
    return tool_call if isinstance(tool_call, list) else [tool_call]


def parse_tool_args(raw: Any) -> dict[str, Any]:
    """Parse raw framework tool arguments into a mapping."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stringify_compact(value: Any, *, limit: int = 4000) -> str:
    return compact_json(value, limit=limit)


def _result_text(results: Any) -> str:
    parts: list[str] = []
    items = results if isinstance(results, list) else [results]
    for item in items:
        values = list(item) if isinstance(item, tuple) else [item]
        for current in values:
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
    return (
        '"ok": false' in result_text
        or '"success": false' in result_text
        or "tool_error" in result_text
    )


def structured_result_payloads(results: Any) -> list[dict[str, Any]]:
    """Extract result dictionaries from tuples, ToolOutput, ToolMessage, and JSON."""
    payloads: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if value is None:
            return
        value_id = id(value)
        if value_id in seen:
            return
        seen.add(value_id)
        if isinstance(value, dict):
            payloads.append(value)
            for child in value.values():
                if isinstance(child, (dict, list, tuple)):
                    visit(child)
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
            return
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except (TypeError, ValueError):
                dumped = None
            if isinstance(dumped, dict):
                visit(dumped)
        for attr in ("data", "content"):
            child = getattr(value, attr, None)
            if child is not None and child is not value:
                visit(child)
        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith("{") and raw.endswith("}"):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    visit(parsed)

    visit(results)
    return payloads


def result_failed(results: Any) -> bool:
    """Return whether a framework result represents a failed tool call."""
    for payload in structured_result_payloads(results):
        if payload.get("ok") is False or payload.get("success") is False:
            return True
        error = payload.get("error")
        if error not in (None, "", False) and payload.get("ok") is not True:
            return True
    return _looks_like_tool_failure(_result_text(results))


def _result_target_fingerprints(results: Any) -> set[str]:
    fingerprints: set[str] = set()
    for payload in structured_result_payloads(results):
        fingerprints.update(_tool_target_fingerprints(payload))
        target_family = payload.get("target_family")
        if isinstance(target_family, str) and target_family.strip():
            fingerprints.add(target_family.strip().lower()[:600])
    return fingerprints


def _result_target_family(results: Any) -> str:
    for payload in structured_result_payloads(results):
        target_family = payload.get("target_family")
        if isinstance(target_family, str) and target_family.strip():
            return target_family.strip()[:1200]
    return ""


def _result_state_fingerprint(results: Any) -> str:
    keys = (
        "ok",
        "error",
        "selected_date",
        "field_value",
        "display_value",
        "selected_value",
        "selected_values",
        "selected_text",
        "selected_texts",
        "steps_ok",
        "steps_failed",
    )
    states: list[dict[str, Any]] = []
    for payload in structured_result_payloads(results):
        state = {key: payload.get(key) for key in keys if key in payload}
        if state:
            states.append(state)
    return _stringify_compact(states, limit=1800)


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
    target_keys = {
        "target",
        "selector",
        "selector_hint",
        "field_selector",
        "resolved_field_selector",
        "trigger_selector",
        "target_family",
        "element",
        "ref",
    }
    fingerprints: set[str] = set()
    for key, value in _iter_argument_strings(args):
        if not value:
            continue
        lower_value = value.lower()
        if key in target_keys:
            fingerprints.add(lower_value[:600])
            if "react-datepicker" in lower_value:
                year_match = re.search(r"\b(19\d{2}|20\d{2})\b", lower_value)
                if "year" in lower_value:
                    suffix = year_match.group(1) if year_match else "*"
                    fingerprints.add(f"calendar-year-control:{suffix}")
                if "month" in lower_value:
                    fingerprints.add("calendar-month-control")
                if "day" in lower_value or "choose " in lower_value:
                    fingerprints.add(f"calendar-day-control:{lower_value[-180:]}")
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


class BrowserMcpUsageLimiter:
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
        "browser_select_option",
        "browser_wait_for",
        "browser_take_screenshot",
        "browser_evaluate",
        "browser_run_code",
        "browser_run_code_unsafe",
        "browser_press_key",
    }
    _raw_interaction_tools = set(_limited_tools)

    def __init__(self) -> None:
        raw_enabled = os.getenv("BROWSER_MCP_USAGE_LIMITS", "1").strip().lower()
        self.enabled = raw_enabled not in {"0", "false", "no", "off"}
        self.reset()

    def reset(self) -> None:
        """Reset per-invocation limiter state while preserving configuration."""
        self.last_tool = ""
        self.last_signature = ""
        self.consecutive = 0
        self.raw_streak = 0
        self.raw_fail_streak = 0
        self.last_failed_signature = ""
        self.last_failure_semantic_context = False
        self.last_semantic_failure_target_family = ""
        self.recent_failed_signatures: deque[str] = deque(maxlen=12)
        self.recent_failed_targets: deque[str] = deque(maxlen=20)
        self.recent_raw_failures: deque[bool] = deque(maxlen=8)
        self.semantic_state: dict[str, tuple[str, int]] = {}

    @property
    def raw_streak_limit(self) -> int:
        """Configured maximum alternating raw primitive streak."""
        try:
            return max(1, int(os.getenv("BROWSER_MCP_RAW_STREAK_LIMIT", "5")))
        except (TypeError, ValueError):
            return 5

    def _limit_for(self, tool_name: str, args: dict[str, Any]) -> int:
        defaults = {
            "browser_snapshot": 1,
            "browser_click": 2,
            "browser_type": 2,
            "browser_fill_form": 1,
            "browser_select_option": 1,
            "browser_wait_for": 1,
            "browser_take_screenshot": 1,
            "browser_evaluate": 3,
            "browser_run_code": 3,
            "browser_run_code_unsafe": 3,
            "browser_press_key": 3,
        }
        if tool_name == "browser_press_key":
            key = str(args.get("key") or "").strip().lower()
            if key == "escape":
                return 2
            if key in {"pagedown", "pageup", "home", "end"}:
                return 3
        env_key = f"BROWSER_MCP_LIMIT_{tool_name.upper()}"
        try:
            return max(0, int(os.getenv(env_key, defaults.get(tool_name, 2))))
        except (TypeError, ValueError):
            return defaults.get(tool_name, 2)

    @staticmethod
    def _signature(tool_name: str, args: dict[str, Any]) -> str:
        return f"{tool_name}:{_stringify_compact(args, limit=1200)}"

    @staticmethod
    def _semantic_route_message(
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

    @staticmethod
    def _call_parts(tool_call_or_name: Any, tool_args: Any = None) -> tuple[str, dict[str, Any]]:
        if isinstance(tool_call_or_name, str):
            return normalized_browser_tool_name(tool_call_or_name), parse_tool_args(tool_args)
        return (
            normalized_browser_tool_name(getattr(tool_call_or_name, "name", "")),
            parse_tool_args(getattr(tool_call_or_name, "arguments", tool_args)),
        )

    def blocked_reason(self, tool_call_or_name: Any, tool_args: Any = None) -> str | None:
        """Return a policy error when the next call must be skipped."""
        if not self.enabled:
            return None
        if isinstance(tool_call_or_name, list):
            if len(tool_call_or_name) != 1:
                return None
            tool_call_or_name = tool_call_or_name[0]

        tool_name, args = self._call_parts(tool_call_or_name, tool_args)
        signature = self._signature(tool_name, args)
        target_fingerprints = _tool_target_fingerprints(args)

        if tool_name in self._semantic_tools:
            previous = self.semantic_state.get(signature)
            if previous and previous[1] >= 1:
                return (
                    "semantic_no_progress_limited: the same semantic operation produced the same state twice. "
                    "Stop retrying this field, probe the current state once, or report the exact failure."
                )
            return None

        if tool_name in {"browser_evaluate", "browser_run_code", "browser_run_code_unsafe"}:
            code = str(args.get("code") or args.get("function") or "").lower()
            broad_patterns = (
                "document.body.innertext",
                "document.body.textcontent",
                "document.documentelement.outerhtml",
                "queryselectorall('*')",
                'queryselectorall("*")',
            )
            if any(pattern in code for pattern in broad_patterns):
                return (
                    "mcp_usage_limited: broad browser code DOM dumps are disabled. "
                    "Use browser_probe_interactives, browser_probe_cards, browser_probe_form_fields, "
                    "browser_probe_dropdown, browser_probe_calendar, or a narrow selector-specific script instead."
                )

        if tool_name not in self._limited_tools:
            return None

        if self.last_failure_semantic_context and tool_name in self._raw_interaction_tools:
            return (
                "mcp_usage_limited: raw_fallback_after_semantic_widget_failure. "
                "The last dropdown/calendar/form interaction failed, so the first raw fallback is blocked. "
                + self._semantic_route_message(
                    tool_name,
                    args,
                    prefer_form_for_field_entry=True,
                )
            )

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

        if tool_name == "browser_press_key":
            next_count = self.consecutive + 1 if signature == self.last_signature else 1
        else:
            next_count = self.consecutive + 1 if tool_name == self.last_tool else 1
        limit = self._limit_for(tool_name, args)
        if limit >= 0 and next_count > limit:
            return (
                f"mcp_usage_limited: repeated {tool_name} calls are limited to {limit} consecutive call(s). "
                + self._semantic_route_message(tool_name, args)
            )

        next_raw_streak = self.raw_streak + 1 if tool_name in self._raw_interaction_tools else 0
        if next_raw_streak > self.raw_streak_limit:
            return (
                f"mcp_usage_limited: raw browser primitive streak exceeded {self.raw_streak_limit} calls. "
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

    def record_result(self, tool_call_or_name: Any, results: Any, tool_args: Any = None) -> str | None:
        """Record a completed call and return an optional diagnostic event name."""
        if isinstance(tool_call_or_name, list):
            if len(tool_call_or_name) != 1:
                self.last_tool = ""
                self.last_signature = ""
                self.consecutive = 0
                self.raw_streak = 0
                return None
            tool_call_or_name = tool_call_or_name[0]

        if any(
            payload.get("policy") == "browser_mcp_usage_limiter"
            for payload in structured_result_payloads(results)
        ):
            return None

        tool_name, args = self._call_parts(tool_call_or_name, tool_args)
        signature = self._signature(tool_name, args)
        target_fingerprints = _tool_target_fingerprints(args)

        if tool_name in self._semantic_tools:
            semantic_failed = result_failed(results)
            result_state = _result_state_fingerprint(results)
            previous_state, repeated = self.semantic_state.get(signature, ("", 0))
            repeated = repeated + 1 if result_state and result_state == previous_state else 0
            self.semantic_state[signature] = (result_state, repeated)
            self.last_tool = ""
            self.last_signature = ""
            self.consecutive = 0
            self.raw_streak = 0
            self.raw_fail_streak = 0
            self.last_failed_signature = ""
            self.last_failure_semantic_context = semantic_failed and tool_name in _SEMANTIC_FAILURE_TOOLS
            if semantic_failed:
                self.last_semantic_failure_target_family = _result_target_family(results)
                _append_unique(self.recent_failed_signatures, signature)
                semantic_targets = target_fingerprints | _result_target_fingerprints(results)
                for fingerprint in semantic_targets:
                    _append_unique(self.recent_failed_targets, fingerprint)
            else:
                self.last_semantic_failure_target_family = ""
            self.recent_raw_failures.clear()
            return "semantic_failure_recorded" if self.last_failure_semantic_context else None

        if tool_name == "browser_press_key":
            same_call = signature == self.last_signature
        else:
            same_call = tool_name == self.last_tool
        if same_call:
            self.consecutive += 1
        else:
            self.consecutive = 1
        self.last_tool = tool_name
        self.last_signature = signature

        if tool_name in self._raw_interaction_tools:
            self.raw_streak += 1
        else:
            self.raw_streak = 0

        failed = result_failed(results)
        if failed and tool_name in self._raw_interaction_tools:
            self.raw_fail_streak += 1
            self.last_failed_signature = signature
            self.last_failure_semantic_context = False
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
        return None

    def blocked_payload(self, tool_name: str, reason: str, tool_args: Any = None) -> dict[str, Any]:
        """Build the stable result returned when a call is skipped before dispatch."""
        normalized_name = normalized_browser_tool_name(tool_name)
        if "raw_fallback_after_semantic_widget_failure" in reason:
            reason_code = "raw_fallback_after_semantic_widget_failure"
        elif reason.startswith("semantic_no_progress_limited"):
            reason_code = "semantic_no_progress_limited"
        elif "raw browser primitive streak exceeded" in reason:
            reason_code = "raw_primitive_streak_limited"
        elif normalized_name == "browser_press_key" and "repeated browser_press_key" in reason:
            reason_code = "repeated_key_limited"
        else:
            reason_code = "mcp_usage_limited"
        payload: dict[str, Any] = {
            "ok": False,
            "error": reason,
            "policy": "browser_mcp_usage_limiter",
            "tool_name": tool_name,
            "normalized_tool_name": normalized_name,
            "reason": reason_code,
        }
        args = parse_tool_args(tool_args)
        if normalized_name == "browser_press_key" and args.get("key"):
            payload["key"] = args["key"]
        if self.last_semantic_failure_target_family:
            payload["target_family"] = self.last_semantic_failure_target_family
        return payload

    def blocked_tool_results(self, tool_call: Any, reason: str) -> list[tuple[Any, ToolMessage]]:
        """Build AbilityManager-compatible blocked results for ReAct workers."""
        results: list[tuple[Any, ToolMessage]] = []
        for call in tool_calls_list(tool_call):
            payload = self.blocked_payload(
                getattr(call, "name", ""),
                reason,
                getattr(call, "arguments", None),
            )
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


__all__ = [
    "BrowserMcpUsageLimiter",
    "normalized_browser_tool_name",
    "parse_tool_args",
    "result_failed",
    "structured_result_payloads",
    "tool_calls_list",
]
