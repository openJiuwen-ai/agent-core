# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.sys_operation.cwd import init_cwd
from openjiuwen.core.sys_operation.result import ExecuteCodeData, ExecuteCodeResult
from openjiuwen.harness.tools import CodeTool


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


@pytest.mark.asyncio
async def test_code_tool_uses_context_cwd(sys_op, tmp_path):
    workspace = tmp_path / "member-workspace"
    workspace.mkdir()
    init_cwd(str(workspace), workspace=str(workspace))
    code_tool = CodeTool(sys_op)

    code_res = await code_tool.invoke(
        {
            "code": "import os; print(os.getcwd())",
            "language": "python",
        }
    )

    assert code_res.success is True
    assert code_res.data["exit_code"] == 0
    assert code_res.data["stdout"].strip() == str(workspace.resolve())


@pytest.mark.asyncio
async def test_code_tool_does_not_pass_cwd_for_non_local_operation():
    class FakeCodeOperation:
        def __init__(self):
            self.kwargs = None

        async def execute_code(self, code: str, **kwargs):
            self.kwargs = kwargs
            return ExecuteCodeResult(
                code=StatusCode.SUCCESS.code,
                message="Code executed successfully",
                data=ExecuteCodeData(
                    code_content=code,
                    language=kwargs["language"],
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                ),
            )

    class FakeOperation:
        mode = OperationMode.SANDBOX

        def __init__(self):
            self.code_operation = FakeCodeOperation()

        def code(self):
            return self.code_operation

    operation = FakeOperation()
    code_tool = CodeTool(operation)

    code_res = await code_tool.invoke({"code": "print('ok')", "language": "python"})

    assert code_res.success is True
    assert "cwd" not in operation.code_operation.kwargs
