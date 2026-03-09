# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.deepagents.tools import CodeTool


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_code_tools_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=[]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest.mark.asyncio
async def test_code_tool(sys_op):
    code_tool = CodeTool(sys_op)

    code_res = await code_tool.invoke({"code": "print('你好')", "language": "python"})
    assert code_res.success is True
    assert code_res.data["exit_code"] == 0
    assert code_res.data["stderr"] == ""
    assert "你好" in code_res.data["stdout"]
    assert code_res.error is None


@pytest.mark.asyncio
async def test_code_tool_error(sys_op):
    code_tool = CodeTool(sys_op)

    err_res = await code_tool.invoke({"code": "def f(:\n    pass", "language": "python"})
    assert err_res.success is False
    assert err_res.data["exit_code"] != 0
    assert err_res.data["stderr"] != ""


@pytest.mark.asyncio
async def test_code_tool_unsupported_language(sys_op):
    code_tool = CodeTool(sys_op)

    lang_res = await code_tool.invoke({"code": "print(1)", "language": "ruby"})
    assert lang_res.success is False
    assert lang_res.error is not None
