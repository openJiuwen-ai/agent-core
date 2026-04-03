# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
import tempfile

import pytest

from openjiuwen.core.common.exception.codes import StatusCode


@pytest.mark.asyncio
async def test_fs_read_write(local_op):
    """Test text and binary read/write behavior in offline sandbox mode."""
    text_path = "test_basics.txt"
    content = "Hello, world!\nLine 2"

    write_res = await local_op.fs().write_file(path=text_path, content=content, prepend_newline=False)
    assert write_res.code == StatusCode.SUCCESS.code

    read_res = await local_op.fs().read_file(path=text_path)
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == content

    append_file = "test_append.txt"
    await local_op.fs().write_file(path=append_file, content="Appended", prepend_newline=True, append_newline=False)
    appended = await local_op.fs().read_file(path=append_file)
    assert appended.data.content == "\nAppended"

    bin_file = "test.bin"
    bin_data = b"\x00\x01\x02"
    await local_op.fs().write_file(path=bin_file, content=bin_data, mode="bytes")
    read_bin = await local_op.fs().read_file(path=bin_file, mode="bytes")
    assert read_bin.data.content == bin_data


@pytest.mark.asyncio
async def test_fs_read_head_tail_line_range(local_op):
    """Test line slicing for read_file and read_file_stream."""
    path = "multi_line.txt"
    content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    await local_op.fs().write_file(path, content, prepend_newline=False)

    head = await local_op.fs().read_file(path=path, head=3)
    assert head.data.content == "Line 1\nLine 2\nLine 3\n"

    tail = await local_op.fs().read_file(path=path, tail=2)
    assert tail.data.content == "Line 4\nLine 5"

    line_range = await local_op.fs().read_file(path=path, line_range=(2, 4))
    assert line_range.data.content == "Line 2\nLine 3\nLine 4\n"

    chunks = []
    async for chunk in local_op.fs().read_file_stream(path=path, line_range=(2, 4)):
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert "".join(text for text, _ in chunks) == "Line 2\nLine 3\nLine 4\n"
    assert chunks[-1][1] is True


@pytest.mark.asyncio
async def test_fs_read_file_mutually_exclusive_params(local_op):
    """Test mutually exclusive text slicing parameters."""
    path = "multi_line.txt"
    await local_op.fs().write_file(path, "line1\nline2\nline3", prepend_newline=False)

    res = await local_op.fs().read_file(path=path, head=2, tail=1)
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in res.message

    chunks = []
    async for chunk in local_op.fs().read_file_stream(path=path, head=-1, tail=2):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code


@pytest.mark.asyncio
async def test_fs_read_file_negative_zero_params(local_op):
    """Test negative and zero text slicing behavior."""
    path = "multi_line.txt"
    content = "line1\nline2\nline3\nline4\nline5"
    await local_op.fs().write_file(path, content, prepend_newline=False)

    assert (await local_op.fs().read_file(path=path, head=-5)).data.content == ""
    assert (await local_op.fs().read_file(path=path, tail=-5)).data.content == ""
    assert (await local_op.fs().read_file(path=path, head=0)).data.content == content
    assert (await local_op.fs().read_file(path=path, tail=0)).data.content == content
    assert (await local_op.fs().read_file(path=path, line_range=(0, 0))).data.content == ""
    assert (await local_op.fs().read_file(path=path, line_range=(1, -1))).data.content == ""

    chunks = []
    async for chunk in local_op.fs().read_file_stream(path=path, head=-5):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].data.chunk_content == ""


@pytest.mark.asyncio
async def test_fs_read_file_binary_mode_parameters(local_op):
    """Test text-only slicing parameters are rejected for binary mode."""
    path = "binary_test.txt"
    await local_op.fs().write_file(path, "Hello\nLine 2", prepend_newline=False)

    res = await local_op.fs().read_file(path=path, mode="bytes", head=2)
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in res.message

    chunks = []
    async for chunk in local_op.fs().read_file_stream(path=path, mode="bytes", line_range=(1, 2)):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code


@pytest.mark.asyncio
async def test_fs_security_and_streams(local_op):
    """Test path traversal denial and normal text streaming."""
    denied = await local_op.fs().read_file("../outside.txt")
    assert denied.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "Access denied" in denied.message or "outside sandbox root" in denied.message

    await local_op.fs().write_file("stream.txt", "line1\nline2", prepend_newline=False)
    chunks = []
    async for chunk in local_op.fs().read_file_stream("stream.txt"):
        chunks.append(chunk.data.chunk_content)
    assert "".join(chunks) == "line1\nline2"


@pytest.mark.asyncio
async def test_fs_upload_download(local_op):
    """Test upload/download behavior without relying on AIO service."""
    with tempfile.TemporaryDirectory() as temp_dir:
        local_source = os.path.join(temp_dir, "upload.txt")
        with open(local_source, "w", encoding="utf-8") as file:
            file.write("Hello, upload and download!")

        upload_res = await local_op.fs().upload_file(local_path=local_source, target_path="uploaded.txt")
        assert upload_res.code == StatusCode.SUCCESS.code

        local_target = os.path.join(temp_dir, "downloaded.txt")
        download_res = await local_op.fs().download_file(source_path="uploaded.txt", local_path=local_target)
        assert download_res.code == StatusCode.SUCCESS.code

        with open(local_target, "r", encoding="utf-8") as file:
            assert file.read() == "Hello, upload and download!"

        stream_upload = []
        async for chunk in local_op.fs().upload_file_stream(local_path=local_source, target_path="stream_uploaded.txt"):
            stream_upload.append(chunk)
        assert len(stream_upload) == 1

        stream_target = os.path.join(temp_dir, "stream_downloaded.txt")
        stream_download = []
        async for chunk in local_op.fs().download_file_stream(source_path="stream_uploaded.txt",
                                                              local_path=stream_target):
            stream_download.append(chunk)
        assert len(stream_download) == 1


@pytest.mark.asyncio
async def test_fs_list_operations(local_op):
    """Test list_files and list_directories behavior."""
    await local_op.fs().write_file("file1.txt", "Content 1", prepend_newline=False)
    await local_op.fs().write_file("dir1/file2.txt", "Content 2", prepend_newline=False)
    await local_op.fs().write_file("dir1/subdir1/file3.txt", "Content 3", prepend_newline=False)
    await local_op.fs().write_file("dir2/file4.txt", "Content 4", prepend_newline=False)

    list_res = await local_op.fs().list_files(".")
    assert list_res.code == StatusCode.SUCCESS.code
    assert "file1.txt" in {item.name for item in list_res.data.list_items}

    recursive_res = await local_op.fs().list_files(".", recursive=True)
    assert recursive_res.code == StatusCode.SUCCESS.code
    assert {item.name for item in recursive_res.data.list_items} >= {"file1.txt", "file2.txt", "file3.txt", "file4.txt"}

    txt_res = await local_op.fs().list_files(".", recursive=True, file_types=[".txt"])
    assert txt_res.code == StatusCode.SUCCESS.code
    assert len(txt_res.data.list_items) >= 4

    dirs_res = await local_op.fs().list_directories(".", recursive=True)
    assert dirs_res.code == StatusCode.SUCCESS.code
    assert {item.name for item in dirs_res.data.list_items} >= {"dir1", "dir2", "subdir1"}


@pytest.mark.asyncio
async def test_fs_search_operations(local_op):
    """Test search_files pattern and exclude behavior."""
    await local_op.fs().write_file("test1.txt", "Content 1", prepend_newline=False)
    await local_op.fs().write_file("test2.txt", "Content 2", prepend_newline=False)
    await local_op.fs().write_file("data1.csv", "CSV content", prepend_newline=False)
    await local_op.fs().write_file("subdir/test3.txt", "Content 3", prepend_newline=False)

    txt_res = await local_op.fs().search_files(".", "*.txt")
    assert txt_res.code == StatusCode.SUCCESS.code
    assert {item.name for item in txt_res.data.matching_files} >= {"test1.txt", "test2.txt", "test3.txt"}

    exclude_res = await local_op.fs().search_files(".", "*", exclude_patterns=["*.csv"])
    assert exclude_res.code == StatusCode.SUCCESS.code
    assert all(not item.name.endswith(".csv") for item in exclude_res.data.matching_files)

    no_match = await local_op.fs().search_files(".", "*.xyz")
    assert no_match.code == StatusCode.SUCCESS.code
    assert no_match.data.total_matches == 0
