# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trusted pre-dispatch routing never retries delivered operations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from openjiuwen.core.sys_operation import LocalWorkConfig
from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig, SandboxGatewayConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import SandboxGatewayClient
from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as jb


def client(guard=None):
    gateway = SimpleNamespace(handle_request=AsyncMock(), handle_stream_request=AsyncMock(),
                              get_or_create_provider=AsyncMock())
    config = SandboxGatewayConfig(launcher_config=PreDeployLauncherConfig(
        sandbox_type="jiuwenbox", base_url="http://localhost:8321", extra_params={"runtime_guard": guard},
    ))
    return SandboxGatewayClient(config=config, isolation_key="project", gateway=gateway), gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("create", [True, False])
async def test_prepare_checks_barrier_before_optional_preparation(create):
    c, gateway = client()
    events = []
    provider = SimpleNamespace(
        _assert_policy_ready=Mock(side_effect=lambda: events.append("check")),
        prepare_for_execution=AsyncMock(side_effect=lambda: events.append("prepare")),
    )
    gateway.get_or_create_provider.return_value = provider
    assert await c.prepare("fs", create=create) is provider
    gateway.get_or_create_provider.assert_awaited_once_with(config=c._config, isolation_key="project", op_type="fs")
    assert events == (["check", "prepare"] if create else ["check"])
    gateway.handle_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("hooks", ["none", "check_only", "prepare_only"])
async def test_prepare_supports_providers_with_optional_hooks(hooks):
    c, gateway = client()
    provider = SimpleNamespace()
    if hooks == "check_only":
        provider._assert_policy_ready = Mock()
    elif hooks == "prepare_only":
        provider.prepare_for_execution = AsyncMock()
    gateway.get_or_create_provider.return_value = provider
    assert await c.prepare("shell") is provider
    if hooks == "check_only":
        provider._assert_policy_ready.assert_called_once()
    elif hooks == "prepare_only":
        provider.prepare_for_execution.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_barrier_failure_never_prepares_or_dispatches():
    c, gateway = client()
    error = RuntimeError("policy application pending")
    provider = SimpleNamespace(_assert_policy_ready=Mock(side_effect=error), prepare_for_execution=AsyncMock())
    gateway.get_or_create_provider.return_value = provider
    with pytest.raises(RuntimeError) as caught:
        await c.prepare("code")
    assert caught.value is error
    provider.prepare_for_execution.assert_not_awaited()
    gateway.handle_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_gateway_provider_resolver_preserves_cache_and_keyword_arguments(monkeypatch):
    from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxGateway
    from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry

    c, _ = client()
    gateway = SandboxGateway()
    endpoint = SimpleNamespace(base_url="http://localhost:8321")
    resolver = AsyncMock(return_value=endpoint)
    monkeypatch.setattr(gateway, "_get_endpoint", resolver)
    factory = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(SandboxRegistry, "create_provider", factory)
    for _ in range(2):
        assert await gateway.get_or_create_provider(config=c._config, isolation_key="project", op_type="fs") \
            is factory.return_value
    resolver.assert_awaited_once_with(config=c._config, isolation_key="project")
    factory.assert_called_once_with(sandbox_type="jiuwenbox", operation_type="fs", endpoint=endpoint, config=c._config)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("failed", [False, True])
async def test_missing_launcher_config_preserves_original_gateway_outcome(streaming, failed):
    c, gateway = client()
    c._config = SandboxGatewayConfig()
    error = httpx.ReadTimeout("original transport error")

    async def stream():
        yield "sandbox"

    if failed:
        gateway.handle_request.side_effect = error
        gateway.handle_stream_request.side_effect = error
    else:
        gateway.handle_request.return_value = SimpleNamespace(code=0, data="sandbox")
        gateway.handle_stream_request.return_value = stream()
    if failed:
        with pytest.raises(httpx.ReadTimeout) as caught:
            if streaming:
                _ = [item async for item in c.invoke_stream("shell", "run")]
            else:
                await c.invoke("shell", "run")
        assert caught.value is error
    elif streaming:
        assert [item async for item in c.invoke_stream("shell", "run")] == ["sandbox"]
    else:
        assert await c.invoke("shell", "run") == "sandbox"


@pytest.fixture
def audit_events(monkeypatch):
    from openjiuwen.core.sys_operation.sandbox import fallback_audit

    events = []
    monkeypatch.setattr(fallback_audit, "_fallback_audit_hook", None)
    # Keep the original provider registration API working for both routes.
    jb.set_fallback_audit_hook(lambda **fields: events.append(fields))
    return events


@pytest.mark.asyncio
@pytest.mark.parametrize("op_type", ["fs", "shell", "code"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_trusted_host_mode_change_is_audited_even_when_provider_replay_is_never(
    monkeypatch, audit_events, op_type, streaming,
):
    import openjiuwen.core.sys_operation as operations

    c, gateway = client(AsyncMock(return_value=LocalWorkConfig()))
    c._config.launcher_config.extra_params["fallback_policy"] = "never"

    async def stream(**kwargs):
        yield "local"

    local = stream if streaming else AsyncMock(return_value="local")
    monkeypatch.setattr(operations, "SysOperation", lambda card: SimpleNamespace(
        **{op_type: lambda: SimpleNamespace(run=local)},
    ))
    if streaming:
        assert [item async for item in c.invoke_stream(op_type, "run", command="once")] == ["local"]
    else:
        assert await c.invoke(op_type, "run", command="once") == "local"
    assert len(audit_events) == 1
    event = audit_events[0]
    assert event["op"] == f"{op_type}.run"
    assert event["policy"] == "runtime_guard" and event["provider_policy"] == "never"
    assert event["action_taken"] == "local_fallback"
    gateway.handle_request.assert_not_awaited()
    gateway.handle_stream_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_guard_rejection_is_audited_without_masking_error_or_dispatching(audit_events, streaming):
    error = RuntimeError("auto_fallback=false or ACL cleanup pending")
    c, gateway = client(AsyncMock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        if streaming:
            _ = [item async for item in c.invoke_stream("shell", "run", command=["invalid"])]
        else:
            await c.invoke("shell", "run", command=["invalid"])
    assert caught.value is error
    assert len(audit_events) == 1 and audit_events[0]["action_taken"] == "reject"
    gateway.handle_request.assert_not_awaited()
    gateway.handle_stream_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_failing_shared_audit_sink_does_not_change_guard_error(monkeypatch, audit_events):
    def fail(**kwargs):
        raise ValueError("audit sink failed")

    jb.set_fallback_audit_hook(fail)
    error = RuntimeError("ACL cleanup pending")
    c, _ = client(AsyncMock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        await c.invoke("fs", "read_file")
    assert caught.value is error


def test_provider_and_gateway_use_the_same_registered_audit_sink(audit_events):
    jb._emit_fallback_audit(domain="sandbox", op="execute_cmd", policy="never", action_taken="reject")
    assert audit_events == [{"domain": "sandbox", "op": "execute_cmd", "policy": "never", "action_taken": "reject"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("op_type", ["fs", "shell", "code"])
async def test_retained_client_routes_all_operations_locally_after_degradation(monkeypatch, op_type):
    import openjiuwen.core.sys_operation as operations

    guard = AsyncMock(return_value=None)
    c, gateway = client(guard)
    gateway.handle_request.return_value = SimpleNamespace(code=0, data="sandbox")
    assert await c.invoke(op_type, "run", value="once") == "sandbox"
    local = AsyncMock(return_value="local")
    constructor = Mock(return_value=SimpleNamespace(**{op_type: lambda: SimpleNamespace(run=local)}))
    monkeypatch.setattr(operations, "SysOperation", constructor)
    guard.return_value = LocalWorkConfig(shell_allowlist=None)
    assert await c.invoke(op_type, "run", value="twice") == "local"
    assert await c.invoke(op_type, "run", value="three") == "local"
    assert local.await_count == 2
    gateway.handle_request.assert_awaited_once()
    constructor.assert_called_once()


@pytest.mark.asyncio
async def test_post_dispatch_timeout_is_never_replayed():
    guard = AsyncMock(return_value=None)
    c, gateway = client(guard)
    gateway.handle_request.side_effect = httpx.ReadTimeout("response lost after execution")
    with pytest.raises(httpx.ReadTimeout):
        await c.invoke("shell", "execute", command="side effect")
    guard.assert_awaited_once()
    gateway.handle_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_observer_cannot_mask_dispatched_error():
    c, gateway = client(AsyncMock(return_value=None))
    observer = Mock(side_effect=ValueError("diagnostics failed"))
    c._config.launcher_config.extra_params["runtime_error_handler"] = observer
    gateway.handle_request.side_effect = httpx.ReadTimeout("response lost")
    with pytest.raises(httpx.ReadTimeout, match="response lost"):
        await c.invoke("shell", "execute", command="side effect")
    observer.assert_called_once()
    gateway.handle_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_error_result_is_observed_without_replay():
    from openjiuwen.core.sys_operation.result import ExecuteCmdResult

    c, gateway = client(AsyncMock(return_value=None))
    observer = Mock()
    c._config.launcher_config.extra_params["runtime_error_handler"] = observer
    result = ExecuteCmdResult(code=1, message="transport failed")
    gateway.handle_request.return_value = SimpleNamespace(code=0, data=result)
    assert await c.invoke("shell", "execute_cmd", command="once") is result
    observer.assert_called_once()
    gateway.handle_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_stream_is_not_replayed():
    guard = AsyncMock(return_value=None)
    c, gateway = client(guard)

    async def stream():
        yield "executed"
        raise httpx.ReadTimeout("lost stream")

    gateway.handle_stream_request.return_value = stream()
    received = []
    with pytest.raises(httpx.ReadTimeout):
        async for item in c.invoke_stream("shell", "execute_stream", command="side effect"):
            received.append(item)
    assert received == ["executed"]
    guard.assert_awaited_once()
    gateway.handle_stream_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_stream_is_supported(monkeypatch):
    import openjiuwen.core.sys_operation as operations

    c, gateway = client(AsyncMock(return_value=LocalWorkConfig()))

    async def stream(**kwargs):
        yield kwargs

    monkeypatch.setattr(operations, "SysOperation", lambda card: SimpleNamespace(
        shell=lambda: SimpleNamespace(execute_stream=stream),
    ))
    assert [item async for item in c.invoke_stream("shell", "execute_stream", command="once")] == [{"command": "once"}]
    gateway.handle_stream_request.assert_not_awaited()


@pytest.mark.parametrize("kind", [None, "runtime_start"])
def test_creation_error_requires_successful_cleanup_before_it_is_retryable(monkeypatch, kind):
    connection = httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(lambda request:
        httpx.Response(201, json={"id": "sb-test", "phase": "error", "failure_kind": kind, "error_message": "failed"})))
    monkeypatch.setattr(jb, "build_jiuwenbox_http_client", lambda *a, **kw: connection)
    box = jb._JiuwenBoxClient("http://localhost")
    monkeypatch.setattr(box, "delete_sandbox", Mock())
    expected = jb.JiuwenBoxUnavailableError if kind else RuntimeError
    with pytest.raises(expected):
        box.create_sandbox()
    box.delete_sandbox.assert_called_once_with("sb-test")
    box.delete_sandbox.side_effect = ValueError("ACL cleanup failed")
    with pytest.raises(ValueError, match="ACL cleanup failed"):
        box.create_sandbox()
    connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_preparation_checks_existing_instance_before_any_tool_is_sent(monkeypatch, ready):
    provider = jb._JiuwenBoxProviderMixin()
    provider._assert_policy_ready = Mock()
    provider._shared_scope_key = lambda: "probe"
    monkeypatch.setattr(provider, "_creation_failures", {})
    provider._acquire_sandbox_id = AsyncMock(return_value="old")
    connection = SimpleNamespace(sandbox_ready=Mock(return_value=ready))
    provider._get_client = Mock(return_value=connection)
    provider._recreate_sandbox_after_loss = AsyncMock(return_value="new")
    await provider.prepare_for_execution()
    connection.sandbox_ready.assert_called_once_with("old")
    if ready:
        provider._recreate_sandbox_after_loss.assert_not_awaited()
    else:
        provider._recreate_sandbox_after_loss.assert_awaited_once_with(stale_sandbox_id="old")


@pytest.mark.parametrize("phase,expected", [("ready", True), ("stopped", False),
                                         ("error", None), ("deleting", None)])
def test_instance_probe_never_turns_unknown_acl_or_pending_phase_into_recreation(monkeypatch, phase, expected):
    connection = httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"phase": phase, "error_message": "ACL/policy pending"}),
    ))
    monkeypatch.setattr(jb, "build_jiuwenbox_http_client", lambda *a, **kw: connection)
    box = jb._JiuwenBoxClient("http://localhost")
    if expected is None:
        with pytest.raises(RuntimeError, match="ACL/policy pending"):
            box.sandbox_ready("old")
    else:
        assert box.sandbox_ready("old") is expected
    connection.close()


def test_provisioning_is_polled_until_ready_without_recreating_or_dispatching(monkeypatch):
    phases = iter(["provisioning", "provisioning", "ready"])
    requests = []

    def respond(request):
        requests.append(request.method)
        return httpx.Response(200, json={"phase": next(phases)})

    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(respond)) as connection:
        monkeypatch.setattr(jb, "build_jiuwenbox_http_client", lambda *a, **kw: connection)
        sleep = Mock()
        monkeypatch.setattr(jb.time, "sleep", sleep)
        assert jb._JiuwenBoxClient("http://localhost").sandbox_ready("pending") is True
    assert requests == ["GET"] * 3
    assert sleep.call_count == 2


def test_provisioning_timeout_blocks_without_marking_the_creator_retryable(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request.method)
        return httpx.Response(200, json={"phase": "provisioning"})

    with httpx.Client(base_url="http://localhost", timeout=1, transport=httpx.MockTransport(respond)) as connection:
        monkeypatch.setattr(jb, "build_jiuwenbox_http_client", lambda *a, **kw: connection)
        ticks = iter([0.0, 0.0, 1.0])
        monkeypatch.setattr(jb.time, "monotonic", lambda: next(ticks))
        with pytest.raises(RuntimeError, match="timed out while provisioning") as caught:
            jb._JiuwenBoxClient("http://localhost").sandbox_ready("pending")
        assert not isinstance(caught.value, jb.JiuwenBoxUnavailableError)
    assert requests == ["GET"]
