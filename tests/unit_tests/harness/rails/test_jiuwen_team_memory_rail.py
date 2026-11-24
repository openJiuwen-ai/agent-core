# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team extraction ownership, failure independence, filtering and drain."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.rails import JiuwenTeamMemoryRail
from tests.unit_tests.harness.rails.test_jiuwen_memory_coding_rail import context
from tests.unit_tests.harness.rails.test_jiuwen_memory_rail import MockAgent, MockMemoryProvider


@pytest.mark.asyncio
async def test_team_turn_submits_verified_human_source_once():
    provider = MockMemoryProvider()
    provider.sync_turn = AsyncMock()
    rail = JiuwenTeamMemoryRail(provider)
    agent = MockAgent()
    rail.init(agent)
    try:
        internal = context(agent, "agent assignment")
        await rail.before_invoke(internal)
        await rail.after_invoke(internal)
        human = context(agent, "wrapped")
        human.extra["run_context"] = SimpleNamespace(extra={
            "raw_query": "Use the confirmed brief", "raw_query_origin": "human",
        })
        await rail.before_invoke(human)
        await rail.after_invoke(human)
        await rail.drain()
        provider.sync_turn.assert_awaited_once()
        sources = provider.sync_turn.await_args.kwargs["sources"]
        assert [(item["role"], item["text"]) for item in sources] == [
            ("user", "Use the confirmed brief"), ("assistant", "answer"),
        ]
    finally:
        await rail.aclose()


@pytest.mark.asyncio
async def test_team_write_deduplicates_the_same_acceptance_version():
    provider = MockMemoryProvider()
    provider.sync_turn = AsyncMock()
    rail = JiuwenTeamMemoryRail(provider)
    agent = MockAgent()
    rail.init(agent)
    try:
        event = SimpleNamespace(task_id="task-1", acceptance_version="first", content="assignment",
                                summary="", note="")
        delivery = context(agent, "execute task-1")
        await rail.before_invoke(delivery)
        rail.record_task_delivery(event.task_id, event.acceptance_version)
        await rail.on_task_accepted(event)
        await rail.on_task_accepted(event)
        await rail.drain()
        provider.sync_turn.assert_not_awaited()
        await rail.after_invoke(delivery)
        await rail.drain()
        provider.sync_turn.assert_awaited_once()
        sources = provider.sync_turn.await_args.kwargs["sources"]
        assert [(item["role"], item["text"]) for item in sources] == [
            ("agent", "assignment"), ("assistant", "answer"),
        ]
        event.acceptance_version = "second"
        event.summary = "validated fix"
        await rail.on_task_accepted(event)
        await rail.drain()
        assert provider.sync_turn.await_count == 2
    finally:
        await rail.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("early_exit", ["detached", "source_error"])
async def test_interrupted_finish_releases_delivery_for_later_acceptance(early_exit):
    def check_content(text):
        if text == "human request":
            raise RuntimeError("source rejected before submission")
        return True

    provider = MockMemoryProvider()
    provider.sync_turn = AsyncMock()
    rail = JiuwenTeamMemoryRail(provider, content_allowed=check_content)
    first, second = MockAgent(), MockAgent()
    rail.init(first)
    rail.init(second)
    try:
        delivery = context(first)
        delivery.extra["run_context"] = SimpleNamespace(extra={
            "raw_query": "human request", "raw_query_origin": "human",
        })
        event = SimpleNamespace(task_id="task", acceptance_version="1", content="",
                                summary="validated outcome", note="")
        await rail.before_invoke(delivery)
        rail.record_task_delivery(event.task_id, event.acceptance_version)
        if early_exit == "detached":
            rail.uninit(first)
            await rail.after_invoke(delivery)
        else:
            with pytest.raises(RuntimeError, match="source rejected"):
                await rail.after_invoke(delivery)

        await rail.on_task_accepted(event)
        await rail.drain()
        provider.sync_turn.assert_awaited_once()
        assert [source["text"] for source in provider.sync_turn.await_args.kwargs["sources"]] == [
            "validated outcome",
        ]
    finally:
        await rail.aclose()


@pytest.mark.asyncio
async def test_close_drains_accepted_delivery():
    started, release = asyncio.Event(), asyncio.Event()

    async def sync(*_args, **_kwargs):
        started.set()
        await release.wait()

    provider = MockMemoryProvider()
    provider.sync_turn = AsyncMock(side_effect=sync)
    provider.shutdown = AsyncMock()
    rail = JiuwenTeamMemoryRail(provider)
    rail.init(MockAgent())
    event = SimpleNamespace(task_id="task", acceptance_version="1", content="",
                            summary="validated outcome", note="")
    await rail.on_task_accepted(event)
    await asyncio.wait_for(started.wait(), timeout=2)
    close = asyncio.create_task(rail.aclose())
    await asyncio.sleep(0)
    provider.shutdown.assert_not_awaited()
    release.set()
    await close
    provider.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_dispose_drains_own_memory_and_leaves_other_member_live():
    from openjiuwen.agent_teams.harness import NativeHarness
    from openjiuwen.core.runner import Runner
    from tests.unit_tests.agent_teams.harness.fixtures import make_spec, start_harness

    await Runner.start()
    providers = [MockMemoryProvider(), MockMemoryProvider()]
    rails = [JiuwenTeamMemoryRail(provider) for provider in providers]
    natives = [NativeHarness(make_spec(), extra_rails=[rail]) for rail in rails]
    started, release = asyncio.Event(), asyncio.Event()

    async def write(*args, **kwargs):
        started.set()
        await release.wait()

    providers[0].sync_turn = write
    for provider in providers:
        provider.shutdown = AsyncMock()
    try:
        for native, rail in zip(natives, rails):
            await start_harness(native)
            await rail.before_invoke(context(native))
        turn = context(natives[0], "human request")
        turn.extra["run_context"] = {"extra": {
            "raw_query": "human request", "raw_query_origin": "human",
        }}
        await rails[0].before_invoke(turn)
        await rails[0].after_invoke(turn)
        await asyncio.wait_for(started.wait(), 5)
        closing = asyncio.create_task(natives[0].dispose())
        await asyncio.sleep(0)
        providers[0].shutdown.assert_not_awaited()
        release.set()
        await asyncio.wait_for(closing, 5)
        await natives[0].dispose()
        providers[0].shutdown.assert_awaited_once()
        assert not rails[0].attached_agents
        providers[1].shutdown.assert_not_awaited()
        await rails[1].before_invoke(context(natives[1], "still live"))
        assert providers[1].prefetch_queries[-1] == "still live"
    finally:
        release.set()
        for native in natives:
            await native.dispose()
        await Runner.stop()
    providers[1].shutdown.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["start", "run_once"])
async def test_team_rebuild_releases_retired_native_memory(monkeypatch, entry):
    from openjiuwen.agent_teams.harness import NativeHarness
    from openjiuwen.agent_teams.harness.team_harness import TeamHarness
    from openjiuwen.agent_teams.schema import TeamRole
    from openjiuwen.core.runner import Runner
    from tests.unit_tests.agent_teams.harness.fixtures import make_spec

    spec = make_spec()
    resolve_parts = spec.resolve_parts
    providers, rails = [], []

    def resolve(context=None):
        parts = resolve_parts(context)
        provider = MockMemoryProvider()
        provider.shutdown = AsyncMock()
        rail = JiuwenTeamMemoryRail(provider)
        providers.append(provider)
        rails.append(rail)
        parts.rails.append(rail)
        return parts

    async def run_once(native, content, *, session=None):
        await native.ensure_initialized()
        return {"output": "done"}

    monkeypatch.setattr(spec, "resolve_parts", resolve)
    if entry == "run_once":
        monkeypatch.setattr(NativeHarness, "run_once", run_once)
    await Runner.start()
    harness = TeamHarness.build(agent_spec=spec, role=TeamRole.LEADER, member_name="leader")
    try:
        await harness.start()
        await rails[0].before_invoke(context(harness._native))
        for _ in range(2):
            old_native, old_rail, old_provider = harness._native, rails[-1], providers[-1]
            await harness.stop()
            old_provider.shutdown.assert_not_awaited()
            if entry == "start":
                await harness.start()
            else:
                await harness.run_once("next turn")
            assert harness._native is not old_native
            old_provider.shutdown.assert_awaited_once()
            assert not old_rail.attached_agents
            await rails[-1].before_invoke(context(harness._native))
            providers[-1].shutdown.assert_not_awaited()
            if entry == "run_once":
                # A non-streaming native needs a supervisor before round-end stop.
                await harness._native.start()
    finally:
        await harness.dispose()
        await Runner.stop()
    assert all(provider.shutdown.await_count == 1 for provider in providers)
