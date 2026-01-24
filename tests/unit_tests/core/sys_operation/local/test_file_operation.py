# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.sys_operation.sys_operation import SysOperationCard, SysOperation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.local.config import LocalWorkConfig
from openjiuwen.core.common.exception.codes import StatusCode


@pytest.fixture
def work_dir():
    # Create a temporary directory for tests
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup after tests
    shutil.rmtree(temp_dir)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(work_dir):
    await Runner.start()
    try:
        card_id = "test_fs_op"
        card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(work_dir=work_dir))
        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()
        op = Runner.resource_mgr.get_sys_operation(card_id)
        yield op
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()


@pytest.mark.asyncio
async def test_fs_read_write(sys_op, work_dir):
    """Test combined read and write operations."""
    # 1. Basic Write & Read
    file_name = "test_basics.txt"
    content = "Hello, world!\nLine 2"

    # Write
    write_res = await sys_op.fs().write_file(path=file_name, content=content, prepend_newline=False)
    assert write_res.code == StatusCode.SUCCESS.code

    # Read
    read_res = await sys_op.fs().read_file(path=file_name)
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == content

    # 2. Append/Prepend
    append_file = "test_append.txt"
    await sys_op.fs().write_file(path=append_file, content="Initial", prepend_newline=False)
    res = await sys_op.fs().read_file(path=append_file)
    assert res.data.content == "Initial"
    # Prepend newline
    await sys_op.fs().write_file(path=append_file, content="Appended", mode="text", prepend_newline=True,
                                 append_newline=False)

    res = await sys_op.fs().read_file(path=append_file)
    assert res.data.content == "\nAppended"

    # 3. Binary
    bin_file = "test.bin"
    bin_data = b"\x00\x01\x02"
    await sys_op.fs().write_file(path=bin_file, content=bin_data, mode="bytes")
    read_bin = await sys_op.fs().read_file(path=bin_file, mode="bytes")
    assert read_bin.data.content == bin_data


@pytest.mark.asyncio
async def test_fs_list_search_non_blocking(sys_op, work_dir):
    # Setup many files
    for i in range(200):
        subdir = os.path.join(work_dir, f"dir_{i // 20}")
        os.makedirs(subdir, exist_ok=True)
        with open(os.path.join(subdir, f"file_{i}.txt"), "w") as f:
            f.write("x" * 1024)

    heartbeat_count = 0
    stop = False

    async def heartbeat():
        nonlocal heartbeat_count
        while not stop:
            heartbeat_count += 1
            await asyncio.sleep(0.01)  # 10ms

    hb_task = asyncio.create_task(heartbeat())

    try:
        start = asyncio.get_running_loop().time()

        list_res = await sys_op.fs().list_files(".", recursive=True)
        assert list_res.code == StatusCode.SUCCESS.code

        search_res = await sys_op.fs().search_files(".", "*.txt")
        assert search_res.code == StatusCode.SUCCESS.code

        elapsed = asyncio.get_running_loop().time() - start

    finally:
        stop = True
        await hb_task

    expected_ticks = elapsed / 0.01

    assert heartbeat_count >= expected_ticks * 0.3, (
        f"heartbeat too slow: {heartbeat_count} vs expected {expected_ticks}"
    )


@pytest.mark.asyncio
async def test_fs_security_and_streams(sys_op, work_dir):
    """Test error handling (security) and streams."""
    # 1. Security (Path Traversal)
    res = await sys_op.fs().read_file("../outside.txt")
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "Access denied" in res.message or "traverses outside" in res.message

    # 2. Streams
    stream_file = "stream.txt"
    await sys_op.fs().write_file(stream_file, "line1\nline2", prepend_newline=False)

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(stream_file):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append(chunk.data.chunk_content)

    assert chunks == ["line1", "line2"]
