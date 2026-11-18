# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pipeline C: net_guard allow/deny only; miss uses defaults (allow)."""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.netguard.net_guard import (
    NetGuardChecker,
    build_net_guard_checker,
    match_net_pattern,
)


def _checker(**kwargs: object) -> NetGuardChecker:
    section = {"enabled": True, "defaults": "allow", "urls": {}, **kwargs}
    checker = build_net_guard_checker({"net_guard": section})
    assert checker is not None
    return checker


def test_missing_section_does_not_build_checker() -> None:
    assert build_net_guard_checker({"enabled": True}) is None


def test_disabled_does_not_build_checker() -> None:
    assert build_net_guard_checker({"net_guard": {"enabled": False, "urls": {"localhost": "deny"}}}) is None


def test_non_fetch_tool_is_skipped() -> None:
    checker = _checker(urls={"localhost": "deny"})
    assert checker.evaluate("mcp_free_search", {"url": "http://localhost/"}) is None
    assert checker.evaluate("bash", {"command": "curl http://localhost/"}) is None


def test_miss_with_defaults_allow_does_not_lift() -> None:
    checker = _checker(defaults="allow", urls={"localhost": "deny"})
    assert checker.evaluate("mcp_fetch_webpage", {"url": "https://example.com/"}) is None


def test_miss_with_defaults_deny_denies() -> None:
    checker = _checker(defaults="deny", urls={})
    result = checker.evaluate("mcp_fetch_webpage", {"url": "https://example.com/"})
    assert result is not None
    assert result.permission == PermissionLevel.DENY
    assert "net_guard" in (result.matched_rule or "")


def test_defaults_ask_requires_approval() -> None:
    checker = _checker(defaults="ask", urls={})
    assert checker.evaluate("mcp_fetch_webpage", {"url": "https://example.com/"}).needs_approval


def test_hostname_deny_hits_link_local() -> None:
    checker = _checker(urls={"169.254.169.254": "deny"})
    result = checker.evaluate(
        "mcp_fetch_webpage",
        {"url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert result is not None
    assert result.permission == PermissionLevel.DENY


def test_hostname_glob_deny() -> None:
    checker = _checker(urls={"*.internal.example": "deny"})
    result = checker.evaluate("mcp_fetch_webpage", {"url": "https://api.internal.example/v1"})
    assert result is not None
    assert result.permission == PermissionLevel.DENY
    assert checker.evaluate("mcp_fetch_webpage", {"url": "https://example.com/"}) is None


def test_localhost_and_loopback_ignore_default_port() -> None:
    checker = _checker(urls={"localhost": "deny", "127.0.0.1": "deny"})
    for url in ("http://localhost/", "http://localhost:80/", "http://127.0.0.1:443/health"):
        result = checker.evaluate("mcp_fetch_webpage", {"url": url})
        assert result is not None, url
        assert result.permission == PermissionLevel.DENY, url


@pytest.mark.parametrize("pattern,target,matched", [
    ("127.0.0.1:8080", "127.0.0.1:8080", True),
    ("127.0.0.1:8080", "127.0.0.1:8081", False),
    ("127.0.0.1:8080", "127.0.0.2:8080", False),
    ("127.0.0.1:8080", "127.0.0.1", False),
    ("127.0.0.1:80", "http://127.0.0.1/data", True),
    ("127.0.0.1:80", "https://127.0.0.1/data", False),
    ("[::1]:8080", "[0:0:0:0:0:0:0:1]:8080", True),
    ("[::1]:8080", "[::1]:8081", False),
    ("*.internal.example:443", "https://api.internal.example/data", True),
    ("*.internal.example:443", "https://api.internal.example:8443/data", False),
    ("127.0.0.1", "127.0.0.1:8081", True),
    ("::1", "[::1]:8081", True),
])
def test_endpoint_rule_binds_host_and_port(pattern, target, matched):
    assert match_net_pattern(pattern, target) is matched


@pytest.mark.parametrize("action", ["allow", "ask", "deny"])
def test_endpoint_rules_use_all_existing_network_actions(action):
    checker = _checker(defaults="deny" if action == "allow" else "allow", urls={"127.0.0.1:8080": action})
    result = checker.evaluate("mcp_fetch_webpage", {"url": "http://127.0.0.1:8080/data"})
    other = checker.evaluate("mcp_fetch_webpage", {"url": "http://127.0.0.1:8081/data"})
    if action == "allow":
        assert result is None  # An allowed network check does not lift another guard's ASK.
        assert other is not None and other.is_denied
    else:
        assert result is not None and result.permission == PermissionLevel(action)
        assert other is None


def test_port_deny_overrides_builtin_loopback_allow(tmp_path):
    engine = PermissionEngine({"enabled": True, "net_guard": {
        "enabled": True, "defaults": "deny", "urls": {"127.0.0.1:8080": "deny"},
    }}, workspace_root=tmp_path)
    assert engine.check_network_access("127.0.0.1:8080").is_denied
    assert engine.check_network_access("127.0.0.1:8081").is_allowed


def test_url_glob_and_prefix() -> None:
    checker = _checker(
        urls={
            "https://evil.example/admin*": "deny",
            "https://blocked.example/secret": "deny",
        }
    )
    glob_hit = checker.evaluate("mcp_fetch_webpage", {"url": "https://evil.example/admin/users"})
    assert glob_hit is not None
    assert glob_hit.permission == PermissionLevel.DENY
    prefix_hit = checker.evaluate("mcp_fetch_webpage", {"url": "https://blocked.example/secret/token"})
    assert prefix_hit is not None
    assert prefix_hit.permission == PermissionLevel.DENY
    assert checker.evaluate("mcp_fetch_webpage", {"url": "https://blocked.example/public"}) is None


def test_ask_url_entry_requires_approval() -> None:
    checker = _checker(defaults="allow", urls={"localhost": "ask"})
    assert checker.evaluate("mcp_fetch_webpage", {"url": "http://localhost/"}).needs_approval


def test_missing_url_arg_uses_defaults() -> None:
    checker = _checker(defaults="deny", urls={"localhost": "allow"})
    result = checker.evaluate("mcp_fetch_webpage", {})
    assert result is not None
    assert result.permission == PermissionLevel.DENY


def test_extracts_first_non_empty_url_alias_key() -> None:
    checker = _checker(urls={"evil.example": "deny"})
    result = checker.evaluate("mcp_fetch_webpage", {"href": "https://evil.example/x", "url": ""})
    assert result is not None
    assert result.permission == PermissionLevel.DENY


def test_fetch_aliases_match_canonical_name() -> None:
    checker = _checker(urls={"localhost": "deny"})
    for name in ("mcp_fetch_webpage", "fetch_webpage", "web_fetch_webpage"):
        result = checker.evaluate(name, {"url": "http://localhost/"})
        assert result is not None, name
        assert result.permission == PermissionLevel.DENY, name


@pytest.mark.asyncio
async def test_engine_allow_plus_net_deny_is_deny() -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "tools": {"mcp_fetch_webpage": "allow"},
            "defaults": {"*": "allow"},
            "net_guard": {
                "enabled": True,
                "defaults": "allow",
                "urls": {"169.254.169.254": "deny"},
            },
        }
    )
    result = await engine.check_permission(
        "mcp_fetch_webpage",
        {"url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert result.permission == PermissionLevel.DENY
    assert "net_guard" in (result.matched_rule or "")


@pytest.mark.asyncio
async def test_engine_ask_plus_net_allow_stays_ask() -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "tools": {"mcp_fetch_webpage": "ask"},
            "net_guard": {
                "enabled": True,
                "defaults": "allow",
                "urls": {"localhost": "deny"},
            },
        }
    )
    result = await engine.check_permission(
        "mcp_fetch_webpage",
        {"url": "https://example.com/"},
    )
    assert result.permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_engine_alias_web_fetch_webpage_matches_canonical() -> None:
    engine = PermissionEngine(
        {
            "enabled": True,
            "tools": {"mcp_fetch_webpage": "allow"},
            "defaults": {"*": "allow"},
            "net_guard": {
                "enabled": True,
                "defaults": "allow",
                "urls": {"localhost": "deny"},
            },
        }
    )
    canonical = await engine.check_permission("mcp_fetch_webpage", {"url": "http://localhost/"})
    alias = await engine.check_permission("web_fetch_webpage", {"url": "http://localhost/"})
    assert canonical.permission == PermissionLevel.DENY
    assert alias.permission == PermissionLevel.DENY


def test_rail_alias_maps_web_fetch_webpage() -> None:
    from openjiuwen.harness.rails.security.tool_security_rail import TOOL_NAME_ALIASES

    assert TOOL_NAME_ALIASES["web_fetch_webpage"] == "mcp_fetch_webpage"
    assert TOOL_NAME_ALIASES["fetch_webpage"] == "mcp_fetch_webpage"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("rules", [
    [("*.example.com", "ask"), ("https://api.example.com/health", "allow")],
    [("api.example.com", "ask"), ("https://*", "allow")],
])
def test_ask_precedes_allow_and_deny_precedes_ask(reverse, rules):
    if reverse:
        rules = list(reversed(rules))
    checker = NetGuardChecker({"enabled": True, "urls": dict(rules)})
    assert checker.check_url("https://api.example.com/health").needs_approval
    assert checker.check_url("https://api.example.com/private").needs_approval
    checker = NetGuardChecker({"enabled": True, "urls": {**dict(rules), "api.example.com": "deny"}})
    assert checker.check_url("https://api.example.com/health").is_denied
