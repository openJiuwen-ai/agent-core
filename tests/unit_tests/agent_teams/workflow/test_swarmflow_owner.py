# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run attribution: the ``executor`` field on workflow progress events.

Every progress event names the member currently hosting the run — teammate
launches and leader launches alike (and the tool's own WORKFLOW_STOPPED
announcement). A harness with no team context attributes nothing. Legacy
events that predate the field keep deserializing with None.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.schema.events import TeamEvent, WorkflowProgressTeamEvent
from openjiuwen.agent_teams.workflow.engine.progress import (
    ProgressKind,
    WorkflowProgressEvent,
)
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool


class _HarnessStub:
    """The parent-agent surface the tool reads identity off."""

    def __init__(self, role: str | None, member_name: str | None) -> None:
        self.model = "m"
        self.background_task_controller = None
        self.build_context = SimpleNamespace(role=role, member_name=member_name) if role else None


class _RecMessager:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, *, topic_id, message):
        self.published.append((topic_id, message))


def _tool(role: str | None, member_name: str | None, messager: Any = None) -> SwarmflowTool:
    tool = object.__new__(SwarmflowTool)
    tool._parent_agent = _HarnessStub(role, member_name)
    tool._messager = messager
    tool._team_name = "t"
    tool._language = "cn"
    tool._model_resolver = None
    tool._worker_base_spec = None
    tool._human_base_spec = None
    tool._budget = None
    tool._governor = None
    tool._team_backend = None
    return tool


def _inputs(run_id: str) -> dict[str, Any]:
    return {
        "_run_id": run_id,
        "_agent_gate": None,
        "_workflow_ticket": None,
        "_completion_ctx": {},
        "script_path": "/tmp/flow.py",
    }


async def _emit_two_events(*_a, observer=None, **_k) -> Any:
    """Engine stub: emit one started + one phase event, then finish."""
    observer.emit(WorkflowProgressEvent(kind=ProgressKind.WORKFLOW_STARTED, name="research"))
    observer.emit(WorkflowProgressEvent(kind=ProgressKind.PHASE, phase="Search"))
    return {"ok": True}


@pytest.mark.asyncio
async def test_teammate_run_events_carry_the_executor_member_name():
    """Every progress event of a teammate-hosted run names the member."""
    messager = _RecMessager()
    tool = _tool("teammate", "worker-1", messager=messager)

    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=_emit_two_events,
    ):
        await tool.run_background("task-1", _inputs("wf_t1"))
        await asyncio.sleep(0)  # drain the fire-and-forget publishes

    assert messager.published, "no progress events were published"
    for _topic, message in messager.published:
        assert message.event_type == TeamEvent.WORKFLOW_PROGRESS
        assert message.payload["executor"] == "worker-1"
        assert "owner" not in message.payload


@pytest.mark.asyncio
async def test_leader_run_events_name_the_leader_as_executor():
    """Leader runs are attributed like any other host — the leader's name."""
    messager = _RecMessager()
    tool = _tool("leader", "leader", messager=messager)

    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=_emit_two_events,
    ):
        await tool.run_background("task-2", _inputs("wf_l1"))
        await asyncio.sleep(0)

    assert messager.published
    for _topic, message in messager.published:
        assert message.payload["executor"] == "leader"


@pytest.mark.asyncio
async def test_run_events_without_team_context_stay_unattributed():
    """A harness with no build context (library embedding) attributes nothing."""
    messager = _RecMessager()
    tool = _tool(None, None, messager=messager)

    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=_emit_two_events,
    ):
        await tool.run_background("task-3", _inputs("wf_n1"))
        await asyncio.sleep(0)

    assert messager.published
    for _topic, message in messager.published:
        assert message.payload["executor"] is None


@pytest.mark.asyncio
async def test_stopped_announcement_carries_the_executor():
    """The tool's own WORKFLOW_STOPPED announcement (cold-start / paused stop)
    names the acting member like the engine events do."""
    messager = _RecMessager()
    tool = _tool("teammate", "worker-1", messager=messager)
    token = set_session_id("s-attr")

    try:
        announced = await tool._announce_stopped("wf_t2")
    finally:
        reset_session_id(token)

    assert announced is True
    (_topic, message), = messager.published
    assert message.event_type == TeamEvent.WORKFLOW_PROGRESS
    assert message.payload["kind"] == ProgressKind.WORKFLOW_STOPPED
    assert message.payload["run_id"] == "wf_t2"
    assert message.payload["executor"] == "worker-1"


@pytest.mark.asyncio
async def test_stopped_announcement_of_a_leader_run_names_the_leader():
    messager = _RecMessager()
    tool = _tool("leader", "leader", messager=messager)
    token = set_session_id("s-attr")

    try:
        announced = await tool._announce_stopped("wf_l2")
    finally:
        reset_session_id(token)

    assert announced is True
    (_topic, message), = messager.published
    assert message.payload["executor"] == "leader"


def test_legacy_event_without_executor_deserializes_to_none():
    """Events predating the field (and payloads that omit it) read as None."""
    event = WorkflowProgressTeamEvent(team_name="t", kind="phase", phase="Search")
    assert event.executor is None
    assert event.model_dump()["executor"] is None
    assert "owner" not in WorkflowProgressTeamEvent.model_fields

    payload = WorkflowProgressTeamEvent.model_validate(
        {"team_name": "t", "kind": "phase", "phase": "Search"}
    )
    assert payload.executor is None
