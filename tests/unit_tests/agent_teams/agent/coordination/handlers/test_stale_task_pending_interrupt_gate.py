# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the ``has_pending_interrupt`` gate in the stale-task sweep.

When a teammate waits on user-mediated approval a pending interrupt hangs
in the round while the harness reports IDLE; without a gate the stale-task
sweep would misreport the member as stuck past ``stale_claim_idle_timeout``.
``_check_stale_claimed_tasks`` now returns early when the round has a
pending interrupt, so approval waits (which can hang >>150s when a client
is present) are not nudged or escalated. With no pending interrupt the
existing urge/report behavior is unchanged.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.agent_teams.agent.coordination.handlers.stale_task import (
    ScheduledStaleTaskHandler,
    StaleTaskHandler,
)


def test_no_stale_report_when_pending_interrupt() -> None:
    """有 pending interrupt 时不催促/不升级卡死上报（user-mediated 必需）。"""
    h = StaleTaskHandler.__new__(StaleTaskHandler)
    h._round = MagicMock()
    h._round.has_pending_interrupt = MagicMock(return_value=True)
    h._round.idle_seconds = MagicMock(return_value=99999)
    h._infra = MagicMock()
    h._last_stale_nudge = {}
    h._stale_claim_streak = {}
    h._escalated_claims = set()
    h._idle_claim_seconds = 600
    h._STALE_CLAIM_ESCALATE_STREAK = 3
    h._self_nudge_idle_claim = AsyncMock()
    h._escalate_stale_claim = AsyncMock()
    h._own_stalled_tasks = AsyncMock(return_value=[MagicMock(task_id="t1")])

    asyncio.run(h._check_stale_claimed_tasks())

    h._self_nudge_idle_claim.assert_not_awaited()
    h._escalate_stale_claim.assert_not_awaited()
    assert h._escalated_claims == set()


def test_stale_report_when_no_pending_interrupt() -> None:
    """无 pending + idle>600 → 维持现状催促。"""
    h = StaleTaskHandler.__new__(StaleTaskHandler)
    h._round = MagicMock()
    h._round.has_pending_interrupt = MagicMock(return_value=False)
    h._round.idle_seconds = MagicMock(return_value=99999)
    h._infra = MagicMock()
    h._last_stale_nudge = {}
    h._stale_claim_streak = {}
    h._escalated_claims = set()
    h._idle_claim_seconds = 600
    h._STALE_CLAIM_ESCALATE_STREAK = 3
    h._self_nudge_idle_claim = AsyncMock()
    h._own_stalled_tasks = AsyncMock(return_value=[MagicMock(task_id="t1", updated_at=0)])

    asyncio.run(h._check_stale_claimed_tasks())

    h._self_nudge_idle_claim.assert_awaited()  # 现状催促不变


def test_no_stale_report_when_pending_interrupt_scheduled() -> None:
    """Scheduled 模式同款 gate（pre-F_65 updated_at 计时）：pending interrupt 时不催促。

    ScheduledStaleTaskHandler._check_stale_claimed_tasks 钉 pre-F_65 的 task.updated_at
    计时（活跃集含 IN_REVIEW），不经 _own_stalled_tasks / idle_seconds；但 gate 与基类
    同款——relevant 非空后、updated_at 计时循环前有 has_pending_interrupt 闸门，挂着 pending
    interrupt（如 user-mediated 审批等用户）时直接 return，不进催促循环。
    """
    h = ScheduledStaleTaskHandler.__new__(ScheduledStaleTaskHandler)
    h._blueprint = MagicMock(member_name="t1")
    task = MagicMock(task_id="t1", assignee="t1", updated_at=0)
    task_manager = MagicMock()
    task_manager.list_tasks = AsyncMock(return_value=[task])
    h._infra = MagicMock(task_manager=task_manager)
    h._round = MagicMock()
    h._round.has_pending_interrupt = MagicMock(return_value=True)
    h._last_stale_nudge = {}
    h._self_nudge_stale_claim = AsyncMock()

    asyncio.run(h._check_stale_claimed_tasks())

    h._self_nudge_stale_claim.assert_not_awaited()
