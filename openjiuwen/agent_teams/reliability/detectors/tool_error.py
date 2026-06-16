# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool-call error-rate detector."""

from __future__ import annotations

import time
from typing import Callable

from openjiuwen.agent_teams.reliability.anomaly import AnomalyKind
from openjiuwen.agent_teams.reliability.detectors.base import ErrorBurstDetector
from openjiuwen.agent_teams.reliability.signals import SignalKind


class ToolErrorRateDetector(ErrorBurstDetector):
    """Detect frequent or consecutive tool-call failures.

    Errors arrive as TOOL_EXCEPTION signals; an AFTER_TOOL_CALL (success)
    clears the consecutive streak.
    """

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        rate_threshold: int = 5,
        consecutive_threshold: int = 3,
        now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            name="tool_error_rate",
            error_kind=SignalKind.TOOL_EXCEPTION,
            reset_kind=SignalKind.AFTER_TOOL_CALL,
            anomaly_kind=AnomalyKind.TOOL_ERROR_RATE,
            window_seconds=window_seconds,
            rate_threshold=rate_threshold,
            consecutive_threshold=consecutive_threshold,
            now=now,
        )
