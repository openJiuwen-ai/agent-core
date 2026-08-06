# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""FS provider tests for yuanrong.

Unit helpers are ungated. End-to-end FS paths require ``RUN_YUANRONG_TEST=1``.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import SandboxGatewayClient

requires_yuanrong = pytest.mark.skipif(
    os.environ.get("RUN_YUANRONG_TEST") != "1",
    reason="Requires running YuanRong cluster",
)

SANDBOX_BASE_PATH = "/tmp"


def _base_url() -> str:
    return os.environ.get("YUANRONG_TEST_BASE_URL", "http://127.0.0.1:8080")


def _executor() -> str:
    value = os.environ.get("YUANRONG_TEST_EXECUTOR", "default").strip().lower()
    return value if value in {"default", "docker"} else "default"


def _extra_params() -> dict:
    params: dict = {"executor": _executor()}
    if _executor() == "docker":
        image = os.environ.get("YUANRONG_TEST_IMAGE")
        if image:
            params["image"] = image
    return params


async def _remove_sys_operation_with_sandbox_release(sys_operation_id: str) -> None:
    sys_op = Runner.resource_mgr.get_sys_operation(sys_operation_id)
    if sys_op is not None and sys_op.isolation_key_template:
        try:
            await SandboxGatewayClient.release(sys_op.isolation_key_template, on_stop="delete")
        except Exception as exc:
            if "not found" not in str(exc).lower():
                raise
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=sys_operation_id)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture() -> AsyncIterator[SysOperation]:
    await Runner.start()
    card_id = f"yuanrong_fs_op_{uuid.uuid4().hex[:8]}"
    card = SysOperationCard(
        id=card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.CUSTOM,
                custom_id=card_id,
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url=_base_url(),
                sandbox_type="yuanrong",
                idle_ttl_seconds=600,
                extra_params=_extra_params(),
            ),
            timeout_seconds=30,
        ),
    )
    add_res = Runner.resource_mgr.add_sys_operation(card)
    assert add_res.is_ok()
    try:
        yield Runner.resource_mgr.get_sys_operation(card_id)
    finally:
        await _remove_sys_operation_with_sandbox_release(card_id)
        await Runner.stop()


@pytest.mark.asyncio
@requires_yuanrong
async def test_fs_read_write(sys_op):
    file_name = f"{SANDBOX_BASE_PATH}/test_basics_{uuid.uuid4().hex[:8]}.txt"
    content = "Hello, world!\nLine 2"

    write_res = await sys_op.fs().write_file(path=file_name, content=content, prepend_newline=False)
    assert write_res.code == StatusCode.SUCCESS.code

    read_res = await sys_op.fs().read_file(path=file_name)
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == content

    append_file = f"{SANDBOX_BASE_PATH}/test_append_{uuid.uuid4().hex[:8]}.txt"
    await sys_op.fs().write_file(path=append_file, content="Initial", prepend_newline=False)
    await sys_op.fs().write_file(
        path=append_file,
        content="Appended",
        mode="text",
        append=True,
        prepend_newline=True,
        append_newline=False,
    )
    res = await sys_op.fs().read_file(path=append_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Initial\nAppended"

    bin_file = f"{SANDBOX_BASE_PATH}/test_{uuid.uuid4().hex[:8]}.bin"
    bin_data = b"\x00\x01\x02"
    await sys_op.fs().write_file(path=bin_file, content=bin_data, mode="bytes")
    read_bin = await sys_op.fs().read_file(path=bin_file, mode="bytes")
    print("zzx: read_bin = ", read_bin)
    assert read_bin.code == StatusCode.SUCCESS.code
    assert read_bin.data.content == bin_data


@pytest.mark.asyncio
@requires_yuanrong
async def test_fs_read_head_tail(sys_op):
    multi_line_file = f"{SANDBOX_BASE_PATH}/test_head_tail_{uuid.uuid4().hex[:8]}.txt"
    multi_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    await sys_op.fs().write_file(multi_line_file, multi_content, prepend_newline=False)

    res = await sys_op.fs().read_file(path=multi_line_file, head=3)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 1\nLine 2\nLine 3\n"

    res = await sys_op.fs().read_file(path=multi_line_file, tail=2)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 4\nLine 5"


@pytest.mark.asyncio
@requires_yuanrong
async def test_fs_list_and_search(sys_op):
    marker = uuid.uuid4().hex[:8]
    root = f"{SANDBOX_BASE_PATH}/yuanrong_fs_list_{marker}"
    await sys_op.shell().execute_cmd(f"mkdir -p {root}/sub")
    await sys_op.fs().write_file(f"{root}/a.py", "print(1)", prepend_newline=False)
    await sys_op.fs().write_file(f"{root}/b.txt", "hello", prepend_newline=False)
    await sys_op.fs().write_file(f"{root}/sub/c.py", "print(2)", prepend_newline=False)

    list_res = await sys_op.fs().list_files(path=root, recursive=False)
    assert list_res.code == StatusCode.SUCCESS.code
    names = {item.name for item in list_res.data.list_items}
    assert "a.py" in names
    assert "b.txt" in names

    dirs_res = await sys_op.fs().list_directories(path=root, recursive=False)
    assert dirs_res.code == StatusCode.SUCCESS.code
    assert any(item.name == "sub" for item in dirs_res.data.list_items)

    search_res = await sys_op.fs().search_files(path=root, pattern="*.py")
    assert search_res.code == StatusCode.SUCCESS.code
    matched = {item.name for item in search_res.data.matching_files}
    assert "a.py" in matched
    assert "c.py" in matched
    assert "b.txt" not in matched


@pytest.mark.asyncio
@requires_yuanrong
async def test_fs_upload_download(sys_op, tmp_path: Path):
    marker = uuid.uuid4().hex[:8]
    local_src = tmp_path / f"upload_{marker}.txt"
    local_dst = tmp_path / f"download_{marker}.txt"
    remote = f"{SANDBOX_BASE_PATH}/yuanrong_upload_{marker}.txt"
    payload = f"upload-payload-{marker}"
    local_src.write_text(payload, encoding="utf-8")

    upload_res = await sys_op.fs().upload_file(str(local_src), remote, overwrite=True)
    assert upload_res.code == StatusCode.SUCCESS.code

    download_res = await sys_op.fs().download_file(remote, str(local_dst), overwrite=True)
    assert download_res.code == StatusCode.SUCCESS.code
    assert local_dst.read_text(encoding="utf-8") == payload


def test_build_wrapped_command_includes_cwd_env_timeout():
    from openjiuwen.extensions.sys_operation.sandbox.providers.yuanrong import _build_wrapped_command

    cmd = _build_wrapped_command(
        "echo hi",
        cwd="/tmp",
        timeout=3,
        environment={"FOO": "bar"},
    )
    assert "cd /tmp" in cmd
    assert "FOO=bar" in cmd or "FOO='bar'" in cmd
    assert "timeout 3s" in cmd
    assert "echo hi" in cmd


@pytest.mark.asyncio
async def test_launcher_delete_dispatches_yuanrong(monkeypatch):
    from openjiuwen.core.sys_operation.sandbox.launchers.pre_deployment_launcher import PreDeploymentLauncher
    from openjiuwen.extensions.sys_operation.sandbox.providers import yuanrong as yr_mod

    calls: list[dict] = []

    async def _fake_delete(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(yr_mod, "delete_yuanrong_sandbox", _fake_delete)
    launcher = PreDeploymentLauncher()
    await launcher.delete(
        "",
        sandbox_type="yuanrong",
        base_url="http://127.0.0.1:8080",
        isolation_key="custom_pre_deploy_yuanrong_agent1",
    )
    assert len(calls) == 1
    assert calls[0]["shared_key"] == "http://127.0.0.1:8080|custom_pre_deploy_yuanrong_agent1"
    assert calls[0]["reason"] == "teardown"
