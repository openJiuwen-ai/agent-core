# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""CompiledGraph 取消路径的 checkpoint 保留语义。

取消(CancelledError)不得被 post_workflow_execute 误判为正常完成:
传 ({}, None) 会触发 checkpointer 的"完成即清除"分支,毁掉恢复现场,
导致后续 resume 只能从头重跑。取消必须走异常保存分支保留 checkpoint。
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest


def _compiled_graph_cls():
    # graph.py 存在模块级循环导入(graph->vertex->workflow 包->graph)。
    # 注意:函数内延迟导入只在进程里已有其他模块先完成 graph.py 导入时
    # 有效;本文件单独运行(本地调试/CI 分片/pytest 首个收集文件)时自己
    # 就是首个导入者,直接从 graph.graph 导入必失败(partially initialized)。
    # 先经 workflow 包入口完成初始化解开环,再导入 CompiledGraph。
    import openjiuwen.core.workflow  # noqa: F401
    from openjiuwen.core.graph.graph import CompiledGraph

    return CompiledGraph


def _fake_session():
    session = Mock()
    session.session_id.return_value = "S1"
    session.workflow_id.return_value = "W1"
    session.state.return_value = Mock()
    return session


@pytest.mark.asyncio
async def test_invoke_cancelled_routes_to_exception_save_branch():
    """取消时 post_workflow_execute 收到非 None exception,而非 ({}, None)。"""
    pregel = Mock()
    pregel.run = AsyncMock(side_effect=asyncio.CancelledError())
    checkpointer = AsyncMock()
    graph = _compiled_graph_cls()(pregel=pregel, checkpointer=checkpointer)

    with pytest.raises(asyncio.CancelledError):
        await graph._invoke({"query": "q"}, _fake_session())

    checkpointer.post_workflow_execute.assert_awaited_once()
    args = checkpointer.post_workflow_execute.await_args.args
    assert len(args) == 3
    assert isinstance(args[2], asyncio.CancelledError), (
        "取消必须以非 None exception 走保存分支,否则 checkpoint 被清除"
    )


@pytest.mark.asyncio
async def test_invoke_cancelled_propagates_original_cancel_despite_save_reraise():
    """checkpointer 保存分支 re-raise 传入异常时被吞,原始取消链继续传播。"""
    pregel = Mock()
    original = asyncio.CancelledError()
    pregel.run = AsyncMock(side_effect=original)
    checkpointer = AsyncMock()
    checkpointer.post_workflow_execute = AsyncMock(
        side_effect=asyncio.CancelledError("workflow exception save")
    )
    graph = _compiled_graph_cls()(pregel=pregel, checkpointer=checkpointer)

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await graph._invoke({"query": "q"}, _fake_session())

    assert excinfo.value is original
