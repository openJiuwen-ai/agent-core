# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""FS provider tests for ADX.

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

requires_adx = pytest.mark.skipif(
    os.environ.get("RUN_YUANRONG_TEST") != "1",
    reason="Requires a running ADX HTTP service",
)

SANDBOX_BASE_PATH = "/tmp"


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
    card_id = f"adx_fs_op_{uuid.uuid4().hex[:8]}"
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
                sandbox_type="adx-environment",
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
@requires_adx
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
@requires_adx
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
@requires_adx
async def test_fs_list_and_search(sys_op):
    marker = uuid.uuid4().hex[:8]
    root = f"{SANDBOX_BASE_PATH}/adx_fs_list_{marker}"
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
@requires_adx
async def test_fs_upload_download(sys_op, tmp_path: Path):
    marker = uuid.uuid4().hex[:8]
    local_src = tmp_path / f"upload_{marker}.txt"
    local_dst = tmp_path / f"download_{marker}.txt"
    remote = f"{SANDBOX_BASE_PATH}/adx_upload_{marker}.txt"
    payload = f"upload-payload-{marker}"
    local_src.write_text(payload, encoding="utf-8")

    upload_res = await sys_op.fs().upload_file(str(local_src), remote, overwrite=True)
    assert upload_res.code == StatusCode.SUCCESS.code

    download_res = await sys_op.fs().download_file(remote, str(local_dst), overwrite=True)
    assert download_res.code == StatusCode.SUCCESS.code
    assert local_dst.read_text(encoding="utf-8") == payload


def test_adx_http_request_and_response_contract():
    import json

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx import (
        _adx_create_body,
        _decode_read_payload,
        _exec_args,
        _item_from_payload,
        _list_depth,
        _normalize_exec_result,
        _normalize_list_items,
    )

    body = _adx_create_body(
        {
            "image": "ubuntu:22.04",
            "user": " 1000 ",
            "workdir": "/ws",
            "mounts": [{"source": "/host", "target": "/ws"}],
        },
        "docker",
    )
    assert body["runtime"] == "docker"
    assert body["cpu"] == 1000
    assert body["memory"] == 1024
    assert body["idleTimeoutSeconds"] == 0
    assert body["extra_config"] == {"user": "1000"}
    assert json.loads(body["rootfs"]) == {
        "type": "image",
        "imageurl": "ubuntu:22.04",
        "workdir": "/ws",
        "mounts": [{"source": "/host", "target": "/ws"}],
    }
    assert "image" not in body
    assert body["mounts"] == [{"type": "bind", "host_path": "/host", "target": "/ws", "options": ["rw"]}]
    assert body["cwd"] == "/ws"
    assert "workdir" not in body

    overridden = _adx_create_body(
        {"cpu": "2000", "memory": 4096, "idle_timeout": -1, "user": "  "},
        "conch",
    )
    assert overridden["runtime"] == "conch"
    assert overridden["cpu"] == 2000
    assert overridden["memory"] == 4096
    assert overridden["idleTimeoutSeconds"] == 0
    positive = _adx_create_body({"idleTimeoutSeconds": 30}, "docker")
    assert positive["idleTimeoutSeconds"] == 30
    assert "extra_config" not in overridden
    aliased = json.loads(_adx_create_body({"rootfs": {"image": "ubuntu:22.04"}}, "docker")["rootfs"])
    assert aliased == {"imageurl": "ubuntu:22.04"}
    plain = _adx_create_body({}, "supervisor")
    assert plain["runtime"] == "supervisor"
    quoted = _adx_create_body({}, "docker'")
    assert quoted["runtime"] == "docker"

    native = _adx_create_body({
        "runtime": "runsc",
        "rootfs_template": "tool-runtime",
        "cpu_limit": 2000,
        "mem_limit": 2048,
        "user": 1001,
        "group": 1002,
        "mounts": [{"type": "bind", "host_path": "/shared/workspace", "target": "/workspace", "options": ["rw"]}],
        "network": {"blockNetwork": True},
        "env": {"MODE": "test"},
    }, "supervisor")
    assert native["runtime"] == "runsc"
    assert native["rootfs"] == {"type": "template", "name": "tool-runtime"}
    assert native["cpu_limit"] == 2000 and native["mem_limit"] == 2048
    assert (native["user"], native["group"]) == ("1001", "1002")
    assert native["mounts"][0]["host_path"] == "/shared/workspace"
    assert native["network"] == {"blockNetwork": True}
    assert native["env"] == {"MODE": "test"}

    args = _exec_args("echo hi", cwd="/tmp", timeout=3, environment={"FOO": "bar"})
    assert args["cwd"] == "/tmp"
    assert args["timeout"] == 3
    assert args["cmd"].startswith("export FOO=bar && ")
    assert args["cmd"].endswith("echo hi")
    assert "/bin/sh" not in args["cmd"]
    assert _list_depth(False, 9) == 1
    assert _list_depth(True, None) == 21
    assert _list_depth(True, 1) == 2

    assert _normalize_exec_result(
        {"stdout": "ok", "stderr": "", "exit_code": 0}
    )["returncode"] == 0
    assert _decode_read_payload({"data": "6869", "error": None}, binary=True) == b"hi"
    try:
        _decode_read_payload({"data": None, "error": "No such file or directory"}, binary=False)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing file should raise FileNotFoundError")

    items = _normalize_list_items(
        {
            "entries": [
                {
                    "name": "a.py",
                    "path": "/ws/a.py",
                    "type": "file",
                    "size": 4,
                    "permissions": None,
                    "modified_time": 1695441203.5,
                }
            ],
            "error": None,
        }
    )
    item = _item_from_payload(items[0])
    assert item.is_directory is False
    assert item.type is None
    assert item.modified_time == "1695441203.5"
    try:
        _normalize_list_items(
            {"entries": [], "error_code": "FILESYSTEM_ERROR", "error": "search files failed"}
        )
    except OSError as exc:
        assert "FILESYSTEM_ERROR" in str(exc)
    else:
        raise AssertionError("search error envelope should raise")


@pytest.mark.parametrize("sandbox_type", ["adx-environment", "yuanrong"])
@pytest.mark.asyncio
async def test_launcher_delete_dispatches_adx(monkeypatch, sandbox_type):
    from openjiuwen.core.sys_operation.sandbox.launchers.pre_deployment_launcher import PreDeploymentLauncher
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx

    calls: list[dict] = []

    async def _fake_delete(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(adx, "delete_adx_sandbox", _fake_delete)
    launcher = PreDeploymentLauncher()
    await launcher.delete(
        "",
        sandbox_type=sandbox_type,
        base_url="http://127.0.0.1:18093",
        isolation_key=f"custom_pre_deploy_{sandbox_type}_agent1",
    )
    assert len(calls) == 1
    assert calls[0]["shared_key"] == f"http://127.0.0.1:18093|custom_pre_deploy_{sandbox_type}_agent1"
    assert calls[0]["reason"] == "teardown"


def test_legacy_adx_provider_aliases():
    from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx, yuanrong

    for operation, provider in (
        ("fs", adx.AdxFSProvider),
        ("shell", adx.AdxShellProvider),
        ("code", adx.AdxCodeProvider),
    ):
        assert SandboxRegistry.get_provider_cls("adx-environment", operation) is provider
        assert SandboxRegistry.get_provider_cls("yuanrong", operation) is provider

    assert yuanrong.build_yuanrong_shared_scope_key is adx.build_adx_shared_scope_key
    assert yuanrong.delete_yuanrong_sandbox is adx.delete_adx_sandbox
