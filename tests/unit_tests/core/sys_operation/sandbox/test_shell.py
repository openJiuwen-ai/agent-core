# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import logging

import pytest

logger = logging.getLogger(__name__)


# ==================== Shell Tests ====================

@pytest.mark.asyncio
async def test_execute_cmd(local_op):
    """Basic shell command execution."""
    res = await local_op.shell().execute_cmd(command="echo hello_shell")
    logger.info(f"execute_cmd result: {res}")
    assert res.code == 0
    assert "hello_shell" in res.data.stdout


@pytest.mark.asyncio
async def test_execute_cmd_stream(local_op):
    """Stream shell command output, verify chunks and exit_code."""
    chunks = []
    async for chunk in local_op.shell().execute_cmd_stream(command="echo hello_stream"):
        logger.info(f"cmd_stream chunk #{chunk.data.chunk_index}, type={chunk.data.type}, text={chunk.data.text!r}")
        assert chunk.code == 0
        chunks.append(chunk)

    assert len(chunks) > 0
    full_text = "".join(c.data.text for c in chunks)
    assert "hello_stream" in full_text
    # Last chunk should carry exit_code
    assert chunks[-1].data.exit_code is not None
