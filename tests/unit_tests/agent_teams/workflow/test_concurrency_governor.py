# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for Swarmflow ConcurrencyGovernor."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.workflow.concurrency import (
    ConcurrencyGovernor,
    ConcurrencyLimits,
    RunAgentAdmission,
    validate_swarmflow_concurrency,
)
from openjiuwen.agent_teams.workflow.engine.backends import MockBackend
from openjiuwen.agent_teams.workflow.engine.cap import resolve_agents_per_run_cap
from openjiuwen.agent_teams.workflow.engine.runner import run_workflow
from openjiuwen.core.common.exception.errors import ValidationError


@pytest.mark.asyncio
async def test_admit_workflow_rejects_at_max():
    gov = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=2, max_agents_total=8),
        agents_per_run_cap=2,
    )
    assert await gov.admit_workflow() is not None
    assert await gov.admit_workflow() is not None
    assert await gov.admit_workflow() is None


@pytest.mark.asyncio
async def test_release_allows_re_admit():
    gov = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=1, max_agents_total=4),
        agents_per_run_cap=2,
    )
    adm = await gov.admit_workflow()
    assert adm is not None
    await gov.release_workflow(adm.ticket)
    assert await gov.admit_workflow() is not None


@pytest.mark.asyncio
async def test_global_sem_limits_across_runs():
    limits = ConcurrencyLimits(max_workflows=2, agents_per_run=2, max_agents_total=3)
    l2 = validate_swarmflow_concurrency(limits)
    gov = ConcurrencyGovernor(limits, agents_per_run_cap=l2)
    gates = []
    for _ in range(2):
        adm = await gov.admit_workflow()
        assert adm is not None
        gates.append(adm.agent_gate)
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def hold(gate: RunAgentAdmission) -> None:
        nonlocal active, peak
        async with gate.acquire():
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.05)
            async with lock:
                active -= 1

    await asyncio.gather(hold(gates[0]), hold(gates[1]), hold(gates[0]), hold(gates[1]))
    assert peak <= 3


def test_build_rejects_l3_below_l2():
    with pytest.raises(ValidationError, match="max_agents_total"):
      validate_swarmflow_concurrency(
          ConcurrencyLimits(agents_per_run=32, max_agents_total=16),
      )


def test_build_rejects_l3_below_l1():
    with pytest.raises(ValidationError, match="must be >= max_workflows"):
        validate_swarmflow_concurrency(
            ConcurrencyLimits(max_workflows=16, max_agents_total=8, agents_per_run=4),
        )


def test_resolve_agents_per_run_cap_matches_runtime():
    from openjiuwen.agent_teams.workflow.engine.runtime import Runtime
    from openjiuwen.agent_teams.workflow.engine.journal import Journal

    rt = Runtime(backend=MockBackend(), journal=Journal())
    rt.cap_override = 7
    assert resolve_agents_per_run_cap(None, cap_override=7) == rt.make_cap()


@pytest.mark.asyncio
async def test_release_on_cancelled_error():
    gov = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=1, max_agents_total=4),
        agents_per_run_cap=2,
    )
    adm = await gov.admit_workflow()
    assert adm is not None

    async def flaky() -> None:
        await gov.release_workflow(adm.ticket)
        raise asyncio.CancelledError()

    task = asyncio.create_task(flaky())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await gov.admit_workflow() is not None


def test_engine_default_admission_matches_legacy(tmp_path):
    script = tmp_path / "one.py"
    script.write_text(
        'from swarmflow import agent\n'
        'META = {"name": "t", "phases": []}\n'
        'async def run(args):\n'
        '    return await agent("hi", label="w")\n',
        encoding="utf-8",
    )
    result = asyncio.run(run_workflow(str(script), backend=MockBackend(), cap=1))
    assert result is not None


@pytest.mark.asyncio
async def test_agent_gate_limits_per_run_parallelism(tmp_path):
    """agents_per_run=2 caps concurrent backend.run inside parallel(5)."""
    script = tmp_path / "parallel.py"
    script.write_text(
        "from swarmflow import agent, parallel\n"
        "META = {'name': 'par', 'phases': []}\n"
        "async def run(args):\n"
        "    async def one(i):\n"
        "        return await agent(f'prompt-{i}', label=f'w{i}')\n"
        "    return await parallel([lambda i=i: one(i) for i in range(5)])\n",
        encoding="utf-8",
    )
    limits = ConcurrencyLimits(max_workflows=1, agents_per_run=2, max_agents_total=8)
    l2 = validate_swarmflow_concurrency(limits)
    gov = ConcurrencyGovernor(limits, agents_per_run_cap=l2)
    adm = await gov.admit_workflow()
    assert adm is not None

    active = 0
    peak = 0
    lock = asyncio.Lock()

    class _CountingBackend(MockBackend):
        async def run(self, prompt, opts, schema_json=None):  # type: ignore[override]
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            try:
                return await super().run(prompt, opts, schema_json)
            finally:
                async with lock:
                    active -= 1

    await run_workflow(
        str(script),
        backend=_CountingBackend(),
        agent_gate=adm.agent_gate,
    )
    await gov.release_workflow(adm.ticket)
    assert peak <= 2


@pytest.mark.asyncio
async def test_release_is_idempotent_for_same_ticket():
    """A second release of the same ticket is a no-op — it cannot over-admit L1."""
    gov = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=2, max_agents_total=8),
        agents_per_run_cap=2,
    )
    adm = await gov.admit_workflow()
    assert adm is not None
    await gov.release_workflow(adm.ticket)
    # Repeating the release on the same ticket must not free a second slot.
    await gov.release_workflow(adm.ticket)
    assert gov.snapshot().active_workflows == 0
    # Still capped at max_workflows — no over-admission from the double release.
    a1 = await gov.admit_workflow()
    a2 = await gov.admit_workflow()
    assert a1 is not None and a2 is not None
    assert await gov.admit_workflow() is None


@pytest.mark.asyncio
async def test_release_unknown_ticket_is_noop():
    """Releasing a ticket that was never admitted does not free an L1 slot."""
    gov = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=2, max_agents_total=8),
        agents_per_run_cap=2,
    )
    adm = await gov.admit_workflow()
    assert adm is not None
    # An unadmitted ticket is a no-op: the one admitted slot stays occupied.
    await gov.release_workflow(99999)
    assert gov.snapshot().active_workflows == 1
