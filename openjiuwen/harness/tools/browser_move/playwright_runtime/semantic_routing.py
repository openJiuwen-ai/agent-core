# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Semantic routing helpers for browser MCP fallback policy.

This module deliberately avoids scanning raw JSON text for substrings.  Instead,
it extracts structured field signals from tool arguments, tokenizes field-like
values, and maps those signals to a small route registry.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable

_FIELD_VALUE_KEYS = {
    "aria-label",
    "aria_label",
    "data-testid",
    "field",
    "field_selector",
    "id",
    "label",
    "name",
    "option_text",
    "placeholder",
    "query",
    "selector",
    "selector_hint",
    "text",
    "testid",
    "value",
}
_ROLE_KEYS = {"aria_role", "role"}
_INPUT_TYPE_KEYS = {"input_type", "type"}
_GENERIC_SELECTOR_TOKENS = {
    "aria",
    "button",
    "class",
    "data",
    "div",
    "form",
    "has",
    "href",
    "input",
    "label",
    "li",
    "nth",
    "placeholder",
    "ref",
    "role",
    "span",
    "testid",
    "text",
    "type",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class SemanticSignal:
    """Structured browser-field signal extracted from a tool call."""

    tokens: frozenset[str]
    roles: frozenset[str]
    input_types: frozenset[str]


@dataclass(frozen=True)
class SemanticRoute:
    """A semantic fallback route from low-level MCP usage to helper tools."""

    name: str
    tools: tuple[str, ...]
    message: str
    tokens: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    input_types: frozenset[str] = frozenset()

    def score(self, signal: SemanticSignal) -> int:
        token_hits = len(self.tokens & signal.tokens)
        role_hits = len(self.roles & signal.roles)
        input_type_hits = len(self.input_types & signal.input_types)
        return token_hits + (role_hits * 3) + (input_type_hits * 3)


CALENDAR_ROUTE = SemanticRoute(
    name="calendar",
    tools=("browser_probe_calendar", "browser_select_calendar_date"),
    message="Use browser_probe_calendar/browser_select_calendar_date for date-picker/calendar/date fields.",
    tokens=frozenset({
        "birth",
        "birthday",
        "calendar",
        "date",
        "datepicker",
        "dob",
        "expiration",
        "expiry",
        "month",
        "year",
    }),
    input_types=frozenset({"date", "datetime", "datetime-local", "month"}),
)
DROPDOWN_ROUTE = SemanticRoute(
    name="dropdown",
    tools=("browser_probe_dropdown", "browser_select_dropdown_option"),
    message="Use browser_probe_dropdown/browser_select_dropdown_option for dropdown/autocomplete fields.",
    tokens=frozenset({
        "airport",
        "arrival",
        "autocomplete",
        "combobox",
        "country",
        "departure",
        "destination",
        "dropdown",
        "gender",
        "listbox",
        "nationality",
        "origin",
        "region",
        "return",
        "title",
    }),
    roles=frozenset({"combobox", "listbox", "option"}),
)
FORM_ROUTE = SemanticRoute(
    name="form",
    tools=("browser_probe_form_fields", "browser_fill_form_semantic", "browser_batch_interact"),
    message=(
        "Use browser_probe_form_fields first, then browser_fill_form_semantic or "
        "browser_batch_interact with verified selector_hint values."
    ),
    tokens=frozenset({
        "address",
        "contact",
        "document",
        "email",
        "firstname",
        "first",
        "given",
        "id",
        "lastname",
        "last",
        "name",
        "passenger",
        "passport",
        "phone",
        "surname",
        "traveler",
        "traveller",
    }),
    roles=frozenset({"textbox"}),
    input_types=frozenset({"email", "number", "password", "tel", "text"}),
)
SEMANTIC_ROUTES = (CALENDAR_ROUTE, DROPDOWN_ROUTE, FORM_ROUTE)
_FORM_ENTRY_TOOLS = {"browser_type", "browser_fill_form"}


def _insert_identifier_boundaries(text: str) -> str:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return re.sub(r"[^A-Za-z0-9]+", " ", separated)


def _tokens_from_text(text: str) -> set[str]:
    normalized = _insert_identifier_boundaries(text).lower()
    return {token for token in _TOKEN_RE.findall(normalized) if token not in _GENERIC_SELECTOR_TOKENS}


def _iter_signal_strings(value: Any, parent_key: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).replace("_", "-").lower()
            yield from _iter_signal_strings(child, key_text)
        return
    if isinstance(value, list):
        for child in value:
            yield from _iter_signal_strings(child, parent_key)
        return
    if not isinstance(value, str):
        return

    if parent_key in _FIELD_VALUE_KEYS or parent_key in _ROLE_KEYS or parent_key in _INPUT_TYPE_KEYS:
        yield parent_key, value


def extract_semantic_signal(args: dict[str, Any]) -> SemanticSignal:
    """Extract token, role, and input-type signals from browser tool arguments."""

    tokens: set[str] = set()
    roles: set[str] = set()
    input_types: set[str] = set()
    for key, value in _iter_signal_strings(args):
        value_text = value.strip()
        if not value_text:
            continue
        normalized_value = value_text.lower().strip()
        if key in _ROLE_KEYS:
            roles.update(_tokens_from_text(normalized_value))
        elif key in _INPUT_TYPE_KEYS:
            input_types.update(_tokens_from_text(normalized_value))
        else:
            tokens.update(_tokens_from_text(value_text))
    return SemanticSignal(
        tokens=frozenset(tokens),
        roles=frozenset(roles),
        input_types=frozenset(input_types),
    )


def classify_semantic_route(signal: SemanticSignal) -> SemanticRoute | None:
    """Return the best route for a semantic signal, or None if no route matches."""

    scored = [(route.score(signal), route) for route in SEMANTIC_ROUTES]
    scored = [item for item in scored if item[0] > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def has_semantic_route(args: dict[str, Any]) -> bool:
    """Return whether args contain enough signal for a semantic fallback route."""

    return classify_semantic_route(extract_semantic_signal(args)) is not None


def semantic_route_message(
    tool_name: str,
    args: dict[str, Any],
    *,
    prefer_form_for_field_entry: bool = False,
) -> str:
    """Build a fallback instruction for a blocked low-level browser call."""

    signal = extract_semantic_signal(args)
    route = classify_semantic_route(signal)
    if prefer_form_for_field_entry and tool_name in _FORM_ENTRY_TOOLS:
        explicit_calendar_tokens = {"calendar", "datepicker"}
        explicit_dropdown_tokens = {"autocomplete", "combobox", "dropdown", "listbox"}
        if signal.input_types & CALENDAR_ROUTE.input_types or signal.tokens & explicit_calendar_tokens:
            route = CALENDAR_ROUTE
        elif signal.roles & DROPDOWN_ROUTE.roles or signal.tokens & explicit_dropdown_tokens:
            route = DROPDOWN_ROUTE
        else:
            route = FORM_ROUTE
    if route is not None:
        return route.message
    return (
        "Use browser_probe_interactives/browser_probe_form_fields and then browser_fill_form_semantic "
        "or the matching semantic helper."
    )


def compact_json(value: Any, *, limit: int = 4000) -> str:
    """Compact JSON/string utility shared by limiter tests and diagnostics."""

    try:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:limit]
