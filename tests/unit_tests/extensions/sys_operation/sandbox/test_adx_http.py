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
    sandbox_type: str = "adx",
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


@pytest.mark.parametrize("sandbox_type", ["adx", "yuanrong"])
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
        if sandbox_type == "adx":
            assert body["runtime"] == "runsc"
            assert body["rootfs"] == {"type": "template", "name": "tool-runtime"}
            assert body["network"] == {"blockNetwork": True}
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
        card.gateway_config.auth_headers = {"Authorization": "Bearer local-test"}
        extra = card.gateway_config.launcher_config.extra_params
        if sandbox_type == "adx":
            extra.clear()
            extra.update(runtime="runsc", rootfs_template="tool-runtime", network={"blockNetwork": True})
        else:
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
