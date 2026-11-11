# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shell output files must remain readable through the configured sandbox."""

import re
import shutil
import tempfile
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation import cwd
from openjiuwen.core.sys_operation.result.shell_operation_result import (
    ExecuteCmdChunkData,
    ExecuteCmdData,
    ExecuteCmdResult,
    ExecuteCmdStreamResult,
)
from openjiuwen.harness.tools.filesystem import ReadFileTool
from openjiuwen.harness.tools.shell import BashTool, PowerShellTool


@pytest.fixture
def sandbox(request, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    allowed_root = tmp_path / "allowed"
    workspace = allowed_root / "workspace"
    outside_root = tmp_path / "outside"
    temp_root = tmp_path / "system-temp"
    for directory in (workspace, outside_root, temp_root):
        directory.mkdir(parents=True)

    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    monkeypatch.setattr(cwd, "_cwd_state", ContextVar("shell_output_test_cwd", default=None))
    monkeypatch.delenv("OPENJIUWEN_BASH_STRICT", raising=False)

    explicit_roots = None
    current_dir = allowed_root
    if request.param == "no-workspace":
        workspace = None
    if request.param in ("custom", "custom-outside"):
        explicit_roots = [str(allowed_root)]
    if request.param == "custom-outside":
        workspace = outside_root
        current_dir = outside_root
    if request.param == "relative-root":
        monkeypatch.chdir(allowed_root)
        explicit_roots = [""]
    cwd.init_cwd(str(current_dir), workspace=str(workspace) if workspace else None)

    operation = SysOperation(SysOperationCard(
        id="shell-output-sandbox",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(
            restrict_to_sandbox=True,
            sandbox_root=explicit_roots,
        ),
    ))
    return operation, allowed_root, temp_root


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("entry", ["invoke", "stream", "error", "timeout"])
@pytest.mark.parametrize(
    "sandbox", ["defaults", "no-workspace", "custom", "custom-outside", "relative-root"], indirect=True,
)
async def test_large_shell_output_can_be_read_after_tool_returns(tool_type, entry, sandbox, monkeypatch):
    operation, allowed_root, temp_root = sandbox
    stdout = "\n".join(f"row-{index:03d} 中文 " + "x" * 100 for index in range(300))
    stderr = "stderr-marker"
    exit_code = 1 if entry == "error" else 0
    status = StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR if entry == "timeout" else StatusCode.SUCCESS
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(return_value=ExecuteCmdResult(
        code=status.code,
        message="execution timed out" if entry == "timeout" else "success",
        data=ExecuteCmdData(
            command="echo sandbox-output",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        ),
    ))

    async def stream_output(*args, **kwargs):
        yield ExecuteCmdStreamResult(
            code=StatusCode.SUCCESS.code,
            message="success",
            data=ExecuteCmdChunkData(text=stdout, type="stdout", chunk_index=0),
        )
        yield ExecuteCmdStreamResult(
            code=StatusCode.SUCCESS.code,
            message="success",
            data=ExecuteCmdChunkData(text=stderr, type="stderr", chunk_index=1, exit_code=exit_code),
        )

    shell.execute_cmd_stream = stream_output
    monkeypatch.setattr(operation, "shell", lambda: shell)
    tool = tool_type(operation, language="en")
    inputs = {"command": "echo sandbox-output", "workdir": str(allowed_root), "max_output_chars": 20000}
    if entry == "stream":
        outputs = [output async for output in tool.stream(inputs)]
        result = outputs[-1]
    else:
        result = await tool.invoke(inputs)

    assert result.success is (entry not in ("error", "timeout"))
    rendered = tool.render_for_llm(result)
    match = re.search(r"Full output saved to: ([^\r\n]+)", rendered)
    assert match is not None, rendered
    output_path = Path(match.group(1))

    # Read through the actual tool first, so the original failure is a sandbox denial.
    read = await ReadFileTool(operation).invoke({
        "file_path": str(output_path),
        "offset": 0,
        "limit": 2,
    })
    assert read.success, read.error
    assert "row-000" in str(read.data)
    assert output_path.resolve().is_relative_to(allowed_root.resolve())
    assert not output_path.resolve().is_relative_to(temp_root.resolve())
    assert output_path.read_bytes() == f"{stdout}\n--- stderr ---\n{stderr}".encode("utf-8")
    assert len(rendered) < 5000
    assert output_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
async def test_short_output_does_not_create_output_directory(tool_type, sandbox, monkeypatch):
    operation, allowed_root, temp_root = sandbox
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(return_value=ExecuteCmdResult(
        code=StatusCode.SUCCESS.code,
        message="success",
        data=ExecuteCmdData(command="echo short", stdout="short\n", exit_code=0),
    ))
    monkeypatch.setattr(operation, "shell", lambda: shell)

    result = await tool_type(operation).invoke({"command": "echo short"})

    assert result.success
    assert "Full output saved to:" not in result.data["content"]
    assert not list(allowed_root.rglob("*.txt"))
    assert not list(temp_root.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("entry", ["invoke", "stream"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
async def test_real_shell_output_can_be_read_in_sandbox(tool_type, entry, sandbox):
    if shutil.which("python") is None:
        pytest.skip("Python executable is unavailable on PATH")
    if tool_type is PowerShellTool and not (shutil.which("powershell") or shutil.which("pwsh")):
        pytest.skip("PowerShell is unavailable")
    operation, allowed_root, _ = sandbox
    command = (
        "python -c \"print('\\n'.join('readback-marker-' + str(i) + ' ' + 'x'*100 "
        "for i in range(300)))\""
    )
    tool = tool_type(operation, language="en")
    inputs = {"command": command, "workdir": str(allowed_root), "max_output_chars": 20000}
    if entry == "stream":
        results = [result async for result in tool.stream(inputs)]
        result = results[-1]
    else:
        result = await tool.invoke(inputs)

    assert result.success, result.error
    match = re.search(r"Full output saved to: ([^\r\n]+)", tool.render_for_llm(result))
    assert match is not None
    output_path = Path(match.group(1))
    assert "readback-marker-299" in output_path.read_text(encoding="utf-8")
    read = await ReadFileTool(operation).invoke({
        "file_path": str(output_path), "offset": 0, "limit": 2,
    })
    assert read.success, read.error
    assert "readback-marker-0" in str(read.data)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
async def test_shell_output_does_not_grant_access_to_other_temp_files(tool_type, sandbox, monkeypatch):
    operation, allowed_root, temp_root = sandbox
    unrelated_file = temp_root / "unrelated.txt"
    unrelated_file.write_text("outside sandbox", encoding="utf-8")
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(return_value=ExecuteCmdResult(
        code=StatusCode.SUCCESS.code,
        message="success",
        data=ExecuteCmdData(command="echo output", stdout="x" * 30000, exit_code=0),
    ))
    monkeypatch.setattr(operation, "shell", lambda: shell)
    result = await tool_type(operation).invoke({"command": "echo output"})
    assert result.success

    read = await ReadFileTool(operation).invoke({"file_path": str(unrelated_file)})

    assert not read.success
    assert "outside sandbox" in read.error
