# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for parent spawn-group summary after SESSION_SPAWN completion."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.interaction import InputDispatchMode, SendInputRequest
from openjiuwen.harness.tools.subagent.session_notify import (
    SessionTaskNotifyContext,
)
from openjiuwen.harness.tools.subagent.session_tools import SessionToolkit


def _make_agent_for_summary(
    *,
    toolkit: SessionToolkit | None,
    interaction_started: bool = False,
) -> DeepAgent:
    agent = DeepAgent.__new__(DeepAgent)
    agent._invoke_active = False
    agent._loop_session = SimpleNamespace(get_session_id=lambda: "parent-session")
    agent._session_toolkit = toolkit
    agent._interaction_started = interaction_started
    agent._spawn_summary_deliverer = None
    agent.send_input = AsyncMock()
    agent.invoke = AsyncMock(return_value={"output": "summary"})
    return agent


class _IdleParentStub:
    def __init__(self) -> None:
        self.deep_config = SimpleNamespace(language="cn")
        self.schedule_auto_invoke_on_spawn_done = AsyncMock()


@pytest.mark.asyncio
class TestDeliverSpawnSummary:
    async def test_send_input_when_no_deliverer_and_has_consumer(self) -> None:
        agent = _make_agent_for_summary(
            toolkit=SessionToolkit(),
            interaction_started=True,
        )
        agent.has_output_stream = lambda: True  # type: ignore[method-assign]
        agent._spawn_summary_deliverer = None

        delivered = await agent._deliver_spawn_summary("summary prompt")

        assert delivered is True
        agent.send_input.assert_awaited_once()
        request = agent.send_input.await_args.args[0]
        assert isinstance(request, SendInputRequest)
        assert "summary prompt" in request.inputs["query"]
        assert "原样" in request.inputs["query"]
        assert request.mode is InputDispatchMode.FOLLOW_UP
        agent.invoke.assert_not_awaited()

    async def test_prefers_deliverer_even_when_output_consumer_exists(self) -> None:
        agent = _make_agent_for_summary(
            toolkit=SessionToolkit(),
            interaction_started=True,
        )
        agent.has_output_stream = lambda: True  # type: ignore[method-assign]
        agent._spawn_summary_deliverer = AsyncMock(return_value=True)

        delivered = await agent._deliver_spawn_summary(
            "summary prompt", request_id="req-1"
        )

        assert delivered is True
        agent._spawn_summary_deliverer.assert_awaited_once_with(
            "summary prompt", "req-1"
        )
        agent.send_input.assert_not_awaited()

    async def test_uses_deliverer_when_no_output_consumer(self) -> None:
        agent = _make_agent_for_summary(
            toolkit=SessionToolkit(),
            interaction_started=True,
        )
        agent.has_output_stream = lambda: False  # type: ignore[method-assign]
        agent._spawn_summary_deliverer = AsyncMock(return_value=True)

        delivered = await agent._deliver_spawn_summary(
            "summary prompt", request_id="req-1"
        )

        assert delivered is True
        agent._spawn_summary_deliverer.assert_awaited_once_with(
            "summary prompt", "req-1"
        )
        agent.send_input.assert_not_awaited()

    async def test_invoke_when_interaction_not_started(self) -> None:
        agent = _make_agent_for_summary(toolkit=SessionToolkit())
        agent._spawn_summary_deliverer = None

        delivered = await agent._deliver_spawn_summary("summary prompt")

        assert delivered is True
        agent.invoke.assert_awaited_once()
        invoke_inputs = agent.invoke.await_args.args[0]
        assert "summary prompt" in invoke_inputs["query"]
        assert "原样" in invoke_inputs["query"]
        agent.send_input.assert_not_awaited()

    async def test_delivery_failure_releases_spawn_summary_claim(self) -> None:
        toolkit = SessionToolkit()
        toolkit.upsert_running("t1", "a", "g", "s", "d", request_id="req-1")
        toolkit.mark_completed("t1", "ok")
        claimed = toolkit.try_claim_request_spawn_summary("req-1")
        assert claimed is not None

        agent = _make_agent_for_summary(toolkit=toolkit)
        agent._loop_session = None
        agent._spawn_summary_deliverer = None

        await agent.schedule_auto_invoke_on_spawn_done(
            "summary",
            delay=0,
            claim_task_ids=["t1"],
        )

        assert "t1" not in toolkit._spawn_summary_claimed_task_ids


@pytest.mark.asyncio
async def test_complete_session_spawn_card_only_until_group_ready() -> None:
    from openjiuwen.core.controller.modules.event_handler import EventHandlerInput
    from openjiuwen.core.controller.schema.dataframe import JsonDataFrame
    from openjiuwen.core.controller.schema.event import TaskCompletionEvent
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.tools import SESSION_SPAWN_TASK_TYPE

    toolkit = SessionToolkit()
    notifier = AsyncMock()
    toolkit.set_notifier(notifier)
    toolkit.upsert_running(
        "t-sh", "a", "g", "s-sh", "task A", request_id="req-1"
    )
    toolkit.upsert_running(
        "t-hz", "b", "g", "s-hz", "task B", request_id="req-1"
    )

    agent = _IdleParentStub()
    handler = TaskLoopEventHandler(agent)
    handler.set_session_toolkit(toolkit)

    async def _complete(task_id: str, output: str) -> None:
        event = TaskCompletionEvent(
            task_result=[JsonDataFrame(data={"output": output})],
            metadata={"task_id": task_id, "task_type": SESSION_SPAWN_TASK_TYPE},
        )
        await handler._complete_session_spawn(
            task_id,
            EventHandlerInput.model_construct(event=event, session=MagicMock()),
            is_error=False,
        )

    await _complete("t-sh", "result A")
    assert notifier.notify_session_task_done.await_count == 1
    agent.schedule_auto_invoke_on_spawn_done.assert_not_called()

    await _complete("t-hz", "result B")
    await asyncio.sleep(0)

    assert notifier.notify_session_task_done.await_count == 2
    agent.schedule_auto_invoke_on_spawn_done.assert_called_once()
    content = agent.schedule_auto_invoke_on_spawn_done.call_args.args[0]
    assert "result A" in content and "result B" in content
    assert "组汇总" in content
    assert "task A" in content and "task B" in content
    assert "禁止说" not in content
    assert "任务结果：" not in content
    assert set(
        agent.schedule_auto_invoke_on_spawn_done.call_args.kwargs["claim_task_ids"]
    ) == {"t-sh", "t-hz"}
    assert (
        agent.schedule_auto_invoke_on_spawn_done.call_args.kwargs[
            "summary_request_id"
        ]
        == "req-1"
    )


@pytest.mark.asyncio
async def test_stale_request_id_still_queues_summary() -> None:
    """New user request_id must not skip an older spawn group's summary."""
    from openjiuwen.core.controller.modules.event_handler import EventHandlerInput
    from openjiuwen.core.controller.schema.dataframe import JsonDataFrame
    from openjiuwen.core.controller.schema.event import TaskCompletionEvent
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.tools import SESSION_SPAWN_TASK_TYPE

    toolkit = SessionToolkit()
    toolkit.set_notifier(AsyncMock())
    toolkit.set_notify_context(SessionTaskNotifyContext(request_id="req-new"))
    toolkit.upsert_running(
        "t1", "a", "g", "s", "task", request_id="req-old"
    )

    agent = _IdleParentStub()
    handler = TaskLoopEventHandler(agent)
    handler.set_session_toolkit(toolkit)

    event = TaskCompletionEvent(
        task_result=[JsonDataFrame(data={"output": "done"})],
        metadata={"task_id": "t1", "task_type": SESSION_SPAWN_TASK_TYPE},
    )
    await handler._complete_session_spawn(
        "t1",
        EventHandlerInput.model_construct(event=event, session=MagicMock()),
        is_error=False,
    )
    await asyncio.sleep(0)

    agent.schedule_auto_invoke_on_spawn_done.assert_called_once()
    assert (
        agent.schedule_auto_invoke_on_spawn_done.call_args.kwargs[
            "summary_request_id"
        ]
        == "req-old"
    )


@pytest.mark.asyncio
async def test_two_request_groups_schedule_independent_summaries() -> None:
    from openjiuwen.core.controller.modules.event_handler import EventHandlerInput
    from openjiuwen.core.controller.schema.dataframe import JsonDataFrame
    from openjiuwen.core.controller.schema.event import TaskCompletionEvent
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.tools import SESSION_SPAWN_TASK_TYPE

    toolkit = SessionToolkit()
    toolkit.set_notifier(AsyncMock())
    toolkit.upsert_running("t-nj", "a", "g", "s1", "南京", request_id="req-a")
    toolkit.upsert_running("t-sz", "b", "g", "s2", "苏州", request_id="req-a")
    toolkit.upsert_running("t-hz", "c", "g", "s3", "杭州", request_id="req-b")
    toolkit.upsert_running("t-sh", "d", "g", "s4", "上海", request_id="req-b")

    agent = _IdleParentStub()
    handler = TaskLoopEventHandler(agent)
    handler.set_session_toolkit(toolkit)

    async def _complete(task_id: str, output: str) -> None:
        event = TaskCompletionEvent(
            task_result=[JsonDataFrame(data={"output": output})],
            metadata={"task_id": task_id, "task_type": SESSION_SPAWN_TASK_TYPE},
        )
        await handler._complete_session_spawn(
            task_id,
            EventHandlerInput.model_construct(event=event, session=MagicMock()),
            is_error=False,
        )

    await _complete("t-nj", "nj")
    await _complete("t-sz", "sz")
    await _complete("t-hz", "hz")
    await _complete("t-sh", "sh")
    await asyncio.sleep(0)

    assert agent.schedule_auto_invoke_on_spawn_done.call_count == 2
    request_ids = {
        call.kwargs["summary_request_id"]
        for call in agent.schedule_auto_invoke_on_spawn_done.call_args_list
    }
    assert request_ids == {"req-a", "req-b"}


@pytest.mark.asyncio
async def test_active_parent_schedules_summary_not_steer() -> None:
    from openjiuwen.core.controller.modules.event_handler import EventHandlerInput
    from openjiuwen.core.controller.schema.dataframe import JsonDataFrame
    from openjiuwen.core.controller.schema.event import TaskCompletionEvent
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.tools import SESSION_SPAWN_TASK_TYPE

    toolkit = SessionToolkit()
    toolkit.set_notifier(AsyncMock())
    toolkit.upsert_running("t1", "a", "g", "s", "task", request_id="req-1")

    agent = MagicMock()
    agent.is_invoke_active = True
    agent.deep_config = SimpleNamespace(language="cn")
    agent.schedule_auto_invoke_on_spawn_done = AsyncMock()

    handler = TaskLoopEventHandler(agent)
    handler.set_session_toolkit(toolkit)
    handler.interaction_queues = MagicMock()

    event = TaskCompletionEvent(
        task_result=[JsonDataFrame(data={"output": "ok"})],
        metadata={"task_id": "t1", "task_type": SESSION_SPAWN_TASK_TYPE},
    )
    await handler._complete_session_spawn(
        "t1",
        EventHandlerInput.model_construct(event=event, session=MagicMock()),
        is_error=False,
    )
    await asyncio.sleep(0)

    handler.interaction_queues.push_steer.assert_not_called()
    agent.schedule_auto_invoke_on_spawn_done.assert_called_once()


@pytest.mark.asyncio
async def test_try_claim_request_spawn_summary_excludes_already_summarized_tasks() -> None:
    toolkit = SessionToolkit()
    toolkit.upsert_running("t1", "a", "g", "s1", "d1", request_id="req-1")
    toolkit.mark_completed("t1", "ok")
    first = toolkit.try_claim_request_spawn_summary("req-1")
    assert first is not None
    assert toolkit.try_claim_request_spawn_summary("req-1") is None

    toolkit.upsert_running("t2", "b", "g", "s2", "d2", request_id="req-1")
    assert toolkit.has_running_for_request("req-1") is True
    toolkit.mark_completed("t2", "ok2")
    second = toolkit.try_claim_request_spawn_summary("req-1")
    assert second is not None
    assert {row.task_id for row in second} == {"t2"}


@pytest.mark.asyncio
async def test_late_completion_after_cancel_is_noop() -> None:
    from openjiuwen.core.controller.modules.event_handler import EventHandlerInput
    from openjiuwen.core.controller.schema.dataframe import JsonDataFrame
    from openjiuwen.core.controller.schema.event import TaskCompletionEvent
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.tools import SESSION_SPAWN_TASK_TYPE

    toolkit = SessionToolkit()
    notifier = AsyncMock()
    toolkit.set_notifier(notifier)
    toolkit.upsert_running("t1", "a", "g", "s", "d", request_id="req-1")
    toolkit.mark_canceled("t1", reason="canceled")

    agent = _IdleParentStub()
    handler = TaskLoopEventHandler(agent)
    handler.set_session_toolkit(toolkit)

    event = TaskCompletionEvent(
        task_result=[JsonDataFrame(data={"output": "late"})],
        metadata={"task_id": "t1", "task_type": SESSION_SPAWN_TASK_TYPE},
    )
    await handler._complete_session_spawn(
        "t1",
        EventHandlerInput.model_construct(event=event, session=MagicMock()),
        is_error=False,
    )

    notifier.notify_session_task_done.assert_not_awaited()
    agent.schedule_auto_invoke_on_spawn_done.assert_not_called()


def test_format_request_spawn_summary_template_includes_results() -> None:
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.tools.subagent.session_tools import SessionTaskRow

    rows = [
        SessionTaskRow(
            task_id="t1",
            subagent_id="a",
            subagent_type="g",
            sub_session_id="s1",
            description="宁波天气",
            status="completed",
            result="晴 25°C",
            request_id="req-1",
        ),
        SessionTaskRow(
            task_id="t2",
            subagent_id="b",
            subagent_type="g",
            sub_session_id="s2",
            description="苏州天气",
            status="completed",
            result="多云 24°C",
            request_id="req-1",
        ),
    ]
    text = TaskLoopEventHandler._format_request_spawn_summary_template(rows, "cn")
    assert "组汇总" in text
    assert "宁波天气" in text and "苏州天气" in text
    assert "晴 25°C" in text and "多云 24°C" in text


def test_format_request_spawn_summary_template_clips_by_length_only() -> None:
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.tools.subagent.session_tools import SessionTaskRow

    long_desc = "A" * 80
    long_result = "B" * 300
    rows = [
        SessionTaskRow(
            task_id="t1",
            subagent_id="a",
            subagent_type="g",
            sub_session_id="s1",
            description=long_desc,
            status="completed",
            result=long_result,
            request_id="req-1",
        ),
    ]
    text = TaskLoopEventHandler._format_request_spawn_summary_template(rows, "cn")
    assert "…" in text
    assert len(text) < len(long_desc) + len(long_result)


@pytest.mark.asyncio
async def test_build_summary_uses_template_when_polish_unusable() -> None:
    from openjiuwen.harness.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.harness.tools.subagent.session_tools import SessionTaskRow

    rows = [
        SessionTaskRow(
            task_id="t1",
            subagent_id="a",
            subagent_type="g",
            sub_session_id="s1",
            description="任务A",
            status="completed",
            result="详细结果内容足够长用于模板",
            request_id="req-1",
        ),
    ]
    agent = SimpleNamespace(
        synthesize_spawn_group_summary=AsyncMock(
            return_value="所有任务已完成"
        )
    )
    handler = TaskLoopEventHandler(SimpleNamespace(deep_config=None))
    content = await handler._build_request_spawn_summary_content(rows, "cn", agent)
    assert "组汇总" in content
    assert "详细结果内容足够长用于模板" in content
    assert content != "所有任务已完成"