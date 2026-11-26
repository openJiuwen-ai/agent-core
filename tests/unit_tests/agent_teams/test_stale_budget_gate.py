# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""stale sweeps must stop once the shared round budget is spent.

After total-budget exhaustion the leader's wrap-up sequence ends member
rounds and repools their tasks. A self-healing nudge at that point only
restarts rounds on a closed ledger, so all three sweeps (member
stale-claim, leader stale-pending, leader stale-inprogress) gate on the
ledger probe before touching the task board.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers import StaleTaskHandler
from openjiuwen.agent_teams.schema.team import TeamRole


def _handler(probe):
    host = SimpleNamespace(
        idle_seconds=lambda: 9999.0,
        is_agent_running=lambda: False,
    )
    if probe is not None:
        host.is_round_budget_exhausted = probe
    task_manager = SimpleNamespace()
    blueprint = SimpleNamespace(
        member_name="leader",
        role=TeamRole.LEADER,
        spec=SimpleNamespace(
            stale_claim_idle_timeout=120,
            stale_pending_idle_timeout=120,
            stale_inprogress_silence_timeout=240,
            stale_inprogress_age_timeout=600,
        ),
    )
    infra = SimpleNamespace(task_manager=task_manager, team_backend=None)
    return StaleTaskHandler(host, blueprint, infra, SimpleNamespace())


def _boom(*_args, **_kwargs):
    raise AssertionError("sweep must not reach the task board")


def test_probe_without_capability_is_fail_open():
    handler = _handler(None)
    assert handler._budget_exhausted() is False


def test_truthy_non_bool_probe_is_fail_open():
    """魔法桩属性/非 bool 返回不得把自愈扫描整体静默关掉。"""
    handler = _handler(lambda: object())
    assert handler._budget_exhausted() is False


def test_probe_failure_is_fail_open():
    def _raise():
        raise RuntimeError("ledger exploded")

    handler = _handler(_raise)
    assert handler._budget_exhausted() is False


@pytest.mark.asyncio
async def test_stale_claim_skipped_when_budget_exhausted(monkeypatch):
    handler = _handler(lambda: True)

    async def _should_not_run(_task_manager):
        raise AssertionError("stale-claim must not sweep after exhaustion")

    monkeypatch.setattr(handler, "_own_stalled_tasks", _should_not_run)
    await handler._check_stale_claimed_tasks()


@pytest.mark.asyncio
async def test_stale_pending_skipped_when_budget_exhausted():
    handler = _handler(lambda: True)
    handler._infra.task_manager = SimpleNamespace(list_tasks=_boom)
    await handler._check_stale_pending_tasks()


@pytest.mark.asyncio
async def test_stale_inprogress_skipped_when_budget_exhausted(monkeypatch):
    handler = _handler(lambda: True)

    async def _should_not_run():
        raise AssertionError("stale-inprogress must not probe after exhaustion")

    monkeypatch.setattr(handler, "_load_inprogress_sweep_inputs", _should_not_run)
    await handler._check_stale_inprogress_tasks()
