# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""CompiledGraph 取消路径的 checkpoint 保留语义。

取消(CancelledError)不得被 post_workflow_execute 误判为正常完成:
传 ({}, None) 会触发 checkpointer 的"完成即清除"分支,毁掉恢复现场,
导致后续 resume 只能从头重跑。同时按图侧快照分流(2026-10-26 检视评审):
图侧已有可续跑快照(HITL 暂停后取消/恢复运行中再取消)才走异常保存分支
保留现场;全新执行的中途取消(图侧无快照)不触发 io 侧落盘——此前无条件
走保存分支会写 io 侧孤儿快照,使 session 既不能 InteractiveInput resume
(图侧校验拒绝)也不能普通输入重跑(workflow state exists 拒绝)。
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


def _resumable_graph_state():
    # channel_values 非空即满足 GraphState.is_resumable 的可续跑口径。
    from openjiuwen.core.graph.store import create_state

    return create_state(ns="W1", step=1, channel_snapshot={"ch": "v"})


def _mock_graph_store(checkpointer, graph_state):
    graph_store = Mock()
    graph_store.get = AsyncMock(return_value=graph_state)
    checkpointer.graph_store = Mock(return_value=graph_store)
    return graph_store


@pytest.mark.asyncio
async def test_invoke_cancelled_with_resumable_snapshot_routes_to_exception_save_branch():
    """图侧有可续跑快照(HITL 暂停后取消/恢复中再取消)时,post_workflow_execute 收到非 None exception。"""
    pregel = Mock()
    pregel.run = AsyncMock(side_effect=asyncio.CancelledError())
    checkpointer = AsyncMock()
    graph_store = _mock_graph_store(checkpointer, _resumable_graph_state())
    graph = _compiled_graph_cls()(pregel=pregel, checkpointer=checkpointer)

    with pytest.raises(asyncio.CancelledError):
        await graph._invoke({"query": "q"}, _fake_session())

    graph_store.get.assert_awaited_once_with("S1", "W1")
    checkpointer.post_workflow_execute.assert_awaited_once()
    args = checkpointer.post_workflow_execute.await_args.args
    assert len(args) == 3
    assert isinstance(args[2], asyncio.CancelledError), (
        "可续跑现场的取消必须以非 None exception 走保存分支,否则 checkpoint 被清除"
    )


@pytest.mark.asyncio
async def test_invoke_cancelled_without_graph_snapshot_keeps_rerunnable():
    """全新执行的中途取消(图侧无快照)不触发 io 侧落盘,保留可重跑语义。"""
    pregel = Mock()
    pregel.run = AsyncMock(side_effect=asyncio.CancelledError())
    checkpointer = AsyncMock()
    _mock_graph_store(checkpointer, None)
    graph = _compiled_graph_cls()(pregel=pregel, checkpointer=checkpointer)

    with pytest.raises(asyncio.CancelledError):
        await graph._invoke({"query": "q"}, _fake_session())

    checkpointer.post_workflow_execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoke_cancelled_with_unresumable_snapshot_skips_save():
    """图侧快照存在但不可续跑(pending/channel 全空)时同样不落盘。"""
    from openjiuwen.core.graph.store import create_state

    pregel = Mock()
    pregel.run = AsyncMock(side_effect=asyncio.CancelledError())
    checkpointer = AsyncMock()
    _mock_graph_store(checkpointer, create_state(ns="W1", step=0, channel_snapshot={}))
    graph = _compiled_graph_cls()(pregel=pregel, checkpointer=checkpointer)

    with pytest.raises(asyncio.CancelledError):
        await graph._invoke({"query": "q"}, _fake_session())

    checkpointer.post_workflow_execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoke_cancelled_propagates_original_cancel_despite_save_reraise():
    """checkpointer 保存分支 re-raise 传入异常时被吞,原始取消链继续传播。"""
    pregel = Mock()
    original = asyncio.CancelledError()
    pregel.run = AsyncMock(side_effect=original)
    checkpointer = AsyncMock()
    _mock_graph_store(checkpointer, _resumable_graph_state())
    checkpointer.post_workflow_execute = AsyncMock(
        side_effect=asyncio.CancelledError("workflow exception save")
    )
    graph = _compiled_graph_cls()(pregel=pregel, checkpointer=checkpointer)

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await graph._invoke({"query": "q"}, _fake_session())

    assert excinfo.value is original


@pytest.mark.asyncio
async def test_invoke_cancelled_propagates_original_cancel_despite_save_failure():
    """保存分支抛后端错误(非 CancelledError)时同样被吞,原始取消链继续传播。

    若不捕获,后端错误会顶替原始取消,破坏上游取消处理(检视意见 P2)。
    """
    pregel = Mock()
    original = asyncio.CancelledError()
    pregel.run = AsyncMock(side_effect=original)
    checkpointer = AsyncMock()
    _mock_graph_store(checkpointer, _resumable_graph_state())
    checkpointer.post_workflow_execute = AsyncMock(
        side_effect=RuntimeError("backend save failed")
    )
    graph = _compiled_graph_cls()(pregel=pregel, checkpointer=checkpointer)

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await graph._invoke({"query": "q"}, _fake_session())

    assert excinfo.value is original
