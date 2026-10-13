# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host exit (P3) + net_guard check_url / enforce_host_exit."""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.outbound import policy as host_exit
from openjiuwen.harness.security.outbound import sync_client
from openjiuwen.harness.security.outbound.limits import OutboundLimitError
from openjiuwen.harness.security.outbound.policy import OutboundBlockedError
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.netguard.net_guard import NetGuardChecker

_DENY_EVIL = {"*.evil.example": "deny"}


@pytest.fixture(autouse=True)
def _reset_host_exit():
    host_exit.reset_host_exit_policy()
    yield
    host_exit.reset_host_exit_policy()


def _section(**overrides):
    base = {"enabled": True, "defaults": "allow", "urls": {}}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# NetGuardChecker
# --------------------------------------------------------------------------- #
def test_check_url_is_tool_agnostic():
    checker = NetGuardChecker(_section(urls=_DENY_EVIL))
    result = checker.check_url("https://a.evil.example/x")
    assert result is not None and result.permission == PermissionLevel.DENY
    assert result.matched_rule == "net_guard:url:*.evil.example"
    assert checker.check_url("https://ok.example/") is None


def test_evaluate_only_covers_fixed_fetch_tools():
    checker = NetGuardChecker(_section(urls=_DENY_EVIL, tools=["my_http_tool"]))
    assert checker.evaluate("my_http_tool", {"url": "https://x.evil.example"}) is None
    assert checker.evaluate("fetch_webpage", {"url": "https://x.evil.example"}) is not None


def test_enforce_host_exit_defaults_true():
    assert NetGuardChecker(_section()).enforce_host_exit is True
    assert NetGuardChecker(_section(enforce_host_exit=False)).enforce_host_exit is False


def test_builtin_floor_cannot_be_widened_by_listing_every_key():
    from openjiuwen.harness.security.permission_engine.core import prepare_permissions_for_engine
    from openjiuwen.harness.security.permission_engine.netguard.net_urls import load_package_net_urls

    packaged = load_package_net_urls()
    denied = [k for k, v in packaged.items() if v == "deny"]
    if not denied:
        pytest.skip("package ships no net_urls deny floor")
    widened = {k: "allow" for k in packaged}
    cfg = prepare_permissions_for_engine({"net_guard": {"enabled": True, "urls": widened}})
    urls = cfg["net_guard"]["urls"]
    assert all(urls[k] == "deny" for k in denied)


# --------------------------------------------------------------------------- #
# Host exit policy states
# --------------------------------------------------------------------------- #
def test_unset_and_disabled_pass_through():
    host_exit.check_outbound_url("https://a.evil.example/")
    host_exit.publish_host_exit_policy({"net_guard": {"enabled": False, "urls": {"*": "deny"}}})
    assert host_exit.get_host_exit_state().mode == host_exit.STATE_DISABLED
    host_exit.check_outbound_url("https://a.evil.example/")


def test_partial_config_without_net_guard_keeps_state():
    host_exit.publish_host_exit_policy({"net_guard": _section(urls=_DENY_EVIL)})
    host_exit.publish_host_exit_policy({"file_guard": {}})
    assert host_exit.get_host_exit_state().mode == host_exit.STATE_ACTIVE


def test_permission_engine_does_not_publish():
    from openjiuwen.harness.security.permission_engine.core import PermissionEngine

    host_exit.publish_host_exit_policy({"net_guard": _section(urls=_DENY_EVIL)})
    engine = PermissionEngine({"net_guard": {"enabled": False}})
    engine.update_config({"net_guard": {"enabled": False}})
    assert host_exit.get_host_exit_state().mode == host_exit.STATE_ACTIVE


def test_active_blocks_denied_and_bad_scheme():
    host_exit.publish_host_exit_policy({"net_guard": _section(urls=_DENY_EVIL)})
    with pytest.raises(OutboundBlockedError) as exc:
        host_exit.check_outbound_url("https://a.evil.example/upload")
    assert exc.value.matched_rule == "net_guard:url:*.evil.example"
    with pytest.raises(OutboundBlockedError):
        host_exit.check_outbound_url("file:///etc/passwd")
    host_exit.check_outbound_url("https://ok.example/")
    with pytest.raises(OutboundBlockedError) as oversized_url:
        host_exit.check_outbound_url("https://ok.example/?" + "x" * 8192)
    assert oversized_url.value.matched_rule == "host_exit:url_size"
    with pytest.raises(OutboundBlockedError) as userinfo:
        host_exit.check_outbound_url("https://user:secret@ok.example/")
    assert userinfo.value.matched_rule == "host_exit:userinfo"


def test_not_enforced_passes_through():
    host_exit.publish_host_exit_policy(
        {"net_guard": _section(urls={"*": "deny"}, enforce_host_exit=False)}
    )
    assert host_exit.get_host_exit_state().mode == host_exit.STATE_NOT_ENFORCED
    host_exit.check_outbound_url("https://anything.example")
    assert host_exit.describe_host_exit()["enforced"] is False


def test_active_policy_publishes_default_and_configured_limits():
    state = host_exit.publish_host_exit_policy(
        {"net_guard": _section(max_request_mb=2, timeout_seconds=7)}
    )
    assert state.policy is not None
    assert state.policy.max_request_bytes == 2 * 1024 * 1024
    assert state.policy.timeout_seconds == 7
    description = host_exit.describe_host_exit()
    assert description["limits_active"] is True
    assert description["limits"] == {"max_request_mb": 2, "timeout_seconds": 7}


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_request_mb", 0),
        ("max_request_mb", True),
        ("timeout_seconds", -1),
        ("timeout_seconds", "60"),
    ],
)
def test_invalid_limit_config_is_fail_closed(field, value):
    state = host_exit.publish_host_exit_policy({"net_guard": _section(**{field: value})})
    assert state.mode == host_exit.STATE_ERROR
    with pytest.raises(OutboundBlockedError):
        host_exit.check_outbound_url("https://ok.example")


def test_build_error_is_fail_closed(monkeypatch):
    def _broken(*_a, **_k):
        raise ValueError("bad section")

    monkeypatch.setattr(host_exit, "NetGuardChecker", _broken)
    state = host_exit.publish_host_exit_policy({"net_guard": _section()})
    assert state.mode == host_exit.STATE_ERROR
    with pytest.raises(OutboundBlockedError) as exc:
        host_exit.check_outbound_url("https://ok.example")
    assert exc.value.matched_rule == "host_exit:error"


def test_mcp_loopback_exemption():
    host_exit.publish_host_exit_policy(
        {"net_guard": _section(urls={"localhost": "deny", "127.0.0.1": "deny", "remote.mcp": "deny"})}
    )
    host_exit.check_mcp_server_url("http://localhost:9000/sse")
    host_exit.check_mcp_server_url("http://127.0.0.1:9000/sse")
    with pytest.raises(OutboundBlockedError):
        host_exit.check_mcp_server_url("http://remote.mcp/sse")
    exemption_ids = {e["id"] for e in host_exit.describe_host_exit()["exemptions"]}
    assert {"model_inference", "billing_report", "np_pipe", "mcp_loopback"} <= exemption_ids


@pytest.mark.parametrize("url", ["file://localhost/etc/passwd", "gopher://127.0.0.1:6379/_INFO"])
def test_mcp_loopback_exemption_requires_http_scheme(url):
    host_exit.publish_host_exit_policy({"net_guard": _section()})
    with pytest.raises(OutboundBlockedError) as exc:
        host_exit.check_mcp_server_url(url)
    assert exc.value.matched_rule == "host_exit:scheme"


@pytest.mark.parametrize("url", ["http://127.0.0.1/sse", "gopher://127.0.0.1:6379/_INFO"])
def test_mcp_loopback_exemption_does_not_bypass_fail_closed(monkeypatch, url):
    def _broken(*_a, **_k):
        raise ValueError("bad section")

    monkeypatch.setattr(host_exit, "NetGuardChecker", _broken)
    host_exit.publish_host_exit_policy({"net_guard": _section()})
    with pytest.raises(OutboundBlockedError) as exc:
        host_exit.check_mcp_server_url(url)
    assert exc.value.matched_rule == "host_exit:error"


# --------------------------------------------------------------------------- #
# Sync client: every redirect hop is checked
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None and status in (301, 302, 303, 307, 308)
        self.closed = False

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._responses.pop(0)

    def close(self):
        pass


def test_sync_redirect_to_denied_host_is_blocked():
    host_exit.publish_host_exit_policy({"net_guard": _section(urls=_DENY_EVIL)})
    session = _Session([_Resp(302, "https://a.evil.example/collect")])
    with pytest.raises(OutboundBlockedError):
        sync_client.get("https://start.example/", session=session)
    assert len(session.calls) == 1
    assert session.calls[0][2]["allow_redirects"] is False


def test_sync_redirect_rewrites_method_and_strips_auth():
    host_exit.publish_host_exit_policy({"net_guard": _section()})
    final = _Resp(200)
    session = _Session([_Resp(303, "https://b.example/done"), final])
    resp = sync_client.post(
        "https://a.example/submit",
        session=session,
        json={"k": "v"},
        headers={"Authorization": "Bearer x", "X-Other": "1"},
    )
    assert resp is final
    method, url, kwargs = session.calls[1]
    assert (method, url) == ("GET", "https://b.example/done")
    assert "json" not in kwargs
    assert kwargs["headers"] == {"X-Other": "1"}


def test_sync_cross_origin_redirect_strips_all_caller_credentials():
    host_exit.publish_host_exit_policy({"net_guard": _section()})
    final = _Resp(200)
    session = _Session([_Resp(302, "https://a.example:8443/next"), final])
    sync_client.get(
        "https://a.example/start",
        session=session,
        auth=("user", "secret"),
        headers={
            "Authorization": "Bearer x",
            "Cookie": "sid=secret",
            "Proxy-Authorization": "Basic x",
            "X-Other": "1",
        },
    )
    kwargs = session.calls[1][2]
    assert "auth" not in kwargs
    assert kwargs["headers"] == {"X-Other": "1"}


def test_sync_https_to_http_redirect_is_blocked():
    host_exit.publish_host_exit_policy({"net_guard": _section()})
    first = _Resp(302, "http://a.example/insecure")
    session = _Session([first])
    with pytest.raises(OutboundBlockedError) as exc:
        sync_client.get("https://a.example/start", session=session)
    assert exc.value.matched_rule == "host_exit:downgrade"
    assert first.closed
    assert len(session.calls) == 1


def test_sync_rejects_known_oversized_body_before_connecting():
    host_exit.publish_host_exit_policy({"net_guard": _section(max_request_mb=1)})
    session = _Session([_Resp(200)])
    with pytest.raises(OutboundLimitError) as exc:
        sync_client.post("https://a.example/upload", session=session, data=b"x" * (1024 * 1024 + 1))
    assert exc.value.code == "OUTBOUND_REQUEST_TOO_LARGE"
    assert session.calls == []


def test_sync_multipart_counts_form_and_file_together():
    host_exit.publish_host_exit_policy({"net_guard": _section(max_request_mb=1)})
    session = _Session([_Resp(200)])
    with pytest.raises(OutboundLimitError) as exc:
        sync_client.post(
            "https://a.example/upload",
            session=session,
            data={"description": "small"},
            files={"file": ("data.bin", b"x" * (1024 * 1024))},
        )
    assert exc.value.code == "OUTBOUND_REQUEST_TOO_LARGE"
    assert session.calls == []


def test_sync_rejects_oversized_headers_before_connecting():
    host_exit.publish_host_exit_policy({"net_guard": _section()})
    session = _Session([_Resp(200)])
    with pytest.raises(OutboundLimitError) as exc:
        sync_client.get(
            "https://a.example/",
            session=session,
            headers={"X-Large": "x" * (64 * 1024)},
        )
    assert exc.value.code == "OUTBOUND_HEADERS_TOO_LARGE"
    assert session.calls == []


def test_sync_policy_caps_timeout_without_lengthening_caller_timeout():
    host_exit.publish_host_exit_policy({"net_guard": _section(timeout_seconds=7)})
    session = _Session([_Resp(200), _Resp(200)])
    sync_client.get("https://a.example/", session=session)
    sync_client.get("https://a.example/", session=session, timeout=2)
    assert session.calls[0][2]["timeout"] == pytest.approx(7, abs=0.1)
    assert session.calls[1][2]["timeout"] == 2.0


class _AsyncResp:
    def __init__(self, status, location=None):
        self._status = status
        self.headers = {"Location": location} if location else {}
        self.released = False

    @property
    def status(self):
        if self.released:
            raise AssertionError("status read after release()")
        return self._status

    def release(self):
        self.released = True


class _AsyncSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_async_redirect_to_denied_host_is_blocked():
    from openjiuwen.harness.security.outbound import async_client

    host_exit.publish_host_exit_policy({"net_guard": _section(urls=_DENY_EVIL)})
    first = _AsyncResp(307, "https://a.evil.example/")
    session = _AsyncSession([first])
    with pytest.raises(OutboundBlockedError):
        await async_client.request(session, "GET", "https://start.example/")
    assert first.released is True
    assert session.calls[0][2]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_async_cross_host_redirect_strips_auth():
    import aiohttp

    from openjiuwen.harness.security.outbound import async_client

    host_exit.publish_host_exit_policy({"net_guard": _section()})
    final = _AsyncResp(200)
    session = _AsyncSession([_AsyncResp(302, "https://b.example/next"), final])
    resp = await async_client.request(
        session,
        "GET",
        "https://a.example/start",
        auth=aiohttp.BasicAuth("user", "pass"),
        headers={"Authorization": "Bearer x", "X-Other": "1"},
    )
    assert resp is final
    _method, url, kwargs = session.calls[1]
    assert url == "https://b.example/next"
    assert "auth" not in kwargs
    assert kwargs["headers"] == {"X-Other": "1"}


@pytest.mark.asyncio
async def test_async_rejects_known_oversized_body_and_caps_timeout():
    from openjiuwen.harness.security.outbound import async_client

    host_exit.publish_host_exit_policy(
        {"net_guard": _section(max_request_mb=1, timeout_seconds=9)}
    )
    session = _AsyncSession([_AsyncResp(200)])
    with pytest.raises(OutboundLimitError):
        await async_client.request(
            session,
            "POST",
            "https://a.example/upload",
            data=b"x" * (1024 * 1024 + 1),
        )
    assert session.calls == []

    await async_client.request(session, "GET", "https://a.example/")
    timeout = session.calls[0][2]["timeout"]
    assert timeout.total == pytest.approx(9, abs=0.1)


def test_sync_too_many_redirects():
    session = _Session([_Resp(302, "https://loop.example/") for _ in range(3)])
    with pytest.raises(sync_client.RequestException):
        sync_client.get("https://loop.example/", session=session, max_redirects=2)


@pytest.mark.parametrize("defaults,rules", [
    ("ask", {}),
    ("allow", {"review.example": "ask"}),
    ("allow", {"https://start.example/private": "ask"}),
])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_sync_redirect_ask_stops_before_target_connection(defaults, rules, status):
    target = "https://start.example/private" if "https://start.example/private" in rules else "https://review.example/"
    host_exit.publish_host_exit_policy({"net_guard": _section(defaults=defaults, urls=rules)})
    first = _Resp(status, target)
    session = _Session([first])
    with pytest.raises(OutboundBlockedError, match="requires approval") as exc:
        sync_client.get("https://start.example/approved", session=session)
    assert exc.value.url == target
    assert first.closed
    assert len(session.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defaults,rules", [("ask", {}), ("allow", {"review.example": "ask"})])
async def test_async_redirect_ask_stops_before_target_connection(defaults, rules):
    from openjiuwen.harness.security.outbound import async_client

    host_exit.publish_host_exit_policy({"net_guard": _section(defaults=defaults, urls=rules)})
    first = _AsyncResp(302, "https://review.example/")
    session = _AsyncSession([first])
    with pytest.raises(OutboundBlockedError, match="requires approval"):
        await async_client.request(session, "GET", "https://start.example/approved")
    assert first.released
    assert len(session.calls) == 1


def test_explicit_allow_redirect_can_proceed_without_matching_ask():
    host_exit.publish_host_exit_policy({"net_guard": _section(defaults="ask", urls={
        "review.example": "ask", "https://api.example.com/health": "allow",
    })})
    final = _Resp(200)
    session = _Session([_Resp(302, "https://api.example.com/health"), final])
    assert sync_client.get("https://start.example/approved", session=session) is final
    assert len(session.calls) == 2
