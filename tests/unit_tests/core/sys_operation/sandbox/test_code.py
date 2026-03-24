# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import logging

import pytest

from openjiuwen.core.sys_operation import OperationMode

logger = logging.getLogger(__name__)


# ==================== Code Tests ====================

@pytest.mark.asyncio
async def test_execute_code(local_op):
    """Execute Python code and verify stdout."""
    res = await local_op.code().execute_code(code='print("hello_code")', language="python")
    logger.info(f"execute_code result: {res}")
    assert res.code == 0
    assert "hello_code" in res.data.stdout


@pytest.mark.asyncio
async def test_execute_code_stream(local_op):
    """Stream code execution output, verify chunks."""
    chunks = []
    async for chunk in local_op.code().execute_code_stream(
            code='print("line1")\nprint("line2")', language="python"
    ):
        logger.info(f"code_stream chunk #{chunk.data.chunk_index}, type={chunk.data.type}, text={chunk.data.text!r}")
        assert chunk.code == 0
        chunks.append(chunk)

    assert len(chunks) > 0
    full_text = "".join(c.data.text for c in chunks)
    assert "line1" in full_text
    assert "line2" in full_text
    assert chunks[-1].data.exit_code is not None


# ==================== Discovery Test ====================

def test_sandbox_discovery():
    """Test that Sandbox operations are correctly discovered."""
    from openjiuwen.core.sys_operation.registry import OperationRegistry

    fs_op = OperationRegistry.get_operation_info("fs", OperationMode.SANDBOX)
    assert fs_op is not None
    assert fs_op.name == "fs"
    assert fs_op.mode == OperationMode.SANDBOX

    shell_op = OperationRegistry.get_operation_info("shell", OperationMode.SANDBOX)
    assert shell_op is not None

    code_op = OperationRegistry.get_operation_info("code", OperationMode.SANDBOX)
    assert code_op is not None
