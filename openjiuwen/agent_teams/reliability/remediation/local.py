# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local automated remediation: reversible self-steering, intensity-limited."""

from __future__ import annotations

import time
from typing import Callable

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind
from openjiuwen.agent_teams.reliability.remediation.action import RemediationAction
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.reliability.window import SlidingWindowCounter

# Model-group verdicts: the member's model is unavailable or was already
# switched by the router, so a self-correction nudge cannot help.
_NO_STEER_KINDS = frozenset({AnomalyKind.MODEL_UNAVAILABLE, AnomalyKind.MODEL_ROUTE_NOTICE})


class LocalAutoRemediator:
    """Reversible in-process self-correction, rate-limited by restart intensity.

    Lives in the member process, driven by the rail. When the policy calls for
    ``LOCAL_STEER`` on an anomaly, returns a non-destructive correction message
    for the rail to push into the steering queue — but only within the
    restart-intensity budget (``intensity`` actions per ``period_seconds``), so
    automated nudging never becomes a storm. Past the budget it returns None,
    deferring entirely to the leader / user path. Destructive actions are never
    taken here.
    """

    def __init__(
        self,
        policy: RemediationPolicy,
        *,
        intensity: int = 5,
        period_seconds: float = 60.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._policy = policy
        self._intensity = intensity
        self._now = now
        self._window = SlidingWindowCounter(period_seconds)

    def steer_message(self, anomaly: Anomaly) -> str | None:
        """Return a self-correction steer message, or None if not allowed / over budget."""
        if anomaly.kind in _NO_STEER_KINDS:
            return None
        if RemediationAction.LOCAL_STEER not in self._policy.actions_for(anomaly.severity):
            return None
        used = self._window.add(self._now())
        if used > self._intensity:
            return None
        return t("reliability.steer_self_correct", kind=anomaly.kind.value, summary=anomaly.summary)

    def reset(self) -> None:
        """Reset the restart-intensity window."""
        self._window.reset()
