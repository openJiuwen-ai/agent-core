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
from openjiuwen.agent_teams.schema.events import (
    TeamEvent,
    format_swarmflow_human_reply_target,
    parse_swarmflow_human_reply_target,
    swarmflow_human_reply_topic,
)


def test_format_and_parse_swarmflow_reply_target_roundtrip():
    """Colon encoding round-trips legacy and run-scoped targets."""
    assert format_swarmflow_human_reply_target("review:host:0") == "swarmflow:review:host:0"
    assert parse_swarmflow_human_reply_target("review:host:0") == (None, "review:host:0")
    assert format_swarmflow_human_reply_target("review:host:0", "run-1") == "swarmflow:run-1:review:host:0"
    assert parse_swarmflow_human_reply_target("run-1:review:host:0") == ("run-1", "review:host:0")


def test_detects_swarmflow_human_reply():
    """Legacy target with engine-style corr ({phase}:{label}:{turn}) stays legacy."""
    payloads = [
        HumanAgentMessage(body="yes, approved", sender="user", target="swarmflow:review:host:0")
    ]
    assert TeamRuntimeManager._as_swarmflow_human_reply(payloads) == (
        None,
        "review:host:0",
        "yes, approved",
    )


def test_legacy_and_run_scoped_parse_differ_for_same_correlation_id():
    """Same corr id must parse to legacy vs run-scoped depending on run_id prefix."""
    corr = "review:host:0"
    legacy = [HumanAgentMessage(body="a", sender="user", target=f"swarmflow:{corr}")]
    run_scoped = [
        HumanAgentMessage(body="b", sender="user", target=f"swarmflow:run-1:{corr}")
    ]
    assert TeamRuntimeManager._as_swarmflow_human_reply(legacy) == (None, corr, "a")
    assert TeamRuntimeManager._as_swarmflow_human_reply(run_scoped) == ("run-1", corr, "b")


def test_detects_run_scoped_swarmflow_reply():
    """Run-scoped ``swarmflow:<run_id>:<corr>`` yields (run_id, corr, answer)."""
    payloads = [HumanAgentMessage(body="ok", sender="user", target="swarmflow:run-1:review:host:0")]
    assert TeamRuntimeManager._as_swarmflow_human_reply(payloads) == ("run-1", "review:host:0", "ok")


def test_detects_run_scoped_simple_correlation_id():
    """Run-scoped with a corr that has no colons still splits on the first colon."""
    payloads = [HumanAgentMessage(body="ok", sender="user", target="swarmflow:run-1:abc123")]
    assert TeamRuntimeManager._as_swarmflow_human_reply(payloads) == ("run-1", "abc123", "ok")


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
    """The reply is published as WORKFLOW_HUMAN_REPLY on the run's reply topic.

    Legacy form (run_id=None) routes to the legacy session+team scope.
    """
    messager = _CapturingMessager()
    entry = SimpleNamespace(
        team_name="t",
        current_session_id="s1",
        agent=SimpleNamespace(team_backend=SimpleNamespace(messager=messager)),
    )

    result = asyncio.run(
        TeamRuntimeManager._route_swarmflow_human_reply(entry, None, "abc123", "approved")
    )

    assert result.ok
    assert len(messager.published) == 1
    topic, message = messager.published[0]
    assert topic == swarmflow_human_reply_topic("s1", "t")
    assert message.event_type == TeamEvent.WORKFLOW_HUMAN_REPLY
    assert message.payload == {"correlation_id": "abc123", "answer": "approved"}


def test_route_legacy_engine_correlation_uses_legacy_topic():
    """Legacy target with engine-style corr must not land on a run-scoped topic."""
    messager = _CapturingMessager()
    entry = SimpleNamespace(
        team_name="t",
        current_session_id="s1",
        agent=SimpleNamespace(team_backend=SimpleNamespace(messager=messager)),
    )

    result = asyncio.run(
        TeamRuntimeManager._route_swarmflow_human_reply(entry, None, "review:host:0", "approved")
    )

    assert result.ok
    topic, message = messager.published[0]
    assert topic == swarmflow_human_reply_topic("s1", "t")
    assert message.payload == {"correlation_id": "review:host:0", "answer": "approved"}


def test_route_publishes_run_scoped_reply():
    """A run-scoped reply lands on the run_id-namespaced topic."""
    messager = _CapturingMessager()
    entry = SimpleNamespace(
        team_name="t",
        current_session_id="s1",
        agent=SimpleNamespace(team_backend=SimpleNamespace(messager=messager)),
    )

    result = asyncio.run(
        TeamRuntimeManager._route_swarmflow_human_reply(entry, "run-1", "review:host:0", "ok")
    )

    assert result.ok
    topic, message = messager.published[0]
    assert topic == swarmflow_human_reply_topic("s1", "t", "run-1")
    assert topic == "session:s1:team:t:run:run-1:swarmflow_human_reply"
    assert message.payload == {"correlation_id": "review:host:0", "answer": "ok"}


def test_route_without_messager_fails_cleanly():
    """No messager (e.g. a god-view-only agent) returns a clear failure, no raise."""
    entry = SimpleNamespace(
        team_name="t",
        current_session_id="s1",
        agent=SimpleNamespace(team_backend=None),
    )
    result = asyncio.run(
        TeamRuntimeManager._route_swarmflow_human_reply(entry, None, "abc", "x")
    )
    assert not result.ok
    assert result.reason == "no_messager"


def test_concurrent_runs_isolate_replies_by_run_id():
    """Same session+team, two runs with an identical correlation_id.

    Each reply must land on its own run-scoped topic, so a run only ever
    receives its own replies — never the other run's. This is the routing-side
    guarantee that concurrent runs with colliding correlation ids don't
    cross-resolve each other's pending human turn.
    """
    corr = "review:host:0"
    run1 = _CapturingMessager()
    run2 = _CapturingMessager()
    entry1 = SimpleNamespace(
        team_name="t",
        current_session_id="s1",
        agent=SimpleNamespace(team_backend=SimpleNamespace(messager=run1)),
    )
    entry2 = SimpleNamespace(
        team_name="t",
        current_session_id="s1",
        agent=SimpleNamespace(team_backend=SimpleNamespace(messager=run2)),
    )

    asyncio.run(TeamRuntimeManager._route_swarmflow_human_reply(entry1, "run-1", corr, "run1-answer"))
    asyncio.run(TeamRuntimeManager._route_swarmflow_human_reply(entry2, "run-2", corr, "run2-answer"))

    # Each run published exactly one reply, to distinct run-scoped topics.
    assert len(run1.published) == 1
    assert len(run2.published) == 1
    t1, m1 = run1.published[0]
    t2, m2 = run2.published[0]
    assert t1 == "session:s1:team:t:run:run-1:swarmflow_human_reply"
    assert t2 == "session:s1:team:t:run:run-2:swarmflow_human_reply"
    assert t1 != t2
    # Same correlation id, distinct answers — each stays on its own topic.
    assert m1.payload == {"correlation_id": corr, "answer": "run1-answer"}
    assert m2.payload == {"correlation_id": corr, "answer": "run2-answer"}
