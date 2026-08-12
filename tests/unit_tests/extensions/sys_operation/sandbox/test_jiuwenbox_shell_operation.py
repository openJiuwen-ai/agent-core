# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Jiuwenbox shell-execution tests: E2E (gated) and hybrid routing unit wiring.

E2E tests require a running jiuwenbox service (``RUN_JIUWENBOX_TEST=1``).
Hybrid wiring tests use a mocked ``JiuwenBoxShellProvider`` and do not need
a live sandbox.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.result import ExecuteCmdStreamResult
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as jb
from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import (
    build_jiuwenbox_http_client,
    resolve_jiuwenbox_cli_bin,
)


LONG_RUNNING_COMMAND = ["/usr/bin/python3", "-c", "import time; time.sleep(36000)"]


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint if "://" in endpoint else f"http://{endpoint}"


def _sandbox_has_file(client: httpx.Client, sandbox_id: str, path: str) -> bool:
    """Probe whether ``path`` exists inside the sandbox via jiuwenbox's exec API.

    We can't pair a second ``SysOperationCard`` on the same sandbox to do
    this from the SDK side: ``SysOperationMgr`` rejects the second add
    because ``isolation_key_template`` collides for two CUSTOM-scope cards
    sharing the same ``custom_id``. Going through the HTTP API bypasses
    that guard and is sufficient since we only need a yes/no answer.
    """
    resp = client.post(
        f"/api/v1/sandboxes/{sandbox_id}/exec",
        json={"command": ["/usr/bin/test", "-f", path]},
    )
    resp.raise_for_status()
    return resp.json()["exit_code"] == 0


def _build_card(
    *,
    card_id: str,
    base_url: str,
    sandbox_id: str,
    extra_params: dict[str, Any] | None = None,
) -> SysOperationCard:
    """Build a SysOperationCard pinned to an existing sandbox via ``custom_id``."""
    return SysOperationCard(
        id=card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.CUSTOM,
                custom_id=sandbox_id,
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url=base_url,
                sandbox_type="jiuwenbox",
                idle_ttl_seconds=600,
                extra_params=dict(extra_params or {}),
            ),
            timeout_seconds=30,
        ),
    )


@pytest.fixture
def server_endpoint() -> str:
    return os.environ.get("JIUWENBOX_TEST_SERVER", "127.0.0.1:8321")


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(server_endpoint, monkeypatch) -> AsyncIterator[SysOperation]:
    base_url = _normalize_endpoint(server_endpoint)
    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]

        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)
        await Runner.start()
        card_id = f"jiuwenbox_shell_op_{uuid4().hex[:8]}"
        card = _build_card(card_id=card_id, base_url=base_url, sandbox_id=sandbox_id)

        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()
        try:
            yield Runner.resource_mgr.get_sys_operation(card_id)
        finally:
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_basic_execution(sys_op):
    res = await sys_op.shell().execute_cmd(command="echo hello world")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert "hello world" in res.data.stdout.strip()
    assert res.data.exit_code == 0
    assert res.data.command == "echo hello world"

    res = await sys_op.shell().execute_cmd(command="ls -la /")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert res.data.stdout.strip()
    assert res.data.exit_code == 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_environment_variables(sys_op):
    env = {"TEST_VAR": "custom_value"}
    res = await sys_op.shell().execute_cmd(command="echo $TEST_VAR", environment=env)
    assert res.code == StatusCode.SUCCESS.code
    assert "custom_value" in res.data.stdout.strip()


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_cwd(sys_op):
    await sys_op.shell().execute_cmd(command="mkdir -p /tmp/jiuwenbox_shell_cwd/subdir")
    res = await sys_op.shell().execute_cmd(command="pwd", cwd="/tmp/jiuwenbox_shell_cwd/subdir")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.stdout.strip() == "/tmp/jiuwenbox_shell_cwd/subdir"


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_timeout(sys_op):
    res = await sys_op.shell().execute_cmd(command="python3 -c \"import time; time.sleep(5)\"", timeout=1)
    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message.lower()


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_ping_timeout(sys_op):
    res = await sys_op.shell().execute_cmd(
        command="for i in 1 2 3 4 5 6 7 8 9 10; do echo 127.0.0.1; sleep 1; done",
        timeout=1,
    )
    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message.lower()
    assert res.data is not None
    assert "127.0.0.1" in res.data.stdout


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_list_tools(sys_op):
    tools = sys_op.shell().list_tools()
    assert len(tools) == 3
    tool_names = [tool.name for tool in tools]
    assert "execute_cmd" in tool_names
    assert "execute_cmd_stream" in tool_names
    assert "execute_cmd_background" in tool_names

    exec_tool = next(tool for tool in tools if tool.name == "execute_cmd")
    assert "command" in exec_tool.input_params["properties"]
    assert exec_tool.input_params["required"] == ["command"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_execute_cmd_stream_basic(sys_op):
    cmd = "echo chunk1; sleep 0.01; echo chunk2; sleep 0.01; echo error_chunk 1>&2"
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command=cmd):
        stream_results.append(result)

    assert len(stream_results) > 0
    assert all(isinstance(result, ExecuteCmdStreamResult) for result in stream_results)

    stdout_chunks = [result.data for result in stream_results if result.data.type == "stdout"]
    stderr_chunks = [result.data for result in stream_results if result.data.type == "stderr"]
    exit_chunk = next((result.data for result in stream_results if result.data.exit_code is not None), None)

    stdout_content = "".join(chunk.text for chunk in stdout_chunks)
    assert "chunk1" in stdout_content
    assert "chunk2" in stdout_content
    assert len(stderr_chunks) >= 1
    assert "error_chunk" in stderr_chunks[0].text
    assert exit_chunk is not None
    assert exit_chunk.exit_code == 0
    assert exit_chunk.chunk_index == len(stream_results) - 1


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_execute_cmd_stream_timeout(sys_op):
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command="sleep 10", timeout=1):
        stream_results.append(result)

    error_result = next(
        (result for result in stream_results if result.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code),
        None,
    )
    assert error_result is not None
    assert "timeout" in error_result.message.lower()
    assert error_result.data.exit_code == -1


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_execute_cmd_stream_empty_command(sys_op):
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command=""):
        stream_results.append(result)

    assert len(stream_results) == 1
    error_result = stream_results[0]
    assert error_result.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "command can not be empty" in error_result.message
    assert error_result.data.chunk_index == 0
    assert error_result.data.exit_code == -1


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_execute_cmd_stream_continuous_output(sys_op):
    cmd = "for i in 1 2 3; do echo 127.0.0.1; sleep 0.1; done"
    stream_results = []
    async for res in sys_op.shell().execute_cmd_stream(command=cmd, timeout=10):
        stream_results.append(res)

    stdout_chunks = [result for result in stream_results if result.data.type == "stdout"]
    assert len(stdout_chunks) >= 1
    combined_stdout = "".join(result.data.text for result in stdout_chunks)
    assert "127.0.0.1" in combined_stdout

    exit_chunk = next(result for result in stream_results if result.data.exit_code is not None)
    assert exit_chunk.data.exit_code == 0


# ===========================================================================
# E2E tests: ``excluded_commands`` pre-route + ``fallback_on_failure``.
#
# Key idea: register a single ``SysOperationCard`` carrying the routing flag
# under test, then verify behaviour by inspecting both
#   1. the host filesystem (so local execution truly happened);
#   2. the sandbox container directly via the jiuwenbox HTTP exec API (so
#      the command did not run there — or did run there but failed before
#      writing). We can't pair a second SDK card on the same sandbox for
#      step 2: ``SysOperationMgr`` rejects two CUSTOM-scope cards that
#      share the same ``custom_id`` (their isolation_key_template collides).
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_excluded_commands_pre_routes_to_host_not_sandbox(
    server_endpoint, monkeypatch, tmp_path: Path
):
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    host_marker = tmp_path / f"jiuwenbox_local_route_{marker}.txt"
    payload = "local-via-pre-route"

    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]
        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)

        await Runner.start()
        local_card_id = f"jiuwenbox_local_route_local_{marker}"
        local_added = False

        local_card = _build_card(
            card_id=local_card_id,
            base_url=base_url,
            sandbox_id=sandbox_id,
            extra_params={"excluded_commands": ["printf *"]},
        )

        try:
            assert Runner.resource_mgr.add_sys_operation(local_card).is_ok()
            local_added = True

            local_op = Runner.resource_mgr.get_sys_operation(local_card_id)

            cmd = f"printf {payload} > {host_marker}"
            res = await local_op.shell().execute_cmd(cmd)
            assert res.code == StatusCode.SUCCESS.code
            assert res.data.exit_code == 0

            # 1) Host file really exists ⇒ the pre-route ran the command on
            # the test host's filesystem.
            assert host_marker.exists()
            assert host_marker.read_text() == payload

            # 2) The sandbox container never saw the host's tmp_path tree,
            # so the same path must NOT exist inside the sandbox.
            assert not _sandbox_has_file(client, sandbox_id, str(host_marker))
        finally:
            if local_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=local_card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_non_excluded_command_still_runs_in_sandbox(
    server_endpoint, monkeypatch
):
    """Sanity: when ``excluded_commands`` is set but the command does not
    match, execution must still go to the sandbox (the file appears in the
    sandbox, not the host).
    """
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    sandbox_path = f"/tmp/jiuwenbox_in_sandbox_{marker}.txt"
    payload = "runs-in-sandbox"

    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]
        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)

        await Runner.start()
        card_id = f"jiuwenbox_no_match_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            sandbox_id=sandbox_id,
            # The pattern below will NOT match ``echo ...``.
            extra_params={"excluded_commands": ["git *"]},
        )

        try:
            assert Runner.resource_mgr.add_sys_operation(card).is_ok()
            card_added = True
            sys_op = Runner.resource_mgr.get_sys_operation(card_id)

            res = await sys_op.shell().execute_cmd(f"echo -n {payload} > {sandbox_path}")
            assert res.code == StatusCode.SUCCESS.code
            assert res.data.exit_code == 0

            # File visible in the sandbox via the FS provider (which always
            # routes through the sandbox API).
            read_res = await sys_op.fs().read_file(sandbox_path)
            assert read_res.code == StatusCode.SUCCESS.code
            assert read_res.data.content == payload
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fallback_on_failure_does_not_rerun_locally_on_sandbox_nonzero_exit(
    server_endpoint, monkeypatch, tmp_path: Path
):
    """``fallback_on_failure=True`` must NOT re-run locally when the sandbox
    successfully executed the command but returned a non-zero exit code.
    """
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    host_marker = tmp_path / f"jiuwenbox_no_local_rerun_{marker}.txt"

    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]
        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)

        await Runner.start()
        fallback_card_id = f"jiuwenbox_fallback_card_{marker}"
        fallback_added = False

        fallback_card = _build_card(
            card_id=fallback_card_id,
            base_url=base_url,
            sandbox_id=sandbox_id,
            extra_params={"fallback_on_failure": True},
        )

        try:
            assert Runner.resource_mgr.add_sys_operation(fallback_card).is_ok()
            fallback_added = True

            fallback_op = Runner.resource_mgr.get_sys_operation(fallback_card_id)

            cmd = f"printf SHOULD-NOT-LAND > {host_marker}; exit 5"
            res = await fallback_op.shell().execute_cmd(cmd)
            assert res.code == StatusCode.SUCCESS.code
            assert res.data.exit_code == 5
            assert not host_marker.exists()
        finally:
            if fallback_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=fallback_card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_no_fallback_when_flag_off_keeps_failure_in_sandbox_only(
    server_endpoint, monkeypatch, tmp_path: Path
):
    """Without ``fallback_on_failure``, a sandbox non-zero exit must NOT
    trigger local execution: the host file must remain absent, and the
    surfaced result still carries the sandbox's non-zero ``exit_code``.
    """
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    host_marker = tmp_path / f"jiuwenbox_no_fallback_{marker}.txt"

    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]
        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)

        await Runner.start()
        card_id = f"jiuwenbox_no_fallback_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            sandbox_id=sandbox_id,
        )  # no fallback_on_failure

        try:
            assert Runner.resource_mgr.add_sys_operation(card).is_ok()
            card_added = True
            sys_op = Runner.resource_mgr.get_sys_operation(card_id)

            cmd = f"printf SHOULD-NOT-LAND > {host_marker}; exit 5"
            res = await sys_op.shell().execute_cmd(cmd)

            assert res.code == StatusCode.SUCCESS.code
            assert res.data.exit_code == 5
            # Local fallback never ran — host marker must not exist.
            assert not host_marker.exists()
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


def _sandbox_read_text(client: httpx.Client, sandbox_id: str, path: str) -> str:
    resp = client.post(
        f"/api/v1/sandboxes/{sandbox_id}/exec",
        json={"command": ["bash", "-lc", f"cat -- {path}"]},
    )
    resp.raise_for_status()
    body = resp.json()
    assert body["exit_code"] == 0, body
    return body.get("stdout") or ""


_HAS_JIUWENBOX_CLI = resolve_jiuwenbox_cli_bin() is not None or shutil.which("jiuwenbox") is not None


# ===========================================================================
# E2E: hybrid excluded_commands rewrite (local leaf + remote leaf).
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
@pytest.mark.skipif(not _HAS_JIUWENBOX_CLI, reason="Requires jiuwenbox CLI on PATH/venv")
async def test_hybrid_cat_local_pipe_dd_remote(
    server_endpoint, monkeypatch, tmp_path: Path
):
    """E1: cat(host) | dd(sandbox) proves bidirectional routing."""
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    host_file = tmp_path / f"hybrid_host_{marker}.txt"
    payload = f"hybrid-payload-{marker}"
    host_file.write_text(payload, encoding="utf-8")
    sandbox_marker = f"/tmp/from_pipe_{marker}.txt"

    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]
        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)

        await Runner.start()
        card_id = f"jiuwenbox_hybrid_pipe_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            sandbox_id=sandbox_id,
            extra_params={"excluded_commands": ["cat"]},
        )
        try:
            assert Runner.resource_mgr.add_sys_operation(card).is_ok()
            card_added = True
            sys_op = Runner.resource_mgr.get_sys_operation(card_id)

            cmd = f"cat {host_file} | dd of={sandbox_marker} status=none"
            res = await sys_op.shell().execute_cmd(cmd)
            assert res.code == StatusCode.SUCCESS.code, res.message
            assert res.data is not None
            assert res.data.exit_code == 0
            assert res.data.command == cmd

            assert _sandbox_has_file(client, sandbox_id, sandbox_marker)
            assert _sandbox_read_text(client, sandbox_id, sandbox_marker) == payload
            assert not Path(sandbox_marker).exists()
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
@pytest.mark.skipif(not _HAS_JIUWENBOX_CLI, reason="Requires jiuwenbox CLI on PATH/venv")
async def test_hybrid_or_short_circuit_remote_side_effect(
    server_endpoint, monkeypatch, tmp_path: Path
):
    """E2: local false || remote write — marker only in sandbox."""
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    sandbox_marker = f"/tmp/remote_or_{marker}.txt"
    host_same_path = Path(sandbox_marker)

    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]
        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)

        await Runner.start()
        card_id = f"jiuwenbox_hybrid_or_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            sandbox_id=sandbox_id,
            extra_params={"excluded_commands": ["false"]},
        )
        try:
            assert Runner.resource_mgr.add_sys_operation(card).is_ok()
            card_added = True
            sys_op = Runner.resource_mgr.get_sys_operation(card_id)

            cmd = f"false || sh -c 'printf remote-ok > {sandbox_marker}'"
            res = await sys_op.shell().execute_cmd(cmd)
            assert res.code == StatusCode.SUCCESS.code, res.message
            assert res.data is not None
            assert res.data.exit_code == 0
            assert _sandbox_has_file(client, sandbox_id, sandbox_marker)
            assert _sandbox_read_text(client, sandbox_id, sandbox_marker) == "remote-ok"
            assert not host_same_path.exists()
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
@pytest.mark.skipif(not _HAS_JIUWENBOX_CLI, reason="Requires jiuwenbox CLI on PATH/venv")
async def test_hybrid_cwd_and_env_reach_remote_leaf(server_endpoint, monkeypatch):
    """E3: execute_cmd cwd/environment are forwarded to remote leaf."""
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    sandbox_marker = f"/tmp/env_cwd_{marker}.txt"

    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]
        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)

        await Runner.start()
        card_id = f"jiuwenbox_hybrid_env_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            sandbox_id=sandbox_id,
            extra_params={"excluded_commands": ["false"]},
        )
        try:
            assert Runner.resource_mgr.add_sys_operation(card).is_ok()
            card_added = True
            sys_op = Runner.resource_mgr.get_sys_operation(card_id)

            # Ensure /tmp exists as cwd; write marker with env value from remote leaf.
            cmd = f"false || sh -c 'printf \"%s:%s\" \"$HYBRID_MARK\" \"$(pwd)\" > {sandbox_marker}'"
            res = await sys_op.shell().execute_cmd(
                cmd,
                cwd="/tmp",
                environment={"HYBRID_MARK": f"mark-{marker}"},
            )
            assert res.code == StatusCode.SUCCESS.code, res.message
            content = _sandbox_read_text(client, sandbox_id, sandbox_marker)
            assert content.startswith(f"mark-{marker}:")
            assert content.endswith("/tmp") or "/tmp" in content
            assert not Path(sandbox_marker).exists()
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_hybrid_unsupported_falls_back_to_sandbox(
    server_endpoint, monkeypatch
):
    """E4: when rewrite reports unsupported, original command runs wholly in sandbox."""
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    payload = f"unsup-{marker}"
    sandbox_marker = f"/tmp/hybrid_unsupported_{marker}.txt"

    with build_jiuwenbox_http_client(base_url) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]
        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)

        await Runner.start()
        card_id = f"jiuwenbox_hybrid_unsup_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            sandbox_id=sandbox_id,
            extra_params={"excluded_commands": ["printf"]},
        )
        try:
            assert Runner.resource_mgr.add_sys_operation(card).is_ok()
            card_added = True
            sys_op = Runner.resource_mgr.get_sys_operation(card_id)

            # Force unsupported classification; provider should still run wholly remote.
            monkeypatch.setattr(
                jb,
                "plan_command_rewrite",
                lambda *args, **kwargs: jb.RewritePlan(
                    mode="unsupported",
                    reason="forced",
                    normalized_command=str(args[0]) if args else "",
                ),
            )
            cmd = f"printf '%s' '{payload}' > {sandbox_marker}"
            res = await sys_op.shell().execute_cmd(cmd)
            assert res.code == StatusCode.SUCCESS.code, res.message
            assert res.data is not None
            assert res.data.exit_code == 0
            assert _sandbox_has_file(client, sandbox_id, sandbox_marker)
            assert _sandbox_read_text(client, sandbox_id, sandbox_marker) == payload
            assert not Path(sandbox_marker).exists()
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)

# ===========================================================================
# Unit: hybrid excluded_commands rewrite wiring (mocked provider).
# ===========================================================================


class _HybridShell(jb.JiuwenBoxShellProvider):
    """Minimal shell provider for routing tests (no real HTTP)."""

    def __init__(self, *, sandbox_id: str = "sid-1", base_url: str = "http://127.0.0.1:9") -> None:
        self.endpoint = SandboxEndpoint(base_url=base_url, sandbox_id=sandbox_id)
        self.config = MagicMock()
        self.config.timeout_seconds = 30
        self.config.launcher_config = MagicMock()
        self.config.launcher_config.extra_params = {"excluded_commands": ["cat"]}
        self._client = MagicMock()
        self._sandbox_id = sandbox_id
        self._timeout_seconds = 30
        self._recreate_calls = 0

    def _launcher_extra_params(self, create: bool = False) -> dict[str, Any]:
        return self.config.launcher_config.extra_params

    def _get_client(self) -> MagicMock:
        return self._client

    def _get_sandbox_id(self) -> str:
        return self._sandbox_id

    async def _recreate_sandbox_after_loss(self, *, stale_sandbox_id: str) -> str:
        self._recreate_calls += 1
        self._sandbox_id = "sid-recreated"
        return self._sandbox_id


class TestHybridShellWiring:
    """Provider wiring coverage for hybrid excluded_commands shell rewrite."""

    @pytest.fixture(autouse=True)
    def _reset_caches(self):
        jb.reset_rewrite_caches_for_tests()
        yield
        jb.reset_rewrite_caches_for_tests()

    @pytest.mark.asyncio
    async def test_i1_hybrid_runs_rewritten_local_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        shell._client.get_sandbox.return_value = {"id": "sid-1"}
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: "/usr/bin/jiuwenbox")
        monkeypatch.setattr(jb, "probe_jiuwenbox_cli", lambda _bin: (True, None))

        captured: dict[str, Any] = {}

        async def fake_pg(argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs.get("env")
            return {"stdout": "ok", "stderr": "", "exit_code": 0, "local": True}

        monkeypatch.setattr(jb, "_run_local_subprocess_process_group", fake_pg)
        monkeypatch.setattr(jb, "_run_local_subprocess", AsyncMock())
        monkeypatch.setattr(
            shell,
            "_run_exec_pipeline",
            AsyncMock(side_effect=AssertionError("remote pipeline must not run for hybrid")),
        )

        result = await shell.execute_cmd("cat f | grep a")
        assert result.code == StatusCode.SUCCESS.code
        assert result.data is not None
        assert result.data.command == "cat f | grep a"
        assert captured["argv"][0:2] == ["bash", "-lc"]
        rewritten = captured["argv"][2]
        assert "sandbox exec" in rewritten
        assert "--stdin -" in rewritten
        assert rewritten.startswith("cat f |")
        assert captured["env"]["JIUWENBOX_URL"] == "http://127.0.0.1:9"
        assert "JIUWENBOX_API_TOKEN" not in rewritten

    @pytest.mark.asyncio
    async def test_i2_local_all_and_remote_all_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        shell.config.launcher_config.extra_params = {"excluded_commands": ["cat"]}

        local_mock = AsyncMock(return_value={"stdout": "L", "stderr": "", "exit_code": 0, "local": True})
        monkeypatch.setattr(jb, "_run_local_subprocess", local_mock)
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: (_ for _ in ()).throw(AssertionError("cli")))

        res_local = await shell.execute_cmd("cat a | cat b")
        assert res_local.code == StatusCode.SUCCESS.code
        local_mock.assert_awaited()
        assert local_mock.await_args.args[0] == ["bash", "-lc", "cat a | cat b"]

        shell2 = _HybridShell()
        shell2.config.launcher_config.extra_params = {"excluded_commands": ["git*"]}
        pipeline = AsyncMock(return_value=({"stdout": "R", "stderr": "", "exit_code": 0}, None))
        monkeypatch.setattr(shell2, "_run_exec_pipeline", pipeline)
        monkeypatch.setattr(jb, "_run_local_subprocess", AsyncMock(side_effect=AssertionError("local")))
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: (_ for _ in ()).throw(AssertionError("cli")))

        res_remote = await shell2.execute_cmd("echo a || echo b")
        assert res_remote.code == StatusCode.SUCCESS.code
        pipeline.assert_awaited()

    @pytest.mark.asyncio
    async def test_i3_cli_missing_returns_config_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: None)
        local_mock = AsyncMock()
        remote_mock = AsyncMock()
        monkeypatch.setattr(jb, "_run_local_subprocess", local_mock)
        monkeypatch.setattr(jb, "_run_local_subprocess_process_group", local_mock)
        monkeypatch.setattr(shell, "_run_exec_pipeline", remote_mock)

        result = await shell.execute_cmd("cat f | grep a")
        assert result.code != StatusCode.SUCCESS.code
        assert "jiuwenbox CLI" in result.message
        local_mock.assert_not_awaited()
        remote_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_i4_preflight_recreates_stale_sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()

        def get_sandbox(sid: str):
            request = httpx.Request("GET", f"http://x/api/v1/sandboxes/{sid}")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("missing", request=request, response=response)

        shell._client.get_sandbox.side_effect = get_sandbox
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: "/usr/bin/jiuwenbox")
        monkeypatch.setattr(jb, "probe_jiuwenbox_cli", lambda _bin: (True, None))

        captured: dict[str, Any] = {}

        async def fake_pg(argv, **kwargs):
            captured["argv"] = argv
            return {"stdout": "", "stderr": "", "exit_code": 0, "local": True}

        monkeypatch.setattr(jb, "_run_local_subprocess_process_group", fake_pg)

        result = await shell.execute_cmd("cat f | grep a")
        assert result.code == StatusCode.SUCCESS.code
        assert shell._recreate_calls == 1
        assert "sid-recreated" in captured["argv"][2]

    @pytest.mark.asyncio
    async def test_i5_hybrid_nonzero_does_not_fallback_or_rerun(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        shell.config.launcher_config.extra_params = {
            "excluded_commands": ["cat"],
            "fallback_on_failure": True,
        }
        shell._client.get_sandbox.return_value = {"id": "sid-1"}
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: "/usr/bin/jiuwenbox")
        monkeypatch.setattr(jb, "probe_jiuwenbox_cli", lambda _bin: (True, None))

        calls = {"pg": 0}

        async def fake_pg(argv, **kwargs):
            calls["pg"] += 1
            return {"stdout": "", "stderr": "boom", "exit_code": 7, "local": True}

        monkeypatch.setattr(jb, "_run_local_subprocess_process_group", fake_pg)
        monkeypatch.setattr(jb, "_run_local_subprocess", AsyncMock(side_effect=AssertionError("no fallback")))
        monkeypatch.setattr(
            shell,
            "_run_exec_pipeline",
            AsyncMock(side_effect=AssertionError("no pipeline")),
        )

        result = await shell.execute_cmd("cat f | grep a")
        assert result.code == StatusCode.SUCCESS.code
        assert result.data is not None
        assert result.data.exit_code == 7
        assert calls["pg"] == 1
        assert shell._recreate_calls == 0

    @pytest.mark.asyncio
    async def test_i6_fallback_on_failure_still_applies_to_remote_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        shell.config.launcher_config.extra_params = {
            "excluded_commands": ["git*"],
            "fallback_on_failure": True,
        }
        local_mock = AsyncMock(return_value={"stdout": "fb", "stderr": "", "exit_code": 0, "local": True})
        monkeypatch.setattr(jb, "_run_local_subprocess", local_mock)

        async def pipeline(**kwargs):
            return await kwargs["local_op"](), None

        monkeypatch.setattr(shell, "_run_exec_pipeline", pipeline)
        result = await shell.execute_cmd("echo a || echo b")
        assert result.code == StatusCode.SUCCESS.code
        assert result.data is not None
        assert result.data.stdout == "fb"

    @pytest.mark.asyncio
    async def test_i7_timeout_maps_to_124(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        shell._client.get_sandbox.return_value = {"id": "sid-1"}
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: "/usr/bin/jiuwenbox")
        monkeypatch.setattr(jb, "probe_jiuwenbox_cli", lambda _bin: (True, None))
        monkeypatch.setattr(
            jb,
            "_run_local_subprocess_process_group",
            AsyncMock(return_value={"stdout": "", "stderr": "timed out", "exit_code": 124, "local": True}),
        )
        result = await shell.execute_cmd("cat f | grep a", timeout=1)
        assert result.data is not None
        assert result.data.exit_code == 124
        assert result.code != StatusCode.SUCCESS.code

    @pytest.mark.asyncio
    async def test_i8_orchestration_env_overrides_caller_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        shell._client.get_sandbox.return_value = {"id": "sid-1"}
        monkeypatch.setenv("JIUWENBOX_API_TOKEN", "provider-token")
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: "/usr/bin/jiuwenbox")
        monkeypatch.setattr(jb, "probe_jiuwenbox_cli", lambda _bin: (True, None))
        captured: dict[str, Any] = {}

        async def fake_pg(argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs.get("env")
            return {"stdout": "", "stderr": "", "exit_code": 0, "local": True}

        monkeypatch.setattr(jb, "_run_local_subprocess_process_group", fake_pg)
        await shell.execute_cmd(
            "cat f | grep a",
            environment={"JIUWENBOX_API_TOKEN": "caller-token", "FOO": "1"},
        )
        assert captured["env"]["JIUWENBOX_API_TOKEN"] == "provider-token"
        assert captured["env"]["FOO"] == "1"
        assert "--api-token" not in captured["argv"][2]

    @pytest.mark.asyncio
    async def test_unsupported_mixed_runs_remote_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        shell.config.launcher_config.extra_params = {"excluded_commands": ["echo"]}
        pipeline = AsyncMock(return_value=({"stdout": "in-sandbox", "stderr": "", "exit_code": 0}, None))
        monkeypatch.setattr(shell, "_run_exec_pipeline", pipeline)
        monkeypatch.setattr(jb, "resolve_jiuwenbox_cli_bin", lambda: (_ for _ in ()).throw(AssertionError("cli")))
        monkeypatch.setattr(jb, "_run_local_subprocess", AsyncMock(side_effect=AssertionError("local")))
        monkeypatch.setattr(
            jb,
            "plan_command_rewrite",
            lambda *args, **kwargs: jb.RewritePlan(
                mode="unsupported",
                reason="forced",
                normalized_command=str(args[0]) if args else "",
            ),
        )

        result = await shell.execute_cmd("echo a | grep b")
        assert result.code == StatusCode.SUCCESS.code
        assert result.data is not None
        assert result.data.stdout == "in-sandbox"
        pipeline.assert_awaited()

    @pytest.mark.asyncio
    async def test_i9_empty_patterns_skips_rewrite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shell = _HybridShell()
        shell.config.launcher_config.extra_params = {"excluded_commands": []}
        plan_mock = MagicMock(side_effect=AssertionError("plan must not run"))
        monkeypatch.setattr(jb, "plan_command_rewrite", plan_mock)
        pipeline = AsyncMock(return_value=({"stdout": "x", "stderr": "", "exit_code": 0}, None))
        monkeypatch.setattr(shell, "_run_exec_pipeline", pipeline)
        result = await shell.execute_cmd("cat f | grep a")
        assert result.code == StatusCode.SUCCESS.code
        pipeline.assert_awaited()
        plan_mock.assert_not_called()

