# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Turn timeout tests for SubagentInstance."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.ids import new_task_id
from openjiuwen.harness.subagent_runtime.models import SubagentStatusKind, UserInputOp
from tests.unit_tests.harness.subagent_runtime.test_instance import (
    MockAgent,
    MockSession,
    _make_instance,
)


@pytest.mark.asyncio
async def test_turn_timeout_sets_errored_with_code() -> None:
    agent = MockAgent(delay_s=0.2)
    instance, _, _ = _make_instance(
        agent=agent,
        session=MockSession(),
        semaphore=asyncio.Semaphore(5),
    )
    instance._turn_timeout_s = 0.05
    await instance.start_worker()

    await instance.enqueue(UserInputOp(query="slow", task_id=new_task_id()))
    await asyncio.sleep(0.3)

    status = instance.agent_status()
    assert status.kind is SubagentStatusKind.ERRORED
    assert status.error_code == "TIMEOUT"
    assert instance.is_closed() is False


@pytest.mark.asyncio
async def test_turn_timeout_disabled_when_none_or_zero() -> None:
    agent = MockAgent(delay_s=0.15)
    instance, _, _ = _make_instance(
        agent=agent,
        session=MockSession(),
        semaphore=asyncio.Semaphore(5),
    )
    instance._turn_timeout_s = None
    await instance.start_worker()

    await instance.enqueue(UserInputOp(query="slow", task_id=new_task_id()))
    await asyncio.sleep(0.25)

    assert instance.agent_status().kind is SubagentStatusKind.COMPLETED


@pytest.mark.asyncio
async def test_turn_timeout_does_not_override_completed_status() -> None:
    agent = MockAgent(delay_s=0.0)
    instance, _, _ = _make_instance(
        agent=agent,
        session=MockSession(),
        semaphore=asyncio.Semaphore(5),
    )
    instance._turn_timeout_s = 0.05
    await instance.start_worker()

    await instance.enqueue(UserInputOp(query="fast", task_id=new_task_id()))
    await asyncio.sleep(0.05)

    assert instance.agent_status().kind is SubagentStatusKind.COMPLETED
    assert instance.agent_status().error_code is None
