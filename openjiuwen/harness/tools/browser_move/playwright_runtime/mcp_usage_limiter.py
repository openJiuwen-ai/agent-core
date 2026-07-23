# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared browser MCP usage policy for ReAct and DeepAgent execution paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, deque
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

_READ_ONLY_TOOLS = {
    "browser_console_messages",
    "browser_find",
    "browser_get_element_coordinates",
    "browser_network_requests",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_probe_interactives",
    "browser_probe_cards",
    "browser_probe_form_fields",
    "browser_probe_dropdown",
    "browser_probe_calendar",
}

_PROGRESS_EXEMPT_TOOLS = {
    "browser_cancel_run",
    "browser_clear_cancel",
    "browser_list_custom_actions",
    "browser_runtime_health",
}

_SEMANTIC_MUTATION_TOOLS = {
    "browser_fill_form_semantic",
    "browser_select_dropdown_option",
    "browser_select_calendar_date",
    "browser_batch_interact",
}

_RAW_MUTATING_TOOLS = {
    "browser_click",
    "browser_type",
    "browser_fill_form",
    "browser_select_option",
    "browser_evaluate",
    "browser_run_code",
    "browser_run_code_unsafe",
    "browser_press_key",
}

_PROGRESS_STATE_KEYS = {
    "calendar_closed",
    "checked",
    "completed_steps",
    "display_value",
    "field_date",
    "field_matches_target",
    "field_results",
    "field_value",
    "fields",
    "filled",
    "filled_count",
    "form_values",
    "selected_date",
    "selected_text",
    "selected_texts",
    "selected_value",
    "selected_values",
    "stage",
    "status",
    "value",
    "values",
    "visible_calendar_count",
}

_PROGRESS_IGNORED_KEYS = {
    "elapsed_ms",
    "error",
    "ok",
    "policy",
    "query",
    "reason",
    "request_id",
    "screenshot",
    "session_id",
    "success",
    "timestamp",
    "title",
}


def normalized_browser_tool_name(name: str) -> str:
    """Normalize official Playwright aliases to stable browser tool names."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    known = (
        "browser_console_messages",
        "browser_cancel_run",
        "browser_clear_cancel",
        "browser_find",
        "browser_get_element_coordinates",
        "browser_list_custom_actions",
        "browser_navigate",
        "browser_network_requests",
        "browser_runtime_health",
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


def _targets_overlap(current: set[str], failed: set[str]) -> bool:
    """Return whether two target fingerprint sets identify the same control."""
    for current_value in current:
        for failed_value in failed:
            if current_value == failed_value:
                return True
            if len(current_value) < 6 or len(failed_value) < 6:
                continue
            if current_value in failed_value or failed_value in current_value:
                return True
    return False


def _normalize_progress_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw[:1200]
    if not parts.scheme or not parts.netloc:
        return raw[:1200]
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            query,
            "",
        )
    )[:1200]


def _progress_url_stage(value: str) -> str:
    """Collapse volatile query values so URL cycling is not counted as progress."""
    normalized = _normalize_progress_url(value)
    if not normalized:
        return ""
    try:
        parts = urlsplit(normalized)
    except ValueError:
        return normalized
    query_keys = sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)})
    query = "&".join(query_keys)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, ""))[:1200]


def _result_url(results: Any) -> str:
    for payload in structured_result_payloads(results):
        for key in ("url", "page_url", "current_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _normalize_progress_url(value)
        page = payload.get("page")
        if isinstance(page, dict):
            value = page.get("url")
            if isinstance(value, str) and value.strip():
                return _normalize_progress_url(value)
    text = _result_text(results)
    match = re.search(r"https?://[^\s\]\[\)\(\"'<>]+", text)
    return _normalize_progress_url(match.group(0)) if match else ""


def _prune_progress_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:160]
    if isinstance(value, dict):
        pruned: dict[str, Any] = {}
        for key in sorted(value):
            key_text = str(key)
            if key_text.lower() in _PROGRESS_IGNORED_KEYS:
                continue
            pruned[key_text] = _prune_progress_value(value[key], depth=depth + 1)
        return pruned
    if isinstance(value, (list, tuple)):
        return [_prune_progress_value(item, depth=depth + 1) for item in list(value)[:30]]
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:160]


def _fingerprint_payload(value: Any) -> str:
    compact = _stringify_compact(_prune_progress_value(value), limit=5000)
    if not compact:
        return ""
    return hashlib.sha256(compact.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _observation_fingerprint(results: Any) -> str:
    payloads = structured_result_payloads(results)
    if payloads:
        fingerprint = _fingerprint_payload(payloads)
        if fingerprint:
            return fingerprint
    text = _result_text(results).strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _mutation_state_fingerprint(results: Any) -> str:
    states: list[dict[str, Any]] = []
    for payload in structured_result_payloads(results):
        state = {
            key: _prune_progress_value(payload.get(key))
            for key in _PROGRESS_STATE_KEYS
            if key in payload
        }
        if state:
            states.append(state)
    return _fingerprint_payload(states) if states else ""


class BrowserMcpUsageLimiter:
    """Runtime gate that routes inefficient MCP loops into semantic helpers."""

    _read_only_tools = set(_READ_ONLY_TOOLS)
    _progress_exempt_tools = set(_PROGRESS_EXEMPT_TOOLS)
    _semantic_mutation_tools = set(_SEMANTIC_MUTATION_TOOLS)
    _semantic_tools = _read_only_tools | _semantic_mutation_tools
    _limited_tools = {
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_select_option",
        "browser_wait_for",
        "browser_evaluate",
        "browser_run_code",
        "browser_run_code_unsafe",
        "browser_press_key",
    }
    _raw_mutating_tools = set(_RAW_MUTATING_TOOLS)
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
        self.last_semantic_failure_targets: set[str] = set()
        self.recent_failed_signatures: deque[str] = deque(maxlen=12)
        self.recent_failed_targets: deque[str] = deque(maxlen=20)
        self.recent_raw_failures: deque[bool] = deque(maxlen=8)
        self.semantic_state: dict[str, tuple[str, int]] = {}
        self.no_progress_turns = 0
        self.progress_budget_exhausted = False
        self.strategy_change_required = False
        self.strategy_block_count = 0
        self.strategy_blocked_families: set[str] = set()
        self.recent_tool_families: deque[str] = deque(maxlen=8)
        self.recent_tool_signatures: deque[str] = deque(maxlen=8)
        self.recent_progress_fingerprints: deque[str] = deque(maxlen=24)
        self.seen_progress_urls: deque[str] = deque(maxlen=24)
        self.current_progress_url = ""
        self.observation_progress_count = 0
        self.last_observation_fingerprints: dict[str, str] = {}
        self.last_progress_event = ""

    @property
    def raw_streak_limit(self) -> int:
        """Configured maximum alternating raw primitive streak."""
        try:
            return max(1, int(os.getenv("BROWSER_MCP_RAW_STREAK_LIMIT", "5")))
        except (TypeError, ValueError):
            return 5

    @property
    def progress_soft_limit(self) -> int:
        """Turns without progress before repeated strategy families are blocked."""
        try:
            return max(1, int(os.getenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "5")))
        except (TypeError, ValueError):
            return 5

    @property
    def progress_hard_limit(self) -> int:
        """Turns without progress before all further browser calls are blocked."""
        try:
            configured = int(os.getenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "8"))
        except (TypeError, ValueError):
            configured = 8
        return max(self.progress_soft_limit + 1, configured)

    @property
    def observation_progress_limit(self) -> int:
        """Unique observation changes allowed to reset a stable-page budget."""
        try:
            return max(0, int(os.getenv("BROWSER_TASK_OBSERVATION_PROGRESS_LIMIT", "2")))
        except (TypeError, ValueError):
            return 2

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

    def _tool_family(self, tool_name: str) -> str:
        if tool_name in self._read_only_tools:
            return "observation"
        if tool_name == "browser_navigate":
            return "navigation"
        if tool_name == "browser_batch_interact":
            return "batch"
        if tool_name in self._semantic_mutation_tools:
            return "semantic_mutation"
        if tool_name in self._raw_mutating_tools:
            return "raw_mutation"
        if tool_name == "browser_wait_for":
            return "wait"
        return "other"

    def _soft_repeat_threshold(self) -> int:
        """Exact-call repetitions needed before a strategy family is soft-blocked."""
        return self.progress_soft_limit

    def _strategy_families(self) -> set[str]:
        """Return families containing a sufficiently repeated exact tool signature."""
        recent_families = list(self.recent_tool_families)[-self.progress_soft_limit:]
        recent_signatures = list(self.recent_tool_signatures)[-self.progress_soft_limit:]
        signature_counts = Counter(recent_signatures)
        threshold = self._soft_repeat_threshold()
        return {
            family
            for family, signature in zip(recent_families, recent_signatures)
            if signature_counts[signature] >= threshold
        }

    def _predicted_strategy_families(
        self,
        candidate_family: str,
        candidate_signature: str,
    ) -> set[str]:
        """Return the candidate family only for a repeated exact tool call."""
        history_limit = max(1, self.progress_soft_limit - 1)
        recent_signatures = list(self.recent_tool_signatures)[-history_limit:]
        threshold = self._soft_repeat_threshold()
        if recent_signatures.count(candidate_signature) + 1 < threshold:
            return set()
        return {candidate_family}

    def _predictive_task_progress_blocked_reason(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        """Block a repeated family before its call crosses a progress threshold."""
        if self.progress_budget_exhausted or self.strategy_change_required:
            return self._task_progress_blocked_reason(tool_name)

        predicted_turns = self.no_progress_turns + 1
        if predicted_turns >= self.progress_hard_limit:
            self.no_progress_turns = predicted_turns
            self.progress_budget_exhausted = True
            self.last_progress_event = "task_progress_budget_exhausted"
            return self._task_progress_blocked_reason(tool_name)
        if predicted_turns < self.progress_soft_limit:
            return None

        family = self._tool_family(tool_name)
        signature = self._signature(tool_name, args)
        blocked_families = self._predicted_strategy_families(family, signature)
        if family not in blocked_families:
            return None

        # Count the blocked threshold-crossing attempt once. Runtime after-tool
        # processing ignores limiter-generated results, so it cannot be counted twice.
        self.no_progress_turns = predicted_turns
        self.strategy_change_required = True
        self.strategy_block_count = 0
        self.strategy_blocked_families = blocked_families
        self.last_progress_event = "task_progress_strategy_change_required"
        return self._task_progress_blocked_reason(tool_name)

    def _task_progress_blocked_reason(self, tool_name: str) -> str | None:
        if self.progress_budget_exhausted:
            return (
                "browser_task_progress_budget_exhausted: no meaningful browser progress was observed within "
                f"{self.progress_hard_limit} completed tool turns. Stop calling browser tools and return a compact "
                "partial-progress report with the current page, completed steps, blocking condition, and next step."
            )
        if not self.strategy_change_required:
            return None
        family = self._tool_family(tool_name)
        if family not in self.strategy_blocked_families:
            return None
        self.strategy_block_count += 1
        if self.strategy_block_count >= 2:
            self.progress_budget_exhausted = True
            self.last_progress_event = "task_progress_budget_exhausted"
            return (
                "browser_task_progress_budget_exhausted: the required strategy change was ignored twice. "
                "Stop calling browser tools and return a compact partial-progress report."
            )
        blocked_names = ", ".join(sorted(self.strategy_blocked_families))
        return (
            "browser_task_strategy_change_required: the current browser strategy produced no meaningful progress. "
            f"Do not repeat these tool families now: {blocked_names}. Choose a materially different strategy, "
            "use a target-specific semantic helper, navigate to a genuinely new workflow stage, or report the blocker."
        )

    def _mark_progress(self, fingerprint: str, *, observation: bool = False) -> None:
        _append_unique(self.recent_progress_fingerprints, fingerprint)
        self.no_progress_turns = 0
        self.progress_budget_exhausted = False
        self.strategy_change_required = False
        self.strategy_block_count = 0
        self.strategy_blocked_families.clear()
        self.recent_tool_families.clear()
        self.recent_tool_signatures.clear()
        if observation:
            self.observation_progress_count += 1
        else:
            self.observation_progress_count = 0

    def _mark_no_progress(self) -> None:
        self.no_progress_turns += 1
        if self.no_progress_turns >= self.progress_hard_limit:
            self.progress_budget_exhausted = True
            self.strategy_change_required = False
            self.strategy_blocked_families.clear()
            self.last_progress_event = "task_progress_budget_exhausted"
            return
        if self.no_progress_turns >= self.progress_soft_limit and not self.strategy_change_required:
            blocked_families = self._strategy_families()
            if blocked_families:
                self.strategy_change_required = True
                self.strategy_block_count = 0
                self.strategy_blocked_families = blocked_families
                self.last_progress_event = "task_progress_strategy_change_required"

    def _record_task_progress(self, tool_name: str, args: dict[str, Any], results: Any) -> None:
        self.last_progress_event = ""
        family = self._tool_family(tool_name)
        self.recent_tool_families.append(family)
        self.recent_tool_signatures.append(self._signature(tool_name, args))
        failed = result_failed(results)
        result_url = _result_url(results)
        if not result_url and tool_name == "browser_navigate" and not failed:
            result_url = _normalize_progress_url(str(args.get("url") or ""))

        if result_url:
            self.current_progress_url = result_url
            url_stage = _progress_url_stage(result_url)
            url_fingerprint = f"url:{url_stage}"
            if url_stage and url_stage not in self.seen_progress_urls:
                _append_unique(self.seen_progress_urls, url_stage)
                self._mark_progress(url_fingerprint)
                return

        if not failed and tool_name in self._semantic_mutation_tools:
            state_fingerprint = _mutation_state_fingerprint(results)
            if state_fingerprint:
                target = sorted(_tool_target_fingerprints(args))[:3]
                fingerprint = f"mutation:{tool_name}:{_stringify_compact(target)}:{state_fingerprint}"
                if fingerprint not in self.recent_progress_fingerprints:
                    self._mark_progress(fingerprint)
                    return

        if not failed and tool_name in self._read_only_tools:
            observation_fingerprint = _observation_fingerprint(results)
            observation_key = f"{self.current_progress_url}|{tool_name}"
            previous = self.last_observation_fingerprints.get(observation_key, "")
            self.last_observation_fingerprints[observation_key] = observation_fingerprint
            fingerprint = f"observation:{observation_key}:{observation_fingerprint}"
            if (
                previous
                and observation_fingerprint
                and observation_fingerprint != previous
                and fingerprint not in self.recent_progress_fingerprints
                and self.observation_progress_count < self.observation_progress_limit
            ):
                self._mark_progress(fingerprint, observation=True)
                return

        self._mark_no_progress()

    def blocked_reason(self, tool_call_or_name: Any, tool_args: Any = None) -> str | None:
        """Return a policy error when the next call must be skipped."""
        if not self.enabled:
            return None
        if isinstance(tool_call_or_name, list):
            if len(tool_call_or_name) != 1:
                return None
            tool_call_or_name = tool_call_or_name[0]

        tool_name, args = self._call_parts(tool_call_or_name, tool_args)
        if not tool_name.startswith("browser_") or tool_name in self._progress_exempt_tools:
            return None
        signature = self._signature(tool_name, args)
        target_fingerprints = _tool_target_fingerprints(args)

        if self.progress_budget_exhausted:
            return self._task_progress_blocked_reason(tool_name)

        if tool_name in self._read_only_tools:
            return self._predictive_task_progress_blocked_reason(tool_name, args)

        if tool_name in self._semantic_mutation_tools:
            previous = self.semantic_state.get(signature)
            if previous and previous[1] >= 1:
                return (
                    "semantic_no_progress_limited: the same semantic operation produced the same state twice. "
                    "Stop retrying this field, probe the current state once, or report the exact failure."
                )
            return self._predictive_task_progress_blocked_reason(tool_name, args)

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
            return self._predictive_task_progress_blocked_reason(tool_name, args)

        if (
            self.last_failure_semantic_context
            and self.last_semantic_failure_target_family
            and tool_name in self._raw_mutating_tools
            and _targets_overlap(target_fingerprints, self.last_semantic_failure_targets)
        ):
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

        return self._predictive_task_progress_blocked_reason(tool_name, args)

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
        if not tool_name.startswith("browser_") or tool_name in self._progress_exempt_tools:
            return None
        signature = self._signature(tool_name, args)
        target_fingerprints = _tool_target_fingerprints(args)

        self._record_task_progress(tool_name, args, results)

        if tool_name in self._read_only_tools:
            self.last_tool = ""
            self.last_signature = ""
            self.consecutive = 0
            self.raw_streak = 0
            self.raw_fail_streak = 0
            self.recent_raw_failures.clear()
            return None

        if tool_name in self._semantic_mutation_tools:
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
            target_family = _result_target_family(results) if semantic_failed else ""
            semantic_targets = target_fingerprints | _result_target_fingerprints(results)
            self.last_failure_semantic_context = bool(semantic_failed and target_family and semantic_targets)
            if self.last_failure_semantic_context:
                self.last_semantic_failure_target_family = target_family
                self.last_semantic_failure_targets = set(semantic_targets)
                _append_unique(self.recent_failed_signatures, signature)
                for fingerprint in semantic_targets:
                    _append_unique(self.recent_failed_targets, fingerprint)
            else:
                self.last_semantic_failure_target_family = ""
                self.last_semantic_failure_targets.clear()
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
            self.last_semantic_failure_target_family = ""
            self.last_semantic_failure_targets.clear()
            self.recent_raw_failures.append(True)
            _append_unique(self.recent_failed_signatures, signature)
            for fingerprint in target_fingerprints:
                _append_unique(self.recent_failed_targets, fingerprint)
        elif tool_name in self._raw_interaction_tools:
            self.raw_fail_streak = 0
            self.last_failure_semantic_context = False
            self.last_semantic_failure_target_family = ""
            self.last_semantic_failure_targets.clear()
            self.recent_raw_failures.append(False)
        else:
            self.raw_fail_streak = 0
        return None

    def blocked_payload(self, tool_name: str, reason: str, tool_args: Any = None) -> dict[str, Any]:
        """Build the stable result returned when a call is skipped before dispatch."""
        normalized_name = normalized_browser_tool_name(tool_name)
        if "raw_fallback_after_semantic_widget_failure" in reason:
            reason_code = "raw_fallback_after_semantic_widget_failure"
        elif reason.startswith("semantic_no_progress_limited"):
            reason_code = "semantic_no_progress_limited"
        elif reason.startswith("browser_task_strategy_change_required"):
            reason_code = "task_progress_strategy_change_required"
        elif reason.startswith("browser_task_progress_budget_exhausted"):
            reason_code = "task_progress_budget_exhausted"
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
        if reason_code.startswith("task_progress_"):
            payload["no_progress_turns"] = self.no_progress_turns
            payload["soft_limit"] = self.progress_soft_limit
            payload["hard_limit"] = self.progress_hard_limit
            if self.strategy_blocked_families:
                payload["blocked_families"] = sorted(self.strategy_blocked_families)
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
