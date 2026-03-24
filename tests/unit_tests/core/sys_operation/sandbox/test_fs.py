# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import base64
import logging
import os
import tempfile
from typing import AsyncIterator

import pytest
import pytest_asyncio

from openjiuwen.core.sys_operation.result import (
    ReadFileStreamResult, ReadFileChunkData,
    DownloadFileStreamResult, DownloadFileChunkData,
)

logger = logging.getLogger(__name__)


# ==================== FS Tests ====================

@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_write_and_read_file(real_aio_op):
    """Write a file then read it back, verify content matches."""
    content = "hello aio sandbox test"
    path = "/tmp/test_aio_write_read.txt"
    write_res = await real_aio_op.fs().write_file(path=path, content=content)
    logger.info(f"write_file result: {write_res}")
    assert write_res.code == 0
    read_res = await real_aio_op.fs().read_file(path=path)
    logger.info(f"read_file result: {read_res}")
    assert read_res.code == 0
    assert read_res.data.content == content


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_write_file_bytes(real_aio_op):
    """Write bytes content to verify the closure bug is fixed."""
    content = b"bytes content test"
    path = "/tmp/test_aio_bytes.txt"
    write_res = await real_aio_op.fs().write_file(path=path, content=content)
    logger.info(f"write_file bytes result: {write_res}")
    assert write_res.code == 0

    read_res = await real_aio_op.fs().read_file(path=path)
    assert read_res.code == 0
    assert read_res.data.content == content.decode("utf-8")


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_list_files(real_aio_op):
    """List files in /tmp, verify all items are files (not directories)."""
    # Ensure at least one file exists
    await real_aio_op.fs().write_file(path="/tmp/test_list.txt", content="x")
    res = await real_aio_op.fs().list_files(path="/tmp")
    logger.info(f"list_files result: code={res.code}, total={res.data.total_count}")
    assert res.code == 0
    assert res.data.total_count > 0
    for item in res.data.list_items:
        assert item.is_directory is False


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_list_files_recursive(real_aio_op):
    """List files recursively and verify the recursive flag."""
    res = await real_aio_op.fs().list_files(path="/tmp", recursive=True)
    logger.info(f"list_files recursive: code={res.code}, total={res.data.total_count}")
    assert res.code == 0
    assert res.data.recursive is True


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_list_directories(real_aio_op):
    """List directories in /tmp, verify all items are directories."""
    # First create a nested directory structure under /tmp
    await real_aio_op.fs().write_file(path="/tmp/test_list_dirs/subdir/file.txt", content="x", prepend_newline=False)
    res = await real_aio_op.fs().list_directories(path="/tmp")
    logger.info(f"list_directories result: code={res.code}, total={res.data.total_count}")
    assert res.code == 0
    assert res.data.total_count > 0
    for item in res.data.list_items:
        assert item.is_directory is True


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_read_file_stream(real_aio_op):
    """Stream-read a file, reassemble chunks and compare with read_file."""
    path = "/tmp/test_stream.txt"
    content = "line1\nline2\nline3"
    await real_aio_op.fs().write_file(path=path, content=content)
    read_res = await real_aio_op.fs().read_file(path=path)
    expected = read_res.data.content

    chunks = []
    async for chunk in real_aio_op.fs().read_file_stream(path=path, chunk_size=64):
        logger.info(
            f"chunk #{chunk.data.chunk_index}, is_last={chunk.data.is_last_chunk}, len={len(chunk.data.chunk_content)}")
        assert chunk.code == 0
        chunks.append(chunk)

    assert len(chunks) > 0
    # Verify chunk_index is sequential
    for i, c in enumerate(chunks):
        assert c.data.chunk_index == i
    assert chunks[-1].data.is_last_chunk is True
    reassembled = "".join(c.data.chunk_content for c in chunks)
    assert reassembled == expected


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_search_files(real_aio_op):
    """Create a file then search for it by glob pattern."""
    path = "/tmp/test_search_unique_xyz.txt"
    await real_aio_op.fs().write_file(path=path, content="searchable")

    res = await real_aio_op.fs().search_files(path="/tmp", pattern="*search_unique_xyz*")
    logger.info(f"search_files result: code={res.code}, matches={res.data.total_matches}")
    assert res.code == 0
    assert res.data.total_matches >= 1
    found_paths = [f.path for f in res.data.matching_files]
    assert path in found_paths


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_upload_and_download_file(real_aio_op, tmp_path):
    """Upload a local file to sandbox, then download it back and verify."""
    # Create local file
    local_src = tmp_path / "upload_src.txt"
    local_src.write_text("upload download roundtrip test", encoding="utf-8")

    sandbox_path = "/tmp/test_uploaded.txt"
    up_res = await real_aio_op.fs().upload_file(
        local_path=str(local_src), target_path=sandbox_path
    )
    logger.info(f"upload_file result: {up_res}")
    assert up_res.code == 0
    assert up_res.data.size > 0

    # Download back
    local_dst = tmp_path / "download_dst.txt"
    dl_res = await real_aio_op.fs().download_file(
        source_path=sandbox_path, local_path=str(local_dst), overwrite=True
    )
    logger.info(f"download_file result: {dl_res}")
    assert dl_res.code == 0
    assert local_dst.read_text(encoding="utf-8") == "upload download roundtrip test"


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_download_file_stream(real_aio_op, tmp_path):
    """Stream-download a file from sandbox and verify local content."""
    # Ensure file exists in sandbox
    sandbox_path = "/tmp/test_dl_stream.txt"
    content = "stream download content line1\nline2\nline3"
    await real_aio_op.fs().write_file(path=sandbox_path, content=content)

    local_dst = tmp_path / "dl_stream.txt"
    chunks = []
    async for chunk in real_aio_op.fs().download_file_stream(
            source_path=sandbox_path, local_path=str(local_dst), overwrite=True, chunk_size=16
    ):
        logger.info(f"dl_stream chunk #{chunk.data.chunk_index}, is_last={chunk.data.is_last_chunk}")
        assert chunk.code == 0
        chunks.append(chunk)

    assert len(chunks) > 0
    assert chunks[-1].data.is_last_chunk is True
    assert local_dst.read_text(encoding="utf-8") == content


# ==================== Binary File Tests ====================

# Minimal valid 1x1 red PNG (base64 encoded)
_MINIMAL_PNG_1X1_RED = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running AIO sandbox")
async def test_read_write_binary_image(real_aio_op, tmp_path):
    """Read binary image data, write it back, and verify data consistency.

    This test validates that the AIO sandbox correctly handles binary file operations:
    1. Creates a minimal PNG image
    2. Writes it to sandbox in binary mode
    3. Reads it back in binary mode
    4. Downloads to local filesystem
    5. Verifies the binary content matches the original
    """
    sandbox_path = "/tmp/test_image.png"

    # Step 1: Write binary image to sandbox
    write_res = await real_aio_op.fs().write_file(
        path=sandbox_path,
        content=_MINIMAL_PNG_1X1_RED,
        mode="bytes"
    )
    logger.info(f"write_file binary result: {write_res}")
    assert write_res.code == 0, f"write_file binary failed: {write_res.message}"
    assert write_res.data.mode == "bytes"

    # Step 2: Download to verify binary content
    local_verify = tmp_path / "verify_image.png"
    dl_res = await real_aio_op.fs().download_file(
        source_path=sandbox_path,
        local_path=str(local_verify),
        overwrite=True
    )
    assert dl_res.code == 0, f"download_file failed: {dl_res.message}"
    verify_content = local_verify.read_bytes()
    assert verify_content == _MINIMAL_PNG_1X1_RED, "Downloaded binary content does not match original"

    # Step 3: Download the file to local filesystem
    local_dst = tmp_path / "downloaded_image.png"
    dl_res = await real_aio_op.fs().download_file(
        source_path=sandbox_path,
        local_path=str(local_dst),
        overwrite=True
    )
    logger.info(f"download_file result: {dl_res}")
    assert dl_res.code == 0, f"download_file failed: {dl_res.message}"

    # Step 4: Verify downloaded content matches original
    downloaded_content = local_dst.read_bytes()
    assert downloaded_content == _MINIMAL_PNG_1X1_RED, "Downloaded binary content does not match original"

