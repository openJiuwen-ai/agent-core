# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import List

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.sys_operation.base import BaseOperation
from openjiuwen.core.sys_operation.registry import OperationRegistry, operation
from tests.unit_tests.core.sys_operation.local.custom_operation import LocalCalculatorOperation


@pytest_asyncio.fixture(name="calc_card")
def calculator_card_fixture():
    """Create a SysOperationCard for calculator operations."""
    OperationRegistry.register(LocalCalculatorOperation)

    card_id = "test_calculator_op"
    card = SysOperationCard(
        id=card_id,
        mode=OperationMode.LOCAL
    )
    yield card


@pytest_asyncio.fixture(name="calc_sys_op")
async def calculator_sys_op_fixture(calc_card):
    """Create and register calculator SysOperation."""
    await Runner.start()
    try:
        card_id = calc_card.id
        add_res = Runner.resource_mgr.add_sys_operation(calc_card)
        assert add_res.is_ok()
        op = Runner.resource_mgr.get_sys_operation(card_id)
        yield op
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()


@pytest.mark.asyncio
async def test_custom_calculator_list_tools(calc_card, calc_sys_op):
    """Test that calculator operation exposes correct tools."""
    calc_op = calc_sys_op.calculator()
    tools: List[ToolCard] = calc_op.list_tools()

    # Convert to dict for easier lookup
    tools_dict = {tool.name: tool for tool in tools}

    # Verify all expected tools are present
    assert len(tools) == 1
    expected_tools = ["add"]
    for tool_name in expected_tools:
        assert tool_name in tools_dict

    # Verify tool schemas
    add_tool = tools_dict["add"]
    assert "a" in add_tool.input_params["properties"]
    assert "b" in add_tool.input_params["properties"]
    assert add_tool.input_params["required"] == ["a", "b"]


@pytest.mark.asyncio
async def test_custom_calculator_direct_invocation(calc_card, calc_sys_op):
    """Test direct invocation of calculator operations."""
    calc_op = calc_sys_op.calculator()

    # Test addition
    result = await calc_op.add(10, 5)
    assert result == 15

    # Test addition with different values
    result = await calc_op.add(20, 8)
    assert result == 28


@pytest.mark.asyncio
async def test_custom_calculator_tool_invocation(calc_card, calc_sys_op):
    """Test calculator operations through ResourceMgr tool interface."""
    rm = Runner.resource_mgr

    # Get calculator tools via card proxy
    add_tool_id = calc_card.calculator.add
    assert add_tool_id == f"{calc_card.id}.calculator.add"

    add_tool = rm.get_tool(add_tool_id)
    assert add_tool is not None
    assert add_tool.card.name == "add"

    # Test add tool invocation
    res = await add_tool.invoke({"a": 100, "b": 50})
    assert res == 150


@pytest.mark.asyncio
async def test_multi_mode_fs_coexistence():
    """Test that built-in FS for local and sandbox modes can coexist."""
    # The registry auto-initializes once accessed
    local_fs = OperationRegistry.get_operation_info("fs", OperationMode.LOCAL)
    sandbox_fs = OperationRegistry.get_operation_info("fs", OperationMode.SANDBOX)

    assert local_fs is not None
    assert sandbox_fs is not None

    # They should be different implementations (or at least both present)
    assert local_fs.mode == OperationMode.LOCAL
    assert sandbox_fs.mode == OperationMode.SANDBOX

    # Verify we can get them from the same registry
    supported_local = OperationRegistry.get_supported_operations(OperationMode.LOCAL)
    supported_sandbox = OperationRegistry.get_supported_operations(OperationMode.SANDBOX)

    assert "fs" in supported_local
    assert "fs" in supported_sandbox
