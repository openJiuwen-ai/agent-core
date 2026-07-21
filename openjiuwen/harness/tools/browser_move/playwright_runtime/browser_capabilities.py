# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trusted Playwright capability catalog and task-scoped resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


CORE_BROWSER_CAPABILITY_NAME = "core"

# Keep this list explicit. Prefix-based matching could accidentally expose a
# newly introduced Playwright tool before its capability policy is reviewed.
CORE_BROWSER_TOOL_NAMES: tuple[str, ...] = (
    "browser_click",
    "browser_close",
    "browser_console_messages",
    "browser_drag",
    "browser_drop",
    "browser_evaluate",
    "browser_file_upload",
    "browser_fill_form",
    "browser_find",
    "browser_handle_dialog",
    "browser_hover",
    "browser_navigate",
    "browser_navigate_back",
    "browser_network_request",
    "browser_network_requests",
    "browser_press_key",
    "browser_resize",
    "browser_run_code_unsafe",
    "browser_select_option",
    "browser_snapshot",
    "browser_tabs",
    "browser_take_screenshot",
    "browser_type",
    "browser_wait_for",
)

PDF_BROWSER_TOOL_NAMES: tuple[str, ...] = ("browser_pdf_save",)

VISION_BROWSER_TOOL_NAMES: tuple[str, ...] = (
    "browser_mouse_click_xy",
    "browser_mouse_down",
    "browser_mouse_drag_xy",
    "browser_mouse_move_xy",
    "browser_mouse_up",
    "browser_mouse_wheel",
)

DEVTOOLS_BROWSER_TOOL_NAMES: tuple[str, ...] = (
    "browser_annotate",
    "browser_hide_highlight",
    "browser_highlight",
    "browser_resume",
    "browser_start_tracing",
    "browser_start_video",
    "browser_stop_tracing",
    "browser_stop_video",
    "browser_video_chapter",
    "browser_video_hide_actions",
    "browser_video_show_actions",
)

CONFIG_BROWSER_TOOL_NAMES: tuple[str, ...] = ("browser_get_config",)

NETWORK_BROWSER_TOOL_NAMES: tuple[str, ...] = (
    "browser_network_state_set",
    "browser_route",
    "browser_route_list",
    "browser_unroute",
)

STORAGE_BROWSER_TOOL_NAMES: tuple[str, ...] = (
    "browser_cookie_clear",
    "browser_cookie_delete",
    "browser_cookie_get",
    "browser_cookie_list",
    "browser_cookie_set",
    "browser_localstorage_clear",
    "browser_localstorage_delete",
    "browser_localstorage_get",
    "browser_localstorage_list",
    "browser_localstorage_set",
    "browser_sessionstorage_clear",
    "browser_sessionstorage_delete",
    "browser_sessionstorage_get",
    "browser_sessionstorage_list",
    "browser_sessionstorage_set",
    "browser_set_storage_state",
    "browser_storage_state",
)

TESTING_BROWSER_TOOL_NAMES: tuple[str, ...] = (
    "browser_generate_locator",
    "browser_verify_element_visible",
    "browser_verify_list_visible",
    "browser_verify_text_visible",
    "browser_verify_value",
)


@dataclass(frozen=True)
class BrowserCapability:
    """An approved browser capability exposed to the main agent."""

    name: str
    description: str
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedBrowserCapabilities:
    """Validated task-scoped capability and tool selection."""

    requested_names: tuple[str, ...]
    selected_names: tuple[str, ...]
    rejected_names: tuple[str, ...]
    allowed_tool_names: tuple[str, ...]


DEFAULT_BROWSER_CAPABILITIES: tuple[BrowserCapability, ...] = (
    BrowserCapability(
        name=CORE_BROWSER_CAPABILITY_NAME,
        description="Navigate, inspect, and interact with web pages using the standard Playwright browser tools.",
        tool_names=CORE_BROWSER_TOOL_NAMES,
    ),
    BrowserCapability(
        name="pdf",
        description="Save the current browser page as a PDF artifact.",
        tool_names=PDF_BROWSER_TOOL_NAMES,
    ),
    BrowserCapability(
        name="vision",
        description="Use coordinate-based mouse interactions for tasks that require visual positioning.",
        tool_names=VISION_BROWSER_TOOL_NAMES,
    ),
    BrowserCapability(
        name="devtools",
        description="Use Playwright developer tools for annotations, highlighting, tracing, and video capture.",
        tool_names=DEVTOOLS_BROWSER_TOOL_NAMES,
    ),
    BrowserCapability(
        name="config",
        description="Inspect the resolved Playwright MCP configuration when diagnosing browser runtime setup.",
        tool_names=CONFIG_BROWSER_TOOL_NAMES,
    ),
    BrowserCapability(
        name="network",
        description="Change browser network state and add, inspect, or remove request mocks for network testing.",
        tool_names=NETWORK_BROWSER_TOOL_NAMES,
    ),
    BrowserCapability(
        name="storage",
        description=(
            "Inspect or modify cookies and browser storage, including saving or restoring sensitive session state."
        ),
        tool_names=STORAGE_BROWSER_TOOL_NAMES,
    ),
    BrowserCapability(
        name="testing",
        description="Generate Playwright locators and verify expected elements, lists, text, or values.",
        tool_names=TESTING_BROWSER_TOOL_NAMES,
    ),
)

def _stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize names and preserve their first-seen order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


def _build_capability_index(
    available_capabilities: Iterable[BrowserCapability],
) -> dict[str, BrowserCapability]:
    """Build and validate the trusted capability catalog."""
    catalog: dict[str, BrowserCapability] = {}
    for capability in available_capabilities:
        name = capability.name.strip()
        if not name:
            raise ValueError("Browser capability name cannot be empty.")
        if name in catalog:
            raise ValueError(f"Duplicate browser capability name: {name}")
        catalog[name] = capability
    if CORE_BROWSER_CAPABILITY_NAME not in catalog:
        raise ValueError(f"Browser capability catalog must include {CORE_BROWSER_CAPABILITY_NAME!r}.")
    return catalog


def resolve_browser_capabilities(
    requested_names: Iterable[str] | None,
    available_capabilities: Iterable[BrowserCapability] = DEFAULT_BROWSER_CAPABILITIES,
) -> ResolvedBrowserCapabilities:
    """Validate a main-agent selection and expand it to allowed tool names.

    The main agent is responsible for selecting capabilities from their
    descriptions. This resolver performs no task interpretation: it always adds
    the core capability, rejects unknown names, and expands approved capability
    names into a deterministic task-scoped tool allowlist.
    """
    catalog = _build_capability_index(available_capabilities)
    requested = _stable_unique(requested_names or ())

    selected: list[str] = [CORE_BROWSER_CAPABILITY_NAME]
    rejected: list[str] = []
    for name in requested:
        if name not in catalog:
            rejected.append(name)
            continue
        if name not in selected:
            selected.append(name)

    allowed_tool_names = _stable_unique(
        tool_name for capability_name in selected for tool_name in catalog[capability_name].tool_names
    )

    return ResolvedBrowserCapabilities(
        requested_names=requested,
        selected_names=tuple(selected),
        rejected_names=tuple(rejected),
        allowed_tool_names=allowed_tool_names,
    )


__all__ = [
    "CONFIG_BROWSER_TOOL_NAMES",
    "CORE_BROWSER_CAPABILITY_NAME",
    "CORE_BROWSER_TOOL_NAMES",
    "DEFAULT_BROWSER_CAPABILITIES",
    "DEVTOOLS_BROWSER_TOOL_NAMES",
    "NETWORK_BROWSER_TOOL_NAMES",
    "PDF_BROWSER_TOOL_NAMES",
    "STORAGE_BROWSER_TOOL_NAMES",
    "TESTING_BROWSER_TOOL_NAMES",
    "VISION_BROWSER_TOOL_NAMES",
    "BrowserCapability",
    "ResolvedBrowserCapabilities",
    "resolve_browser_capabilities",
]
