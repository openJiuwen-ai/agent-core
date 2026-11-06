# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package net_urls: merge into net_guard.urls; overlay cannot widen deny."""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.permission_engine.core import (
    PermissionEngine,
    prepare_permissions_for_engine,
)
from openjiuwen.harness.security.permission_engine.netguard.net_urls import (
    load_package_net_urls,
    merge_package_net_urls,
)


def test_package_net_urls_keep_metadata_deny_and_loopback_allow() -> None:
    urls = load_package_net_urls()
    assert urls
    for host in ("169.254.169.254", "metadata.google.internal"):
        assert urls[host] == "deny"
    assert urls["localhost"] == "allow"
    assert urls["127.0.0.1"] == "allow"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]:7897"])
@pytest.mark.parametrize("default,explicit,v4,v6", [("allow", None, "allow", "allow"),
    ("deny", None, "allow", "deny"), ("allow", "deny", "deny", "deny"),
    ("deny", "ask", "ask", "ask")])
def test_loopback_allow_preserves_explicit_decisions(host, default, explicit, v4, v6) -> None:
    pattern = "::1" if host.startswith("[") else host
    engine = PermissionEngine({"enabled": True, "net_guard": {"enabled": True, "defaults": default,
                              "urls": {pattern: explicit} if explicit else {}}})
    assert engine.check_network_access(host).permission.value == (v6 if host.startswith("[") else v4)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
@pytest.mark.parametrize("explicit", ["ask", "deny"])
def test_loopback_url_rules_override_builtin_allow(host, explicit) -> None:
    engine = PermissionEngine({"enabled": True, "net_guard": {"enabled": True, "defaults": "deny",
        "urls": {f"http://{host}:7897/private": explicit}}})
    assert engine.check_network_access(f"http://{host}:7897/private").permission.value == explicit
    assert engine.check_network_access(f"http://{host}:7897/public").is_allowed


@pytest.mark.parametrize("host", ["169.254.169.254", "metadata.google.internal"])
def test_metadata_deny_survives_a_specific_url_allow(host) -> None:
    engine = PermissionEngine({"enabled": True, "net_guard": {"enabled": True, "defaults": "allow",
        "urls": {f"http://{host}/latest/*": "allow"}}})
    assert engine.check_network_access(f"http://{host}/latest/meta-data").is_denied


def test_overlay_cannot_widen_package_net_url() -> None:
    effective = merge_package_net_urls(
        {
            "net_guard": {
                "enabled": True,
                "defaults": "allow",
                "urls": {"169.254.169.254": "allow", "evil.example": "deny"},
            }
        }
    )
    urls = effective["net_guard"]["urls"]
    assert urls["169.254.169.254"] == "deny"
    assert urls["localhost"] == "allow"
    assert urls["evil.example"] == "deny"


def test_merge_skipped_when_net_guard_disabled() -> None:
    effective = merge_package_net_urls(
        {"net_guard": {"enabled": False, "urls": {}}}
    )
    assert effective["net_guard"].get("urls") == {}


def test_engine_does_not_inject_when_net_guard_absent() -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "tools": {"mcp_fetch_webpage": "allow"},
        }
    )
    assert "net_guard" not in engine.config


def test_engine_injects_package_urls_when_enabled_without_them() -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "net_guard": {"enabled": True, "defaults": "allow", "urls": {}},
        }
    )
    urls = (engine.config.get("net_guard") or {}).get("urls") or {}
    assert urls["localhost"] == "allow"
    assert urls.get("169.254.169.254") == "deny"


def test_prepare_skips_reinject_when_package_keys_present() -> None:
    cfg = prepare_permissions_for_engine(
        {
            "net_guard": {
                "enabled": True,
                "defaults": "allow",
                "urls": {
                    "169.254.169.254": "deny",
                    "metadata.google.internal": "deny",
                    "localhost": "deny",
                    "127.0.0.1": "deny",
                    "custom.example": "deny",
                },
            }
        }
    )
    assert cfg["net_guard"]["urls"]["custom.example"] == "deny"
    assert cfg["net_guard"]["urls"]["localhost"] == "deny"
