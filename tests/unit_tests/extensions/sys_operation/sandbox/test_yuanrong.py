# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end smoke tests for the yuanrong sandbox provider.

Gated on ``RUN_YUANRONG_TEST=1``. Requires a reachable YuanRong cluster
(``YR_SERVER_ADDRESS`` / env already configured for ``yr.init()``).
"""
from __future__ import annotations

import os
import shlex
import uuid
from typing import AsyncIterator, Optional

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import SandboxGatewayClient
from openjiuwen.extensions.sys_operation.sandbox.providers import yuanrong as yr_provider

requires_yuanrong = pytest.mark.skipif(
    os.environ.get("RUN_YUANRONG_TEST") != "1",
    reason="Requires running YuanRong cluster",
)


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


def _build_card(card_id: str, *, custom_id: Optional[str] = None) -> SysOperationCard:
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
                sandbox_type="yuanrong",
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
    card_id = f"yuanrong_smoke_{uuid.uuid4().hex[:8]}"
    card = _build_card(card_id, custom_id=card_id)
    add_res = Runner.resource_mgr.add_sys_operation(card)
    assert add_res.is_ok()
    try:
        yield Runner.resource_mgr.get_sys_operation(card_id)
    finally:
        await _remove_sys_operation_with_sandbox_release(card_id)
        await Runner.stop()


@pytest.mark.asyncio
@requires_yuanrong
async def test_shell_and_code_share_sandbox(sys_op: SysOperation):
    marker = uuid.uuid4().hex[:8]
    path = f"/tmp/yuanrong_share_{marker}.txt"
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
    # YuanRong execute may prepend extra lines (e.g. runtime banner); assert payload is present.
    assert content in (code_read.data.stdout or "")

    code_write_path = f"/tmp/yuanrong_code_{marker}.txt"
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
@requires_yuanrong
async def test_release_deletes_cached_sandbox():
    await Runner.start()
    marker = uuid.uuid4().hex[:8]
    card_id = f"yuanrong_delete_{marker}"
    card = _build_card(card_id, custom_id=card_id)
    assert Runner.resource_mgr.add_sys_operation(card).is_ok()
    try:
        sys_op = Runner.resource_mgr.get_sys_operation(card_id)
        res = await sys_op.shell().execute_cmd("echo delete-me")
        assert res.code == StatusCode.SUCCESS.code
        assert yr_provider._YuanrongProviderMixin._shared_instances

        await _release_sandbox_for_sys_operation(card_id)
        remaining = [
            key for key in yr_provider._YuanrongProviderMixin._shared_instances
            if card_id in key or marker in key
        ]
        assert remaining == []
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()


@pytest.mark.asyncio
@requires_yuanrong
async def test_docker_executor_smoke():
    if _executor() != "docker":
        pytest.skip("Set YUANRONG_TEST_EXECUTOR=docker to run docker smoke")

    await Runner.start()
    card_id = f"yuanrong_docker_{uuid.uuid4().hex[:8]}"
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
