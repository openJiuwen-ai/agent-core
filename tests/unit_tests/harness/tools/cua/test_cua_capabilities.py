# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for cua capability resolution."""

from openjiuwen.harness.tools.cua.cua_capabilities import (
    APP_LIFECYCLE_CUA_TOOL_NAMES,
    APP_TERMINATION_CUA_TOOL_NAMES,
    CORE_CUA_TOOL_NAMES,
    DEFAULT_CUA_AGENT_CAPABILITY_NAMES,
    DEFAULT_CUA_CAPABILITIES,
    INPUT_CUA_TOOL_NAMES,
    resolve_cua_capabilities,
)
from openjiuwen.harness.tools.cua.desktop_tools import CUA_DESKTOP_TOOL_NAMES


def test_core_only_selection_exposes_exactly_core_tools() -> None:
    resolved = resolve_cua_capabilities([])

    assert resolved.requested_names == ()
    assert resolved.selected_names == ("core",)
    assert resolved.rejected_names == ()
    assert resolved.allowed_tool_names == CORE_CUA_TOOL_NAMES


def test_input_selection_adds_input_tools_to_core() -> None:
    resolved = resolve_cua_capabilities(["input"])

    assert resolved.selected_names == ("core", "input")
    assert resolved.allowed_tool_names == CORE_CUA_TOOL_NAMES + INPUT_CUA_TOOL_NAMES
    assert not set(APP_LIFECYCLE_CUA_TOOL_NAMES).intersection(resolved.allowed_tool_names)


def test_multiple_categories_preserve_requested_order() -> None:
    resolved = resolve_cua_capabilities(["app_lifecycle", "input"])

    assert resolved.requested_names == ("app_lifecycle", "input")
    assert resolved.selected_names == ("core", "app_lifecycle", "input")
    assert resolved.allowed_tool_names == (CORE_CUA_TOOL_NAMES + APP_LIFECYCLE_CUA_TOOL_NAMES + INPUT_CUA_TOOL_NAMES)


def test_duplicate_categories_are_deduplicated_stably() -> None:
    resolved = resolve_cua_capabilities(["input", "input", "app_lifecycle", "input"])

    assert resolved.requested_names == ("input", "app_lifecycle")
    assert resolved.selected_names == ("core", "input", "app_lifecycle")
    assert len(resolved.allowed_tool_names) == len(set(resolved.allowed_tool_names))


def test_unknown_category_is_reported_as_rejected() -> None:
    resolved = resolve_cua_capabilities(["unknown", "input"])

    assert resolved.selected_names == ("core", "input")
    assert resolved.rejected_names == ("unknown",)


def test_default_agent_capabilities_resolve_cleanly() -> None:
    resolved = resolve_cua_capabilities(DEFAULT_CUA_AGENT_CAPABILITY_NAMES)

    assert resolved.rejected_names == ()
    assert resolved.selected_names == ("core", "input", "app_lifecycle")


def test_catalog_names_exactly_the_built_tools() -> None:
    # A catalog name with no tool behind it would be granted and silently
    # missing; a tool in no bundle could never be granted at all.
    catalog = {name for capability in DEFAULT_CUA_CAPABILITIES for name in capability.tool_names}
    assert catalog == set(CUA_DESKTOP_TOOL_NAMES)


def test_catalog_bundles_do_not_overlap() -> None:
    # A tool in two bundles would make capability revocation unreliable: the
    # allowlist would still contain it via the other bundle.
    all_names = [name for capability in DEFAULT_CUA_CAPABILITIES for name in capability.tool_names]
    assert len(all_names) == len(set(all_names))


def test_kill_app_is_granted_only_through_app_termination() -> None:
    # The built-in permission rules deny kill_app, so the default grant must
    # not hand it out: with the engine on it could never run, and with the
    # engine off nothing would gate it. Operators opt in by name.
    default = resolve_cua_capabilities(DEFAULT_CUA_AGENT_CAPABILITY_NAMES)
    assert "kill_app" not in default.allowed_tool_names
    assert "kill_app" not in APP_LIFECYCLE_CUA_TOOL_NAMES

    opted_in = resolve_cua_capabilities(["app_termination"])
    assert set(APP_TERMINATION_CUA_TOOL_NAMES) == {"kill_app"}
    assert "kill_app" in opted_in.allowed_tool_names
