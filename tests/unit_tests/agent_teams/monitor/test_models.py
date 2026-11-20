# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from types import SimpleNamespace

from openjiuwen.agent_teams.monitor.models import MessageInfo, MonitorEvent, MonitorEventType
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


def test_monitor_event_exposes_plan_response_feedback() -> None:
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
    assert dumped["feedback"] == "revise implementation details"
    assert "tool_call_id" not in dumped


def _message_row(**overrides):
    row = {
        "message_id": "msg-1",
        "team_name": "team",
        "from_member_name": "team_leader",
        "to_member_name": "dev",
        "content": "",
        "protocol": "plain",
        "timestamp": 1,
        "broadcast": False,
        "is_read": False,
        "meta": None,
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def test_message_info_exposes_template_meta() -> None:
    meta = {"template": "scheduler_task_start", "refs": {"task": "task-1"}}

    info = MessageInfo.from_internal(_message_row(meta=json.dumps(meta)))

    assert info.content == ""
    assert info.meta == meta


def test_message_info_meta_is_none_for_plain_or_malformed_rows() -> None:
    assert MessageInfo.from_internal(_message_row(content="hi")).meta is None
    assert MessageInfo.from_internal(_message_row(meta="{not json")).meta is None
    assert MessageInfo.from_internal(_message_row(meta=json.dumps({"refs": {}}))).meta is None
