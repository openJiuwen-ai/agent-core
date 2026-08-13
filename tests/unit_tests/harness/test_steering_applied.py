# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for reporting which steers reached model context.

The acknowledgement a host sends says the text was *queued*. This says it was
*applied*, and the two are different facts: a client that only has the first
cannot tell a slow model from an instruction a rail discarded.
"""
from __future__ import annotations

from typing import Any, Dict, List

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.interaction import InteractionEvent, InteractionEventType
from openjiuwen.harness.task_loop.loop_queues import LoopQueues, SteeringInput


def _agent() -> ReActAgent:
    return ReActAgent(AgentCard(name="react", description="test"))


def _capture(agent: ReActAgent) -> List[tuple]:
    calls: List[tuple] = []
    agent.set_steering_applied_sink(
        lambda applied, dropped: calls.append((applied, dropped))
    )
    return calls


def test_no_sink_means_no_work_and_no_failure() -> None:
    """An embedder that never binds the bridge sees identical behaviour."""
    agent = _agent()
    # Deliberately not calling set_steering_applied_sink.
    agent._report_steering_applied([SteeringInput("a", "r1")], ["a"])


def test_applied_entries_carry_the_id_that_queued_them() -> None:
    agent = _agent()
    calls = _capture(agent)

    drained = [SteeringInput("use async", "req-1"), SteeringInput("and retry", "req-2")]
    agent._report_steering_applied(drained, ["use async", "and retry"])

    applied, dropped = calls[0]
    assert applied == [
        {"id": "req-1", "text": "use async"},
        {"id": "req-2", "text": "and retry"},
    ]
    assert dropped == []


def test_a_rail_dropping_a_part_is_reported_as_dropped() -> None:
    """Without this the client's bubble stays pending forever."""
    agent = _agent()
    calls = _capture(agent)

    drained = [SteeringInput("keep me", "req-1"), SteeringInput("drop me", "req-2")]
    # A rail removed the second part during admission.
    agent._report_steering_applied(drained, ["keep me"])

    applied, dropped = calls[0]
    assert applied == [{"id": "req-1", "text": "keep me"}]
    assert dropped == ["req-2"]


def test_reordering_by_a_rail_does_not_scramble_ids() -> None:
    """Rails may reorder the batch; correlation follows content, not position."""
    agent = _agent()
    calls = _capture(agent)

    drained = [SteeringInput("first", "req-1"), SteeringInput("second", "req-2")]
    agent._report_steering_applied(drained, ["second", "first"])

    applied, _ = calls[0]
    assert applied == [
        {"id": "req-2", "text": "second"},
        {"id": "req-1", "text": "first"},
    ]


def test_duplicate_text_resolves_in_queue_order() -> None:
    """Two steers with identical text are matched oldest first.

    Content is the only handle that survives admission, so identical text is
    genuinely ambiguous. Queue order is the only tie-break anyone could mean.
    """
    agent = _agent()
    calls = _capture(agent)

    drained = [SteeringInput("again", "req-1"), SteeringInput("again", "req-2")]
    agent._report_steering_applied(drained, ["again"])

    applied, dropped = calls[0]
    assert applied == [{"id": "req-1", "text": "again"}]
    assert dropped == ["req-2"]


def test_rail_pushed_steering_has_no_id_and_is_never_reported_dropped() -> None:
    """Rails steer with nothing to correlate, so a drop has nobody to tell."""
    agent = _agent()
    calls = _capture(agent)

    drained = [SteeringInput("from a rail"), SteeringInput("from a client", "req-1")]
    agent._report_steering_applied(drained, [])

    applied, dropped = calls[0]
    assert applied == []
    assert dropped == ["req-1"]


def test_text_a_rail_synthesised_is_reported_without_an_id() -> None:
    """The event describes what the model saw, even when no input carried it."""
    agent = _agent()
    calls = _capture(agent)

    drained = [SteeringInput("original", "req-1")]
    agent._report_steering_applied(drained, ["original", "rail addendum"])

    applied, dropped = calls[0]
    assert applied == [
        {"id": "req-1", "text": "original"},
        {"id": None, "text": "rail addendum"},
    ]
    assert dropped == []


def test_a_failing_sink_never_breaks_the_turn() -> None:
    """Observability is not allowed to take down the round it observes."""
    agent = _agent()

    def _boom(applied: List[Dict[str, Any]], dropped: List[str]) -> None:
        raise RuntimeError("consumer exploded")

    agent.set_steering_applied_sink(_boom)
    agent._report_steering_applied([SteeringInput("x", "req-1")], ["x"])


def test_event_payload_shape() -> None:
    event = InteractionEvent.steer_applied(
        applied=[{"id": "req-1", "text": "use async"}], dropped=["req-2"]
    )
    assert event.type is InteractionEventType.STEER_APPLIED
    assert event.payload == {
        "applied": [{"id": "req-1", "text": "use async"}],
        "dropped": ["req-2"],
    }
    # It travels an output stream, like every other interaction event.
    assert event.to_output_schema().type == "steer.applied"


def test_queue_coerces_both_producer_shapes() -> None:
    """Hosts push envelopes, rails push bare strings, both drain the same."""
    queues = LoopQueues()
    queues.push_steer(SteeringInput("with id", "req-1"))
    queues.push_steer("bare string")

    drained = queues.drain_steering()
    assert [(d.text, d.id) for d in drained] == [("with id", "req-1"), ("bare string", None)]
