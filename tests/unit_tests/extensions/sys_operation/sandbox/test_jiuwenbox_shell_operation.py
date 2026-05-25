# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end tests for the jiuwenbox shell-execution paths.

All tests in this module require a running jiuwenbox service and are gated
on ``RUN_JIUWENBOX_TEST=1``. The local-execution tests in particular use a
pair of ``SysOperationCard``s sharing the same sandbox so we can verify
that pre-routed / fallback commands land on the host while the sandbox
is genuinely untouched (or genuinely fails first).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.result import ExecuteCmdStreamResult


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
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
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

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
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

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
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
async def test_fallback_on_failure_runs_locally_after_sandbox_nonzero_exit(
    server_endpoint, monkeypatch, tmp_path: Path
):
    """``fallback_on_failure=True`` → sandbox returns non-zero → local fallback
    runs the same command; the host file lands while the sandbox path stays
    empty (sandbox container has no access to the host's pytest tmp_path).
    """
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid4().hex[:8]
    host_marker = tmp_path / f"jiuwenbox_fallback_{marker}.txt"
    payload = "fallback-payload"

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
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

            # Sandbox: ``printf`` to the host's tmp_path fails (parent does
            # not exist inside the container) and ``exit 5`` makes the whole
            # command non-zero → triggers fallback. Host: tmp_path exists →
            # printf succeeds → ``exit 5`` still surfaces 5.
            cmd = f"printf {payload} > {host_marker}; exit 5"
            res = await fallback_op.shell().execute_cmd(cmd)
            assert res.code == StatusCode.SUCCESS.code
            assert res.data.exit_code == 5

            # 1) Host file written by the local fallback.
            assert host_marker.exists()
            assert host_marker.read_text() == payload

            # 2) Sandbox does not have it (it never saw the host's tmp_path
            # tree, and even the fallback path never travels back there).
            assert not _sandbox_has_file(client, sandbox_id, str(host_marker))
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

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
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
