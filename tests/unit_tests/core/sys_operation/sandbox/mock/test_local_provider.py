# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Tests for local providers (sandbox_type="local").

These tests verify the SandboxRegistry provider registration and routing mechanism
without a running AIO sandbox service.
"""

import logging

import pytest

from openjiuwen.core.common.exception.codes import StatusCode

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_local_fs_read_file(local_op):
    """Test fs.read_file through the local provider."""
    await local_op.fs().write_file(path="test.txt", content="hello local fs", prepend_newline=False)

    res = await local_op.fs().read_file(path="test.txt")
    logger.info("local read_file result: %s", res)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "hello local fs"


@pytest.mark.asyncio
async def test_local_fs_write_file(local_op):
    """Test fs.write_file through the local provider."""
    res = await local_op.fs().write_file(path="write_test.txt", content="hello", prepend_newline=False)
    logger.info("local write_file result: %s", res)
    assert res.code == StatusCode.SUCCESS.code

    read_res = await local_op.fs().read_file(path="write_test.txt")
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == "hello"


@pytest.mark.asyncio
async def test_local_fs_read_file_stream(local_op):
    """Test fs.read_file_stream through the local provider."""
    await local_op.fs().write_file(path="stream_test.txt", content="line1\nline2", prepend_newline=False)

    chunks = []
    async for chunk in local_op.fs().read_file_stream(path="stream_test.txt", chunk_size=16):
        logger.info("local read_file_stream chunk #%s", chunk.data.chunk_index)
        chunks.append(chunk)

    assert len(chunks) == 2
    assert "".join(chunk.data.chunk_content for chunk in chunks) == "line1\nline2"
    assert chunks[-1].data.is_last_chunk is True


@pytest.mark.asyncio
async def test_local_fs_list_files(local_op):
    """Test fs.list_files through the local provider."""
    await local_op.fs().write_file(path="file1.txt", content="1", prepend_newline=False)
    await local_op.fs().write_file(path="dir1/file2.txt", content="2", prepend_newline=False)

    res = await local_op.fs().list_files(path=".", recursive=True)
    logger.info("local list_files result: code=%s, total=%s", res.code, res.data.total_count)
    assert res.code == StatusCode.SUCCESS.code
    names = {item.name for item in res.data.list_items}
    assert {"file1.txt", "file2.txt"}.issubset(names)


@pytest.mark.asyncio
async def test_local_fs_list_directories(local_op):
    """Test fs.list_directories through the local provider."""
    await local_op.fs().write_file(path="dir1/file.txt", content="1", prepend_newline=False)
    await local_op.fs().write_file(path="dir1/subdir/file2.txt", content="2", prepend_newline=False)

    res = await local_op.fs().list_directories(path=".", recursive=True)
    logger.info("local list_directories result: code=%s, total=%s", res.code, res.data.total_count)
    assert res.code == StatusCode.SUCCESS.code
    names = {item.name for item in res.data.list_items}
    assert {"dir1", "subdir"}.issubset(names)
    assert all(item.is_directory for item in res.data.list_items)


@pytest.mark.asyncio
async def test_local_fs_search_files(local_op):
    """Test fs.search_files through the local provider."""
    await local_op.fs().write_file(path="matched.txt", content="match", prepend_newline=False)
    await local_op.fs().write_file(path="nested/other.txt", content="nested", prepend_newline=False)
    await local_op.fs().write_file(path="ignored.csv", content="csv", prepend_newline=False)

    res = await local_op.fs().search_files(path=".", pattern="*.txt")
    logger.info("local search_files result: code=%s, matches=%s", res.code, res.data.total_matches)
    assert res.code == StatusCode.SUCCESS.code
    names = {item.name for item in res.data.matching_files}
    assert {"matched.txt", "other.txt"}.issubset(names)


@pytest.mark.asyncio
async def test_local_fs_upload_file(local_op, tmp_path):
    """Test fs.upload_file through the local provider."""
    local_file = tmp_path / "upload.txt"
    local_file.write_text("upload content", encoding="utf-8")

    res = await local_op.fs().upload_file(local_path=str(local_file), target_path="uploaded.txt")
    logger.info("local upload_file result: %s", res)
    assert res.code == StatusCode.SUCCESS.code

    read_res = await local_op.fs().read_file("uploaded.txt")
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == "upload content"


@pytest.mark.asyncio
async def test_local_fs_download_file_stream(local_op, tmp_path):
    """Test fs.download_file_stream through the local provider."""
    await local_op.fs().write_file("source.txt", "download me", prepend_newline=False)

    local_dst = tmp_path / "dl_stream.txt"
    chunks = []
    async for chunk in local_op.fs().download_file_stream(source_path="source.txt",
                                                          local_path=str(local_dst), chunk_size=16):
        logger.info("local dl_stream chunk #%s, is_last=%s",
                    chunk.data.chunk_index, chunk.data.is_last_chunk)
        chunks.append(chunk)

    assert len(chunks) > 0
    assert chunks[-1].data.is_last_chunk is True
    assert local_dst.read_text(encoding="utf-8") == "download me"


@pytest.mark.asyncio
async def test_local_shell_execute_cmd(local_op):
    """Test shell.execute_cmd through the local provider."""
    res = await local_op.shell().execute_cmd(command="echo hello")
    logger.info("local shell result: %s", res)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert res.data.stdout == "hello\n"


@pytest.mark.asyncio
async def test_local_shell_execute_cmd_stream(local_op):
    """Test shell.execute_cmd_stream through the local provider."""
    chunks = []
    async for chunk in local_op.shell().execute_cmd_stream(command="echo stream"):
        logger.info("local cmd_stream chunk #%s: %s", chunk.data.chunk_index, chunk.data.text)
        chunks.append(chunk)

    assert len(chunks) > 0
    assert "stream" in "".join(chunk.data.text for chunk in chunks if chunk.data.text)
    assert chunks[-1].data.exit_code == 0


@pytest.mark.asyncio
async def test_local_code_execute(local_op):
    """Test code.execute_code through the local provider."""
    res = await local_op.code().execute_code(code='print("hello_local")')
    logger.info("local code result: %s", res)
    assert res.code == StatusCode.SUCCESS.code
    assert "hello_local" in res.data.stdout


@pytest.mark.asyncio
async def test_local_code_execute_stream(local_op):
    """Test code.execute_code_stream through the local provider."""
    chunks = []
    async for chunk in local_op.code().execute_code_stream(code='print("line1")\nprint("line2")'):
        logger.info("local code_stream chunk #%s: %s", chunk.data.chunk_index, chunk.data.text)
        chunks.append(chunk)

    assert len(chunks) >= 1
    assert "line1" in "".join(chunk.data.text for chunk in chunks if chunk.data.text)
    assert "line2" in "".join(chunk.data.text for chunk in chunks if chunk.data.text)
    assert chunks[-1].data.exit_code == 0


@pytest.mark.asyncio
async def test_local_sandbox_discovery():
    """Test that local providers are correctly registered in SandboxRegistry."""
    from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry

    fs_cls = SandboxRegistry.get_provider_cls("local", "fs")
    assert fs_cls is not None
    assert fs_cls.__name__ == "LocalFSProvider"

    shell_cls = SandboxRegistry.get_provider_cls("local", "shell")
    assert shell_cls is not None
    assert shell_cls.__name__ == "LocalShellProvider"

    code_cls = SandboxRegistry.get_provider_cls("local", "code")
    assert code_cls is not None
    assert code_cls.__name__ == "LocalCodeProvider"


@pytest.mark.asyncio
async def test_local_and_aio_providers_coexist():
    """Verify that 'local' and 'aio' providers can coexist in SandboxRegistry."""
    from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry

    local_fs = SandboxRegistry.get_provider_cls("local", "fs")
    aio_fs = SandboxRegistry.get_provider_cls("aio", "fs")

    assert local_fs is not aio_fs
    assert local_fs.__name__ == "LocalFSProvider"
    assert aio_fs.__name__ in ("AIOFSProvider", "MockFSProvider")
