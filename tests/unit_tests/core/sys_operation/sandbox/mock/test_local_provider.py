# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Tests for local providers (sandbox_type="local").

These tests verify the SandboxRegistry provider registration and routing mechanism
WITHOUT:
- AIO sandbox service at localhost:8080
- Writing MockProvider classes that inherit from BaseProvider

The local providers return simple hardcoded values but go through the same
SandboxGateway routing path as real AIO providers.
"""

import logging

import pytest

logger = logging.getLogger(__name__)


# ==================== Local Provider Tests ====================

@pytest.mark.asyncio
async def test_local_fs_read_file(local_op):
    """Test fs.read_file through local provider."""
    res = await local_op.fs().read_file(path="/tmp/test.txt")
    logger.info(f"local read_file result: {res}")
    assert res.code == 0
    assert "local_read_content_for_/tmp/test.txt" in res.data.content


@pytest.mark.asyncio
async def test_local_fs_write_file(local_op):
    """Test fs.write_file through local provider."""
    res = await local_op.fs().write_file(path="/tmp/test.txt", content="hello")
    logger.info(f"local write_file result: {res}")
    assert res.code == 0


@pytest.mark.asyncio
async def test_local_fs_read_file_stream(local_op):
    """Test fs.read_file_stream through local provider."""
    chunks = []
    async for chunk in local_op.fs().read_file_stream(path="/tmp/test.txt", chunk_size=16):
        logger.info(f"local read_file_stream chunk #{chunk.data.chunk_index}")
        chunks.append(chunk)
    assert len(chunks) > 0
    assert chunks[-1].data.is_last_chunk is True


@pytest.mark.asyncio
async def test_local_fs_list_files(local_op):
    """Test fs.list_files through local provider."""
    res = await local_op.fs().list_files(path="/tmp")
    logger.info(f"local list_files result: code={res.code}, total={res.data.total_count}")
    assert res.code == 0
    assert res.data.total_count == 2


@pytest.mark.asyncio
async def test_local_fs_list_directories(local_op):
    """Test fs.list_directories through local provider."""
    res = await local_op.fs().list_directories(path="/tmp")
    logger.info(f"local list_directories result: code={res.code}, total={res.data.total_count}")
    assert res.code == 0
    assert res.data.total_count == 1
    assert res.data.list_items[0].is_directory is True


@pytest.mark.asyncio
async def test_local_fs_search_files(local_op):
    """Test fs.search_files through local provider."""
    res = await local_op.fs().search_files(path="/tmp", pattern="*.txt")
    logger.info(f"local search_files result: code={res.code}, matches={res.data.total_matches}")
    assert res.code == 0
    assert res.data.total_matches == 1


@pytest.mark.asyncio
async def test_local_fs_upload_file(local_op, tmp_path):
    """Test fs.upload_file through local provider."""
    local_file = tmp_path / "upload.txt"
    local_file.write_text("upload content")

    res = await local_op.fs().upload_file(
        local_path=str(local_file),
        target_path="/tmp/uploaded.txt"
    )
    logger.info(f"local upload_file result: {res}")
    assert res.code == 0


@pytest.mark.asyncio
async def test_local_fs_download_file_stream(local_op, tmp_path):
    """Test fs.download_file_stream through local provider."""
    local_dst = tmp_path / "dl_stream.txt"
    chunks = []
    async for chunk in local_op.fs().download_file_stream(
            source_path="/tmp/source.txt", local_path=str(local_dst), chunk_size=16
    ):
        logger.info(f"local dl_stream chunk #{chunk.data.chunk_index}, is_last={chunk.data.is_last_chunk}")
        chunks.append(chunk)

    assert len(chunks) > 0
    assert chunks[-1].data.is_last_chunk is True


@pytest.mark.asyncio
async def test_local_shell_execute_cmd(local_op):
    """Test shell.execute_cmd through local provider."""
    res = await local_op.shell().execute_cmd(command="echo hello")
    logger.info(f"local shell result: {res}")
    assert res.code == 0
    assert "local_shell_output_for: echo hello" in res.data.stdout


@pytest.mark.asyncio
async def test_local_shell_execute_cmd_stream(local_op):
    """Test shell.execute_cmd_stream through local provider."""
    chunks = []
    async for chunk in local_op.shell().execute_cmd_stream(command="echo stream"):
        logger.info(f"local cmd_stream chunk #{chunk.data.chunk_index}: {chunk.data.text}")
        chunks.append(chunk)

    assert len(chunks) > 0
    assert chunks[-1].data.exit_code == 0


@pytest.mark.asyncio
async def test_local_code_execute(local_op):
    """Test code.execute_code through local provider."""
    res = await local_op.code().execute_code(code='print("hello_local")')
    logger.info(f"local code result: {res}")
    assert res.code == 0
    assert "hello_local" in res.data.stdout


@pytest.mark.asyncio
async def test_local_code_execute_stream(local_op):
    """Test code.execute_code_stream through local provider."""
    chunks = []
    async for chunk in local_op.code().execute_code_stream(code='print("line1")\nprint("line2")'):
        logger.info(f"local code_stream chunk #{chunk.data.chunk_index}: {chunk.data.text}")
        chunks.append(chunk)

    assert len(chunks) >= 1
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

    # They should be different classes
    assert local_fs is not aio_fs
    assert local_fs.__name__ == "LocalFSProvider"
    assert aio_fs.__name__ in ("AIOFSProvider", "MockFSProvider")  # Could be real or mock depending on conftest
