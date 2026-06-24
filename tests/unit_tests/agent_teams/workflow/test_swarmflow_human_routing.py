# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inbound routing for swarmflow human replies (seam B, no real runtime/LLM).

A real person's reply to a swarmflow human turn enters through
``interact_agent_team(HumanAgentMessage(target="swarmflow:<corr>"))``. These
tests cover the two static helpers the runtime manager uses to recognise such a
reply and publish it onto the run's dedicated reply topic — without standing up
a team runtime.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openjiuwen.agent_teams.interaction.payload import (
    GodViewMessage,
    HumanAgentMessage,
    OperatorMessage,
)
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.schema.events import TeamEvent, swarmflow_human_reply_topic


def test_detects_swarmflow_human_reply():
    """A single HumanAgentMessage with a swarmflow: target yields (corr, answer)."""
    payloads = [HumanAgentMessage(body="yes, approved", sender="user", target="swarmflow:abc123")]
    assert TeamRuntimeManager._as_swarmflow_human_reply(payloads) == ("abc123", "yes, approved")


def test_non_swarmflow_payloads_are_not_routed():
    """Ordinary payloads (god-view, operator, plain human, multi) are not swarmflow replies."""
    assert TeamRuntimeManager._as_swarmflow_human_reply([GodViewMessage(body="hi")]) is None
    assert TeamRuntimeManager._as_swarmflow_human_reply([OperatorMessage(body="hi", target="dev")]) is None
    assert TeamRuntimeManager._as_swarmflow_human_reply([HumanAgentMessage(body="hi", sender="user", target="lead")]) is None
    assert TeamRuntimeManager._as_swarmflow_human_reply([HumanAgentMessage(body="hi", sender="user")]) is None
    # A swarmflow target but not the sole payload -> not treated as a reply.
    multi = [
        HumanAgentMessage(body="a", sender="user", target="swarmflow:x"),
        HumanAgentMessage(body="b", sender="user", target="swarmflow:y"),
    ]
    assert TeamRuntimeManager._as_swarmflow_human_reply(multi) is None
    # Empty correlation id -> not a valid reply.
    assert TeamRuntimeManager._as_swarmflow_human_reply([HumanAgentMessage(body="a", sender="user", target="swarmflow:")]) is None


class _CapturingMessager:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic_id: str, message) -> None:
        self.published.append((topic_id, message))


def test_route_publishes_reply_on_dedicated_topic():
    """The reply is published as WORKFLOW_HUMAN_REPLY on the run's reply topic."""
    messager = _CapturingMessager()
    entry = SimpleNamespace(
        team_name="t",
        current_session_id="s1",
        agent=SimpleNamespace(team_backend=SimpleNamespace(messager=messager)),
    )

    result = asyncio.run(
        TeamRuntimeManager._route_swarmflow_human_reply(entry, "abc123", "approved")
    )

    assert result.ok
    assert len(messager.published) == 1
    topic, message = messager.published[0]
    assert topic == swarmflow_human_reply_topic("s1", "t")
    assert message.event_type == TeamEvent.WORKFLOW_HUMAN_REPLY
    assert message.payload == {"correlation_id": "abc123", "answer": "approved"}


def test_route_without_messager_fails_cleanly():
    """No messager (e.g. a god-view-only agent) returns a clear failure, no raise."""
    entry = SimpleNamespace(
        team_name="t",
        current_session_id="s1",
        agent=SimpleNamespace(team_backend=None),
    )
    result = asyncio.run(
        TeamRuntimeManager._route_swarmflow_human_reply(entry, "abc", "x")
    )
    assert not result.ok
    assert result.reason == "no_messager"
