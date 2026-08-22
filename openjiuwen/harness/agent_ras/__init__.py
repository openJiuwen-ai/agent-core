# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stable public API for in-process Agent RAS."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from openjiuwen.harness.agent_ras.config import AgentRASConfig
from openjiuwen.harness.agent_ras.detectors.base import Detector
from openjiuwen.harness.agent_ras.models import (
    Anomaly,
    AnomalyKind,
    Severity,
    Signal,
    SignalKind,
)
from openjiuwen.harness.agent_ras.recovery.engine import RecoveryAction

if TYPE_CHECKING:
    from openjiuwen.harness.agent_ras.factory import build_agent_ras_rail
    from openjiuwen.harness.rails.agent_ras_rail import AgentRASRail

__all__ = [
    "AgentRASConfig",
    "AgentRASRail",
    "Anomaly",
    "AnomalyKind",
    "Detector",
    "RecoveryAction",
    "Severity",
    "Signal",
    "SignalKind",
    "build_agent_ras_rail",
]

# AgentRASRail / build_agent_ras_rail stay lazy: importing them eagerly in this
# package __init__ re-enters agent_ras_rail while it is still loading via
# agent_ras_rail → agent_ras.config → agent_ras.__init__.
_LAZY_EXPORTS = {
    "AgentRASRail": ("openjiuwen.harness.rails.agent_ras_rail", "AgentRASRail"),
    "build_agent_ras_rail": (
        "openjiuwen.harness.agent_ras.factory",
        "build_agent_ras_rail",
    ),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, symbol_name = target
    value = getattr(import_module(module_name), symbol_name)
    globals()[name] = value
    return value
