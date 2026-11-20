"""Match provenance must not change permission precedence or leak across calls."""

import asyncio

import pytest

from openjiuwen.harness.security.permission_engine import PermissionEngine, PermissionLevel


def config():
    return {"enabled": True, "package_builtin_rules": False, "defer_unmatched": True,
            "tools": {"bash": "allow"}, "composition": {"schema_version": 2, "revision": "v1",
                "source_order": ["user", "cloud", "builtin"], "net_url_layers": {"example.com": "user"},
                "tool_layers": {"user": {"bash": "allow"}}},
            "net_guard": {"enabled": True, "defaults": "allow", "urls": {"example.com": "allow"}},
            "file_guard": {"enabled": True, "defaults": {"read": "allow", "write": "ask", "exec": "allow"}}}


def test_explicit_and_default_network_allow_have_distinct_provenance():
    engine = PermissionEngine(config())
    explicit = engine.check_network_access("https://example.com/a")
    fallback = engine.check_network_access("https://other.example/b")
    assert explicit.is_allowed and fallback.is_allowed
    assert [(hit.source, hit.permission) for hit in explicit.matches] == [("user", PermissionLevel.ALLOW)]
    assert all(hit.is_default and hit.source is None for hit in fallback.matches)


def test_file_allow_and_ask_keep_each_target(tmp_path):
    cfg = config()
    cfg["file_guard"]["paths"] = [{"path": str(tmp_path / "a"), "write": "allow", "layer": "user"}]
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    result = engine.check_file_accesses([(tmp_path / "a", "write"), (tmp_path / "b", "write")])
    assert result.needs_approval
    assert [(hit.target, hit.permission) for hit in result.targets] == [
        (str(tmp_path / "a"), PermissionLevel.ALLOW), (str(tmp_path / "b"), PermissionLevel.ASK)]
    assert result.matches[0].source == "user"
    assert result.matches[1].is_default


@pytest.mark.asyncio
async def test_tool_unmatched_is_not_upgraded_by_network_allow():
    engine = PermissionEngine(config())
    result = await engine.check_permission("mcp_fetch_webpage", {"url": "https://example.com"})
    assert result.is_undetermined
    assert result.targets[0].permission == PermissionLevel.ALLOW
    assert result.matches[0].source == "user"


@pytest.mark.asyncio
async def test_concurrent_traces_are_isolated():
    engine = PermissionEngine(config())
    results = await asyncio.gather(*(engine.check_permission("mcp_fetch_webpage", {"url": url})
                                   for url in ("https://example.com", "https://other.example")))
    assert {hit.target for hit in results[0].matches} == {"https://example.com"}
    assert {hit.target for hit in results[1].matches} == {"https://other.example"}
