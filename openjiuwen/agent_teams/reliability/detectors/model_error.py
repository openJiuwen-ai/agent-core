# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model-call (including streaming) error detector."""

from __future__ import annotations

import time
from typing import Callable

from openjiuwen.agent_teams.reliability.anomaly import AnomalyKind
from openjiuwen.agent_teams.reliability.detectors.base import ErrorBurstDetector
from openjiuwen.agent_teams.reliability.signals import SignalKind


class ModelStreamErrorDetector(ErrorBurstDetector):
    """Detect frequent or consecutive model-call errors.

    There is no per-chunk streaming hook, so this operates at model-call
    granularity: a MODEL_EXCEPTION raised by the call, or an error surfaced on
    AFTER_MODEL_CALL that the rail re-emits as a MODEL_EXCEPTION signal.
    Mid-stream single-chunk errors are not observable here. An
    AFTER_MODEL_CALL (success) clears the consecutive streak.
    """

    def __init__(
        self,
        *,
        window_seconds: float = 120.0,
        rate_threshold: int = 3,
        consecutive_threshold: int = 2,
        now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            name="model_error",
            error_kind=SignalKind.MODEL_EXCEPTION,
            reset_kind=SignalKind.AFTER_MODEL_CALL,
            anomaly_kind=AnomalyKind.MODEL_ERROR,
            window_seconds=window_seconds,
            rate_threshold=rate_threshold,
            consecutive_threshold=consecutive_threshold,
            now=now,
        )
