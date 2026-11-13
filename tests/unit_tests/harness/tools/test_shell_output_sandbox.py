# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shell output files must remain readable through the configured sandbox."""

import hashlib
import importlib
import os
import re
import shutil
import subprocess
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


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("entry", ["invoke", "stream", "error", "timeout"])
@pytest.mark.parametrize("failure_type", [PermissionError, OSError])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
async def test_persistence_failure_returns_bounded_output(
    tool_type, entry, failure_type, sandbox, monkeypatch,
):
    operation, _, _ = sandbox
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")
    stdout = "OUTPUT-HEAD\n" + "x" * 30000
    stderr = "STDERR-END"
    exit_code = 1 if entry == "error" else 0
    status = StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR if entry == "timeout" else StatusCode.SUCCESS
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(return_value=ExecuteCmdResult(
        code=status.code,
        message="execution timed out" if entry == "timeout" else "success",
        data=ExecuteCmdData(command="echo output", stdout=stdout, stderr=stderr, exit_code=exit_code),
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

    def fail_persistence(*args, **kwargs):
        raise failure_type("output storage unavailable")

    shell.execute_cmd_stream = stream_output
    monkeypatch.setattr(operation, "shell", lambda: shell)
    monkeypatch.setattr(module, "persist_large_output", fail_persistence)
    tool = tool_type(operation)
    if entry == "stream":
        results = [result async for result in tool.stream({"command": "echo output"})]
        result = results[-1]
    else:
        result = await tool.invoke({"command": "echo output"})

    assert result.success is (entry not in ("error", "timeout"))
    rendered = tool.render_for_llm(result)
    assert "Full output could not be saved" in rendered
    assert "Full output saved to:" not in rendered
    assert "OUTPUT-HEAD" in rendered
    assert "STDERR-END" in rendered
    assert len(rendered) < 5000
    if entry == "timeout":
        assert "execution timed out" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
async def test_unusable_output_directory_keeps_command_result(tool_type, sandbox, monkeypatch):
    operation, allowed_root, temp_root = sandbox
    for base in (allowed_root / "workspace", allowed_root):
        (base / ".outputs").write_text("occupied by a file", encoding="utf-8")
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(return_value=ExecuteCmdResult(
        code=StatusCode.SUCCESS.code,
        message="success",
        data=ExecuteCmdData(command="echo output", stdout="OUTPUT-HEAD\n" + "x" * 30000, exit_code=0),
    ))
    monkeypatch.setattr(operation, "shell", lambda: shell)
    result = await tool_type(operation).invoke({"command": "echo output"})

    assert result.success
    assert "Full output could not be saved" in result.data["content"]
    assert not list(temp_root.iterdir())


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True, capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def _set_windows_junction_in_place(directory: Path, target: Path) -> None:
    import ctypes
    import struct
    from ctypes import wintypes

    output_directory = importlib.import_module("openjiuwen.harness.tools.shell._output_directory")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    device_io_control = kernel32.DeviceIoControl
    device_io_control.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    device_io_control.restype = wintypes.BOOL

    # Request write access to turn this existing empty directory into a junction.
    with output_directory._open_windows_file(directory, access=0x40000000, sharing=7, flags=0x02200000) as handle:
        substitute = ("\\??\\" + str(target)).encode("utf-16-le")
        display = str(target).encode("utf-16-le")
        data = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(display))
        data += substitute + b"\0\0" + display + b"\0\0"
        payload = struct.pack("<IHH", 0xA0000003, len(data), 0) + data
        buffer = ctypes.create_string_buffer(payload)
        returned = wintypes.DWORD()
        if not device_io_control(handle, 0x000900A4, buffer, len(payload), None, 0,
                                 ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())


@pytest.mark.skipif(os.name != "nt", reason="Windows directory reparse points")
@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_directory_cannot_become_junction_during_write(tool_type, sandbox, monkeypatch):
    operation, allowed_root, _ = sandbox
    outside = allowed_root.parent / "outside"
    control = allowed_root / "junction-control"
    control.mkdir()
    _set_windows_junction_in_place(control, outside)
    assert control.resolve() == outside.resolve()

    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")
    directory = module._output_dir(operation)
    original_open = os.open
    blocked = []

    def change_directory_before_write(path, flags, *args, **kwargs):
        if Path(path).parent == directory:
            try:
                _set_windows_junction_in_place(directory, outside)
            except OSError as error:
                blocked.append(error.winerror)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", change_directory_before_write)
    content, _ = module.render_tool_content(module.CommandOutput(
        command="echo output", stdout="x" * 30000, stderr="", exit_code=0,
        warning=None, max_output_chars=20000, operation=operation,
    ), False)

    assert list(outside.iterdir()) == []
    assert blocked == [32]  # ERROR_SHARING_VIOLATION
    assert "Full output saved to:" in content


@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_outputs_parent_swap_cannot_write_outside_sandbox(tool_type, sandbox, monkeypatch):
    operation, allowed_root, _ = sandbox
    outside = allowed_root.parent / "outside"
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")
    original_root = module.get_output_root

    def swap_parent_after_resolution(op):
        root = original_root(op)
        _make_directory_link(root, outside)
        return root

    monkeypatch.setattr(module, "get_output_root", swap_parent_after_resolution)
    module.render_tool_content(module.CommandOutput(
        command="echo output", stdout="x" * 30000, stderr="", exit_code=0,
        warning=None, max_output_chars=20000, operation=operation,
    ), False)

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_output_directory_swap_cannot_write_outside_sandbox(tool_type, sandbox, monkeypatch):
    operation, allowed_root, _ = sandbox
    outside = allowed_root.parent / "outside"
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")
    original_directory = module._output_dir

    def swap_directory_after_preparation(op=None):
        directory = original_directory(op)
        directory.rename(directory.with_name(directory.name + "-retained"))
        _make_directory_link(directory, outside)
        return directory

    monkeypatch.setattr(module, "_output_dir", swap_directory_after_preparation)
    module.render_tool_content(module.CommandOutput(
        command="echo output", stdout="x" * 30000, stderr="", exit_code=0,
        warning=None, max_output_chars=20000, operation=operation,
    ), False)

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_dangling_output_symlink_cannot_write_outside_sandbox(tool_type, sandbox):
    operation, allowed_root, _ = sandbox
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")
    stdout = "x" * 30000
    digest = hashlib.sha256(stdout.encode("utf-8")).hexdigest()[:12]
    name = "bash" if tool_type is BashTool else "powershell"
    output_path = module._output_dir(operation) / f"{name}_{digest}.txt"
    outside_file = allowed_root.parent / "outside" / "written.txt"
    try:
        output_path.symlink_to(outside_file)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Creating file symlinks requires Windows privilege")
        raise

    module.render_tool_content(module.CommandOutput(
        command="echo output", stdout=stdout, stderr="", exit_code=0,
        warning=None, max_output_chars=20000, operation=operation,
    ), False)

    assert not outside_file.exists()


@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_no_allowed_output_root_returns_preview_without_temp_fallback(tool_type, sandbox):
    operation, allowed_root, temp_root = sandbox
    outside = allowed_root.parent / "outside"
    for base in (allowed_root / "workspace", allowed_root):
        _make_directory_link(base / ".outputs", outside)
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")

    content, is_error = module.render_tool_content(module.CommandOutput(
        command="echo output", stdout="x" * 30000, stderr="", exit_code=0,
        warning=None, max_output_chars=20000, operation=operation,
    ), False)

    assert not is_error
    assert "Full output could not be saved" in content
    assert "Full output saved to:" not in content
    assert list(temp_root.iterdir()) == []
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_parent_change_during_publication_cannot_write_outside(tool_type, sandbox, monkeypatch):
    operation, allowed_root, _ = sandbox
    outside = allowed_root.parent / "outside"
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")
    directory = module._output_dir(operation)
    output_directory = importlib.import_module("openjiuwen.harness.tools.shell._output_directory")
    publisher = output_directory if os.name == "nt" else os
    replace_name = "_replace_windows_output_file" if os.name == "nt" else "replace"
    original_replace = getattr(publisher, replace_name)
    attempted = []

    def swap_parent_then_publish(source, destination, **kwargs):
        attempted.append(True)
        try:
            directory.parent.rename(directory.parent.with_name(".outputs-retained"))
        except PermissionError:
            assert os.name == "nt"
        else:
            _make_directory_link(directory.parent, outside)
        return original_replace(source, destination, **kwargs)

    monkeypatch.setattr(publisher, replace_name, swap_parent_then_publish)
    content, _ = module.render_tool_content(module.CommandOutput(
        command="echo output", stdout="x" * 30000, stderr="", exit_code=0,
        warning=None, max_output_chars=20000, operation=operation,
    ), False)

    assert attempted
    assert list(outside.iterdir()) == []
    if os.name == "nt":
        assert "Full output saved to:" in content
    else:
        assert "Full output could not be saved" in content


@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_partial_output_write_failure_removes_staging_file(tool_type, sandbox, monkeypatch):
    operation, _, _ = sandbox
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")
    directory = module._output_dir(operation)
    original_fdopen = os.fdopen

    class PartialWriteFailure:
        def __init__(self, descriptor, mode):
            self.stream = original_fdopen(descriptor, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, content):
            self.stream.write(content[:10])
            raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", PartialWriteFailure)
    content, _ = module.render_tool_content(module.CommandOutput(
        command="echo output", stdout="x" * 30000, stderr="", exit_code=0,
        warning=None, max_output_chars=20000, operation=operation,
    ), False)

    assert "Full output could not be saved" in content
    assert list(directory.iterdir()) == []
    directory.rename(directory.with_name(directory.name + "-closed"))


@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_persistence_failure_detail_is_bounded(tool_type, sandbox, monkeypatch):
    operation, _, _ = sandbox
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")

    def fail_persistence(*args, **kwargs):
        raise OSError("E" * 10000)

    monkeypatch.setattr(module, "persist_large_output", fail_persistence)
    content, _ = module.render_tool_content(module.CommandOutput(
        command="echo output", stdout="x" * 30000, stderr="", exit_code=0,
        warning=None, max_output_chars=20000, operation=operation,
    ), False)

    assert "Full output could not be saved" in content
    assert len(content) < 5000


@pytest.mark.parametrize("tool_type", [BashTool, PowerShellTool], ids=["bash", "powershell"])
@pytest.mark.parametrize("sandbox", ["custom"], indirect=True)
def test_preexisting_hardlink_does_not_overwrite_outside_file(tool_type, sandbox):
    operation, allowed_root, _ = sandbox
    module = importlib.import_module(tool_type.__module__.removesuffix("._tool") + "._output")
    content = b"x" * 30000
    digest = hashlib.sha256(content).hexdigest()[:12]
    name = "bash" if tool_type is BashTool else "powershell"
    output_path = module._output_dir(operation) / f"{name}_{digest}.txt"
    outside_file = allowed_root.parent / "outside" / "existing.txt"
    outside_file.write_bytes(b"original outside content")
    output_path.hardlink_to(outside_file)

    saved_path, _ = module.persist_large_output(content.decode("utf-8"), "", operation)

    assert outside_file.read_bytes() == b"original outside content"
    assert Path(saved_path).read_bytes() == content


@pytest.mark.parametrize("fail_in_context", [False, True], ids=["normal", "exception"])
def test_posix_directory_descriptor_is_released(fail_in_context, monkeypatch):
    output_directory = importlib.import_module("openjiuwen.harness.tools.shell._output_directory")
    opened = []
    closed = []

    def open_directory(path, flags, mode, *, dir_fd):
        opened.append((path, flags, mode, dir_fd))
        return 123

    monkeypatch.setattr(os, "open", open_directory)
    monkeypatch.setattr(os, "close", closed.append)
    try:
        with output_directory._open_posix_directory("child", os.O_RDONLY, dir_fd=456) as descriptor:
            assert descriptor == 123
            assert closed == []
            if fail_in_context:
                raise OSError("directory consumer failed")
    except OSError as error:
        assert fail_in_context
        assert str(error) == "directory consumer failed"

    assert opened == [("child", os.O_RDONLY, 0o700, 456)]
    assert closed == [123]


@pytest.mark.skipif(os.name != "nt", reason="Windows file handle sharing")
@pytest.mark.parametrize("fail_in_context", [False, True], ids=["normal", "exception"])
def test_windows_file_handle_is_released(fail_in_context, tmp_path):
    output_directory = importlib.import_module("openjiuwen.harness.tools.shell._output_directory")
    source = tmp_path / "output.txt"
    destination = tmp_path / "closed.txt"
    source.write_bytes(b"complete output")

    try:
        with output_directory._open_windows_file(
            source, access=0x80000000, sharing=0x00000001, flags=0x00200000,
        ) as handle:
            assert isinstance(handle, int)
            with pytest.raises(PermissionError):
                source.rename(destination)
            if fail_in_context:
                raise RuntimeError("handle consumer failed")
    except RuntimeError as error:
        assert fail_in_context
        assert str(error) == "handle consumer failed"

    source.rename(destination)
    assert destination.read_bytes() == b"complete output"


@pytest.mark.skipif(os.name != "nt", reason="Windows file handle errors")
def test_windows_file_handle_reports_open_failure(tmp_path):
    output_directory = importlib.import_module("openjiuwen.harness.tools.shell._output_directory")

    with pytest.raises(FileNotFoundError):
        with output_directory._open_windows_file(
            tmp_path / "missing.txt", access=0x80000000, sharing=0x00000001, flags=0x00200000,
        ):
            pytest.fail("A missing file must fail before entering the context")
