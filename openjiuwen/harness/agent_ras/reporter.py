# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional anomaly sink protocol between Monitor and external reporters.

This module defines only the typing contract. A single-Agent Monitor may pass
``reporter=None`` and skip external reporting.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from openjiuwen.harness.agent_ras.models import Anomaly


@runtime_checkable
class AnomalyReporter(Protocol):
    """Structural protocol: ``async report(anomaly)`` for optional sinks.

    Implementations may also expose ``bind_ctx`` and ``handles_all_anomalies``.
    """

    async def report(self, anomaly: Anomaly) -> None:
        """Forward one detected anomaly to the configured sink."""
        ...
