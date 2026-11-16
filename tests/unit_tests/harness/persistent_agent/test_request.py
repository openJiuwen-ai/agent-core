# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task input and interactive reply values accepted by the execution entries."""

from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.level0

RECEIVED = datetime(2026, 10, 7, 8, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def api():
    return import_module("openjiuwen.harness.persistent_agent")


def direct_source():
    return {"message_id": "m-1", "sender_id": "u-1", "sender_name": "李四", "received_at": RECEIVED}


def group_source():
    return {**direct_source(), "sender_kind": "human", "text": "@agent 帮我看看"}


def test_request_kinds_match_their_sources(api):
    direct = api.RequestInput(kind="direct_message", text="hi", source=direct_source(), reply_target="chat-1")
    assert isinstance(direct.source, api.DirectMessageSource)
    assert direct.trigger_message_id == "m-1"
    assert direct.source.received_at.tzinfo == timezone.utc
    group = api.RequestInput(kind="group_message", text="hi", source=group_source(), reply_target="chat-1")
    assert isinstance(group.source, api.GroupMessage)
    scheduled = api.RequestInput(kind="scheduled", text="daily report", reply_target="chat-1")
    assert scheduled.trigger_message_id is None


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "direct_message"},
        {"kind": "direct_message", "source": group_source()},
        {"kind": "group_message", "source": direct_source()},
        {"kind": "scheduled", "source": direct_source()},
        {"kind": "scheduled", "text": "  "},
        {"kind": "scheduled", "reply_target": "has space"},
        {"kind": "scheduled", "continue_instruction": ""},
    ],
)
def test_invalid_request_input_rejected(api, fields):
    with pytest.raises(ValidationError):
        api.RequestInput(**{"text": "task", "reply_target": "chat-1", **fields})


def test_confirm_reply_accepts_both_reply_kinds(api):
    reply = api.ConfirmReply(
        responses=[
            {"kind": "permission", "tool_call_id": "call-1", "choice": "approve_for_session"},
            {"kind": "answer", "tool_call_id": "call-2", "answers": {"哪个环境？": "不知道"}},
            {"kind": "answer", "tool_call_id": "call-3", "outcome": "timed_out"},
        ]
    )
    assert [type(item) for item in reply.responses] == [api.PermissionResponse, api.AnswerResponse, api.AnswerResponse]
    assert reply.responses[2].answers == {}


@pytest.mark.parametrize(
    "responses",
    [
        [],
        [{"kind": "permission", "tool_call_id": "call-1", "choice": "approve"}] * 2,
        [{"kind": "permission", "tool_call_id": "call-1", "choice": "allow_forever"}],
        [{"kind": "answer", "tool_call_id": "call-1", "outcome": "timed_out", "answers": {"q": "a"}}],
        [{"kind": "answer", "tool_call_id": "call-1", "answers": {}}],
        [{"kind": "other", "tool_call_id": "call-1"}],
    ],
)
def test_invalid_confirm_reply_rejected(api, responses):
    with pytest.raises(ValidationError):
        api.ConfirmReply(responses=responses)
