# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Remediation action taxonomy."""

from __future__ import annotations

from enum import Enum


class RemediationAction(str, Enum):
    """What to do about an anomaly, mapped from severity by the policy.

    ``LOCAL_STEER`` is the only automated action and is strictly reversible
    (a steering nudge to the member's own LLM, rate-limited by restart
    intensity). Destructive actions (stop member, cancel task, spawn) are
    intentionally absent: those are the leader's call via existing team tools.
    """

    OBSERVE_ONLY = "observe_only"
    REPORT_LEADER = "report_leader"
    LOCAL_STEER = "local_steer"
    ESCALATE_USER = "escalate_user"
