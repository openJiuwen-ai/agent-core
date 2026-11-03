# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Duplex router + scheduler: batching, rate limit, failure paths, assembly."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
from openjiuwen.agent_teams.agent.coordination.handlers import MessageHandler
from openjiuwen.agent_teams.duplex import DuplexConfig
from openjiuwen.agent_teams.duplex.handlers import DuplexMessageHandler
from openjiuwen.agent_teams.duplex.policy import MessageSource
from openjiuwen.agent_teams.duplex.router import DuplexRouter
from openjiuwen.agent_teams.duplex.scheduler import RoutedMessage
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.foundation.llm.system_one import ChoiceAnswer

CONFIG = DuplexConfig(enabled=True, mode="active", api_base="http://supervisor.invalid/v1", model="m")


def _answer(choice: str, other: str, p: float) -> ChoiceAnswer:
    return ChoiceAnswer(choice=choice, confidence=p, probabilities={choice: p, other: 1 - p})


class FakeClassifier:
    """Records each batch; optionally holds the first call until released."""

    def __init__(self, answers: dict[str, ChoiceAnswer] | Exception) -> None:
        self.answers = answers
        self.batches: list[list[str]] = []
        self.release = asyncio.Event()
        self.release.set()

    async def classify(self, questions: Any, context: str, messages: Any) -> dict[str, ChoiceAnswer]:
        self.batches.append(list(messages))
        await self.release.wait()
        if isinstance(self.answers, Exception):
            raise self.answers
        return {q: self.answers[q] for q in questions}


class FakeHost:
    def __init__(self, *, running: bool, harness: Any = None) -> None:
        self.running = running
        self.harness = harness
        self.delivered: list[tuple[str, bool]] = []

    def is_agent_running(self) -> bool:
        return self.running

    def has_pending_interrupt(self) -> bool:
        return False

    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None:
        self.delivered.append((content, use_steer))


def _router(host: FakeHost, classifier: FakeClassifier) -> tuple[DuplexRouter, MagicMock, AsyncMock]:
    infra = MagicMock()
    infra.team_backend = None
    infra.message_manager.mark_messages_read = AsyncMock()
    redrain = AsyncMock()
    router = DuplexRouter(host, infra, "dev", CONFIG, redrain=redrain, classifier_factory=lambda: classifier)
    return router, infra, redrain


def _msg(mid: str, source: MessageSource, sender: str = "peer") -> RoutedMessage:
    return RoutedMessage(row=SimpleNamespace(message_id=mid), text=f"<{mid}>", source=source, sender=sender, body=mid)


def _marked(infra: MagicMock) -> list[str]:
    return [row.message_id for call in infra.message_manager.mark_messages_read.await_args_list for row in call.args[0]]


@pytest.mark.level0
@pytest.mark.asyncio
async def test_one_judgment_at_a_time_batches_and_rate_limits() -> None:
    host = FakeHost(running=True)
    classifier = FakeClassifier({"urgency": _answer("APPEND", "INTERRUPT", 0.97)})
    router, infra, redrain = _router(host, classifier)
    classifier.release.clear()

    assert await router.submit(_msg("m1", MessageSource.DIRECT)) is False  # taken over
    await asyncio.sleep(0)  # m1's judgment is now in flight
    assert await router.submit(_msg("m2", MessageSource.DIRECT)) is False
    assert await router.submit(_msg("m3", MessageSource.DIRECT)) is False
    assert router.owns("m2")
    classifier.release.set()
    await router.scheduler.wait_idle()

    assert classifier.batches == [["from peer: m1"], ["from peer: m2", "from peer: m3"]]
    assert host.delivered == [("<m1>", False), ("<m2>", False), ("<m3>", False)]  # APPEND
    assert _marked(infra) == ["m1", "m2", "m3"] and not router.owns("m2")
    assert redrain.await_count == 2

    # Same sender broadcasts twice within 10 s: the second is recorded only.
    host.delivered.clear()
    assert await router.submit(_msg("b1", MessageSource.BROADCAST)) is False
    assert await router.submit(_msg("b2", MessageSource.BROADCAST)) is True
    assert host.delivered == []
    await router.aclose()


@pytest.mark.level0
@pytest.mark.asyncio
async def test_classifier_failure_delivers_and_interrupt_failure_keeps_row_unread() -> None:
    host = FakeHost(running=True)
    router, infra, _ = _router(host, FakeClassifier(RuntimeError("timeout")))
    await router.submit(_msg("m1", MessageSource.DIRECT))
    await router.scheduler.wait_idle()
    assert host.delivered == [("<m1>", False)]  # busy + failure -> append, message not lost
    assert _marked(infra) == ["m1"]

    harness = SimpleNamespace(interrupt=AsyncMock(side_effect=RuntimeError("superseded by pause")))
    host = FakeHost(running=True, harness=harness)
    router, infra, _ = _router(host, FakeClassifier({"urgency": _answer("INTERRUPT", "APPEND", 0.95)}))
    await router.submit(_msg("m2", MessageSource.DIRECT))
    await router.scheduler.wait_idle()
    harness.interrupt.assert_awaited_once()
    assert host.delivered == []  # no steer fallback
    assert _marked(infra) == [] and not router.owns("m2")  # unread: redelivered by the next drain


@pytest.mark.level1
@pytest.mark.asyncio
async def test_visible_only_does_not_wake_and_user_is_never_judged() -> None:
    host = FakeHost(running=False)
    classifier = FakeClassifier({"relevance": _answer("SKIP", "DELIVER", 0.85)})
    router, infra, _ = _router(host, classifier)
    await router.submit(_msg("b1", MessageSource.BROADCAST))
    await router.scheduler.wait_idle()
    assert host.delivered == [] and _marked(infra) == ["b1"]

    host.running = True
    assert await router.submit(_msg("u1", MessageSource.USER, sender="user")) is True
    assert host.delivered == [("<u1>", True)] and len(classifier.batches) == 1


@pytest.mark.level0
def test_disabled_assembles_original_handlers() -> None:
    def build(duplex: DuplexConfig | None) -> EventDispatcher:
        spec = MagicMock(duplex=duplex, reliability=None, stale_claim_idle_timeout=600, stale_pending_idle_timeout=600)
        blueprint = SimpleNamespace(spec=spec, role=TeamRole.TEAMMATE, member_name="dev")
        return EventDispatcher(MagicMock(), blueprint, MagicMock(), MagicMock())

    for duplex in (None, DuplexConfig(enabled=True), DuplexConfig(mode="active", api_base="u", model="m")):
        dispatcher = build(duplex)
        assert type(dispatcher.message) is MessageHandler and dispatcher.duplex_router is None
    assert type(build(CONFIG).message) is DuplexMessageHandler


@pytest.mark.level0
@pytest.mark.asyncio
async def test_held_broadcast_blocks_newer_broadcasts_and_is_redelivered_after_failure() -> None:
    host = FakeHost(running=False)
    classifier = FakeClassifier({"relevance": _answer("DELIVER", "SKIP", 0.9)})
    router, infra, redrain = _router(host, classifier)
    held = RoutedMessage(
        row=SimpleNamespace(message_id="b1", broadcast=True, timestamp=1),
        text="<b1>",
        source=MessageSource.BROADCAST,
        sender="peer",
        body="b1",
    )
    newer = SimpleNamespace(message_id="b2", broadcast=True, timestamp=2)
    host.deliver_input = AsyncMock(side_effect=RuntimeError("runtime gone"))
    classifier.release.clear()

    assert await router.submit(held) is False  # taken over
    assert router.blocks(newer)  # watermark must not pass b1 while it is held
    classifier.release.set()
    await router.scheduler.wait_idle()
    assert _marked(infra) == [] and not router.blocks(newer)
    redrain.assert_not_awaited()  # no hot retry: the periodic poll redelivers

    host.deliver_input = AsyncMock()
    assert await router.submit(held) is False  # next poll: not rate limited by its own first try
    await router.scheduler.wait_idle()
    host.deliver_input.assert_awaited_once_with("<b1>", use_steer=True)
    assert _marked(infra) == ["b1"]


@pytest.mark.level1
@pytest.mark.asyncio
async def test_leader_broadcast_bypasses_classifier_and_rate_limit() -> None:
    host = FakeHost(running=True)
    classifier = FakeClassifier({})
    router, _, _ = _router(host, classifier)
    for mid in ("s1", "s2"):
        assert await router.submit(_msg(mid, MessageSource.LEADER_BROADCAST, sender="team_leader")) is True
    assert host.delivered == [("<s1>", True), ("<s2>", True)] and classifier.batches == []


@pytest.mark.level1
@pytest.mark.asyncio
async def test_final_action_is_decided_once_per_row() -> None:
    host = FakeHost(running=True)
    classifier = FakeClassifier(
        {"urgency": _answer("INTERRUPT", "APPEND", 0.95), "relevance": _answer("DELIVER", "SKIP", 0.95)}
    )
    router, infra, _ = _router(host, classifier)

    async def wake(content: Any, *, use_steer: bool = True) -> None:
        host.delivered.append((content, use_steer))
        host.running = host.running or content == "<b1>"  # delivering b1 wakes the idle member

    host.deliver_input = wake
    classifier.release.clear()
    await router.submit(_msg("m0", MessageSource.DIRECT))
    await asyncio.sleep(0)  # m0 occupies the worker
    host.running = False
    await router.submit(_msg("b1", MessageSource.BROADCAST))  # planned idle
    host.running = True
    await router.submit(_msg("d2", MessageSource.DIRECT))  # planned busy
    host.running = False  # idle again before the batch executes
    classifier.release.set()
    await router.scheduler.wait_idle()

    # Waking on b1 must not turn d2 into an interrupt mid-batch: each row runs once and is marked read.
    assert [c for c, _ in host.delivered].count("<d2>") == 1
    assert {"b1", "d2"} <= set(_marked(infra))


@pytest.mark.level1
@pytest.mark.asyncio
async def test_non_interrupt_rows_are_released_before_the_interrupt() -> None:
    gate = asyncio.Event()

    async def interrupt(content: Any) -> str:
        await gate.wait()  # a safe interrupt waiting for a tool
        return "ok"

    host = FakeHost(running=True, harness=SimpleNamespace(interrupt=interrupt))
    classifier = FakeClassifier(
        {"urgency": _answer("INTERRUPT", "APPEND", 0.95), "relevance": _answer("DELIVER", "SKIP", 0.95)}
    )
    router, infra, _ = _router(host, classifier)
    classifier.release.clear()
    broadcast = RoutedMessage(
        row=SimpleNamespace(message_id="b1", broadcast=True, timestamp=1),
        text="<b1>",
        source=MessageSource.BROADCAST,
        sender="peer",
        body="b1",
    )
    await router.submit(broadcast)  # broadcasts steer at most
    await router.submit(_msg("d1", MessageSource.DIRECT))  # interrupts
    classifier.release.set()
    for _ in range(20):
        await asyncio.sleep(0)

    assert _marked(infra) == ["b1"] and not router.owns("b1") and router.owns("d1")
    assert not router.blocks(SimpleNamespace(message_id="b2", broadcast=True, timestamp=2))
    gate.set()
    await router.scheduler.wait_idle()
    assert _marked(infra) == ["b1", "d1"]


@pytest.mark.level1
@pytest.mark.asyncio
async def test_classifier_is_built_once_and_closed_with_the_router() -> None:
    classifier = FakeClassifier({"urgency": _answer("APPEND", "INTERRUPT", 0.97)})
    classifier.aclose = AsyncMock()
    factory = MagicMock(return_value=classifier)
    infra = MagicMock()
    infra.team_backend = None
    infra.message_manager.mark_messages_read = AsyncMock()
    router = DuplexRouter(FakeHost(running=True), infra, "dev", CONFIG, redrain=AsyncMock(), classifier_factory=factory)
    for mid in ("m1", "m2"):
        await router.submit(_msg(mid, MessageSource.DIRECT))
        await router.scheduler.wait_idle()
    await router.aclose()
    assert factory.call_count == 1 and len(classifier.batches) == 2
    classifier.aclose.assert_awaited_once()
