# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration tests for subagent activity stream wiring."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.control import SubagentControl
from openjiuwen.harness.subagent_runtime.models import SubagentActivity
from openjiuwen.harness.subagent_runtime.persistence import merge_subagent_bucket, read_subagent_bucket
from tests.unit_tests.harness.subagent_runtime.test_control import ControlParentAgent, _patch_create_session
from tests.unit_tests.harness.subagent_runtime.test_instance import MockAgent
from tests.unit_tests.harness.subagent_runtime.test_session_manager import MockSession as ManagerSession


@dataclass
class ActivityMockAgent(MockAgent):
    stream_chunks: list[dict[str, object]] = field(default_factory=list)

    async def stream(
        self,
        inputs: dict[str, str],
        *,
        session: ManagerSession,
    ) -> AsyncIterator[dict[str, object]]:
        _ = inputs
        self.stream_calls += 1
        self.active_streams += 1
        self.max_active_streams = max(self.max_active_streams, self.active_streams)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            for chunk in self.stream_chunks:
                yield chunk
            yield {"type": "llm_output", "payload": {"content": self.output}}
            yield {
                "type": "answer",
                "payload": {"output": self.output, "result_type": "answer"},
            }
        finally:
            self.active_streams -= 1


@dataclass
class ActivityControlParent(ControlParentAgent):
    mock_agent: ActivityMockAgent = field(default_factory=ActivityMockAgent)

    def create_subagent(
        self,
        subagent_type: str,
        subsession_id: str,
        browser_capabilities: list[str] | None = None,
    ) -> ActivityMockAgent:
        super().create_subagent(subagent_type, subsession_id, browser_capabilities)
        return ActivityMockAgent(
            output=self.mock_agent.output,
            delay_s=self.mock_agent.delay_s,
            stream_error=self.mock_agent.stream_error,
            prepare_error=self.mock_agent.prepare_error,
            stream_chunks=list(self.mock_agent.stream_chunks),
        )


@pytest.mark.asyncio
async def test_spawn_emits_tool_call_activity() -> None:
    parent = ActivityControlParent(
        mock_agent=ActivityMockAgent(
            output="done",
            delay_s=0.02,
            stream_chunks=[
                {
                    "type": "tool_call",
                    "payload": {
                        "tool_call": {
                            "tool_name": "grep",
                            "tool_call_id": "call-1",
                            "arguments": {"pattern": "foo"},
                        }
                    },
                },
                {
                    "type": "tool_result",
                    "payload": {
                        "tool_result": {
                            "tool_name": "grep",
                            "tool_call_id": "call-1",
                            "success": True,
                            "summary": "1 match",
                        }
                    },
                },
            ],
        ),
    )
    parent_session = Session(session_id="parent")
    parent_session.write_stream = AsyncMock()
    control = SubagentControl(
        parent,
        "parent",
        config=SubagentRuntimeConfig(enable_activity_stream=True),
        parent_session=parent_session,
    )
    with _patch_create_session():
        spawned = await control.spawn("explore", "hello")
        await asyncio.sleep(parent.mock_agent.delay_s + 0.1)
        await control._manager.remove(spawned.subagent_id, reason="test_cleanup")
        control._registry.release(spawned.subagent_id)

    assert parent_session.write_stream.await_count >= 2
    kinds = [
        call.args[0].payload["subagent_activity"]["kind"]
        for call in parent_session.write_stream.await_args_list
        if call.args[0].type == "subagent_activity"
    ]
    assert "tool_call" in kinds
    assert "tool_result" in kinds


@pytest.mark.asyncio
async def test_running_status_precedes_activity_on_spawn() -> None:
    parent = ActivityControlParent(
        mock_agent=ActivityMockAgent(
            output="done",
            delay_s=0.02,
            stream_chunks=[
                {
                    "type": "tool_call",
                    "payload": {
                        "tool_call": {
                            "tool_name": "grep",
                            "tool_call_id": "call-1",
                            "arguments": {"pattern": "foo"},
                        }
                    },
                },
            ],
        ),
    )
    parent_session = Session(session_id="parent")
    parent_session.write_stream = AsyncMock()
    control = SubagentControl(
        parent,
        "parent",
        config=SubagentRuntimeConfig(enable_activity_stream=True),
        parent_session=parent_session,
    )
    with _patch_create_session():
        spawned = await control.spawn("explore", "hello")
        await asyncio.sleep(parent.mock_agent.delay_s + 0.15)
        await control._manager.remove(spawned.subagent_id, reason="test_cleanup")
        control._registry.release(spawned.subagent_id)

    event_types = [call.args[0].type for call in parent_session.write_stream.await_args_list]
    first_status_index = next(
        index
        for index, event_type in enumerate(event_types)
        if event_type == "subagent_updated"
        and parent_session.write_stream.await_args_list[index].args[0].payload["subagent_updated"]["status"]
        == "running"
    )
    first_activity_index = next(
        index for index, event_type in enumerate(event_types) if event_type == "subagent_activity"
    )
    assert first_status_index < first_activity_index


@pytest.mark.asyncio
async def test_running_status_precedes_activity_on_send_input() -> None:
    parent = ActivityControlParent(
        mock_agent=ActivityMockAgent(
            output="done",
            delay_s=0.02,
            stream_chunks=[
                {
                    "type": "tool_call",
                    "payload": {
                        "tool_call": {
                            "tool_name": "grep",
                            "tool_call_id": "call-2",
                            "arguments": {"pattern": "bar"},
                        }
                    },
                },
            ],
        ),
    )
    parent_session = Session(session_id="parent")
    parent_session.write_stream = AsyncMock()
    control = SubagentControl(
        parent,
        "parent",
        config=SubagentRuntimeConfig(enable_activity_stream=True),
        parent_session=parent_session,
    )
    with _patch_create_session():
        spawned = await control.spawn("explore", "first")
        await asyncio.sleep(parent.mock_agent.delay_s + 0.1)
        parent.mock_agent.stream_chunks = [
            {
                "type": "tool_call",
                "payload": {
                    "tool_call": {
                        "tool_name": "grep",
                        "tool_call_id": "call-2",
                        "arguments": {"pattern": "bar"},
                    }
                },
            },
        ]
        await control.send_input(spawned.subagent_id, "second")
        await asyncio.sleep(parent.mock_agent.delay_s + 0.15)
        await control._manager.remove(spawned.subagent_id, reason="test_cleanup")
        control._registry.release(spawned.subagent_id)

    event_types = [call.args[0].type for call in parent_session.write_stream.await_args_list]
    send_input_running_indexes = [
        index
        for index, call in enumerate(parent_session.write_stream.await_args_list)
        if call.args[0].type == "subagent_updated"
        and call.args[0].payload["subagent_updated"]["status"] == "running"
    ]
    assert len(send_input_running_indexes) >= 2
    second_running_index = send_input_running_indexes[-1]
    activity_indexes = [
        index for index, event_type in enumerate(event_types) if event_type == "subagent_activity"
    ]
    assert activity_indexes
    assert second_running_index < activity_indexes[-1]


@pytest.mark.asyncio
async def test_activity_stream_disabled_has_no_emitter() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.02))
    control = SubagentControl(
        parent,
        "parent",
        config=SubagentRuntimeConfig(enable_activity_stream=False),
        parent_session=Session(session_id="parent"),
    )
    assert control._activity_emitter is None


@pytest.mark.asyncio
async def test_flush_persists_milestone_activities_only() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.02))
    session = Session(session_id="parent")
    control = SubagentControl(
        parent,
        "parent",
        config=SubagentRuntimeConfig(enable_activity_stream=True),
        parent_session=session,
    )
    control._mark_activity_ready("sid-1", "task-1")
    control._handle_activity(
        SubagentActivity(
            subagent_id="sid-1",
            task_id="task-1",
            seq=1,
            kind="tool_call",
            summary="grep(pattern=foo)",
            tool_name="grep",
            tool_call_id="call-1",
            at_ms=1.0,
        )
    )
    control._handle_activity(
        SubagentActivity(
            subagent_id="sid-1",
            task_id="task-1",
            seq=2,
            kind="thinking",
            summary="planning",
            at_ms=2.0,
        )
    )
    control.flush()

    bucket = read_subagent_bucket(session)
    items = bucket["activities"]["sid-1"]
    assert len(items) == 1
    assert items[0]["kind"] == "tool_call"


@pytest.mark.asyncio
async def test_hydrate_restores_persisted_activities() -> None:
    session = Session(session_id="parent")
    merge_subagent_bucket(
        session,
        {
            "activities": {
                "sid-1": [
                    SubagentActivity(
                        subagent_id="sid-1",
                        task_id="task-1",
                        seq=3,
                        kind="tool_result",
                        summary="done",
                        tool_name="grep",
                        tool_call_id="call-1",
                        ok=True,
                        at_ms=3.0,
                    ).to_dict()
                ]
            },
            "revision": 1,
        },
    )
    control = SubagentControl(
        ControlParentAgent(),
        "parent",
        parent_session=session,
    )
    control.hydrate()
    assert list(control._activities["sid-1"])[0].kind == "tool_result"
    assert control._activity_seq["sid-1"] == 3
