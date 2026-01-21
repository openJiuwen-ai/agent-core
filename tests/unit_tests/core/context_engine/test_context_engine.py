#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, patch
import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.foundation.llm import UserMessage, SystemMessage
from openjiuwen.core.session.agent import Session


class TestContextEngine:
    @pytest.fixture
    def session(self):
        return Session("test_session")

    @pytest.fixture
    def another_session(self):
        return Session("another_session")

    @pytest.fixture
    def engine(self):
        return ContextEngine(ContextEngineConfig(default_window_message_num=5, memory_message_num=3))

    @pytest.mark.asyncio
    async def test_create_context_with_history_and_session(self, engine, session):
        history = [UserMessage(content="hello"), SystemMessage(content="sys")]
        context = await engine.create_context(context_id="ctx", session=session, history_messages=history)

        assert isinstance(context, SessionModelContext)
        assert context.session_id() == session.session_id()
        assert context.context_id() == "ctx"
        assert context.get_messages() == history

    @pytest.mark.asyncio
    async def test_create_context_reuses_existing(self, engine, session):
        ctx1 = await engine.create_context(context_id="ctx", session=session)
        ctx2 = await engine.create_context(context_id="ctx", session=session)

        assert ctx1 is ctx2

    @pytest.mark.asyncio
    async def test_create_context_isolated_per_session(self, engine, session, another_session):
        ctx1 = await engine.create_context(context_id="ctx", session=session)
        ctx2 = await engine.create_context(context_id="ctx", session=another_session)

        assert ctx1 is not ctx2
        assert ctx1.session_id() != ctx2.session_id()

    @pytest.mark.asyncio
    async def test_create_context_loads_from_memory_when_no_history(self, engine, session):
        mem_messages = [UserMessage(content="from memory")]
        with patch.object(ContextEngine, "_load_context_from_memory", new=AsyncMock(return_value=mem_messages)):
            context = await engine.create_context(
                context_id="ctx",
                session=session,
                mem_scope_id="scope-1",
            )
        assert context.get_messages() == mem_messages

    @pytest.mark.asyncio
    async def test_save_contexts_persists_and_calls_on_save(self, engine, session):
        context = await engine.create_context(context_id="ctx", session=session)
        await context.add_messages(UserMessage(content="new msg"))

        with patch.object(ContextEngine, "_save_context_to_memory", new=AsyncMock()) as save_mock, \
                patch.object(context, "on_save", wraps=context.on_save) as on_save_spy:
            await engine.save_contexts(["ctx"], session=session, mem_scope_id="scope-1")

        save_mock.assert_awaited_once()
        # ensure we persisted only the newly added message (without history)
        args, kwargs = save_mock.call_args
        assert kwargs["messages"] == [UserMessage(content="new msg")]
        on_save_spy.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_context_all(self, engine, session):
        await engine.create_context(context_id="ctx1", session=session)
        await engine.create_context(context_id="ctx2", session=session)

        engine.clear_context()

        assert engine.get_context(context_id="ctx1", session_id=session.session_id()) is None
        assert engine.get_context(context_id="ctx2", session_id=session.session_id()) is None

    @pytest.mark.asyncio
    async def test_clear_context_by_session(self, engine, session, another_session):
        await engine.create_context(context_id="ctx1", session=session)
        await engine.create_context(context_id="ctx2", session=another_session)

        engine.clear_context(session_id=session.session_id())

        assert engine.get_context(context_id="ctx1", session_id=session.session_id()) is None
        assert engine.get_context(context_id="ctx2", session_id=another_session.session_id()) is not None

    @pytest.mark.asyncio
    async def test_clear_context_by_session_and_context(self, engine, session, another_session):
        await engine.create_context(context_id="ctx1", session=session)
        await engine.create_context(context_id="ctx2", session=another_session)

        engine.clear_context(session_id=session.session_id(), context_id="ctx1")
        engine.clear_context(session_id=another_session.session_id(), context_id="ctx2")

        assert engine.get_context(context_id="ctx1", session_id=session.session_id()) is None
        assert engine.get_context(context_id="ctx2", session_id=another_session.session_id()) is None