# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.deepagents.tools.shell import BashTool


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_shell_tools_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=[]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest.mark.asyncio
async def test_bash_tool(sys_op):
    bash_tool = BashTool(sys_op)

    bash_res = await bash_tool.invoke({"command": "echo 你好"})
    assert bash_res.success is True
    assert bash_res.data["exit_code"] == 0
    assert bash_res.data["stderr"] == ""
    assert "你好" in bash_res.data["stdout"]
    assert bash_res.error is None


@pytest.mark.asyncio
async def test_bash_tool_fail_command(sys_op):
    bash_tool = BashTool(sys_op)

    fail_res = await bash_tool.invoke({"command": "echo fail && exit 1"})
    assert fail_res.success is False
    assert fail_res.data["exit_code"] == 1


@pytest.mark.asyncio
async def test_bash_tool_allowlist(sys_op):
    card_id = "test_shell_allowlist_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=["echo"]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    restricted_op = Runner.resource_mgr.get_sys_operation(card_id)
    bash_tool = BashTool(restricted_op)

    ok_res = await bash_tool.invoke({"command": "echo ok"})
    assert ok_res.success is True

    blocked_res = await bash_tool.invoke({"command": "whoami"})
    assert blocked_res.success is False
    assert blocked_res.error is not None
    assert blocked_res.data is None

    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)