#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Semantic browser progress tracking independent of selectors and DOM churn."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import Any, Dict, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_FILTER_KEY_TOKENS = (
    "filter",
    "sort",
    "order",
    "price",
    "rating",
    "score",
    "star",
    "category",
    "facet",
    "筛选",
    "排序",
    "价格",
    "评分",
    "星级",
    "分类",
)
_MAX_STATE_ITEMS = 32
_MAX_TEXT_LENGTH = 160
_STATE_REVISIT_REPLAN_THRESHOLD = 3
_TRACKING_QUERY_KEYS = {
    "spm",
    "timestamp",
    "ts",
    "cachebuster",
    "_t",
}


def _coerce_tool_args(tool_args: Any) -> Dict[str, Any]:
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, str):
        try:
            parsed = json.loads(tool_args)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _numbers_from_filter_value(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [
            number
            for item in value
            for number in _numbers_from_filter_value(item)
        ][:2]
    if value in (None, ""):
        return []
    return re.findall(r"\d+(?:\.\d+)?", str(value).replace(",", ""))[:2]


def _numeric_filter_value(args: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        numbers = _numbers_from_filter_value(args.get(key))
        if numbers:
            return numbers[0]
    return ""


def price_interval_signature(tool_name: str, tool_args: Any) -> str:
    """Return a cross-site price interval from explicit structured filters."""
    normalized_name = str(tool_name or "").strip().lower()
    if any(token in normalized_name for token in ("evaluate", "run_code")):
        return ""
    args = _coerce_tool_args(tool_args)
    if any(key in args for key in ("function", "expression", "script", "code")):
        return ""

    minimum = _numeric_filter_value(
        args,
        ("min_price", "price_min", "minimum_price", "price_from", "lowest_price"),
    )
    maximum = _numeric_filter_value(
        args,
        ("max_price", "price_max", "maximum_price", "price_to", "highest_price"),
    )
    unbounded_values: list[str] = []
    explicit_range = args.get("price_range") or args.get("price_interval")
    if explicit_range not in (None, ""):
        unbounded_values.extend(_numbers_from_filter_value(explicit_range))

    steps = args.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            operation = str(step.get("op") or "").strip().lower()
            if operation not in {
                "fill",
                "type",
                "autocomplete",
                "select_option",
                "select_visible_text",
            }:
                continue
            descriptor = " ".join(
                str(step.get(key) or "")
                for key in ("field", "name", "label", "placeholder", "description")
            ).lower()
            if not re.search(r"price|amount|价格|价位", descriptor, re.IGNORECASE):
                continue
            raw_value = next(
                (
                    step.get(key)
                    for key in ("value", "text_value", "option_value", "option_text", "values")
                    if step.get(key) not in (None, "")
                ),
                None,
            )
            numbers = _numbers_from_filter_value(raw_value)
            if not numbers:
                continue
            if re.search(r"min|minimum|from|low|最低|起始", descriptor, re.IGNORECASE):
                minimum = minimum or numbers[0]
            elif re.search(r"max|maximum|to|high|最高|截止", descriptor, re.IGNORECASE):
                maximum = maximum or numbers[-1]
            else:
                unbounded_values.extend(numbers)

    if not minimum and unbounded_values:
        minimum = unbounded_values[0]
    if not maximum and len(unbounded_values) >= 2:
        maximum = unbounded_values[1]
    if not minimum and not maximum:
        return ""
    return f"{minimum or '*'}:{maximum or '*'}"


def _compact_text(value: Any, limit: int = _MAX_TEXT_LENGTH) -> str:
    return " ".join(str(value or "").split())[:limit]


def _canonical_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        query_items = [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
        ]
        query = urlencode(sorted(query_items))
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, query, ""))
    except ValueError:
        return raw.split("#", 1)[0]


def _normalize_named_values(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, Mapping):
        source: Iterable[Any] = ({"key": key, "value": item} for key, item in value.items())
    elif isinstance(value, list):
        source = value
    else:
        source = ()

    normalized: list[Dict[str, Any]] = []
    for item in source:
        if isinstance(item, Mapping):
            key = _compact_text(
                item.get("key") or item.get("name") or item.get("label") or item.get("id") or item.get("selector")
            )
            raw_value = item.get("value")
            if raw_value is None:
                raw_value = item.get("text")
            if raw_value is None:
                raw_value = item.get("checked")
        else:
            key = _compact_text(item)
            raw_value = True
        if not key:
            continue
        if isinstance(raw_value, list):
            normalized_value: Any = sorted(_compact_text(entry) for entry in raw_value if _compact_text(entry))[:8]
        elif isinstance(raw_value, bool):
            normalized_value = raw_value
        else:
            normalized_value = _compact_text(raw_value)
        normalized.append({"key": key.lower(), "value": normalized_value})
        if len(normalized) >= _MAX_STATE_ITEMS:
            break
    normalized.sort(key=lambda item: (item["key"], str(item["value"])))
    return normalized


def _normalize_result_count(value: Any) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("value")
    if value in (None, ""):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def build_semantic_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the stable state used for progress and loop decisions."""
    supplied = state.get("semantic_state")
    source = supplied if isinstance(supplied, Mapping) else state
    url = _canonical_url(source.get("url") or state.get("url"))
    form_values = _normalize_named_values(source.get("form_values"))
    selected_filters = _normalize_named_values(source.get("selected_filters"))
    field_coverage = sorted(
        {
            _compact_text(field, 80).lower()
            for field in (source.get("field_coverage") or state.get("field_coverage") or [])
            if _compact_text(field, 80)
        }
    )[:_MAX_STATE_ITEMS]
    return {
        "url": url,
        "form_values": form_values,
        "selected_filters": selected_filters,
        "result_count": _normalize_result_count(source.get("result_count")),
        "field_coverage": field_coverage,
    }


def _digest(value: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _filter_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    filter_query: list[tuple[str, str]] = []
    try:
        query_items = parse_qsl(urlsplit(str(state.get("url") or "")).query, keep_blank_values=True)
    except ValueError:
        query_items = []
    for key, value in query_items:
        if any(token in key.lower() for token in _FILTER_KEY_TOKENS):
            filter_query.append((key.lower(), value))

    filter_forms = [
        item
        for item in state.get("form_values") or []
        if any(token in str(item.get("key") or "") for token in _FILTER_KEY_TOKENS)
    ]
    return {
        "query": sorted(filter_query),
        "form_values": filter_forms,
        "selected_filters": state.get("selected_filters") or [],
    }


class SemanticStateTracker:
    """Detect semantic no-progress and state revisits across browser mutations."""

    def __init__(self, *, history_size: int = 16) -> None:
        self._history: deque[str] = deque(maxlen=max(4, int(history_size)))
        self._filter_history: deque[str] = deque(maxlen=max(4, int(history_size)))
        self._last_state: Dict[str, Any] | None = None
        self._consecutive_no_progress = 0
        self._state_revisit_count = 0
        self._revision = 0
        self._replan_required = False
        self._latest: Dict[str, Any] = {}
        self._field_coverage: set[str] = set()
        self._last_action_group_id = ""

    @property
    def latest(self) -> Dict[str, Any]:
        """Return a copy of the latest compact observation."""
        return dict(self._latest)

    @property
    def current_state(self) -> Dict[str, Any]:
        """Return the latest normalized semantic state without progress metadata."""
        return dict(self._last_state or {})

    def acknowledge_replan(self) -> None:
        """Allow one materially different action after a forced replan."""
        self._replan_required = False
        if self._latest:
            self._latest["replan_required"] = False

    def observe(
        self,
        raw_state: Mapping[str, Any],
        *,
        action_group_id: str = "",
    ) -> Dict[str, Any]:
        """Record one model action group and return its progress classification."""
        normalized_group_id = str(action_group_id or "").strip()
        if normalized_group_id and normalized_group_id == self._last_action_group_id:
            return self.latest
        semantic_state = build_semantic_state(raw_state)
        self._field_coverage.update(semantic_state.get("field_coverage") or [])
        semantic_state["field_coverage"] = sorted(self._field_coverage)
        state_digest = _digest(semantic_state)
        filter_digest = _digest(_filter_state(semantic_state))
        previous_digest = self._history[-1] if self._history else ""
        repeated_state = bool(previous_digest and state_digest == previous_digest)
        state_revisit = bool(not repeated_state and state_digest in self._history)
        aba_loop = bool(
            len(self._history) >= 2 and state_digest == self._history[-2] and state_digest != self._history[-1]
        )
        repeated_filter_state = bool(
            self._filter_history and filter_digest in self._filter_history and filter_digest != self._filter_history[-1]
        )

        if not self._history:
            progress = "initial"
        elif repeated_state:
            progress = "no_progress"
            self._consecutive_no_progress += 1
        elif state_revisit or repeated_filter_state:
            progress = "state_revisit"
            self._consecutive_no_progress += 1
            self._state_revisit_count += 1
        else:
            progress = "progress"
            self._consecutive_no_progress = 0
            self._state_revisit_count = 0

        self._history.append(state_digest)
        self._filter_history.append(filter_digest)
        self._last_state = semantic_state
        self._revision += 1

        reasons: list[str] = []
        if self._consecutive_no_progress >= 3:
            reasons.append("three_consecutive_no_progress_states")
        if self._state_revisit_count >= _STATE_REVISIT_REPLAN_THRESHOLD:
            reasons.append("three_semantic_state_revisits")
        if reasons:
            self._replan_required = True

        if normalized_group_id:
            self._last_action_group_id = normalized_group_id
        self._latest = {
            "revision": self._revision,
            "action_group_id": normalized_group_id,
            "semantic_state": semantic_state,
            "progress": progress,
            "observable_progress": progress in {"initial", "progress"},
            "consecutive_no_progress": self._consecutive_no_progress,
            "state_revisit": state_revisit,
            "state_revisit_count": self._state_revisit_count,
            "aba_loop": aba_loop,
            "repeated_filter_state": repeated_filter_state,
            "replan_required": self._replan_required,
            "replan_reason": reasons,
        }
        return self.latest

    def reset(self) -> None:
        """Clear task-local semantic history."""
        self._history.clear()
        self._filter_history.clear()
        self._last_state = None
        self._consecutive_no_progress = 0
        self._state_revisit_count = 0
        self._revision = 0
        self._replan_required = False
        self._latest = {}
        self._field_coverage.clear()
        self._last_action_group_id = ""


__all__ = ["SemanticStateTracker", "build_semantic_state"]
