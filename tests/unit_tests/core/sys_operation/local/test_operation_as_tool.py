# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig


@pytest.fixture
def work_dir():
    # Create a temporary directory for tests
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup after tests
    shutil.rmtree(temp_dir)


@pytest_asyncio.fixture(name="card")
def sys_op_card_fixture(work_dir):
    card_id = "test_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(work_dir=work_dir))
    yield card


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(card, work_dir):
    await Runner.start()
    try:
        card_id = card.id
        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()
        op = Runner.resource_mgr.get_sys_operation(card_id)
        yield op
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()


@pytest.mark.asyncio
async def test_fs_list_tools_with_dict(card, sys_op):
    """Test list_tools for FS operation with dict conversion."""
    tools: list[ToolCard] = sys_op.fs().list_tools()

    tools_dict = {tool.name: tool for tool in tools}

    assert len(tools) == 10
    assert len(tools_dict) == 10

    expected_names = [
        "read_file", "read_file_stream", "write_file", "upload_file",
        "upload_file_stream", "download_file", "download_file_stream",
        "list_files", "list_directories", "search_files"
    ]
    for name in expected_names:
        assert name in tools_dict

    write_file_tool = tools_dict["write_file"]

    assert write_file_tool.description is not None
    assert "path" in write_file_tool.input_params["properties"]
    assert "content" in write_file_tool.input_params["properties"]
    assert write_file_tool.input_params["required"] == ["path", "content"]
    content_schema = write_file_tool.input_params["properties"]["content"]
    assert "anyOf" in content_schema
    assert len(content_schema["anyOf"]) == 2
    string_schema = content_schema["anyOf"][0]
    assert string_schema == {"type": "string"}
    binary_schema = content_schema["anyOf"][1]
    assert binary_schema == {"type": "string", "format": "binary"}

    read_file_tool = tools_dict["read_file"]

    mode_property = read_file_tool.input_params["properties"]["mode"]
    assert "text" in mode_property["enum"]
    assert "bytes" in mode_property["enum"]
    assert "path" in read_file_tool.input_params["properties"]
    assert read_file_tool.input_params["required"] == ["path"]


@pytest.mark.asyncio
async def test_fs_resource_mgr_read_write_text(card, sys_op, work_dir):
    """Test FS tools integration for text read/write operations."""
    # The sys_op fixture already adds the operation to Runner.resource_mgr
    rm = Runner.resource_mgr

    # Create test files for testing
    test_file = "integration_test.txt"
    content = "resource mgr integration\nline 2\nline 3"
    await sys_op.fs().write_file(test_file, content, prepend_newline=False)

    # Test read_file with different parameter counts
    read_file_tool = rm.get_tool(card.fs.read_file)
    assert read_file_tool is not None
    assert read_file_tool.card.name == f"read_file"

    # Scenario 1: Only required parameter (path)
    res = await read_file_tool.invoke({"path": test_file})
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == content

    # Scenario 2: Two parameters (path + mode)
    res = await read_file_tool.invoke({"path": test_file, "mode": "text"})
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == content

    # Scenario 3: Multiple parameters
    res = await read_file_tool.invoke({"path": test_file, "mode": "text", "head": 2})
    assert res.code == StatusCode.SUCCESS.code
    assert "line 2" in res.data.content  # Should include first 2 lines
    assert "line 3" not in res.data.content  # Should not include line 3

    # Test write_file with different parameter counts
    write_file_tool = rm.get_tool(card.fs.write_file)
    assert write_file_tool is not None
    assert write_file_tool.card.name == f"write_file"

    # Scenario 1: Required parameters only (path + content)
    write_test_file = "write_test.txt"
    write_content = "test write content"
    res = await write_file_tool.invoke({"path": write_test_file, "content": write_content})
    assert res.code == StatusCode.SUCCESS.code

    # Verify file was written
    verify_res = await read_file_tool.invoke({"path": write_test_file})
    assert verify_res.code == StatusCode.SUCCESS.code
    assert write_content in verify_res.data.content


@pytest.mark.asyncio
async def test_fs_resource_mgr_read_write_binary(card, sys_op, work_dir):
    """Test FS tools integration for binary read/write operations."""
    rm = Runner.resource_mgr

    # Test write_file with binary mode
    write_file_tool = rm.get_tool(card.fs.write_file)
    assert write_file_tool is not None

    binary_test_file = "binary_test.bin"
    binary_content = b"\x00\x01\x02\x03\x04\x05\xff\xfe"
    res = await write_file_tool.invoke({"path": binary_test_file, "content": binary_content, "mode": "bytes"})
    assert res.code == StatusCode.SUCCESS.code

    # Test read_file with binary mode
    read_file_tool = rm.get_tool(card.fs.read_file)
    assert read_file_tool is not None

    res = await read_file_tool.invoke({"path": binary_test_file, "mode": "bytes"})
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == binary_content
    assert res.data.mode == "bytes"

    # Test read_file_stream with binary mode
    read_file_stream_tool = rm.get_tool(card.fs.read_file_stream)
    assert read_file_stream_tool is not None

    chunks = []
    # Using small chunk_size to ensure multiple chunks for a small string
    async for chunk_res in read_file_stream_tool.stream({"path": binary_test_file, "mode": "bytes", "chunk_size": 2}):
        assert chunk_res.code == StatusCode.SUCCESS.code
        chunks.append(chunk_res.data.chunk_content)

    assert b"".join(chunks) == binary_content
    assert len(chunks) == 4  # 8 bytes / 2 bytes/chunk = 4 chunks


@pytest.mark.asyncio
async def test_fs_resource_mgr_other_methods(card, sys_op, work_dir):
    """Test FS tools integration for other file system operations."""
    rm = Runner.resource_mgr

    # Create test files for testing
    test_file1 = "test_file1.txt"
    test_file2 = "test_file2.txt"
    await sys_op.fs().write_file(test_file1, "test content 1", prepend_newline=False)
    await sys_op.fs().write_file(test_file2, "test content 2", prepend_newline=False)

    # Test list_files with different parameter counts
    list_files_tool = rm.get_tool(card.fs.list_files)
    assert list_files_tool is not None
    assert list_files_tool.card.name == f"list_files"

    # Scenario 1: Only required parameter (path)
    res = await list_files_tool.invoke({"path": "."})
    assert res.code == StatusCode.SUCCESS.code
    assert len(res.data.list_items) > 0

    # Scenario 2: Two parameters (path + recursive)
    res = await list_files_tool.invoke({"path": ".", "recursive": True})
    assert res.code == StatusCode.SUCCESS.code

    # Scenario 3: Multiple parameters
    res = await list_files_tool.invoke({"path": ".", "recursive": True, "max_depth": 2, "sort_by": "name"})
    assert res.code == StatusCode.SUCCESS.code

    # Test search_files with different parameter counts
    search_files_tool = rm.get_tool(card.fs.search_files)
    assert search_files_tool is not None
    assert search_files_tool.card.name == f"search_files"

    # Scenario 1: Required parameters only (path + pattern)
    res = await search_files_tool.invoke({"path": ".", "pattern": "*.txt"})
    assert res.code == StatusCode.SUCCESS.code


@pytest.mark.asyncio
async def test_shell_resource_mgr_integration(card, sys_op):
    """Test that Shell tools are automatically registered in ResourceMgr."""
    rm = Runner.resource_mgr

    tool = rm.get_tool(card.shell.execute_cmd)
    assert tool is not None
    assert tool.card.name == f"execute_cmd"

    # Verify tool execution through resource_mgr
    invoke_res = await tool.invoke({"command": "echo hello_integration", "options": {"encoding": "utf-8"}})
    assert invoke_res.code == StatusCode.SUCCESS.code
    assert "hello_integration" in invoke_res.data.stdout


@pytest.mark.asyncio
async def test_code_resource_mgr_integration(card, sys_op):
    """Test that Code tools are automatically registered in ResourceMgr."""
    rm = Runner.resource_mgr

    tool = rm.get_tool(card.code.execute_code)
    assert tool is not None
    assert tool.card.name == f"execute_code"

    # Verify tool execution through resource_mgr
    code = "print('hello_integration')"
    invoke_res = await tool.invoke({"code": code, "language": "python", "options": {"encoding": "utf-8"}})
    assert invoke_res.code == StatusCode.SUCCESS.code
    assert "hello_integration" in invoke_res.data.stdout


@pytest.mark.asyncio
async def test_batch_sys_operation_lifecycle(work_dir):
    """Test batch add, get and remove lifecycle for multiple sys operations using lists."""
    await Runner.start()
    try:
        rm = Runner.resource_mgr

        # 1. Create multiple sys operation cards
        card1 = SysOperationCard(id="batch_op_1", mode=OperationMode.LOCAL,
                                 work_config=LocalWorkConfig(work_dir=work_dir))
        card2 = SysOperationCard(id="batch_op_2", mode=OperationMode.LOCAL,
                                 work_config=LocalWorkConfig(work_dir=work_dir))
        card3 = SysOperationCard(id="batch_op_3", mode=OperationMode.LOCAL,
                                 work_config=LocalWorkConfig(work_dir=work_dir))

        # 2. Add multiple sys operations in ONE call
        # Should return a list of Results
        add_results = rm.add_sys_operation(card=[card1, card2, card3])
        assert isinstance(add_results, list)
        assert len(add_results) == 3
        assert all(res.is_ok() for res in add_results)

        # 3. Get multiple sys operations in ONE call
        # Should return a list of SysOperations
        ops = rm.get_sys_operation(sys_operation_id=["batch_op_1", "batch_op_2", "batch_op_3"])
        assert isinstance(ops, list)
        assert len(ops) == 3
        assert all(op is not None for op in ops)

        # Verify tools are registered for all
        assert rm.get_tool(card1.fs.read_file) is not None
        assert rm.get_tool(card2.shell.execute_cmd) is not None
        assert rm.get_tool(card3.code.execute_code) is not None

        # 4. Remove multiple sys operations in ONE call
        # Should return a list of Results
        remove_results = rm.remove_sys_operation(sys_operation_id=["batch_op_1", "batch_op_2"])
        assert isinstance(remove_results, list)
        assert len(remove_results) == 2
        assert all(r.is_ok() for r in remove_results)

        # 5. Final verification
        # Operations 1 and 2 should be gone
        assert rm.get_sys_operation("batch_op_1") is None
        assert rm.get_sys_operation("batch_op_2") is None
        # Tools associated with removed operations should be gone
        assert rm.get_tool(card1.fs.read_file) is None
        assert rm.get_tool(card2.shell.execute_cmd) is None

        # Operation 3 and its tools should still be there
        assert rm.get_sys_operation("batch_op_3") is not None
        assert rm.get_tool(card3.code.execute_code) is not None

        # Cleanup remaining via single ID call (works as before)
        rm.remove_sys_operation(sys_operation_id="batch_op_3")
        assert rm.get_sys_operation("batch_op_3") is None
        assert rm.get_tool(card3.code.execute_code) is None

    finally:
        await Runner.stop()
