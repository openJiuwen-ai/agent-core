#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for per-agent browser isolation (different browsers for different agents).

Covers the BrowserInstanceConfig identity key flowing through BrowserService
(profile / driver / port / user-data-dir) and the teams manifest element that
carries that identity across the spawn-wire boundary as serializable kwargs.

These tests never launch Chrome: they exercise only config resolution and
profile assembly, with the free-port allocator mocked for determinism.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserInstanceConfig,
    BrowserRunGuardrails,
    build_playwright_mcp_config,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.profiles import BrowserProfile
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService


def _seed_live_profile(svc: BrowserService, *, port: int = 63668) -> None:
    """Persist a managed profile for ``svc`` and mark it the global selected one."""
    svc._profile_store.upsert_profile(
        BrowserProfile(
            name=svc._profile_name,
            driver_type="managed",
            cdp_url=f"http://127.0.0.1:{port}",
            debug_port=port,
        ),
        select=True,
    )


def _make_service(instance: BrowserInstanceConfig | None) -> BrowserService:
    return BrowserService(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        mcp_cfg=build_playwright_mcp_config(instance),
        guardrails=BrowserRunGuardrails(),
        instance=instance,
    )


def _isolated_env():
    """Clear browser env + point the profile store at a throwaway path."""
    return patch.dict(
        os.environ,
        {"BROWSER_PROFILE_STORE_PATH": tempfile.mktemp(suffix=".json")},
        clear=True,
    )


def test_distinct_keys_get_distinct_profiles_and_ports() -> None:
    with _isolated_env(), patch.object(BrowserService, "_allocate_free_port", side_effect=[40001, 40002]):
        a = _make_service(BrowserInstanceConfig(key="A", driver_mode="managed"))
        b = _make_service(BrowserInstanceConfig(key="B", driver_mode="managed"))

        assert a._profile_name == "A"
        assert b._profile_name == "B"
        assert a._driver_mode == "managed"

        pa = a._build_managed_profile()
        pb = b._build_managed_profile()

    assert pa.debug_port == 40001
    assert pb.debug_port == 40002
    assert pa.debug_port != pb.debug_port
    assert pa.user_data_dir != pb.user_data_dir


def test_explicit_port_is_honored_without_allocation() -> None:
    with (
        _isolated_env(),
        patch.object(BrowserService, "_allocate_free_port", side_effect=AssertionError("must not allocate")),
    ):
        svc = _make_service(BrowserInstanceConfig(key="pinned", driver_mode="managed", managed_port=9501))
        profile = svc._build_managed_profile()

    assert profile.debug_port == 9501


def test_instance_profile_name_overrides_key() -> None:
    with _isolated_env(), patch.object(BrowserService, "_allocate_free_port", return_value=40010):
        svc = _make_service(BrowserInstanceConfig(key="raw", driver_mode="managed", profile_name="custom-profile"))
        assert svc._profile_name == "custom-profile"


def test_instance_driver_mode_overrides_env() -> None:
    with patch.dict(
        os.environ,
        {"BROWSER_DRIVER": "remote", "BROWSER_PROFILE_STORE_PATH": tempfile.mktemp(suffix=".json")},
        clear=True,
    ):
        svc = _make_service(BrowserInstanceConfig(key="x", driver_mode="managed"))
        assert svc._driver_mode == "managed"


def test_legacy_no_instance_preserves_env_defaults() -> None:
    with _isolated_env():
        svc = _make_service(None)
        assert svc._profile_name == "jiuwenclaw"
        profile = svc._build_managed_profile()

    assert profile.debug_port == 9333


def test_keyed_auto_allocation_ignores_global_env_port() -> None:
    """A keyed instance must not inherit a shared env port (would re-collide)."""
    with (
        patch.dict(
            os.environ,
            {"BROWSER_MANAGED_PORT": "9333", "BROWSER_PROFILE_STORE_PATH": tempfile.mktemp(suffix=".json")},
            clear=True,
        ),
        patch.object(BrowserService, "_allocate_free_port", return_value=45000),
    ):
        svc = _make_service(BrowserInstanceConfig(key="keyed", driver_mode="managed"))
        profile = svc._build_managed_profile()

    assert profile.debug_port == 45000


def test_allocated_port_persists_for_reuse() -> None:
    """The managed profile (with its port) is persisted so a restart reattaches."""
    with _isolated_env(), patch.object(BrowserService, "_allocate_free_port", return_value=46000):
        svc = _make_service(BrowserInstanceConfig(key="persist", driver_mode="managed"))
        profile = svc._build_managed_profile()
        svc._profile_store.upsert_profile(profile, select=True)

        reloaded = svc._profile_store.get_profile("persist")

    assert reloaded is not None
    assert reloaded.debug_port == 46000
    assert reloaded.driver_type == "managed"


# ---------------------------------------------------------------------------
# Cross-key reuse guard — the shared store's global selected_profile must not
# let a second keyed instance attach to a first key's live browser.
# ---------------------------------------------------------------------------


def test_keyed_instance_does_not_adopt_another_keys_selected_profile() -> None:
    """Regression: keyed instances must not attach to another key's live Chrome.

    Repro of the observed bug — ``usd-sgd`` starts first and becomes the global
    selected_profile in the shared store; ``sgd-jpy`` must still get its own
    browser instead of reusing usd-sgd's live CDP endpoint.
    """
    with _isolated_env():
        first = _make_service(BrowserInstanceConfig(key="usd-sgd", driver_mode="managed"))
        _seed_live_profile(first, port=63668)

        second = _make_service(BrowserInstanceConfig(key="sgd-jpy", driver_mode="managed"))
        with patch.object(BrowserService, "_is_cdp_endpoint_ready", return_value=True):
            resolved = second._resolve_existing_cdp_profile()

    assert resolved is None


def test_keyed_instance_reuses_its_own_live_profile() -> None:
    """Same key == intentional sharing: reuse its own profile when CDP is live."""
    with _isolated_env():
        svc = _make_service(BrowserInstanceConfig(key="usd-sgd", driver_mode="managed"))
        _seed_live_profile(svc, port=63668)
        with patch.object(BrowserService, "_is_cdp_endpoint_ready", return_value=True):
            resolved = svc._resolve_existing_cdp_profile()

    assert resolved is not None
    assert resolved.name == "usd-sgd"


def test_legacy_instance_still_adopts_selected_profile() -> None:
    """Unkeyed/legacy preserves the historical single-browser reuse behavior."""
    with _isolated_env():
        seed = _make_service(BrowserInstanceConfig(key="usd-sgd", driver_mode="managed"))
        _seed_live_profile(seed, port=63668)

        legacy = _make_service(None)
        with patch.object(BrowserService, "_is_cdp_endpoint_ready", return_value=True):
            resolved = legacy._resolve_existing_cdp_profile()

    assert resolved is not None
    assert resolved.name == "usd-sgd"


# ---------------------------------------------------------------------------
# Teams manifest plumbing — serializable browser identity across the wire
# ---------------------------------------------------------------------------


def _browser_input(**overrides):
    base = dict(
        browser_key="",
        browser_port=0,
        browser_profile="",
        browser_driver="",
        browser_cdp_url="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_browser_instance_dict_none_when_no_identity() -> None:
    from openjiuwen.agent_teams.rails.subagent_elements import _browser_instance_dict

    assert _browser_instance_dict(_browser_input()) is None


def test_browser_instance_dict_defaults_to_managed() -> None:
    from openjiuwen.agent_teams.rails.subagent_elements import _browser_instance_dict

    out = _browser_instance_dict(_browser_input(browser_key="A"))
    assert out == {"key": "A", "driver_mode": "managed"}


def test_browser_instance_dict_remote_when_cdp_url() -> None:
    from openjiuwen.agent_teams.rails.subagent_elements import _browser_instance_dict

    out = _browser_instance_dict(_browser_input(browser_cdp_url="http://host:9222"))
    assert out["driver_mode"] == "remote"
    assert out["cdp_url"] == "http://host:9222"


def test_distinct_teammate_keys_yield_distinct_dicts() -> None:
    from openjiuwen.agent_teams.rails.subagent_elements import _browser_instance_dict

    a = _browser_instance_dict(_browser_input(browser_key="A", browser_port=9501))
    b = _browser_instance_dict(_browser_input(browser_key="B", browser_port=9502))
    assert a != b
    assert a["managed_port"] == 9501 and b["managed_port"] == 9502
