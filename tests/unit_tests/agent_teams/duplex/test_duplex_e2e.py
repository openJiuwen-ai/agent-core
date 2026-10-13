# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End to end: a teammate's "no Kafka, use Redis" DM safely interrupts a real NativeHarness."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.duplex import DuplexConfig
from openjiuwen.agent_teams.duplex.policy import MessageSource
from openjiuwen.agent_teams.duplex.router import DuplexRouter
from openjiuwen.agent_teams.duplex.scheduler import RoutedMessage
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.core.foundation.llm.system_one import ChoiceAnswer
from openjiuwen.core.runner import Runner
from openjiuwen.harness.schema.task import TaskPlan, TodoItem
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_completed_iterations,
    wait_for_state,
    wait_invoke_running,
)


class _Classifier:
    def __init__(self) -> None:
        self.seen: list[Any] = []

    async def classify(self, questions: Any, context: str, messages: Any) -> dict[str, ChoiceAnswer]:
        self.seen.append((context, list(messages)))
        p = {"INTERRUPT": 0.96, "APPEND": 0.04}
        return {"urgency": ChoiceAnswer(choice="INTERRUPT", confidence=0.96, probabilities=p)}


class _Host:
    """The TeamAgent surface the router uses, over a real NativeHarness."""

    def __init__(self, harness: NativeHarness) -> None:
        self.harness = harness

    def is_agent_running(self) -> bool:
        return self.harness.state is HarnessState.RUNNING

    def has_pending_interrupt(self) -> bool:
        return False

    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None:
        await self.harness.send(content, immediate=use_steer)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_kafka_to_redis_correction_interrupts_and_replans() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=2, sleep_seconds=5.0)
        fake.sleep_from_iteration = 1  # iteration 0 completes, iteration 1 parks in its model call
        state = harness.load_state(harness._session)
        state.task_plan = TaskPlan(goal="Kafka ingestion", tasks=[TodoItem(id="k", content="deploy Kafka")])
        harness.save_state(harness._session, state)

        infra = MagicMock()
        infra.team_backend = None
        infra.message_manager.mark_messages_read = AsyncMock()
        classifier = _Classifier()
        config = DuplexConfig(enabled=True, mode="active", api_base="http://s.invalid/v1", model="m")
        router = DuplexRouter(
            _Host(harness), infra, "dev", config, redrain=AsyncMock(), classifier_factory=lambda: classifier
        )

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("design the Kafka ingestion")
            assert await wait_completed_iterations(fake, 1)
            await wait_invoke_running(fake)

            row = SimpleNamespace(message_id="m1")
            dm = "The environment cannot deploy Kafka; use Redis Streams."
            item = RoutedMessage(row=row, text=f"<dm>{dm}</dm>", source=MessageSource.DIRECT, sender="ops", body=dm)
            assert await router.submit(item) is False
            fake.sleep_seconds = 0.0  # let the continuation round finish quickly
            await router.scheduler.wait_idle()

            assert fake.cancelled_count == 1  # the generation was cancelled, not waited for
            assert harness.load_state(harness._session).task_plan is None
            assert await wait_for_state(harness, HarnessState.IDLE)
            replan = [s for s in fake.seen_steers if "Redis Streams" in s]
            assert replan and '<team-note kind="replan">' in replan[0]
            contents = [
                getattr(m, "content", "") for m in fake.context_engine.get_context(harness.session_id).get_messages()
            ]
            assert contents[:2] == ["design the Kafka ingestion", "assistant-0"]  # no rollback
            assert classifier.seen[0][1] == [f"from ops: {dm}"]
            assert '"recipient": "dev"' in classifier.seen[0][0]
            infra.message_manager.mark_messages_read.assert_awaited_once()
        finally:
            await router.aclose()
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
