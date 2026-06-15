# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio

import pytest

from openjiuwen.core.single_agent.tool_batch_concurrency import (
    ToolBatchConcurrencyController,
    ToolBatchConcurrencyPolicy,
    ToolConcurrencyRule,
)


class FakeToolCall:
    def __init__(self, name: str, call_id: str = "id"):
        self.name = name
        self.id = call_id


class FakeAgentSession:
    def __init__(self, session_id: str):
        self._session_id = session_id

    def get_session_id(self) -> str:
        return self._session_id


class FakeBaseSession:
    def __init__(self, session_id: str):
        self._session_id = session_id

    def session_id(self) -> str:
        return self._session_id


def test_session_label_resolves_get_session_id():
    from openjiuwen.core.single_agent.ability_manager import AbilityManager

    assert AbilityManager._session_label(
        FakeAgentSession("officeclaw_abc123")
    ) == "officeclaw_abc123"


def test_session_label_resolves_session_id_method():
    from openjiuwen.core.single_agent.ability_manager import AbilityManager

    assert AbilityManager._session_label(
        FakeBaseSession("wf-session-42")
    ) == "wf-session-42"


def test_session_label_none_returns_dash():
    from openjiuwen.core.single_agent.ability_manager import AbilityManager

    assert AbilityManager._session_label(None) == "-"


@pytest.mark.asyncio
async def test_gather_with_limit_caps_parallelism():
    peak = 0
    cur = 0
    lock = asyncio.Lock()

    async def run_one(tc: FakeToolCall) -> str:
        nonlocal peak, cur
        async with lock:
            cur += 1
            peak = max(peak, cur)
        await asyncio.sleep(0.05)
        async with lock:
            cur -= 1
        return tc.name

    policy = ToolBatchConcurrencyPolicy(
        tools={"spawn_subagent": ToolConcurrencyRule(limit=3)},
    )
    controller = ToolBatchConcurrencyController(lambda: policy)
    calls = [FakeToolCall("spawn_subagent", f"s{i}") for i in range(8)]
    results = await controller.gather_with_limit(calls, run_one)
    assert len(results) == 8
    assert peak <= 3


@pytest.mark.asyncio
async def test_non_limited_tools_not_capped():
    peak = 0
    cur = 0
    lock = asyncio.Lock()

    async def run_one(tc: FakeToolCall) -> str:
        nonlocal peak, cur
        async with lock:
            cur += 1
            peak = max(peak, cur)
        await asyncio.sleep(0.02)
        async with lock:
            cur -= 1
        return tc.name

    policy = ToolBatchConcurrencyPolicy(
        tools={"spawn_subagent": ToolConcurrencyRule(limit=2)},
    )
    controller = ToolBatchConcurrencyController(lambda: policy)
    calls = [FakeToolCall("read_file", f"r{i}") for i in range(6)]
    await controller.gather_with_limit(calls, run_one, policy=policy)
    assert peak == 6


@pytest.mark.asyncio
async def test_single_limited_call_still_capped_by_policy():
    """Pre-built semaphores apply even when only one call is dispatched."""
    policy = ToolBatchConcurrencyPolicy(
        tools={"spawn_subagent": ToolConcurrencyRule(limit=1)},
    )
    controller = ToolBatchConcurrencyController(lambda: policy)
    entered = False

    async def run_one(tc: FakeToolCall) -> str:
        nonlocal entered
        entered = controller.active()
        return tc.name

    calls = [FakeToolCall("spawn_subagent", "only")]
    await controller.gather_with_limit(calls, run_one)
    assert entered is True


@pytest.mark.asyncio
async def test_batch_scope_is_reentrant():
    policy = ToolBatchConcurrencyPolicy(
        tools={"spawn_subagent": ToolConcurrencyRule(limit=2)},
    )
    controller = ToolBatchConcurrencyController(lambda: policy)
    outer_active = False
    inner_active = False

    async with controller.batch_scope(session_id="s1"):
        outer_active = controller.active()
        async with controller.batch_scope(session_id="s1"):
            inner_active = controller.active()

    assert outer_active is True
    assert inner_active is True
    assert controller.active() is False


@pytest.mark.asyncio
async def test_staggered_create_task_inherits_scope():
    """Simulate StreamingToolExecutor create_task(execute_single) pattern."""
    peak = 0
    cur = 0
    lock = asyncio.Lock()

    policy = ToolBatchConcurrencyPolicy(
        tools={"spawn_subagent": ToolConcurrencyRule(limit=3)},
    )
    controller = ToolBatchConcurrencyController(lambda: policy)

    async def run_one(tc: FakeToolCall) -> str:
        nonlocal peak, cur

        async def _run() -> str:
            nonlocal peak, cur
            async with lock:
                cur += 1
                peak = max(peak, cur)
            await asyncio.sleep(0.05)
            async with lock:
                cur -= 1
            return tc.id

        return await controller.run_with_slot(tc, _run)

    calls = [FakeToolCall("spawn_subagent", f"s{i}") for i in range(8)]

    async with controller.batch_scope(session_id="stream"):
        tasks = [asyncio.create_task(run_one(tc)) for tc in calls]
        results = await asyncio.gather(*tasks)

    assert len(results) == 8
    assert peak <= 3
    assert controller.active() is False


@pytest.mark.asyncio
async def test_run_with_slot_noop_outside_scope():
    policy = ToolBatchConcurrencyPolicy(
        tools={"spawn_subagent": ToolConcurrencyRule(limit=1)},
    )
    controller = ToolBatchConcurrencyController(lambda: policy)
    tc = FakeToolCall("spawn_subagent", "x")

    async def _run() -> str:
        return "ok"

    assert controller.active() is False
    assert await controller.run_with_slot(tc, _run) == "ok"


def test_build_from_policy_skips_non_positive_limit():
    from openjiuwen.core.single_agent.tool_batch_concurrency import _BatchExecutionContext

    policy = ToolBatchConcurrencyPolicy(
        tools={
            "spawn_subagent": ToolConcurrencyRule(limit=2),
            "web_search": ToolConcurrencyRule(limit=0),
        },
    )
    ctx = _BatchExecutionContext.build_from_policy(
        session_id="s1",
        policy=policy,
    )
    assert ctx is not None
    assert "spawn_subagent" in ctx.semaphores
    assert "web_search" not in ctx.semaphores


@pytest.mark.asyncio
async def test_resume_style_batch_scope_caps_parallelism():
    """Simulate interrupt/resume: batch_scope wraps tool batch outside main loop."""
    peak = 0
    cur = 0
    lock = asyncio.Lock()

    async def run_one(tc: FakeToolCall) -> str:
        nonlocal peak, cur

        async def _run() -> str:
            nonlocal peak, cur
            async with lock:
                cur += 1
                peak = max(peak, cur)
            await asyncio.sleep(0.05)
            async with lock:
                cur -= 1
            return tc.id

        return await controller.run_with_slot(tc, _run)

    policy = ToolBatchConcurrencyPolicy(
        tools={"spawn_subagent": ToolConcurrencyRule(limit=2)},
    )
    controller = ToolBatchConcurrencyController(lambda: policy)
    calls = [FakeToolCall("spawn_subagent", f"r{i}") for i in range(6)]

    async with controller.batch_scope(session_id="resume"):
        results = await asyncio.gather(*(run_one(tc) for tc in calls))

    assert len(results) == 6
    assert peak <= 2
    assert controller.active() is False
