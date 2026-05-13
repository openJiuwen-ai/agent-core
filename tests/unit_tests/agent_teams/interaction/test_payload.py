# coding: utf-8
"""Tests for interaction payload types and DeliverResult."""

from __future__ import annotations

import dataclasses

import pytest

from openjiuwen.agent_teams.interaction.payload import (
    DeliverResult,
    GodViewMessage,
    HumanAgentMessage,
    OperatorMessage,
)


def test_deliver_result_success_carries_message_id():
    result = DeliverResult.success("msg-1")
    assert result.ok is True
    assert result.message_id == "msg-1"
    assert result.reason is None


def test_deliver_result_success_allows_omitted_id():
    result = DeliverResult.success()
    assert result.ok is True
    assert result.message_id is None


def test_deliver_result_failure_carries_reason():
    result = DeliverResult.failure("send_failed")
    assert result.ok is False
    assert result.reason == "send_failed"
    assert result.message_id is None


def test_deliver_result_is_frozen():
    result = DeliverResult.success("msg-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.ok = False


def test_god_view_message_carries_body_only():
    payload = GodViewMessage(body="hello")
    assert payload.body == "hello"
    assert dataclasses.is_dataclass(payload)


def test_operator_message_defaults_target_to_none_for_broadcast():
    payload = OperatorMessage(body="ping")
    assert payload.target is None
    assert payload.body == "ping"


def test_operator_message_targets_specific_member():
    payload = OperatorMessage(body="hi", target="dev-1")
    assert payload.target == "dev-1"


def test_human_agent_message_requires_sender():
    payload = HumanAgentMessage(body="ack", sender="human_pm")
    assert payload.sender == "human_pm"
    assert payload.target is None


def test_human_agent_message_supports_target():
    payload = HumanAgentMessage(body="ack", sender="human_pm", target="leader")
    assert payload.target == "leader"


def test_payloads_are_frozen():
    payload = GodViewMessage(body="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload.body = "y"


def test_payload_isinstance_dispatch_works():
    payloads = [
        GodViewMessage(body="g"),
        OperatorMessage(body="o"),
        HumanAgentMessage(body="h", sender="ha"),
    ]
    matched = []
    for p in payloads:
        if isinstance(p, GodViewMessage):
            matched.append("god")
        elif isinstance(p, HumanAgentMessage):
            matched.append("human")
        elif isinstance(p, OperatorMessage):
            matched.append("operator")
    assert matched == ["god", "operator", "human"]
