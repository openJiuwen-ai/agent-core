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
    gateway = SimpleNamespace(handle_request=AsyncMock(), handle_stream_request=AsyncMock())
    config = SandboxGatewayConfig(launcher_config=PreDeployLauncherConfig(
        sandbox_type="jiuwenbox", base_url="http://localhost:8321", extra_params={"runtime_guard": guard},
    ))
    return SandboxGatewayClient(config=config, isolation_key="project", gateway=gateway), gateway


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
