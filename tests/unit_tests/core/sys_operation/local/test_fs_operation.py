# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import os
import random
import shutil
import tempfile
import unittest

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig


@pytest.fixture
def work_dir():
    # Create a temporary directory for tests.
    # realpath resolves macOS /var -> /private/var symlink so the path
    # matches what SysOperation path security checks resolve to.
    temp_dir = os.path.realpath(tempfile.mkdtemp())
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
async def test_fs_read_head(sys_op, work_dir):
    """Test head operations for both normal and stream reading."""
    multi_line_file = "multi_line_test.txt"
    multi_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    await sys_op.fs().write_file(multi_line_file, multi_content, prepend_newline=False)

    # 1. Normal head tests
    # Head with more lines than exist
    res = await sys_op.fs().read_file(path=multi_line_file, head=10)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == multi_content

    # Head with exactly the number of lines
    res = await sys_op.fs().read_file(path=multi_line_file, head=5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == multi_content

    # Head with fewer lines
    res = await sys_op.fs().read_file(path=multi_line_file, head=3)
    assert res.code == StatusCode.SUCCESS.code
    # Get the first 3 lines with original line endings
    expected_head = "Line 1\nLine 2\nLine 3\n"
    # Check if the content matches either with or without trailing newline
    assert res.data.content == expected_head

    # 2. Stream head tests with is_last_chunk validation
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, head=3):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 3
    # Test that only the last chunk has is_last_chunk=True
    assert all(not is_last for _, is_last in chunks[:-1])
    assert chunks[-1][1] == True
    # Test that lines contain original line endings
    # Join chunks and test content
    assert ''.join(line for line, _ in chunks) == expected_head


@pytest.mark.asyncio
async def test_fs_read_tail(sys_op, work_dir):
    """Test tail operations for both normal and stream reading."""
    multi_line_file = "multi_line_test.txt"
    multi_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    await sys_op.fs().write_file(multi_line_file, multi_content, prepend_newline=False)

    # 1. Normal tail tests
    # Tail with more lines than exist (should return all lines)
    res = await sys_op.fs().read_file(path=multi_line_file, tail=10)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == multi_content

    # Tail with exactly the number of lines
    res = await sys_op.fs().read_file(path=multi_line_file, tail=5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == multi_content

    # Tail with fewer lines
    res = await sys_op.fs().read_file(path=multi_line_file, tail=2)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 4\nLine 5"

    # Tail on an empty file
    empty_file = "empty.txt"
    await sys_op.fs().write_file(empty_file, "", prepend_newline=False)
    res = await sys_op.fs().read_file(path=empty_file, tail=5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    # Tail on a file with only 1 line
    single_line_file = "single_line.txt"
    await sys_op.fs().write_file(single_line_file, "Only one line", prepend_newline=False)
    res = await sys_op.fs().read_file(path=single_line_file, tail=5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Only one line"

    # 2. Stream tail tests with is_last_chunk validation
    # Stream tail with more lines than exist
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, tail=10):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 5
    # Test that only the last chunk has is_last_chunk=True
    assert all(not is_last for _, is_last in chunks[:-1])
    assert chunks[-1][1] == True
    # Test that lines contain original line endings
    # Join chunks and test content
    assert ''.join(line for line, _ in chunks) == multi_content

    # Stream tail with fewer lines
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, tail=2):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 2
    # Test that only the last chunk has is_last_chunk=True
    assert all(not is_last for _, is_last in chunks[:-1])
    assert chunks[-1][1] == True
    # Join chunks and test content
    assert ''.join(line for line, _ in chunks) == "Line 4\nLine 5"


@pytest.mark.asyncio
async def test_fs_read_line_range(sys_op, work_dir):
    """Test line range operations for both normal and stream reading."""
    multi_line_file = "multi_line_test.txt"
    multi_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    await sys_op.fs().write_file(multi_line_file, multi_content, prepend_newline=False)

    # 1. Normal line range tests
    # Line range within bounds
    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(2, 4))
    assert res.code == StatusCode.SUCCESS.code
    expected_range = "Line 2\nLine 3\nLine 4\n"
    assert res.data.content == expected_range

    # Line range starting at 1
    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(1, 3))
    assert res.code == StatusCode.SUCCESS.code
    expected_range = "Line 1\nLine 2\nLine 3\n"
    assert res.data.content == expected_range

    # Line range ending at last line
    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(4, 5))
    assert res.code == StatusCode.SUCCESS.code
    expected_range = "Line 4\nLine 5"
    assert res.data.content == expected_range

    # Line range with start > end (should return empty)
    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(4, 2))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    # 2. Stream line range tests with is_last_chunk validation
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(2, 4)):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 3
    # Test that only the last chunk has is_last_chunk=True
    assert all(not is_last for _, is_last in chunks[:-1])
    assert chunks[-1][1] == True
    # Join chunks and test content
    assert ''.join(line for line, _ in chunks) == "Line 2\nLine 3\nLine 4\n"

    # 3. Boundary case: line_range exceeding file length
    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(2, 10))
    assert res.code == StatusCode.SUCCESS.code
    # Should return lines 2-5 (all available lines from line 2 onwards)
    expected_range = "Line 2\nLine 3\nLine 4\nLine 5"
    assert res.data.content == expected_range

    # Stream version: line_range exceeding file length
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(2, 10)):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 4  # Lines 2-5
    # Last chunk should have is_last_chunk=True
    assert chunks[-1][1] == True
    assert ''.join(line for line, _ in chunks) == expected_range

    # 4. Boundary case: line_range starting beyond file end
    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(10, 20))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    # Stream version: line_range starting beyond file end
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(10, 20)):
        chunks.append(chunk)
    # Should return no chunks (empty result)
    assert len(chunks) == 0

    # 5. Boundary case: single line range
    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(3, 3))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 3\n"

    # Stream version: single line range
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(3, 3)):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 1
    assert chunks[0][1] == True  # Single chunk should be marked as last
    assert chunks[0][0] == "Line 3\n"

    # 6. Boundary case: range at exact file end
    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(5, 5))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 5"

    # Stream version: range at exact file end
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(5, 5)):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 1
    assert chunks[0][1] == True
    assert chunks[0][0] == "Line 5"

    # 7. Boundary case: range on empty file
    empty_file = "empty_range.txt"
    await sys_op.fs().write_file(empty_file, "", prepend_newline=False)
    res = await sys_op.fs().read_file(path=empty_file, line_range=(1, 5))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    # Stream version: range on empty file
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=empty_file, line_range=(1, 5)):
        chunks.append(chunk)
    assert len(chunks) == 0


@pytest.mark.asyncio
@unittest.skip("skip perf test")
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

    assert ''.join(chunks) == "line1\nline2"


@pytest.mark.asyncio
async def test_fs_read_file_mutually_exclusive_params(sys_op, work_dir):
    """Test that mutually exclusive parameters cannot be specified simultaneously."""
    # Create a test file with multiple lines
    test_file = "multi_line.txt"
    content = "line1\nline2\nline3\nline4\nline5"
    await sys_op.fs().write_file(test_file, content, prepend_newline=False)

    # Test 1: head and tail cannot be specified together
    res = await sys_op.fs().read_file(path=test_file, head=2, tail=2)
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in res.message
    assert "head" in res.message
    assert "tail" in res.message

    # Test 2: head and line_range cannot be specified together
    res = await sys_op.fs().read_file(path=test_file, head=-1, line_range=(2, 4))
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in res.message
    assert "head" in res.message
    assert "line_range" in res.message

    # Test 3: tail and line_range cannot be specified together
    res = await sys_op.fs().read_file(path=test_file, tail=2, line_range=(2, -1))
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in res.message
    assert "tail" in res.message
    assert "line_range" in res.message

    # Test 4: Test mutually exclusive parameters in read_file_stream
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, head=-1, tail=2):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in chunks[0].message
    assert "head" in chunks[0].message
    assert "tail" in chunks[0].message

    res = await sys_op.fs().read_file(path=test_file, head=0, tail=2)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "line4\nline5"


@pytest.mark.asyncio
async def test_fs_read_file_negative_zero_params(sys_op, work_dir):
    """Test handling of negative and zero values for read parameters."""
    # Create a test file with multiple lines
    test_file = "multi_line.txt"
    content = "line1\nline2\nline3\nline4\nline5"
    await sys_op.fs().write_file(test_file, content, prepend_newline=False)

    # Test 1: Negative head value should return empty content
    res = await sys_op.fs().read_file(path=test_file, head=-5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    # Test 2: Negative tail value should return empty content
    res = await sys_op.fs().read_file(path=test_file, tail=-5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    # Test 3: Zero head value should be treated as not passed (return full content)
    res = await sys_op.fs().read_file(path=test_file, head=0)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == content

    # Test 4: Zero tail value should be treated as not passed (return full content)
    res = await sys_op.fs().read_file(path=test_file, tail=0)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == content

    # Test 5: Zero line_range should return empty content
    res = await sys_op.fs().read_file(path=test_file, line_range=(0, 0))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    res = await sys_op.fs().read_file(path=test_file, line_range=(1, -1))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    # Test 6: Negative head in read_file_stream should return empty content
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, head=-5):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SUCCESS.code
    assert chunks[0].data.chunk_content == ""

    # Test 7: Negative tail in read_file_stream should return empty content
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, tail=-5):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SUCCESS.code
    assert chunks[0].data.chunk_content == ""

    # Test 8: Zero parameters in read_file_stream should be treated as not passed (return full content)
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, head=0):
        chunks.append(chunk)
    assert len(chunks) == 5  # Should return all 5 lines
    assert chunks[0].code == StatusCode.SUCCESS.code
    # Join chunks and test content
    assert ''.join(chunk.data.chunk_content for chunk in chunks) == content


@pytest.mark.asyncio
async def test_fs_read_file_binary_mode_parameters(sys_op, work_dir):
    """Test that text mode only parameters are not allowed in binary mode."""
    # Create a test file
    test_file = "binary_test.txt"
    content = "Hello, world!\nLine 2"
    await sys_op.fs().write_file(test_file, content, prepend_newline=False)

    # Test 1: read_file with head in binary mode should fail
    res = await sys_op.fs().read_file(path=test_file, mode="bytes", head=2)
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in res.message

    # Test 2: read_file with tail in binary mode should fail
    res = await sys_op.fs().read_file(path=test_file, mode="bytes", tail=2)
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in res.message

    # Test 3: read_file with line_range in binary mode should fail
    res = await sys_op.fs().read_file(path=test_file, mode="bytes", line_range=(1, 2))
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in res.message

    # Test 4: read_file_stream with head in binary mode should fail
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, mode="bytes", head=2):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in chunks[0].message

    # Test 5: read_file_stream with tail in binary mode should fail
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, mode="bytes", tail=2):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in chunks[0].message

    # Test 6: read_file_stream with line_range in binary mode should fail
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, mode="bytes", line_range=(1, 2)):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in chunks[0].message


@pytest.mark.asyncio
async def test_fs_large_binary_file(sys_op, work_dir):
    """Test large binary file read and write operations."""
    # Generate 1MB of random binary data
    large_bin_file = "large_test.bin"
    # 1MB = 1024 * 1024 bytes
    large_bin_data = os.urandom(1024 * 1024)

    # Write large binary file
    write_res = await sys_op.fs().write_file(path=large_bin_file, content=large_bin_data, mode="bytes")
    assert write_res.code == StatusCode.SUCCESS.code

    # Read large binary file
    read_res = await sys_op.fs().read_file(path=large_bin_file, mode="bytes", chunk_size=0)
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == large_bin_data

    # Verify file size
    file_path = os.path.join(work_dir, large_bin_file)
    assert os.path.exists(file_path)
    assert os.path.getsize(file_path) == 1024 * 1024

    # Test streaming read of large binary file
    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=large_bin_file, mode="bytes", chunk_size=1024 * 64):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append(chunk.data.chunk_content)

    # Verify streamed data
    streamed_data = b""
    for chunk in chunks:
        streamed_data += chunk
    assert streamed_data == large_bin_data
    assert len(chunks) > 0  # Should have at least one chunk


@pytest.mark.asyncio
async def test_fs_upload_download(sys_op, work_dir):
    """Test upload and download operations for both normal and stream modes."""
    # Create test files
    test_file = "upload_test.txt"
    test_content = "Hello, upload and download!"
    await sys_op.fs().write_file(test_file, test_content, prepend_newline=False)

    # Use absolute paths for local_path
    absolute_test_file = os.path.join(work_dir, test_file)

    # Test 1: Normal upload and download
    upload_target = "uploaded_file.txt"
    upload_res = await sys_op.fs().upload_file(local_path=absolute_test_file, target_path=upload_target)
    assert upload_res.code == StatusCode.SUCCESS.code

    # Use work_dir for download target to ensure it's in the temporary directory
    download_target = os.path.join(work_dir, "downloaded_file.txt")
    download_res = await sys_op.fs().download_file(source_path=upload_target, local_path=download_target)
    assert download_res.code == StatusCode.SUCCESS.code

    # Verify downloaded content
    read_res = await sys_op.fs().read_file(download_target)
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == test_content

    # Test 2: Streaming upload and download
    stream_upload_target = "stream_uploaded.txt"
    chunks = []
    async for chunk in sys_op.fs().upload_file_stream(local_path=absolute_test_file, target_path=stream_upload_target):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append(chunk)
    assert len(chunks) > 0

    # Use work_dir for stream download target
    stream_download_target = os.path.join(work_dir, "stream_downloaded.txt")
    download_chunks = []
    async for chunk in sys_op.fs().download_file_stream(source_path=stream_upload_target,
                                                        local_path=stream_download_target):
        assert chunk.code == StatusCode.SUCCESS.code
        download_chunks.append(chunk)
    assert len(download_chunks) > 0

    # Verify streamed download content
    # Read from the downloaded local file directly
    with open(stream_download_target, 'r') as f:
        stream_content = f.read()
    assert stream_content == test_content

    # Test 3: Boundary case - empty file
    empty_file = "empty.txt"
    await sys_op.fs().write_file(empty_file, "", prepend_newline=False)
    absolute_empty_file = os.path.join(work_dir, empty_file)

    empty_upload_target = "empty_uploaded.txt"
    empty_upload_res = await sys_op.fs().upload_file(local_path=absolute_empty_file, target_path=empty_upload_target)
    assert empty_upload_res.code == StatusCode.SUCCESS.code

    # Use work_dir for empty file download target
    empty_download_target = os.path.join(work_dir, "empty_downloaded.txt")
    empty_download_res = await sys_op.fs().download_file(source_path=empty_upload_target,
                                                         local_path=empty_download_target)
    assert empty_download_res.code == StatusCode.SUCCESS.code

    # Read from the downloaded local file directly
    with open(empty_download_target, 'r') as f:
        empty_content = f.read()
    assert empty_content == ""

    # Test 4: Test chunk_size with 0 and -1 (no limit - read entire file)
    # Create a test file
    large_file = "large_upload_test.txt"
    large_content = "x" * (1024 * 1024)  # 1MB file
    await sys_op.fs().write_file(large_file, large_content, prepend_newline=False)
    absolute_large_file = os.path.join(work_dir, large_file)

    # Test upload with chunk_size=0 (no limit)
    upload_target_0 = "uploaded_large_0.txt"
    upload_res_0 = await sys_op.fs().upload_file(local_path=absolute_large_file, target_path=upload_target_0,
                                                 chunk_size=0)
    assert upload_res_0.code == StatusCode.SUCCESS.code

    # Test upload with chunk_size=-1 (no limit)
    upload_target_neg1 = "uploaded_large_neg1.txt"
    upload_res_neg1 = await sys_op.fs().upload_file(local_path=absolute_large_file, target_path=upload_target_neg1,
                                                    chunk_size=-1)
    assert upload_res_neg1.code == StatusCode.SUCCESS.code

    # Test download with chunk_size=0 (no limit)
    download_target_0 = os.path.join(work_dir, "downloaded_large_0.txt")
    download_res_0 = await sys_op.fs().download_file(source_path=upload_target_0, local_path=download_target_0,
                                                     chunk_size=0)
    assert download_res_0.code == StatusCode.SUCCESS.code

    # Test download with chunk_size=-1 (no limit)
    download_target_neg1 = os.path.join(work_dir, "downloaded_large_neg1.txt")
    download_res_neg1 = await sys_op.fs().download_file(source_path=upload_target_neg1, local_path=download_target_neg1,
                                                        chunk_size=-1)
    assert download_res_neg1.code == StatusCode.SUCCESS.code

    # Verify downloaded content
    with open(download_target_0, 'r') as f:
        downloaded_content_0 = f.read()
    assert downloaded_content_0 == large_content

    with open(download_target_neg1, 'r') as f:
        downloaded_content_neg1 = f.read()
    assert downloaded_content_neg1 == large_content


@pytest.mark.asyncio
async def test_fs_list_operations(sys_op, work_dir):
    """Test list_files and list_directories operations."""
    # Create test directory structure
    os.makedirs(os.path.join(work_dir, "dir1"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "dir1", "subdir1"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "dir2"), exist_ok=True)

    # Create test files
    await sys_op.fs().write_file("file1.txt", "Content 1", prepend_newline=False)
    await sys_op.fs().write_file("dir1/file2.txt", "Content 2", prepend_newline=False)
    await sys_op.fs().write_file("dir1/subdir1/file3.txt", "Content 3", prepend_newline=False)
    await sys_op.fs().write_file("dir2/file4.txt", "Content 4", prepend_newline=False)

    # Test 1: list_files - normal mode
    list_res = await sys_op.fs().list_files(".")
    assert list_res.code == StatusCode.SUCCESS.code
    assert len(list_res.data.list_items) >= 1  # Should find at least file1.txt

    # Test 2: list_files - recursive
    recursive_res = await sys_op.fs().list_files(".", recursive=True)
    assert recursive_res.code == StatusCode.SUCCESS.code
    assert len(recursive_res.data.list_items) >= 4  # Should find all files

    # Test 3: list_files - with file types filter
    txt_files_res = await sys_op.fs().list_files(".", recursive=True, file_types=[".txt"])
    assert txt_files_res.code == StatusCode.SUCCESS.code
    assert len(txt_files_res.data.list_items) >= 4  # Should find all txt files

    # Test 4: list_directories - normal mode
    dirs_res = await sys_op.fs().list_directories(".")
    assert dirs_res.code == StatusCode.SUCCESS.code
    assert len(dirs_res.data.list_items) >= 2  # Should find dir1 and dir2

    # Test 5: list_directories - recursive
    recursive_dirs_res = await sys_op.fs().list_directories(".", recursive=True)
    assert recursive_dirs_res.code == StatusCode.SUCCESS.code
    assert len(recursive_dirs_res.data.list_items) >= 3  # Should find all directories

    # Test 6: Boundary case - empty directory
    empty_dir = "empty_dir"
    os.makedirs(os.path.join(work_dir, empty_dir), exist_ok=True)

    empty_list_res = await sys_op.fs().list_files(empty_dir)
    assert empty_list_res.code == StatusCode.SUCCESS.code
    assert len(empty_list_res.data.list_items) == 0

    empty_dirs_res = await sys_op.fs().list_directories(empty_dir)
    assert empty_dirs_res.code == StatusCode.SUCCESS.code
    assert len(empty_dirs_res.data.list_items) == 0


@pytest.mark.asyncio
async def test_fs_search_operations(sys_op, work_dir):
    """Test search_files operation with various patterns."""
    # Create test files
    await sys_op.fs().write_file("test1.txt", "Content 1", prepend_newline=False)
    await sys_op.fs().write_file("test2.txt", "Content 2", prepend_newline=False)
    await sys_op.fs().write_file("data1.csv", "CSV content", prepend_newline=False)
    await sys_op.fs().write_file("data2.csv", "More CSV", prepend_newline=False)

    # Create subdirectory with files
    os.makedirs(os.path.join(work_dir, "subdir"), exist_ok=True)
    await sys_op.fs().write_file("subdir/test3.txt", "Content 3", prepend_newline=False)

    # Test 1: Search for all txt files
    txt_search_res = await sys_op.fs().search_files(".", "*.txt")
    assert txt_search_res.code == StatusCode.SUCCESS.code
    assert len(txt_search_res.data.matching_files) >= 3  # Should find test1.txt, test2.txt, subdir/test3.txt

    # Test 2: Search for csv files
    csv_search_res = await sys_op.fs().search_files(".", "*.csv")
    assert csv_search_res.code == StatusCode.SUCCESS.code
    assert len(csv_search_res.data.matching_files) >= 2  # Should find data1.csv, data2.csv

    # Test 3: Search with exclude patterns
    exclude_search_res = await sys_op.fs().search_files(".", "*", exclude_patterns=["*.csv"])
    assert exclude_search_res.code == StatusCode.SUCCESS.code
    # Should find txt files but not csv files
    csv_files = [f for f in exclude_search_res.data.matching_files if f.name.endswith(".csv")]
    assert len(csv_files) == 0

    # Test 4: Boundary case - no matching files
    no_match_res = await sys_op.fs().search_files(".", "*.xyz")
    assert no_match_res.code == StatusCode.SUCCESS.code
    assert len(no_match_res.data.matching_files) == 0


@pytest.mark.asyncio
async def test_fs_write_file_append_text(sys_op, work_dir):
    """Test write_file with append=True for text mode."""
    append_file = "test_append_text.txt"

    await sys_op.fs().write_file(path=append_file, content="Line 1", prepend_newline=False, append=False)
    res = await sys_op.fs().read_file(path=append_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 1"

    await sys_op.fs().write_file(path=append_file, content="Line 2", prepend_newline=False, append=True)
    res = await sys_op.fs().read_file(path=append_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 1Line 2"

    await sys_op.fs().write_file(path=append_file, content="\nLine 3", prepend_newline=False, append=True)
    res = await sys_op.fs().read_file(path=append_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 1Line 2\nLine 3"


@pytest.mark.asyncio
async def test_fs_write_file_append_binary(sys_op, work_dir):
    """Test write_file with append=True for binary mode."""
    append_bin_file = "test_append_binary.bin"

    await sys_op.fs().write_file(path=append_bin_file, content=b"\x00\x01", mode="bytes", append=False)
    res = await sys_op.fs().read_file(path=append_bin_file, mode="bytes")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == b"\x00\x01"

    await sys_op.fs().write_file(path=append_bin_file, content=b"\x02\x03", mode="bytes", append=True)
    res = await sys_op.fs().read_file(path=append_bin_file, mode="bytes")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == b"\x00\x01\x02\x03"

    await sys_op.fs().write_file(path=append_bin_file, content=b"\x04\x05", mode="bytes", append=True)
    res = await sys_op.fs().read_file(path=append_bin_file, mode="bytes")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == b"\x00\x01\x02\x03\x04\x05"


@pytest.mark.asyncio
async def test_fs_write_file_append_new_file(sys_op, work_dir):
    """Test write_file with append=True on a non-existent file."""
    new_file = "test_append_new_file.txt"

    res = await sys_op.fs().write_file(path=new_file, content="First content", prepend_newline=False, append=True)
    assert res.code == StatusCode.SUCCESS.code

    res = await sys_op.fs().read_file(path=new_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "First content"


@pytest.mark.asyncio
async def test_concurrent_read_file(sys_op, work_dir):
    """Test concurrent file reading (verify data consistency)"""
    # Define test file path and content
    test_file_path = "test_concurrent_read.txt"
    test_content = "line1\nline2\nline3\nline4\nline5"

    # Prepare test file with fixed content
    await sys_op.fs().write_file(
        path=str(test_file_path),
        content=test_content,
        mode="text",
        append=False,
        prepend_newline=False,
        create_if_not_exist=True
    )

    # Define single read task for concurrency
    async def read_task():
        result = await sys_op.fs().read_file(path=str(test_file_path), mode="text")
        assert result.code == StatusCode.SUCCESS.code
        assert result.data.content == test_content
        return result

    # Create 5 concurrent read tasks
    tasks = [read_task() for _ in range(5)]
    # Execute all read tasks concurrently
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_concurrent_write_file(sys_op, work_dir):
    """Test concurrent file writing (verify that the locking mechanism prevents data overwriting)"""
    # Define test file path and write parameters
    file_path = "test_concurrent_write.txt"
    write_count = 10
    per_write_content = "test_concurrent_write\n"

    # Define single write task for concurrency
    async def write_task():
        write_result = await sys_op.fs().write_file(
            path=file_path,
            content=per_write_content,
            mode="text",
            append=True,
            prepend_newline=False,
            create_if_not_exist=True
        )
        assert write_result.code == StatusCode.SUCCESS.code
        return write_result

    # Create multiple concurrent write tasks
    tasks = [write_task() for _ in range(write_count)]
    # Execute all write tasks concurrently
    await asyncio.gather(*tasks)

    # Verify final file content integrity
    result = await sys_op.fs().read_file(path=file_path, mode="text")
    assert result.code == StatusCode.SUCCESS.code
    assert len(result.data.content.strip().split("\n")) == write_count
    assert result.data.content == per_write_content * write_count


@pytest.mark.asyncio
async def test_concurrent_read_write_mixed(sys_op, work_dir):
    """Test mixed concurrent read/write (verify lock mechanism ensures data correctness)"""
    # Define test file path and task counts
    file_path = "test_concurrent_read_write.txt"
    write_task_count = 8
    read_task_count = 5
    write_content_template = "write_task_{}"

    # Initialize empty test file
    init_result = await sys_op.fs().write_file(
        path=str(file_path),
        content="",
        mode="text",
        append=False,
        prepend_newline=False,
        append_newline=False,
        create_if_not_exist=True,
        permissions="644"
    )
    assert init_result.code == StatusCode.SUCCESS.code, "Failed to initialize empty file"

    # Define concurrent write task with unique content
    async def write_task(task_id: int):
        content = write_content_template.format(task_id)
        result = await sys_op.fs().write_file(
            path=str(file_path),
            content=content,
            mode="text",
            append=True,
            prepend_newline=False,
            append_newline=True,
            create_if_not_exist=True,
            options={"lock_timeout": 30.0}
        )
        assert result.code == StatusCode.SUCCESS.code, f"Write task {task_id} failed"
        return task_id

    # Define concurrent read task with random delay
    async def read_task(task_id: int):
        # Random delay to simulate real concurrent scheduling
        await asyncio.sleep(random.uniform(0, 0.01))
        result = await sys_op.fs().read_file(
            path=str(file_path),
            mode="text",
            encoding="utf-8",
            options={"lock_timeout": 30.0}
        )
        assert result.code == StatusCode.SUCCESS.code, f"Read task {task_id} failed"

        # Validate read content is not corrupted
        lines = [line.strip() for line in result.data.content.split("\n") if line.strip()]
        for line in lines:
            assert line.startswith("write_task_"), f"Read task {task_id} got corrupted data: {line}"
        return len(lines)

    # Create read and write tasks
    write_tasks = [write_task(i) for i in range(write_task_count)]
    read_tasks = [read_task(i) for i in range(read_task_count)]
    mixed_tasks = write_tasks + read_tasks

    # Run mixed concurrent tasks
    await asyncio.gather(*mixed_tasks)

    # Final validation of complete file content
    final_read_result = await sys_op.fs().read_file(
        path=str(file_path),
        mode="text",
        encoding="utf-8"
    )
    assert final_read_result.code == StatusCode.SUCCESS.code, "Failed to read final content"

    # Verify no data loss after concurrent operations
    final_lines = [line.strip() for line in final_read_result.data.content.split("\n") if line.strip()]
    assert len(final_lines) == write_task_count, \
        f"Mixed read/write lost data: expected {write_task_count} lines, got {len(final_lines)} lines"

    # Ensure all write task records are complete
    written_task_ids = [int(line.replace("write_task_", "")) for line in final_lines]
    assert set(written_task_ids) == set(range(write_task_count)), \
        "Some write task content is missing in mixed read/write scenario"


@pytest.mark.asyncio
async def test_concurrent_upload_file(sys_op, work_dir):
    """Test concurrent file upload (verify lock and data integrity)"""
    # Prepare source file
    src_file = os.path.join(work_dir, "upload_source.txt")
    target_file = os.path.join(work_dir, "upload_target.txt")
    upload_content = "concurrent_upload_test_content"

    # Create source file
    await sys_op.fs().write_file(
        path=src_file,
        content=upload_content,
        mode="text",
        append=False,
        prepend_newline=False,
        create_if_not_exist=True
    )

    async def upload_task(task_id: int):
        """Single concurrent upload task"""
        result = await sys_op.fs().upload_file(
            local_path=src_file,
            target_path=target_file,
            overwrite=True,
            create_parent_dirs=True,
            preserve_permissions=True,
            options={"lock_timeout": 30.0}
        )
        assert result.code == StatusCode.SUCCESS.code, f"Upload task {task_id} failed"
        return result

    # Run 8 concurrent uploads
    upload_tasks = [upload_task(i) for i in range(8)]
    await asyncio.gather(*upload_tasks)

    # Verify final target content is correct
    final_result = await sys_op.fs().read_file(path=target_file, mode="text")
    assert final_result.data.content == upload_content, "Concurrent upload data corrupted"


@pytest.mark.asyncio
async def test_concurrent_download_file(sys_op, work_dir):
    """Test concurrent file download (verify data consistency)"""
    # Prepare source file
    src_file = os.path.join(work_dir, "download_source.txt")
    target_file = os.path.join(work_dir, "download_target.txt")
    download_content = "concurrent_download_test_content"

    # Create source file
    await sys_op.fs().write_file(
        path=src_file,
        content=download_content,
        mode="text",
        append=False,
        prepend_newline=False,
        create_if_not_exist=True
    )

    async def download_task(task_id: int):
        """Single concurrent download task"""
        result = await sys_op.fs().download_file(
            source_path=src_file,
            local_path=target_file,
            overwrite=True,
            create_parent_dirs=True,
            preserve_permissions=True,
            options={"lock_timeout": 30.0}
        )
        assert result.code == StatusCode.SUCCESS.code, f"Download task {task_id} failed"

        # Verify content not corrupted
        check = await sys_op.fs().read_file(path=target_file, mode="text")
        assert check.data.content == download_content, "Downloaded content corrupted"
        return result

    # Run 8 concurrent downloads
    download_tasks = [download_task(i) for i in range(8)]
    await asyncio.gather(*download_tasks)


@pytest.mark.asyncio
async def test_concurrent_upload_download_mixed(sys_op, work_dir):
    """Test mixed concurrent upload & download (verify lock ensures data safety)"""
    # Prepare files
    src_file = os.path.join(work_dir, "mixed_src.txt")
    dst_file = os.path.join(work_dir, "mixed_dst.txt")
    base_content = "initial_content"

    # Write initial source
    await sys_op.fs().write_file(
        path=src_file,
        content=base_content,
        mode="text",
        append=False,
        prepend_newline=False,
        create_if_not_exist=True
    )

    async def upload_task(task_id: int):
        """Upload task: overwrite source with new unique content"""
        new_content = f"upload_task_{task_id}"
        # Update source first
        await sys_op.fs().write_file(
            path=src_file,
            content=new_content,
            mode="text",
            append=False,
            prepend_newline=False
        )
        # Upload to destination
        result = await sys_op.fs().upload_file(
            local_path=src_file,
            target_path=dst_file,
            overwrite=True,
            options={"lock_timeout": 30.0}
        )
        assert result.code == StatusCode.SUCCESS.code, f"Upload task {task_id} failed"
        return task_id

    async def download_task(task_id: int):
        """Download task: read and validate content not broken"""
        await asyncio.sleep(random.uniform(0, 0.01))
        result = await sys_op.fs().download_file(
            source_path=src_file,
            local_path=dst_file,
            overwrite=True,
            options={"lock_timeout": 30.0}
        )
        assert result.code == StatusCode.SUCCESS.code, f"Download task {task_id} failed"

        # Ensure content is valid format (no corruption)
        check = await sys_op.fs().read_file(path=dst_file, mode="text")
        content = check.data.content.strip()
        assert content == "" or content.startswith("upload_task_") or content == base_content, \
            f"Download task {task_id} got invalid data: {content}"
        return result

    # Create mixed tasks
    upload_tasks = [upload_task(i) for i in range(6)]
    download_tasks = [download_task(i) for i in range(4)]
    mixed_tasks = upload_tasks + download_tasks

    await asyncio.gather(*mixed_tasks)

    # Final check: target file exists and content valid
    final_check = await sys_op.fs().read_file(path=dst_file, mode="text")
    final_content = final_check.data.content.strip()
    assert final_content != "", "Final content empty after mixed upload/download"
    assert final_content.startswith("upload_task_") or final_content == base_content, \
        "Final content corrupted after mixed concurrency"
