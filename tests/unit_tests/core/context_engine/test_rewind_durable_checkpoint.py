# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.base import ContextEvent
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


class _PassiveCompressor(DialogueCompressor):
    async def trigger_get_context_window(self, context, context_window, **kwargs):
        return True

    async def on_get_context_window(self, context, context_window, **kwargs):
        context.set_messages([UserMessage(content="compressed")])
        context_window.context_messages = context.get_messages()
        return ContextEvent(event_type=self.processor_type()), context_window


@pytest.mark.asyncio
async def test_passive_compression_saves_actual_context_before_commit(monkeypatch):
    session = MagicMock()
    session.get_state.return_value = None
    session.update_state = MagicMock()
    session.commit = AsyncMock()
    monkeypatch.setattr("openjiuwen.core.context_engine.context.context.time.time", lambda: 1700000000.0)
    engine = ContextEngine(ContextEngineConfig())
    context = await engine.create_context(session=session, history_messages=[UserMessage(content="old")])
    context._processors = [_PassiveCompressor(DialogueCompressorConfig())]

    window = await context.get_context_window()

    assert window.get_messages()[0].content == "compressed"
    session.update_state.assert_any_call({"context": None})
    saved = session.update_state.call_args_list[-2].args[0]["context"]
    assert saved["default_context_id"]["messages"][0].content == "compressed"
    session.update_state.assert_any_call({"_last_committed_history_timestamp": 1700000000.0})
    session.commit.assert_awaited_once()


def test_context_rail_injects_history_flush():
    flush = AsyncMock(return_value=True)
    engine = ContextEngine(ContextEngineConfig())
    context = SimpleNamespace(history_flush=None)
    engine._context_pool["existing"] = context
    agent = SimpleNamespace(react_agent=SimpleNamespace(_config=SimpleNamespace(), context_engine=engine))
    rail = ContextProcessorRail(preset=False, history_flush=flush)
    rail._merge_processors = MagicMock(return_value=[])

    rail.init(agent)

    assert engine._history_flush is flush
    assert context.history_flush is flush


@pytest.mark.asyncio
async def test_passive_compression_commit_failure_does_not_break_window():
    session = MagicMock()
    session.get_state.return_value = None
    session.update_state = MagicMock()
    session.commit = AsyncMock(side_effect=RuntimeError("checkpoint unavailable"))
    engine = ContextEngine(ContextEngineConfig())
    context = await engine.create_context(session=session, history_messages=[UserMessage(content="old")])
    context._processors = [_PassiveCompressor(DialogueCompressorConfig())]

    window = await context.get_context_window()

    assert window.get_messages()[0].content == "compressed"
    assert not context._processor_lock.locked()


@pytest.mark.asyncio
async def test_passive_compression_does_not_advance_cursor_when_history_flush_fails():
    session = MagicMock()
    session.get_state.return_value = None
    session.update_state = MagicMock()
    session.commit = AsyncMock()
    engine = ContextEngine(ContextEngineConfig())
    engine._history_flush = AsyncMock(return_value=False)
    context = await engine.create_context(session=session, history_messages=[UserMessage(content="old")])
    context._processors = [_PassiveCompressor(DialogueCompressorConfig())]

    await context.get_context_window()

    assert all("_last_committed_history_timestamp" not in call.args[0] for call in session.update_state.call_args_list)
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("history_flush", [None, AsyncMock(return_value=False)])
async def test_iteration_checkpoint_requires_durable_history(history_flush):
    session = SimpleNamespace(update_state=MagicMock(), commit=AsyncMock())
    context = SimpleNamespace(history_flush=history_flush)
    agent = SimpleNamespace(context_engine=SimpleNamespace(save_contexts=AsyncMock()))
    rail = ContextProcessorRail()

    await rail.after_react_iteration(AgentCallbackContext(agent=agent, session=session, context=context))

    agent.context_engine.save_contexts.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_iteration_checkpoint_saves_context_and_cursor():
    session = SimpleNamespace(update_state=MagicMock(), commit=AsyncMock())
    context = SimpleNamespace(history_flush=AsyncMock(return_value=True))
    agent = SimpleNamespace(context_engine=SimpleNamespace(save_contexts=AsyncMock()))
    rail = ContextProcessorRail()

    await rail.after_react_iteration(AgentCallbackContext(agent=agent, session=session, context=context))

    agent.context_engine.save_contexts.assert_awaited_once_with(session)
    assert "_last_committed_history_timestamp" in session.update_state.call_args.args[0]
    session.commit.assert_awaited_once()
