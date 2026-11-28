# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end smoke tests for the ADX sandbox provider.

Gated on ``RUN_YUANRONG_TEST=1``. Requires a reachable ADX HTTP endpoint.
"""
from __future__ import annotations

import os
import shlex
import uuid
from types import SimpleNamespace
from typing import AsyncIterator, Optional

import pytest
import pytest_asyncio
from aiohttp import web

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.memory.lite.memory_tool_context import MemoryToolContext
from openjiuwen.core.memory.lite.memory_tool_ops import write_memory_with_context
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import SandboxGatewayClient
from openjiuwen.extensions.sys_operation.sandbox.providers import adx

requires_adx = pytest.mark.skipif(
    os.environ.get("RUN_YUANRONG_TEST") != "1",
    reason="Requires a running ADX HTTP service",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [{"head": -1}, {"tail": -1}, {"line_range": (2, 1)}, {"line_range": (0, 1)}])
async def test_read_file_rejects_invalid_line_selection(monkeypatch, selection):
    provider = object.__new__(adx.AdxFSProvider)
    monkeypatch.setattr(provider, "_fs_read_sync", lambda *args, **kwargs: "first\nsecond\n")

    result = await provider.read_file("/workspace/notes.txt", **selection)

    assert result.code != StatusCode.SUCCESS.code
    assert result.data is None


def _base_url() -> str:
    return os.environ.get("YUANRONG_TEST_BASE_URL", "http://127.0.0.1:18093")


def _executor() -> str:
    value = os.environ.get("YUANRONG_TEST_EXECUTOR", "supervisor").strip().lower().strip("'\"")
    return value if value in {"supervisor", "docker"} else "supervisor"


def _extra_params() -> dict:
    params: dict = {"executor": _executor()}
    if _executor() == "docker":
        image = os.environ.get("YUANRONG_TEST_IMAGE")
        if image:
            params["image"] = image
    return params


def _build_card(
    card_id: str,
    *,
    custom_id: Optional[str] = None,
    sandbox_type: str = "adx-environment",
) -> SysOperationCard:
    return SysOperationCard(
        id=card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.CUSTOM if custom_id else ContainerScope.SYSTEM,
                custom_id=custom_id,
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url=_base_url(),
                sandbox_type=sandbox_type,
                idle_ttl_seconds=600,
                extra_params=_extra_params(),
            ),
            timeout_seconds=30,
        ),
    )


async def _release_sandbox_for_sys_operation(sys_operation_id: str) -> None:
    sys_op = Runner.resource_mgr.get_sys_operation(sys_operation_id)
    if sys_op is None:
        return
    isolation_key = sys_op.isolation_key_template
    if not isolation_key:
        return
    try:
        await SandboxGatewayClient.release(isolation_key, on_stop="delete")
    except Exception as exc:
        if "not found" in str(exc).lower():
            return
        raise


async def _remove_sys_operation_with_sandbox_release(sys_operation_id: str) -> None:
    await _release_sandbox_for_sys_operation(sys_operation_id)
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=sys_operation_id)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture() -> AsyncIterator[SysOperation]:
    await Runner.start()
    card_id = f"adx_smoke_{uuid.uuid4().hex[:8]}"
    card = _build_card(card_id, custom_id=card_id)
    add_res = Runner.resource_mgr.add_sys_operation(card)
    assert add_res.is_ok()
    try:
        yield Runner.resource_mgr.get_sys_operation(card_id)
    finally:
        await _remove_sys_operation_with_sandbox_release(card_id)
        await Runner.stop()


@pytest.mark.asyncio
@requires_adx
async def test_shell_and_code_share_sandbox(sys_op: SysOperation):
    marker = uuid.uuid4().hex[:8]
    path = f"/tmp/adx_share_{marker}.txt"
    content = f"shell-to-code-{marker}"

    shell_write = await sys_op.shell().execute_cmd(f"printf %s {shlex.quote(content)} > {path}")
    assert shell_write.code == StatusCode.SUCCESS.code
    assert shell_write.data.exit_code == 0

    code_read = await sys_op.code().execute_code(
        code=f"from pathlib import Path; print(Path({path!r}).read_text())",
        language="python",
    )
    assert code_read.code == StatusCode.SUCCESS.code
    assert code_read.data.exit_code == 0
    # ADX execute may prepend extra lines (e.g. runtime banner); assert payload is present.
    assert content in (code_read.data.stdout or "")

    code_write_path = f"/tmp/adx_code_{marker}.txt"
    code_write = await sys_op.code().execute_code(
        code=f"from pathlib import Path; Path({code_write_path!r}).write_text('code-visible-to-shell')",
        language="python",
    )
    assert code_write.code == StatusCode.SUCCESS.code
    assert code_write.data.exit_code == 0

    shell_read = await sys_op.shell().execute_cmd(f"cat {code_write_path}")
    assert shell_read.code == StatusCode.SUCCESS.code
    assert shell_read.data.exit_code == 0
    assert "code-visible-to-shell" in (shell_read.data.stdout or "")


@pytest.mark.asyncio
@requires_adx
async def test_release_deletes_cached_sandbox():
    await Runner.start()
    marker = uuid.uuid4().hex[:8]
    card_id = f"adx_delete_{marker}"
    card = _build_card(card_id, custom_id=card_id)
    assert Runner.resource_mgr.add_sys_operation(card).is_ok()
    try:
        sys_op = Runner.resource_mgr.get_sys_operation(card_id)
        res = await sys_op.shell().execute_cmd("echo delete-me")
        assert res.code == StatusCode.SUCCESS.code
        assert adx._AdxProviderMixin._shared_instances

        await _release_sandbox_for_sys_operation(card_id)
        remaining = [
            key for key in adx._AdxProviderMixin._shared_instances
            if card_id in key or marker in key
        ]
        assert remaining == []
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()


@pytest.mark.asyncio
@requires_adx
async def test_docker_executor_smoke():
    if _executor() != "docker":
        pytest.skip("Set YUANRONG_TEST_EXECUTOR=docker to run docker smoke")

    await Runner.start()
    card_id = f"adx_docker_{uuid.uuid4().hex[:8]}"
    card = _build_card(card_id, custom_id=card_id)
    assert Runner.resource_mgr.add_sys_operation(card).is_ok()
    try:
        sys_op = Runner.resource_mgr.get_sys_operation(card_id)
        res = await sys_op.shell().execute_cmd("pwd")
        assert res.code == StatusCode.SUCCESS.code
        assert res.data.exit_code == 0
        assert res.data.stdout.strip()
    finally:
        await _remove_sys_operation_with_sandbox_release(card_id)
        await Runner.stop()


@pytest.mark.parametrize("sandbox_type", ["adx-environment", "yuanrong"])
@pytest.mark.asyncio
async def test_sys_operation_uses_the_adx_http_provider(monkeypatch, sandbox_type, tmp_path):
    requests = []
    remote_path = "/workspace/space + 测试.bin"
    remote = {}
    environment_id = "tenant+test"
    payload = bytes(range(256)) * 512

    async def create(request):
        requests.append("create")
        body = await request.json()
        if sandbox_type == "adx-environment":
            assert body["runtime"] == "runsc"
            assert body["rootfs"] == {"type": "local", "path": "/objects/tool/rootfs"}
            assert body["network"] == {"blockNetwork": True}
            assert body["restartPolicy"] == {"maxAttempts": 2}
            assert body["createTimeoutSeconds"] == 60
            assert body["scheduleTimeoutSeconds"] == 20
            assert body["mounts"] == [
                {"type": "bind", "host_path": "/host", "target": "/workspace", "options": ["rw"]},
            ]
        else:
            assert body["runtime"] == "conch"
            assert body["mounts"] == [
                {"type": "bind", "host_path": "/host", "target": "/workspace", "options": ["rw"]}
            ]
            assert body["cwd"] == "/workspace"
        return web.json_response({"data": {"environmentId": environment_id}})

    async def invoke(request):
        assert request.match_info["instance"] == environment_id
        body = await request.json()
        action, args = body["action"], body["args"]
        requests.append(action)
        if action == "process.exec":
            return web.json_response({"stdout": "ready", "stderr": "", "exit_code": 0})
        if action == "file.exists":
            return web.json_response({"exists": args["path"] in remote})
        if action == "file.read":
            return web.json_response({"data": "workspace"})
        if action == "file.write":
            path = args["path"]
            if not args.get("create_if_missing", True) and path not in remote:
                return web.json_response({"error": "No such file"}, status=400)
            data = bytes.fromhex(args["data"]) if args["binary"] else args["data"].encode()
            if args.get("append", False):
                data = remote.get(path, b"") + data
            remote[path] = data
            return web.json_response({"size": len(data), "error": None})
        raise AssertionError(f"Unexpected action: {action}")

    async def file_transfer(request):
        path = request.query["path"]
        assert request.match_info["instance"] == environment_id
        assert request.query["type"] == "file"
        assert path == remote_path
        if request.method == "POST":
            requests.append("upload")
            assert request.content_type == "application/octet-stream"
            assert request.content_length == len(payload)
            remote[path] = await request.read()
            return web.json_response({"bytes_written": len(remote[path])})

        requests.append("download")
        if requests.count("download") == 3:
            response = web.StreamResponse(headers={"Content-Length": str(len(payload))})
            await response.prepare(request)
            await response.write(payload[:1024])
            request.transport.close()
            return response
        return web.Response(body=remote[path])

    async def delete(request):
        assert request.match_info["instance"] == environment_id
        requests.append("delete")
        return web.Response(status=204)

    @web.middleware
    async def auth(request, handler):
        if request.headers.get("Authorization") != "Bearer local-test":
            return web.json_response({"message": "missing test bearer header"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[auth])
    app.router.add_post("/api/environment/v1/environments", create)
    app.router.add_delete("/api/environment/v1/environments/{instance}", delete)
    app.router.add_post("/direct/{instance}/invoke", invoke)
    app.router.add_post("/direct/{instance}/upload", file_transfer)
    app.router.add_get("/direct/{instance}/download", file_transfer)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    monkeypatch.setenv("YUANRONG_TEST_BASE_URL", f"http://127.0.0.1:{runner.addresses[0][1]}")

    await Runner.start()
    card_id = f"adx_rest_{uuid.uuid4().hex[:8]}"
    try:
        card = _build_card(card_id, custom_id=card_id, sandbox_type=sandbox_type)
        extra = card.gateway_config.launcher_config.extra_params
        if sandbox_type == "adx-environment":
            monkeypatch.setenv("TEST_ADX_PROVIDER_TOKEN", "local-test")
            extra.clear()
            extra.update(
                runtime="runsc", rootfs={"type": "local", "path": "/objects/tool/rootfs"}, network={"blockNetwork": True},
                restart_policy={"maxAttempts": 2}, create_timeout=60, schedule_timeout=20,
                token_env="TEST_ADX_PROVIDER_TOKEN",
                mounts=[{"host_path": "/host", "target": "/workspace", "options": ["rw"]}],
            )
        else:
            card.gateway_config.auth_headers = {"Authorization": "Bearer local-test"}
            extra.update(executor="conch", workdir="/workspace", mounts=[{"source": "/host", "target": "/workspace"}])
        assert Runner.resource_mgr.add_sys_operation(card).is_ok()
        sys_op = Runner.resource_mgr.get_sys_operation(card_id)
        shell = await sys_op.shell().execute_cmd("echo ready")
        assert shell.code == StatusCode.SUCCESS.code and shell.data.stdout == "ready"
        fs = sys_op.fs()
        text = await fs.read_file("/workspace/test.txt")
        assert text.code == StatusCode.SUCCESS.code and text.data.content == "workspace"

        local_source = tmp_path / "source.bin"
        local_source.write_bytes(payload)
        disabled_parent_create = await fs.upload_file(str(local_source), remote_path, create_parent_dirs=False)
        assert disabled_parent_create.code != StatusCode.SUCCESS.code
        assert "Parent directory does not exist" in disabled_parent_create.message
        assert "upload" not in requests

        uploaded = [result async for result in fs.upload_file_stream(str(local_source), remote_path, chunk_size=8192)]
        assert uploaded[-1].code == StatusCode.SUCCESS.code and uploaded[-1].data.is_last_chunk
        assert remote[remote_path] == payload
        if_exists = await fs.upload_file(str(local_source), remote_path, overwrite=False)
        assert if_exists.code != StatusCode.SUCCESS.code and "File already exists" in if_exists.message
        assert requests.count("upload") == 1

        binary = [result async for result in fs.read_file_stream(remote_path, mode="bytes", chunk_size=16384)]
        assert all(result.code == StatusCode.SUCCESS.code and result.data.chunk_size <= 16384 for result in binary)
        assert binary[-1].data.is_last_chunk
        assert b"".join(result.data.chunk_content for result in binary) == payload

        local_target = tmp_path / "download.bin"
        downloaded = [result async for result in fs.download_file_stream(remote_path, str(local_target))]
        assert downloaded[-1].code == StatusCode.SUCCESS.code and downloaded[-1].data.is_last_chunk
        assert downloaded[-1].data.chunk_size == len(payload)
        assert local_target.read_bytes() == payload
        failed = await fs.download_file(remote_path, str(local_target), overwrite=True)
        assert failed.code != StatusCode.SUCCESS.code
        assert local_target.read_bytes() == payload
        assert sorted(path.name for path in tmp_path.iterdir()) == ["download.bin", "source.bin"]

        append = await fs.write_file(
            remote_path, b"\x00\xff", mode="bytes", append=True, create_if_not_exist=False
        )
        assert append.code == StatusCode.SUCCESS.code
        assert append.data.size == 2 and remote[remote_path] == payload + b"\x00\xff"

        missing_path = "/workspace/missing.bin"
        missing = await fs.write_file(missing_path, b"extra", mode="bytes", append=True, create_if_not_exist=False)
        assert missing.code != StatusCode.SUCCESS.code and missing_path not in remote
        overwritten = await fs.write_file(remote_path, "replacement", prepend_newline=False)
        assert overwritten.code == StatusCode.SUCCESS.code and remote[remote_path] == b"replacement"

        context = MemoryToolContext(
            workspace=SimpleNamespace(get_node_path=lambda _: "/workspace"),
            sys_operation=sys_op,
        )
        memory_path = "/workspace/notes.md"
        remote[memory_path] = b"original memory"
        memory = await write_memory_with_context(context, "notes.md", "extra memory", append=True)
        assert memory["success"], memory
        assert remote[memory_path] == b"original memory\nextra memory"
    finally:
        try:
            await _remove_sys_operation_with_sandbox_release(card_id)
        finally:
            await Runner.stop()
            await runner.cleanup()
    assert requests.count("create") == 1
    assert requests[-1] == "delete"


@pytest.mark.parametrize("path", ["relative", "/objects/../rootfs", "/objects/abc\n"])
def test_reject_invalid_local_rootfs_path(path):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx import _adx_create_body

    with pytest.raises(ValueError, match="Local rootfs"):
        _adx_create_body({"rootfs": {"type": "local", "path": path}}, "supervisor")


@pytest.mark.asyncio
async def test_local_rootfs_variants_are_cleaned_by_the_original_scope(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    base = "https://example.test"
    scope = adx.build_adx_shared_scope_key(base, "alice")
    first, second, unrelated = (scope + "|rootfs=first", scope + "|rootfs=second", base + "|alice-extra")
    instances = {first: ("one", "runsc", {}), second: ("two", "runsc", {}), unrelated: ("other", "runsc", {})}
    monkeypatch.setattr(adx._AdxProviderMixin, "_shared_instances", instances)
    deleted = []

    def delete(base_url, environment_id, headers):
        deleted.append((base_url, environment_id))

    monkeypatch.setattr(adx, "_delete_environment", delete)
    assert await adx.delete_adx_sandbox(shared_key=scope) == [first, second]
    assert deleted == [(base, "one"), (base, "two")]
    assert set(instances) == {unrelated}


def test_create_body_carries_idle_lifecycle():
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx import _adx_create_body

    body = _adx_create_body({
        "runtime": "runsc",
        "idle_timeout": 900,
        "idle_action": "pause",
        "pause_policy": {
            "retention": {"kind": "ttl", "ttlSeconds": 86400},
            "timeoutSeconds": 300,
            "storage": "configured",
        },
        "failover": True,
    }, "supervisor")
    assert body["idleAction"] == "pause"
    assert body["pauseOptions"] == {
        "retention": {"kind": "ttl", "ttlSeconds": 86400},
        "timeoutSeconds": 300,
        "storage": "configured",
    }
    assert body["failover"] is True


@pytest.mark.parametrize("extra", [
    {"idle_action": "keep"},
    {"idle_action": "pause", "idle_timeout": 0},
    {"idle_action": "pause", "idle_timeout": 900, "pause_policy": {"retention": {"kind": "ttl", "ttlSeconds": 0}}},
    {"idle_action": "pause", "idle_timeout": 900, "pause_policy": {"retention": {"kind": "ttl", "ttlSeconds": -1}}},
    {"idle_action": "pause", "idle_timeout": 900, "pause_policy": {"retention": {"kind": "forever"}, "timeoutSeconds": 0}},
    {"idle_action": "pause", "idle_timeout": 900, "pause_policy": {"retention": {"kind": "forever"}, "storage": "cluster"}},
    {"failover": "yes"},
])
def test_create_body_rejects_invalid_lifecycle(extra):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx import _adx_create_body

    with pytest.raises(ValueError):
        _adx_create_body(dict(extra), "supervisor")


def test_create_body_rejects_pause_for_conch():
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx import _adx_create_body

    with pytest.raises(ValueError, match="conch"):
        _adx_create_body({"runtime": "conch", "idle_action": "pause", "idle_timeout": 900}, "conch")


def test_shared_scope_tracks_lifecycle():
    import copy

    from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    def scope(extra):
        provider = object.__new__(adx.AdxShellProvider)
        provider.endpoint = SandboxEndpoint(base_url="https://example.test", isolation_key="alice")
        provider.config = SandboxGatewayConfig(launcher_config=PreDeployLauncherConfig(
            base_url="https://example.test", sandbox_type="adx-environment", extra_params=extra))
        provider._executor = "supervisor"
        return provider._shared_scope_key()

    pause = {
        "runtime": "runsc", "idle_timeout": 900, "idle_action": "pause",
        "pause_policy": {"retention": {"kind": "ttl", "ttlSeconds": 86400}},
        "failover": True,
    }
    assert "|lifecycle=" not in scope({"runtime": "runsc"})
    assert scope(copy.deepcopy(pause)) == scope(copy.deepcopy(pause))
    assert scope(pause) != scope({**copy.deepcopy(pause), "idle_timeout": 3600})


def _conflict_provider(monkeypatch, responses):
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    deletes = []
    calls = []

    def fake_request(method, url, *, payload=None, headers=None, timeout=30):
        calls.append({"method": method, "url": url, "payload": payload, "headers": headers or {}})
        if "/resume" in url:
            kind = "resume"
        elif "/api/instances" in url:
            kind = "instances"
        else:
            kind = "create"
        return responses[kind].pop(0)

    provider = object.__new__(adx.AdxShellProvider)
    provider._instance = None
    provider._auth_headers = {"X-Auth": "test"}
    provider._timeout_seconds = 30
    provider._executor = "supervisor"
    provider.endpoint = SimpleNamespace(base_url="http://adx.test")
    provider.config = SimpleNamespace(
        launcher_config=SimpleNamespace(
            extra_params={
                "executor": "supervisor",
                "idle_timeout": 900,
                "idle_action": "pause",
                "pause_policy": {"retention": {"kind": "forever"}, "storage": "local"},
            }
        )
    )
    monkeypatch.setattr(adx, "_adx_request", fake_request)
    monkeypatch.setattr(adx, "_delete_environment", lambda base, eid, headers: deletes.append(eid))
    return provider, calls, deletes


def _paused_conflict():
    return {
        "code": 409,
        "data": None,
        "error": {
            "code": "CONFLICT",
            "retry": "never",
            "outcome": "not_started",
            "environmentId": "default-tools-x",
        },
        "message": "environment default-tools-x is paused; resume it explicitly",
    }


def test_create_conflict_resumes_a_paused_environment(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    responses = {
        "create": [(409, _paused_conflict())],
        "instances": [(200, [{"id": "default-tools-x", "status": "paused"}])],
        "resume": [(200, {"data": {"environmentId": "default-tools-x", "state": "running"}})],
    }
    provider, calls, deletes = _conflict_provider(monkeypatch, responses)

    assert provider._create_instance() == ("default-tools-x", "supervisor")
    resume_calls = [call for call in calls if "/resume" in call["url"]]
    assert len(resume_calls) == 1
    assert resume_calls[0]["method"] == "POST"
    assert resume_calls[0]["headers"]["X-ADX-Request-ID"].startswith("resume-")
    assert deletes == []


def test_create_conflict_discards_an_unusable_restore_point(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    responses = {
        "create": [(409, _paused_conflict())],
        "instances": [
            (200, [{"id": "default-tools-x", "status": "paused"}]),
            (200, [{"id": "default-tools-x", "status": "paused"}]),
        ],
        "resume": [
            (
                409,
                {
                    "code": 409,
                    "message": "local restore point is unavailable on another node",
                    "error": {
                        "code": "CONFLICT",
                        "retry": "never",
                        "outcome": "not_started",
                        "environmentId": "default-tools-x",
                    },
                },
            )
        ],
    }
    provider, _, deletes = _conflict_provider(monkeypatch, responses)

    with pytest.raises(adx.AdxHttpError) as raised:
        provider._create_instance()
    assert raised.value.status == 409
    assert deletes == ["default-tools-x"]


def test_create_conflict_transient_resume_failure_keeps_the_environment(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    responses = {
        "create": [(409, _paused_conflict())],
        "instances": [(200, [{"id": "default-tools-x", "status": "paused"}])],
        "resume": [
            (
                503,
                {
                    "code": 503,
                    "message": "owner node missing",
                    "error": {
                        "code": "OUTCOME_UNKNOWN",
                        "retry": "same_operation",
                        "outcome": "unknown",
                        "environmentId": "default-tools-x",
                    },
                },
            )
        ],
    }
    provider, _, deletes = _conflict_provider(monkeypatch, responses)

    with pytest.raises(adx.AdxHttpError) as raised:
        provider._create_instance()
    assert raised.value.status == 503
    assert deletes == []


def test_create_conflict_binds_a_running_environment(monkeypatch):
    responses = {
        "create": [(409, _paused_conflict())],
        "instances": [(200, [{"id": "default-tools-x", "status": "running"}])],
        "resume": [],
    }
    provider, calls, deletes = _conflict_provider(monkeypatch, responses)

    assert provider._create_instance() == ("default-tools-x", "supervisor")
    assert not any("/resume" in call["url"] for call in calls)
    assert deletes == []


def test_create_conflict_recreates_when_the_environment_vanished(monkeypatch):
    responses = {
        "create": [
            (409, _paused_conflict()),
            (200, {"data": {"environmentId": "default-tools-x", "status": "running"}}),
        ],
        "instances": [(404, {"message": "not found"})],
        "resume": [],
    }
    provider, calls, deletes = _conflict_provider(monkeypatch, responses)

    assert provider._create_instance() == ("default-tools-x", "supervisor")
    assert len([call for call in calls if call["method"] == "POST" and call["url"].endswith("/environments")]) == 2
    assert deletes == []


def test_create_uses_a_deterministic_environment_name(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    payloads = []

    def fake_request(method, url, *, payload=None, headers=None, timeout=30):
        payloads.append(dict(payload or {}))
        return 200, {"data": {"environmentId": "default-" + payload["name"]}}

    provider = object.__new__(adx.AdxShellProvider)
    provider._instance = None
    provider._auth_headers = {}
    provider._timeout_seconds = 30
    provider._executor = "runsc"
    provider.endpoint = SimpleNamespace(base_url="http://adx.test")
    provider.config = SimpleNamespace(
        launcher_config=SimpleNamespace(extra_params={"executor": "runsc", "idle_timeout": 0}),
    )
    monkeypatch.setattr(adx, "_adx_request", fake_request)

    first, _ = provider._create_instance()
    second, _ = provider._create_instance()

    assert first == second
    assert len(payloads) == 2
    assert payloads[0]["name"] == payloads[1]["name"]
    assert payloads[0]["name"].startswith("tools-")
    assert first == "default-" + payloads[0]["name"]


def test_create_body_converts_schema_v2_network_settings():
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    body = adx._adx_create_body(
        {
            "idle_timeout": 0,
            "network": {
                "traffic": {
                    "ingress_default_action": "deny",
                    "egress_default_action": "allow",
                    "rules": [
                        {
                            "action": "deny",
                            "direction": "egress",
                            "protocol": "any",
                            "cidr": "10.0.0.0/8",
                            "port_range": 80,
                            "priority": 200,
                        }
                    ],
                }
            },
        },
        "runsc",
    )
    network = body["network"]
    assert network["schemaVersion"] == 2
    assert network["traffic"]["ingressDefaultAction"] == "deny"
    assert network["traffic"]["egressDefaultAction"] == "allow"
    assert network["traffic"]["mode"] == "stateful"
    assert network["traffic"]["rules"][0]["portRange"] == 80
    assert "port_range" not in network["traffic"]["rules"][0]

    legacy = adx._adx_create_body({"idle_timeout": 0, "network": {"block_network": True}}, "runsc")
    assert legacy["network"] == {"block_network": True}


def test_init_adx_uses_the_agentos_token_without_connection(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    monkeypatch.setenv("ADX_TOKEN", "tenant-token")
    provider = object.__new__(adx.AdxShellProvider)
    provider.endpoint = SimpleNamespace(base_url="http://adx.test")
    provider.config = SimpleNamespace(
        launcher_config=SimpleNamespace(extra_params={"executor": "runsc"}),
        timeout_seconds=30,
        auth_headers={},
    )

    provider._init_adx(provider.endpoint, provider.config)

    assert provider._auth_headers["Authorization"] == "Bearer tenant-token"
    assert provider._auth_headers["X-Auth"] == "tenant-token"
