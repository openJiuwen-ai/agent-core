# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package net_urls: merge into net_guard.urls; overlay cannot widen deny."""

from __future__ import annotations

from openjiuwen.harness.security.permission_engine.core import (
    PermissionEngine,
    prepare_permissions_for_engine,
)
from openjiuwen.harness.security.permission_engine.netguard.net_urls import (
    load_package_net_urls,
    merge_package_net_urls,
)


def test_package_net_urls_are_deny_hostnames() -> None:
    urls = load_package_net_urls()
    assert urls
    for host in ("169.254.169.254", "metadata.google.internal", "localhost", "127.0.0.1"):
        assert urls[host] == "deny"


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
    assert urls["localhost"] == "deny"
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
    assert urls.get("localhost") == "deny"
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
