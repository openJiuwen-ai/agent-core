# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real AIO sandbox integration tests.

These tests verify the complete integration with a running AIO sandbox service
at http://localhost:8080.

Requires:
- A running AIO sandbox service at http://localhost:8080
"""

import logging

import pytest
import pytest_asyncio

logger = logging.getLogger(__name__)


# ==================== Real Sandbox Integration Tests ====================

@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_real_aio_shell_execute_cmd(real_aio_op):
    """Test shell.execute_cmd against real AIO sandbox."""
    res = await real_aio_op.shell().execute_cmd(command="echo hello_real_sandbox")
    logger.info(f"shell.execute_cmd result: {res}")
    assert res.code == 0, f"Expected code=0, got {res.code}: {res.message}"
    assert "hello_real_sandbox" in res.data.stdout


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_real_aio_shell_execute_cmd_stream(real_aio_op):
    """Test shell.execute_cmd_stream against real AIO sandbox."""
    chunks = []
    async for chunk in real_aio_op.shell().execute_cmd_stream(command="echo stream_output"):
        logger.info(f"cmd_stream chunk #{chunk.data.chunk_index}: {chunk.data.text}")
        chunks.append(chunk)

    assert len(chunks) > 0
    assert chunks[-1].data.exit_code == 0


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_real_aio_fs_write_and_read_file(real_aio_op):
    """Test fs.write_file and fs.read_file against real AIO sandbox."""
    content = "hello from real sandbox"
    path = "/tmp/test_real_sandbox.txt"

    write_res = await real_aio_op.fs().write_file(path=path, content=content, prepend_newline=False)
    logger.info(f"write_file result: {write_res}")
    assert write_res.code == 0, f"write_file failed: {write_res.message}"

    read_res = await real_aio_op.fs().read_file(path=path)
    logger.info(f"read_file result: {read_res}")
    assert read_res.code == 0, f"read_file failed: {read_res.message}"
    assert read_res.data.content == content


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_real_aio_fs_list_files(real_aio_op):
    """Test fs.list_files against real AIO sandbox."""
    res = await real_aio_op.fs().list_files(path="/tmp")
    logger.info(f"list_files result: code={res.code}, total={res.data.total_count}")
    assert res.code == 0, f"list_files failed: {res.message}"


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_real_aio_fs_search_files(real_aio_op):
    """Test fs.search_files against real AIO sandbox."""
    # Create a file first
    test_path = "/tmp/test_search_real_xyz.txt"
    await real_aio_op.fs().write_file(path=test_path, content="searchable content", prepend_newline=False)

    res = await real_aio_op.fs().search_files(path="/tmp", pattern="*search_real_xyz*")
    logger.info(f"search_files result: code={res.code}, matches={res.data.total_matches}")
    assert res.code == 0
    assert res.data.total_matches >= 1


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_real_aio_code_execute(real_aio_op):
    """Test code.execute_code against real AIO sandbox."""
    code = 'print("hello_from_code")'
    res = await real_aio_op.code().execute_code(code=code, language="python")
    logger.info(f"execute_code result: {res}")
    assert res.code == 0, f"execute_code failed: {res.message}"
    assert "hello_from_code" in res.data.stdout
