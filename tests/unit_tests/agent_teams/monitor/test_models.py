# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TaskPlanRequestEvent,
    TaskPlanResponseEvent,
)


def test_monitor_event_exposes_lightweight_plan_request_fields() -> None:
    event = EventMessage.from_event(
        TaskPlanRequestEvent(
            team_name="team",
            member_name="member",
            task_id="task-1",
            plan_id="plan-1",
            member_plan_md="/tmp/member_plan.md",
            tool_call_id="tool-1",
        ),
    )

    monitor_event = MonitorEvent.from_event_message(event)

    assert monitor_event is not None
    assert monitor_event.event_type == MonitorEventType.TASK_PLAN_REQUEST
    assert monitor_event.task_id == "task-1"
    assert monitor_event.plan_id == "plan-1"
    assert monitor_event.member_plan_md == "/tmp/member_plan.md"
    assert "tool_call_id" not in monitor_event.model_dump()


def test_monitor_event_does_not_expose_plan_response_feedback() -> None:
    event = EventMessage.from_event(
        TaskPlanResponseEvent(
            team_name="team",
            member_name="member",
            task_id="task-1",
            plan_id="plan-1",
            approved=False,
            status="claimed",
            feedback="revise implementation details",
            tool_call_id="tool-1",
        ),
    )

    monitor_event = MonitorEvent.from_event_message(event)

    assert monitor_event is not None
    assert monitor_event.event_type == MonitorEventType.TASK_PLAN_RESPONSE
    assert monitor_event.task_id == "task-1"
    assert monitor_event.plan_id == "plan-1"
    assert monitor_event.approved is False
    dumped = monitor_event.model_dump()
    assert "feedback" not in dumped
    assert "tool_call_id" not in dumped
