# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for openjiuwen.harness.tools.session_tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.errors import FrameworkError
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.session_tools import (
    SESSION_SPAWN_TASK_TYPE,
    SessionToolkit,
    SessionsCancelTool,
    SessionsListTool,
    SessionsSpawnTool,
    build_session_tools,
)


class TestSessionToolkit:
    @staticmethod
    def test_upsert_and_get() -> None:
        tk = SessionToolkit()
        tk.upsert_running("t1", "sub1", "desc")
        row = tk.get("t1")
        assert row is not None
        assert row.task_id == "t1"
        assert row.sub_session_id == "sub1"
        assert row.description == "desc"
        assert row.status == "running"

    @staticmethod
    def test_mark_completed_failed_canceled_clear() -> None:
        tk = SessionToolkit()
        tk.upsert_running("t1", "sub1", "d")
        tk.mark_completed("t1", "ok")
        assert tk.get("t1") and tk.get("t1").status == "completed"
        assert tk.get("t1").result == "ok"

        tk.upsert_running("t2", "sub2", "d2")
        tk.mark_failed("t2", "boom")
        assert tk.get("t2") and tk.get("t2").status == "error"
        assert tk.get("t2").error == "boom"

        tk.upsert_running("t3", "sub3", "d3")
        tk.mark_canceled("t3")
        assert tk.get("t3") and tk.get("t3").status == "canceled"

        tk.clear()
        assert tk.list_all() == []


@pytest.mark.asyncio
class TestSessionsListTool:
    async def test_empty_cn(self) -> None:
        tool = SessionsListTool(SessionToolkit(), language="cn")
        out = await tool.invoke({})
        assert out.success is True
        assert "没有后台" in str(out.data)

    async def test_one_row(self) -> None:
        tk = SessionToolkit()
        tk.upsert_running("tid", "sid", "hello")
        tool = SessionsListTool(tk, language="en")
        out = await tool.invoke({})
        assert out.success is True
        assert "tid" in out.data and "hello" in out.data and "running" in out.data


@pytest.mark.asyncio
class TestSessionsCancelTool:
    async def test_invalid_inputs(self) -> None:
        agent = SimpleNamespace(loop_controller=None)
        tool = SessionsCancelTool(parent_agent=agent, toolkit=SessionToolkit())
        with pytest.raises(FrameworkError) as exc:
            await tool.invoke("not a dict")
        assert "Invalid inputs" in str(exc.value)

    async def test_missing_task_id(self) -> None:
        agent = SimpleNamespace(loop_controller=MagicMock())
        tool = SessionsCancelTool(parent_agent=agent, toolkit=SessionToolkit())
        with pytest.raises(FrameworkError) as exc:
            await tool.invoke({})
        assert "task_id" in str(exc.value)

    async def test_task_not_found(self) -> None:
        ctrl = SimpleNamespace(task_scheduler=MagicMock())
        agent = SimpleNamespace(loop_controller=ctrl)
        tool = SessionsCancelTool(parent_agent=agent, toolkit=SessionToolkit())
        with pytest.raises(FrameworkError) as exc:
            await tool.invoke({"task_id": "nope"})
        assert "not found" in str(exc.value)

    async def test_cancel_success(self) -> None:
        tk = SessionToolkit()
        tk.upsert_running("tid", "sid", "d")
        sched = MagicMock()
        sched.cancel_task = AsyncMock(return_value=True)
        ctrl = SimpleNamespace(task_scheduler=sched)
        agent = SimpleNamespace(loop_controller=ctrl)
        tool = SessionsCancelTool(parent_agent=agent, toolkit=tk, language="cn")
        out = await tool.invoke({"task_id": "tid"})
        assert out.success is True
        assert tk.get("tid") and tk.get("tid").status == "canceled"
        sched.cancel_task.assert_awaited_once_with("tid")

    async def test_cancel_scheduler_returns_false(self) -> None:
        tk = SessionToolkit()
        tk.upsert_running("tid", "sid", "d")
        sched = MagicMock()
        sched.cancel_task = AsyncMock(return_value=False)
        ctrl = SimpleNamespace(task_scheduler=sched)
        agent = SimpleNamespace(loop_controller=ctrl)
        tool = SessionsCancelTool(parent_agent=agent, toolkit=tk, language="en")
        out = await tool.invoke({"task_id": "tid"})
        assert out.success is False
        assert tk.get("tid") and tk.get("tid").status == "running"


@pytest.mark.asyncio
class TestSessionsSpawnTool:
    async def test_enable_task_loop_required(self) -> None:
        agent = SimpleNamespace(deep_config=None, event_handler=None)
        tool = SessionsSpawnTool(parent_agent=agent, toolkit=SessionToolkit())
        with pytest.raises(FrameworkError) as exc:
            await tool.invoke({}, session=MagicMock(spec=Session))
        assert "enable_task_loop" in str(exc.value)

    async def test_spawn_submits_task(self) -> None:
        tm = MagicMock()
        tm.add_task = AsyncMock()
        handler = SimpleNamespace(task_manager=tm)
        dc = SimpleNamespace(enable_task_loop=True)
        agent = SimpleNamespace(deep_config=dc, event_handler=handler)
        tk = SessionToolkit()
        tool = SessionsSpawnTool(parent_agent=agent, toolkit=tk, language="cn")
        sess = MagicMock(spec=Session)
        sess.get_session_id.return_value = "sess-1"
        out = await tool.invoke(
            {"subagent_type": "foo", "task_description": "do work"},
            session=sess,
        )
        assert out.success is True
        assert "已提交" in str(out.data) or "pending" in str(out.data).lower()
        tm.add_task.assert_awaited_once()
        task_arg = tm.add_task.await_args[0][0]
        assert task_arg.task_type == SESSION_SPAWN_TASK_TYPE
        assert task_arg.session_id == "sess-1"
        assert task_arg.metadata is not None
        assert task_arg.metadata["subagent_type"] == "foo"
        rows = tk.list_all()
        assert len(rows) == 1
        assert rows[0].status == "running"
        assert rows[0].description == "do work"


def test_build_session_tools_returns_three() -> None:
    parent = SimpleNamespace(
        deep_config=SimpleNamespace(enable_task_loop=True, subagents=[]),
        event_handler=SimpleNamespace(task_manager=MagicMock()),
        loop_controller=SimpleNamespace(task_scheduler=MagicMock()),
    )
    tools = build_session_tools(parent, SessionToolkit(), language="en")
    assert len(tools) == 3
    names = {t.card.name for t in tools}
    assert names == {"sessions_list", "sessions_spawn", "sessions_cancel"}
