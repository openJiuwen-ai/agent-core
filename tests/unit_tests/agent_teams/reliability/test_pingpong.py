# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the team-level ping-pong detector."""

from openjiuwen.agent_teams.reliability.anomaly import AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.detectors.pingpong import PingPongDetector
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind


def _msg(sender: str, recipient: str) -> Signal:
    return Signal(kind=SignalKind.MESSAGE, member_name=sender, peer_member=recipient)


def test_pingpong_fires_on_sustained_back_and_forth():
    det = PingPongDetector(min_volleys=6)
    volleys = [("a", "b"), ("b", "a"), ("a", "b"), ("b", "a"), ("a", "b")]
    for sender, recipient in volleys:
        assert det.observe(_msg(sender, recipient)) is None
    anomaly = det.observe(_msg("b", "a"))
    assert anomaly is not None
    assert anomaly.kind == AnomalyKind.PING_PONG
    assert anomaly.severity == Severity.MEDIUM
    assert anomaly.peer_member == "a"


def test_pingpong_resets_on_third_party():
    det = PingPongDetector(min_volleys=4)
    det.observe(_msg("a", "b"))
    det.observe(_msg("b", "a"))
    det.observe(_msg("a", "b"))
    assert det.observe(_msg("a", "c")) is None
    det.observe(_msg("c", "a"))
    det.observe(_msg("a", "c"))
    assert det.observe(_msg("a", "b")) is None


def test_pingpong_escalates_to_high():
    det = PingPongDetector(min_volleys=3)
    volleys = [("a", "b"), ("b", "a"), ("a", "b"), ("b", "a"), ("a", "b"), ("b", "a")]
    severities = [r.severity for r in (det.observe(_msg(s, t)) for s, t in volleys) if r is not None]
    assert Severity.MEDIUM in severities
    assert Severity.HIGH in severities


def test_pingpong_ignores_non_message_signal():
    det = PingPongDetector()
    assert det.observe(Signal(kind=SignalKind.AFTER_TOOL_CALL, member_name="a")) is None
