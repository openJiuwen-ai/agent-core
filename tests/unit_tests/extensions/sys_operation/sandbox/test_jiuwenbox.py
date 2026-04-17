# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
import uuid
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import clear_jiuwenbox_shared_sandbox


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RUN_JIUWENBOX_TEST") != "1",
        reason="Requires running Jiuwenbox sandbox",
    ),
]


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint if "://" in endpoint else f"http://{endpoint}"


def _list_sandbox_ids(client: httpx.Client) -> set[str]:
    response = client.get("/api/v1/sandboxes")
    response.raise_for_status()
    return {item["id"] for item in response.json()}


@pytest.fixture
def server_endpoint() -> str:
    return os.environ.get("JIUWENBOX_TEST_SERVER", "127.0.0.1:8321")


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(server_endpoint, monkeypatch) -> AsyncIterator[SysOperation]:
    base_url = _normalize_endpoint(server_endpoint)
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

        await Runner.start()
        card_id = f"jiuwenbox_shared_{uuid.uuid4().hex[:8]}"
        card_added = False
        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.CUSTOM,
                    custom_id=card_id,
                ),
                launcher_config=PreDeployLauncherConfig(
                    base_url=base_url,
                    sandbox_type="jiuwenbox",
                    idle_ttl_seconds=600,
                ),
                timeout_seconds=30,
            ),
        )

        try:
            add_res = Runner.resource_mgr.add_sys_operation(card)
            assert add_res.is_ok()
            card_added = True
            yield Runner.resource_mgr.get_sys_operation(card_id)
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            clear_jiuwenbox_shared_sandbox(base_url)

            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")


async def test_fs_code_shell_share_auto_created_sandbox(sys_op: SysOperation):
    marker = uuid.uuid4().hex[:8]

    fs_path = f"/tmp/jiuwenbox_fs_{marker}.txt"
    fs_content = "fs-visible-to-shell-and-code"
    write_res = await sys_op.fs().write_file(fs_path, fs_content, prepend_newline=False)
    assert write_res.code == StatusCode.SUCCESS.code

    shell_read = await sys_op.shell().execute_cmd(f"cat {fs_path}")
    assert shell_read.code == StatusCode.SUCCESS.code
    assert shell_read.data.exit_code == 0
    assert shell_read.data.stdout == fs_content

    code_read = await sys_op.code().execute_code(
        code=f"from pathlib import Path; print(Path({fs_path!r}).read_text())",
        language="python",
    )
    assert code_read.code == StatusCode.SUCCESS.code
    assert code_read.data.exit_code == 0
    assert code_read.data.stdout.strip() == fs_content

    shell_path = f"/tmp/jiuwenbox_shell_{marker}.txt"
    shell_write = await sys_op.shell().execute_cmd(f"printf shell-visible-to-fs > {shell_path}")
    assert shell_write.code == StatusCode.SUCCESS.code
    assert shell_write.data.exit_code == 0

    fs_read = await sys_op.fs().read_file(shell_path)
    assert fs_read.code == StatusCode.SUCCESS.code
    assert fs_read.data.content == "shell-visible-to-fs"

    code_path = f"/tmp/jiuwenbox_code_{marker}.txt"
    code_write = await sys_op.code().execute_code(
        code=f"from pathlib import Path; Path({code_path!r}).write_text('code-visible-to-shell')",
        language="python",
    )
    assert code_write.code == StatusCode.SUCCESS.code
    assert code_write.data.exit_code == 0

    shell_code_read = await sys_op.shell().execute_cmd(f"cat {code_path}")
    assert shell_code_read.code == StatusCode.SUCCESS.code
    assert shell_code_read.data.exit_code == 0
    assert shell_code_read.data.stdout == "code-visible-to-shell"


async def test_extra_params_shares_sandbox_id_after_memory_cache_cleared(
    sys_op: SysOperation,
    server_endpoint: str,
):
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid.uuid4().hex[:8]
    file_path = f"/tmp/jiuwenbox_extra_{marker}.txt"
    file_content = "visible-via-extra-params-fs"

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

    write_res = await sys_op.fs().write_file(file_path, file_content, prepend_newline=False)
    assert write_res.code == StatusCode.SUCCESS.code

    launcher_config = sys_op._run_config.config.launcher_config
    sandbox_id = launcher_config.extra_params.get("sandbox_id")
    assert isinstance(sandbox_id, str) and sandbox_id

    # Remove in-memory cache on purpose. We will explicitly reuse sandbox_id
    # through a brand-new SysOperation card's launcher_config.extra_params.
    clear_jiuwenbox_shared_sandbox(base_url)

    second_card_id = f"jiuwenbox_extra_reuse_{marker}"
    second_card_added = False
    second_card = SysOperationCard(
        id=second_card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.CUSTOM,
                custom_id=second_card_id,
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url=base_url,
                sandbox_type="jiuwenbox",
                idle_ttl_seconds=600,
                extra_params={"sandbox_id": sandbox_id},
            ),
            timeout_seconds=30,
        ),
    )

    try:
        add_res = Runner.resource_mgr.add_sys_operation(second_card)
        assert add_res.is_ok()
        second_card_added = True

        second_sys_op = Runner.resource_mgr.get_sys_operation(second_card_id)

        shell_read = await second_sys_op.shell().execute_cmd(f"cat {file_path}")
        assert shell_read.code == StatusCode.SUCCESS.code
        assert shell_read.data.exit_code == 0
        assert shell_read.data.stdout == file_content

        code_read = await second_sys_op.code().execute_code(
            code=f"from pathlib import Path; print(Path({file_path!r}).read_text())",
            language="python",
        )
        assert code_read.code == StatusCode.SUCCESS.code
        assert code_read.data.exit_code == 0
        assert code_read.data.stdout.strip() == file_content
    finally:
        if second_card_added:
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=second_card_id)

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        after_ids = _list_sandbox_ids(client)
    assert sandbox_id in after_ids
    assert len(after_ids - before_ids) == 1


async def test_extra_params_policy_and_policy_mode_are_used_to_create_sandbox(
    server_endpoint: str,
    monkeypatch,
):
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid.uuid4().hex[:8]
    policy_name = f"jiuwenbox-extra-policy-{marker}"
    extra_dir = f"/tmp/jiuwenbox-extra-policy-{marker}"

    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

        await Runner.start()
        card_id = f"jiuwenbox_extra_policy_{marker}"
        card_added = False
        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.CUSTOM,
                    custom_id=card_id,
                ),
                launcher_config=PreDeployLauncherConfig(
                    base_url=base_url,
                    sandbox_type="jiuwenbox",
                    idle_ttl_seconds=600,
                    extra_params={
                        "policy_mode": "append",
                        "policy": {
                            "name": policy_name,
                            "filesystem_policy": {
                                "directories": [{
                                    "path": extra_dir,
                                    "permissions": "0777",
                                }],
                                "read_write": [extra_dir],
                            },
                        },
                    },
                ),
                timeout_seconds=30,
            ),
        )

        try:
            add_res = Runner.resource_mgr.add_sys_operation(card)
            assert add_res.is_ok()
            card_added = True

            sys_op = Runner.resource_mgr.get_sys_operation(card_id)
            file_path = f"{extra_dir}/created.txt"
            write_res = await sys_op.fs().write_file(
                file_path,
                "created-with-extra-policy",
                prepend_newline=False,
            )
            assert write_res.code == StatusCode.SUCCESS.code

            launcher_config = sys_op._run_config.config.launcher_config
            sandbox_id = launcher_config.extra_params.get("sandbox_id")
            assert isinstance(sandbox_id, str) and sandbox_id

            policy_resp = client.get(f"/api/v1/policies/{sandbox_id}")
            assert policy_resp.status_code == 200
            policy = policy_resp.json()
            assert policy["name"] == policy_name
            assert {"path": extra_dir, "permissions": "0777"} in policy["filesystem_policy"]["directories"]
            assert extra_dir in policy["filesystem_policy"]["read_write"]
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            clear_jiuwenbox_shared_sandbox(base_url)

            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")
