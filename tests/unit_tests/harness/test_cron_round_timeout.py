# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cron runs get a bounded execution deadline.

The host leaves ``completion_timeout`` unset for ordinary tasks (it would
silently truncate long user work), but a scheduled run has no operator to
stop it. ``resolve_round_timeout`` therefore supplies / enforces the cron
ceiling while leaving every other run kind untouched.
"""
# pylint: disable=protected-access
from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import RunKind
from openjiuwen.harness.deep_agent import (
    CRON_ROUND_TIMEOUT_SECONDS,
    resolve_round_timeout,
)


def test_cron_run_defaults_to_ten_minutes() -> None:
    assert CRON_ROUND_TIMEOUT_SECONDS == 600.0
    assert resolve_round_timeout(None, RunKind.CRON) == 600.0
    assert resolve_round_timeout(None, "cron") == 600.0


def test_cron_run_caps_configured_timeout() -> None:
    assert resolve_round_timeout(6000.0, RunKind.CRON) == 600.0
    assert resolve_round_timeout(120.0, RunKind.CRON) == 120.0


def test_non_cron_runs_keep_configured_value() -> None:
    assert resolve_round_timeout(None, RunKind.NORMAL) is None
    assert resolve_round_timeout(None, "heartbeat") is None
    assert resolve_round_timeout(None, None) is None
    assert resolve_round_timeout(6000.0, RunKind.NORMAL) == 6000.0
