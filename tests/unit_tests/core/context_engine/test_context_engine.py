#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, patch
import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.messages import OffloadUserMessage
from openjiuwen.core.foundation.llm import UserMessage, SystemMessage, AssistantMessage, ToolMessage
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent import AgentCard


class TestContextEngine:
    @pytest.fixture
    def session(self):
        return create_agent_session("test_session", card=AgentCard(id="test_agent"))

    @pytest.fixture
    def same_session(self):
        return create_agent_session("test_session", card=AgentCard(id="test_agent"))

    @pytest.fixture
    def another_session(self):
        return create_agent_session("another_session", card=AgentCard(id="test_agent"))

    @pytest.fixture
    def engine(self):
        return ContextEngine(ContextEngineConfig(default_window_message_num=5))

    @pytest.mark.asyncio
    async def test_create_context_with_history_and_session(self, engine, session):
        history = [UserMessage(content="hello"), SystemMessage(content="sys")]
        context = await engine.create_context(context_id="ctx", session=session, history_messages=history)

        assert isinstance(context, SessionModelContext)
        assert context.session_id() == session.get_session_id()
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
    async def test_clear_context_all(self, engine, session):
        await engine.create_context(context_id="ctx1", session=session)
        await engine.create_context(context_id="ctx2", session=session)

        engine.clear_context()

        assert engine.get_context(context_id="ctx1", session_id=session.get_session_id()) is None
        assert engine.get_context(context_id="ctx2", session_id=session.get_session_id()) is None

    @pytest.mark.asyncio
    async def test_clear_context_by_session(self, engine, session, another_session):
        await engine.create_context(context_id="ctx1", session=session)
        await engine.create_context(context_id="ctx2", session=another_session)

        engine.clear_context(session_id=session.get_session_id())

        assert engine.get_context(context_id="ctx1", session_id=session.get_session_id()) is None
        assert engine.get_context(context_id="ctx2", session_id=another_session.get_session_id()) is not None

    @pytest.mark.asyncio
    async def test_clear_context_by_session_and_context(self, engine, session, another_session):
        await engine.create_context(context_id="ctx1", session=session)
        await engine.create_context(context_id="ctx2", session=another_session)

        engine.clear_context(session_id=session.get_session_id(), context_id="ctx1")
        engine.clear_context(session_id=another_session.get_session_id(), context_id="ctx2")

        assert engine.get_context(context_id="ctx1", session_id=session.get_session_id()) is None
        assert engine.get_context(context_id="ctx2", session_id=another_session.get_session_id()) is None

    @pytest.mark.asyncio
    async def test_context_save_and_load(self, session, same_session):
        from openjiuwen.core.session import get_default_inmemory_checkpointer
        check_pointer = get_default_inmemory_checkpointer()
        await check_pointer.pre_agent_execute(
            session=getattr(session, "_inner").get_inner_session(), inputs=None
        )
        ce_1 = ContextEngine(ContextEngineConfig(default_window_message_num=5))
        context_1 = await ce_1.create_context(
            "test_context",
            session,
        )
        messages = [
            SystemMessage(content="1"),
            UserMessage(content="2"),
            AssistantMessage(content="3"),
            ToolMessage(content="4", tool_call_id=""),
            OffloadUserMessage(content="5", offload_type="in_memory", offload_handle="abc"),
        ]

        await context_1.add_messages(messages)
        await ce_1.save_contexts(session, ["test_context"])
        await session.post_run()

        await check_pointer.pre_agent_execute(
            session=getattr(same_session, "_inner").get_inner_session(), inputs=None
        )
        ce_2 = ContextEngine(ContextEngineConfig(default_window_message_num=5))
        context_2 = await ce_2.create_context(
            "test_context",
            same_session,
        )

        assert context_1.get_messages() == context_2.get_messages()

    @pytest.mark.asyncio
    async def test_context_save_and_load_with_invalid_context_id(self, session, same_session):
        from openjiuwen.core.session import get_default_inmemory_checkpointer
        check_pointer = get_default_inmemory_checkpointer()
        await check_pointer.pre_agent_execute(
            session=getattr(session, "_inner").get_inner_session(), inputs=None
        )
        ce_1 = ContextEngine(ContextEngineConfig(default_window_message_num=5))
        context_1 = await ce_1.create_context(
            "test_context.0.0.1",
            session,
        )
        messages = [
            SystemMessage(content="1"),
            UserMessage(content="2"),
            AssistantMessage(content="3"),
            ToolMessage(content="4", tool_call_id=""),
            OffloadUserMessage(content="5", offload_type="in_memory", offload_handle="abc"),
        ]

        await context_1.add_messages(messages)
        await ce_1.save_contexts(session)
        await session.post_run()

        await check_pointer.pre_agent_execute(
            session=getattr(same_session, "_inner").get_inner_session(), inputs=None
        )
        ce_2 = ContextEngine(ContextEngineConfig(default_window_message_num=5))
        context_2 = await ce_2.create_context(
            "test_context.0.0.1",
            same_session,
        )

        assert context_1.get_messages() == context_2.get_messages()