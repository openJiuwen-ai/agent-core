# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team extraction ownership, failure independence, filtering and drain."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.rails import JiuwenTeamMemoryRail
from openjiuwen.harness.rails.memory import jiuwen_team_memory_rail as module
from tests.unit_tests.harness.rails.test_jiuwen_memory_coding_rail import context
from tests.unit_tests.harness.rails.test_jiuwen_memory_rail import MockAgent, MockMemoryProvider


@pytest.mark.asyncio
async def test_member_turn_and_round_use_responder_model_and_filter(monkeypatch):
    provider = MockMemoryProvider()
    provider.add_team_memories = AsyncMock(return_value=1)
    extract = AsyncMock(return_value=[{"label": "decision", "content": "confirmed"},
                                     {"label": "context", "content": "secret"}])
    monkeypatch.setattr(module, "extract_team_conclusions", extract)
    rail = JiuwenTeamMemoryRail(provider, content_allowed=lambda text: "secret" not in text)
    agent = MockAgent()
    model = object()
    agent._react_agent = SimpleNamespace(_llm=model)
    rail.init(agent)
    try:
        await rail.before_invoke(context(agent))
        await rail.after_invoke(context(agent))
        await rail.drain()
        assert provider.sync_calls == [("query", "answer")]
        assert extract.await_args.args[0] is model
        provider.add_team_memories.assert_awaited_once_with([{"label": "decision", "content": "confirmed"}])
        await rail.conclude_round("finished round", model=model)
        assert extract.await_count == 2
        await rail.before_invoke(context(agent, "secret"))
        await rail.after_invoke(context(agent, "secret"))
        await rail.drain()
        assert extract.await_count == 2
        assert len(provider.sync_calls) == 1
    finally:
        await rail.aclose()


@pytest.mark.asyncio
async def test_missing_model_or_failed_team_extraction_keeps_ordinary_sync(monkeypatch):
    provider = MockMemoryProvider()
    provider.add_team_memories = AsyncMock()
    extract = AsyncMock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(module, "extract_team_conclusions", extract)
    rail = JiuwenTeamMemoryRail(provider)
    agent = MockAgent()
    rail.init(agent)
    try:
        await rail.before_invoke(context(agent))
        await rail.after_invoke(context(agent))
        await rail.drain()
        extract.assert_not_awaited()
        agent._react_agent = SimpleNamespace(_llm=object())
        await rail.before_invoke(context(agent))
        await rail.after_invoke(context(agent))
        await rail.drain()
        assert len(provider.sync_calls) == 2
        provider.add_team_memories.assert_not_awaited()
    finally:
        await rail.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_noncallable_method", [False, True])
async def test_missing_team_writer_skips_extraction_and_preserves_sync(monkeypatch, has_noncallable_method):
    provider = MockMemoryProvider()
    if has_noncallable_method:
        provider.add_team_memories = None
    extract = AsyncMock(return_value=[])
    warning = MagicMock()
    monkeypatch.setattr(module, "extract_team_conclusions", extract)
    monkeypatch.setattr(module.logger, "warning", warning)
    agent = MockAgent()
    model = object()
    monkeypatch.setattr(agent, "_react_agent", SimpleNamespace(_llm=model), raising=False)
    rail = JiuwenTeamMemoryRail(provider)
    rail.init(agent)
    try:
        for _ in range(2):
            await rail.before_invoke(context(agent))
            await rail.after_invoke(context(agent))
            await rail.drain()
        await rail.conclude_round("finished round", model=model)
        assert provider.sync_calls == [("query", "answer"), ("query", "answer")]
        extract.assert_not_awaited()
        warning.assert_called_once()
        assert "add_team_memories" in warning.call_args.args[0]
    finally:
        await rail.aclose()


@pytest.mark.asyncio
async def test_cancelled_round_is_drained_before_provider_close(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    async def extract(*_args):
        started.set()
        await release.wait()
        return [{"label": "lesson", "content": "confirmed"}]
    monkeypatch.setattr(module, "extract_team_conclusions", extract)
    provider = MockMemoryProvider()
    provider.add_team_memories = AsyncMock(return_value=1)
    provider.shutdown = AsyncMock()
    rail = JiuwenTeamMemoryRail(provider)
    rail.init(MockAgent())
    task = asyncio.create_task(rail.conclude_round("round", model=object()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    close = asyncio.create_task(rail.aclose())
    await asyncio.sleep(0)
    provider.shutdown.assert_not_awaited()
    release.set()
    await close
    provider.add_team_memories.assert_awaited_once()
    provider.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_extraction_agent_uses_only_collection_tool():
    import json

    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
    from openjiuwen.core.runner import Runner
    from tests.unit_tests.fixtures.mock_llm import (
        create_text_response,
        create_tool_call_response,
        mock_llm_context,
    )
    model = Model(model_client_config=ModelClientConfig(client_provider="OpenAI",
        api_key="mock", api_base="http://mock-base"), model_config=ModelRequestConfig(model="mock-model"))
    await Runner.start()
    try:
        with mock_llm_context() as llm:
            llm.set_responses([
                create_tool_call_response("collect_team_conclusions", json.dumps({"items": [
                    {"label": "decision", "content": "confirmed", "confirmed": True},
                    {"label": "lesson", "content": "draft", "confirmed": False},
                ]})), create_text_response("analysis complete"),
            ])
            result = await module.extract_team_conclusions(model, "confirmed decision; pending draft")
        assert result == [{"label": "decision", "content": "confirmed"}]
        calls = str(llm.call_history)
        assert "collect_team_conclusions" in calls
        assert "write_memory_file" not in calls and "read_memory_file" not in calls
    finally:
        await Runner.stop()


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
        await rails[0].after_invoke(context(natives[0]))
        await started.wait()
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
