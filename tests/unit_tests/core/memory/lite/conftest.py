# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.memory.lite.coding_memory_tool_context import CodingMemoryToolContext
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness.workspace.workspace import Workspace


@pytest.fixture
def temp_dir():
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest_asyncio.fixture
async def coding_memory_ctx(temp_dir):
    await Runner.start()
    card_id = "test_coding_memory_setup"
    Runner.resource_mgr.add_sys_operation(
        SysOperationCard(
            id=card_id,
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(
                shell_allowlist=["echo", "ls", "dir", "cd", "pwd", "python", "python3", "cat", "mkdir"],
            ),
        )
    )
    sys_op = Runner.resource_mgr.get_sys_operation(card_id)

    coding_memory_dir = os.path.join(temp_dir, "coding_memory")
    os.makedirs(coding_memory_dir, exist_ok=True)
    workspace = Workspace(
        root_path=temp_dir,
        directories=[{"name": "coding_memory", "path": "coding_memory"}],
    )
    ctx = CodingMemoryToolContext(
        workspace=workspace,
        sys_operation=sys_op,
        coding_memory_dir=coding_memory_dir,
        node_name="coding_memory",
    )
    try:
        yield ctx, sys_op, coding_memory_dir
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()
