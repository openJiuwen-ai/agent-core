# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""NativeHarness pause aborts in-flight LLM streams promptly."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.agent_teams.harness.state import ActiveRound


@pytest.mark.asyncio
async def test_hard_cancel_requests_abort_stream_before_cancel_task() -> None:
    cancel_task = AsyncMock()
    harness = SimpleNamespace(
        loop_controller=SimpleNamespace(
            task_scheduler=SimpleNamespace(cancel_task=cancel_task),
        ),
        _cancel_round_task=AsyncMock(),
    )

    abort = MagicMock()
    ctx = SimpleNamespace(request_abort_stream=abort)
    active = ActiveRound(
        round_id=1,
        task_id="t1",
        original_query="q",
        deep_agent=MagicMock(),
        task=MagicMock(done=MagicMock(return_value=True)),
        steering_queue=MagicMock(),
        model_call_in_flight=True,
        model_call_ctx=ctx,
    )

    await NativeHarness._hard_cancel_round(harness, active)

    abort.assert_called_once()
    cancel_task.assert_awaited_once_with("t1")
    harness._cancel_round_task.assert_awaited_once_with(active)


@pytest.mark.asyncio
async def test_force_kill_aborts_llm_and_bounds_wait() -> None:
    from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle

    abort = MagicMock()
    ctx = SimpleNamespace(request_abort_stream=abort)
    active = SimpleNamespace(model_call_ctx=ctx)
    harness = SimpleNamespace(active_round=active)
    agent = SimpleNamespace(resources=SimpleNamespace(harness=harness))

    async def _slow() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(_slow())
    handle = InProcessSpawnHandle(_task=task, agent_ref=agent)
    await handle.force_kill()

    abort.assert_called_once()
    assert task.cancelled() or task.done()
