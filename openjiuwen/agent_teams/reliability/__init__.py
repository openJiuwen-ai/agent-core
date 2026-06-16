# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reliability framework for agent teams.

Active monitoring + remediation for unhealthy member states: lifecycle
signals are collected via a deepagent rail, detectors judge anomalies, and a
tiered policy routes each anomaly to the leader (LLM decision), a reversible
automated local steer, or the user.

``ReliabilityConfig`` is the user-facing entry point (hang it off
``TeamAgentSpec.reliability``). Internal wiring (rail / handler / factory) is
imported directly from submodules by the configurator and dispatcher.
"""

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.config import ReliabilityConfig
from openjiuwen.agent_teams.reliability.remediation.action import RemediationAction
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.agent_teams.reliability.window import SlidingWindowCounter, stable_call_hash

__all__ = [
    "Anomaly",
    "AnomalyKind",
    "ReliabilityConfig",
    "RemediationAction",
    "Severity",
    "Signal",
    "SignalKind",
    "SlidingWindowCounter",
    "stable_call_hash",
]
