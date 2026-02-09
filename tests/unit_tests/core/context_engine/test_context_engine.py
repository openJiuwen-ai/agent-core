# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import (
    MagicMock,
    patch,
)

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import (
    BaseError,
    build_error,
)
from openjiuwen.core.context_engine import (
    ContextEngine,
    ContextEngineConfig,
)
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.messages import OffloadUserMessage
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage, BaseMessage,
)
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.workflow import create_workflow_session
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
        check_pointer = CheckpointerFactory.get_checkpointer()
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
        check_pointer = CheckpointerFactory.get_checkpointer()
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

    # ---------- create_context supplements ----------
    @pytest.mark.asyncio
    async def test_create_context_with_session_none_uses_default_session_id(self, engine):
        context = await engine.create_context(context_id="ctx", session=None)
        assert context.session_id() == "default_session_id"
        assert context.context_id() == "ctx"
        assert engine.get_context(context_id="ctx", session_id="default_session_id") is context

    @pytest.mark.asyncio
    async def test_create_context_empty_history_messages(self, engine, session):
        context = await engine.create_context(context_id="ctx", session=session, history_messages=[])
        assert context.get_messages() == []

    @pytest.mark.asyncio
    async def test_create_context_history_messages_none_creates_empty(self, engine, session):
        context = await engine.create_context(context_id="ctx", session=session)
        assert context.get_messages() == []

    @pytest.mark.asyncio
    async def test_context_id_dots_replaced_by_underscores(self, engine, session):
        context = await engine.create_context(context_id="a.b.c", session=session)
        assert context.context_id() == "a_b_c"
        retrieved = engine.get_context(context_id="a.b.c", session_id=session.get_session_id())
        assert retrieved is context

    @pytest.mark.asyncio
    async def test_create_context_default_context_id(self, engine, session):
        context = await engine.create_context(session=session)
        assert context.context_id() == "default_context_id"

    @pytest.mark.asyncio
    async def test_create_context_with_custom_token_counter(self, engine, session):
        token_counter = MagicMock()
        context = await engine.create_context(
            context_id="ctx", session=session, token_counter=token_counter
        )
        assert context.token_counter() is token_counter

    @pytest.mark.asyncio
    async def test_create_context_with_registered_processor(self, engine, session):
        from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
            MessageOffloaderConfig
        )
        config = MessageOffloaderConfig(tokens_threshold=1000, large_message_threshold=500)
        context = await engine.create_context(
            context_id="ctx",
            session=session,
            processors=[("MessageOffloader", config)],
        )
        assert context is not None
        assert len(getattr(context, "_processors")) == 1

    @pytest.mark.asyncio
    async def test_create_context_unknown_processor_type_raises(self, engine, session):
        from pydantic import BaseModel

        class UnknownProcessorConfig(BaseModel):
            pass

        with pytest.raises(BaseError) as exc_info:
            await engine.create_context(
                context_id="ctx",
                session=session,
                processors=[("UnknownProcessorType", UnknownProcessorConfig())],
            )
        assert exc_info.value.code == StatusCode.CONTEXT_EXECUTION_ERROR.code

    @pytest.mark.asyncio
    async def test_create_context_processor_init_fails_raises(self, engine, session):
        from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
            MessageOffloaderConfig
        )
        with patch.object(
            engine,
            "_create_processor",
            side_effect=build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                msg=f"init processor type 'MessageOffloader' failed",
            )
        ):
            with pytest.raises(BaseError) as exc_info:
                await engine.create_context(
                    context_id="ctx",
                    session=session,
                    processors=[("MessageOffloader", MessageOffloaderConfig())],
                )
        assert exc_info.value.code == StatusCode.CONTEXT_EXECUTION_ERROR.code

    # ---------- get_context supplements ----------
    @pytest.mark.asyncio
    async def test_get_context_returns_none_when_not_exists(self, engine, session):
        assert engine.get_context(context_id="nonexistent", session_id=session.get_session_id()) is None

    @pytest.mark.asyncio
    async def test_get_context_with_dotted_context_id(self, engine, session):
        await engine.create_context(context_id="x.y", session=session)
        ctx = engine.get_context(context_id="x.y", session_id=session.get_session_id())
        assert ctx is not None
        assert ctx.context_id() == "x_y"

    @pytest.mark.asyncio
    async def test_get_context_default_params(self, engine):
        ctx = await engine.create_context(context_id="default_context_id", session=None)
        retrieved = engine.get_context()
        assert retrieved is ctx

    # ---------- clear_context supplements ----------
    @pytest.mark.asyncio
    async def test_clear_context_by_session_when_session_has_no_contexts(self, engine, session):
        engine.clear_context(session_id=session.get_session_id())
        assert engine.get_context(context_id="any", session_id=session.get_session_id()) is None

    @pytest.mark.asyncio
    async def test_clear_context_by_session_and_context_when_context_not_exists(self, engine, session):
        engine.clear_context(session_id=session.get_session_id(), context_id="nonexistent")
        assert engine.get_context(context_id="nonexistent", session_id=session.get_session_id()) is None

    @pytest.mark.asyncio
    async def test_clear_context_all_then_pool_empty(self, engine, session, another_session):
        await engine.create_context(context_id="c1", session=session)
        await engine.create_context(context_id="c2", session=another_session)
        engine.clear_context()
        assert engine.get_context(context_id="c1", session_id=session.get_session_id()) is None
        assert engine.get_context(context_id="c2", session_id=another_session.get_session_id()) is None

    # ---------- save_contexts supplements ----------
    @pytest.mark.asyncio
    async def test_save_contexts_session_none_does_not_raise(self, engine):
        await engine.save_contexts(session=None)

    @pytest.mark.asyncio
    async def test_save_contexts_partial_context_ids_missing_skipped(self, engine, session):
        await engine.create_context(context_id="exists", session=session)
        await engine.save_contexts(session=session, context_ids=["exists", "missing"])
        session_id = session.get_session_id()
        ctx = engine.get_context(context_id="exists", session_id=session_id)
        assert ctx is not None

    @pytest.mark.asyncio
    async def test_save_contexts_context_ids_none_saves_all_for_session(self, engine, session):
        await engine.create_context(context_id="c1", session=session)
        await engine.create_context(context_id="c2", session=session)
        await engine.save_contexts(session=session)
        states = getattr(session, "_inner").get_state("context")
        assert states is not None
        assert "c1" in states and "c2" in states

    # ---------- config ----------
    @pytest.mark.asyncio
    async def test_engine_config_none_uses_default(self):
        eng = ContextEngine(config=None)
        ctx = await eng.create_context(context_id="ctx", session=None)
        assert ctx is not None

    @pytest.mark.asyncio
    async def test_engine_custom_config_reflected_in_context(self, session):
        config = ContextEngineConfig(default_window_message_num=10)
        engine = ContextEngine(config=config)
        context = await engine.create_context(context_id="ctx", session=session)
        assert getattr(context, "_default_window_size") == 10

    # ---------- register_processor ----------
    @pytest.mark.asyncio
    async def test_register_processor_registers_in_map(self):
        from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
            CurrentRoundCompressor,
        )
        assert "CurrentRoundCompressor" in getattr(ContextEngine, "_PROCESSOR_MAP")
        assert getattr(ContextEngine, "_PROCESSOR_MAP")["CurrentRoundCompressor"] is CurrentRoundCompressor

    # ---------- multi session / multi context ----------
    @pytest.mark.asyncio
    async def test_multiple_sessions_and_contexts_isolated(self, engine, session, another_session):
        c1 = await engine.create_context(context_id="ctx_a", session=session)
        c2 = await engine.create_context(context_id="ctx_b", session=session)
        c3 = await engine.create_context(context_id="ctx_a", session=another_session)
        assert c1 is not c2
        assert c1 is not c3
        assert c2 is not c3
        assert engine.get_context("ctx_a", session.get_session_id()) is c1
        assert engine.get_context("ctx_b", session.get_session_id()) is c2
        assert engine.get_context("ctx_a", another_session.get_session_id()) is c3

    # ---------- save context supplements ----------
    @pytest.mark.asyncio
    async def test_save_context_001(self, session, request):
        check_pointer = CheckpointerFactory.get_checkpointer()
        await check_pointer.pre_agent_execute(
            session=getattr(session, "_inner").get_inner_session(), inputs=None
        )
        case_id = request.node.name

        session = create_agent_session(session_id=case_id, card=AgentCard(id=case_id))

        # Initialize engine, create a context below, perform message operations;
        # save the context under this engine for persistence
        engine = ContextEngine()
        history = [SystemMessage(content="智能家具助手", name="first")]
        context = await engine.create_context(context_id="ctx", session=session, history_messages=history)

        messages = [
            UserMessage(content="小智，明早6点帮我自动拉开窗帘", name="first"),
            ToolMessage(content="调用智能窗帘，设置定时", tool_call_id=case_id, name="first"),
            AssistantMessage(content="好的，已为您设置明早6点帮我自动拉开窗帘", name="first"),
            UserMessage(content="小智，报时", name="second"),
            AssistantMessage(content="好的，现在是2026年1月1日 18时15分24秒", name="second"),
        ]
        await context.add_messages(messages)
        await engine.save_contexts(context_ids=["ctx"], session=session)

        # Verify saved data: initialize engine1, create a context with the same id below,
        # load persisted data
        engine1 = ContextEngine()
        context1 = await engine1.create_context(context_id="ctx", session=session)
        assert context.get_messages() == context1.get_messages()

        # 1) The persisted messages obtained when engine1 creates a context should exist in the form of history_message

        assert context1.get_messages() == context1.get_messages()

        # 2) When engine1 creates a context with its own history_messages,
        # context1.get_messages() should get history_messages, not load persisted data.
        engine2 = ContextEngine()
        context2 = await engine2.create_context(context_id="ctx", session=session,
                                                history_messages=[SystemMessage(content="1", name="first")])
        assert context2.get_messages() == [SystemMessage(content="1", name="first")]

    @pytest.mark.asyncio
    async def test_get_context_window(self, request):
        case_id = request.node.name
        engine = ContextEngine()
        history = [
            UserMessage(content="history_1", name="1"),
            ToolMessage(content="history_2", tool_call_id=case_id, name="1"),
            AssistantMessage(content="history_3", name="1"),
            UserMessage(content="history_4", name="2"),
            ToolMessage(content="history_5", tool_call_id=case_id, name="2"),
        ]
        context = await engine.create_context(context_id="ctx", session=create_workflow_session(session_id=case_id),
                                              history_messages=history)
        messages = [
            UserMessage(content="message 1", name="1"),
            ToolMessage(content="message 2", tool_call_id=case_id, name="1"),
            AssistantMessage(content="message 3", name="1")
        ]
        await context.add_messages(messages)

        messages1 = [
            SystemMessage(content="system 1", name="1"),
            UserMessage(content="system 2", name="1"),
            ToolMessage(content="system 3", tool_call_id=case_id, name="1"),
            ToolMessage(content="system 4", tool_call_id=case_id, name="1"),
            AssistantMessage(content="system 5", name="1"),
            BaseMessage(role="system", content="system 6", name="2"),
            UserMessage(content="system 7", name="2"),
            AssistantMessage(content="system 8", name="2"),
        ]

        # Scenario 1: get_context_window only receives system_messages
        # => result: system_messages are the earliest messages in window
        window = await context.get_context_window(system_messages=messages1)
        assert window.get_messages() == messages1 + history + messages

        # Scenario 2: get_context_window receives system_messages, combined with window_size and dialogue_round testing
        # => result: returns the most recent 2 messages of system_messages data
        window = await context.get_context_window(system_messages=messages1, window_size=2)
        result = window.get_messages()
        assert result == messages1[-2:]

    # # Test for pop operation involving _history_messages_size update
    @pytest.mark.asyncio
    async def test_save_context_007(self, request):

        case_id = request.node.name

        session = create_agent_session(session_id=case_id, card=AgentCard(id=case_id))
        check_pointer = CheckpointerFactory.get_checkpointer()
        await check_pointer.pre_agent_execute(
            session=getattr(session, "_inner").get_inner_session(), inputs=None
        )

        engine = ContextEngine()
        history = [SystemMessage(content="智能家具助手", name="first")]
        context = await engine.create_context(context_id="ctx", session=session, history_messages=history)

        messages = [
            UserMessage(content="小智，明早6点帮我自动拉开窗帘", name="first"),
            ToolMessage(content="调用智能窗帘，设置定时", tool_call_id=case_id, name="first"),
            AssistantMessage(content="好的，已为您设置明早6点帮我自动拉开窗帘", name="first"),
            UserMessage(content="小智，报时", name="second"),
            AssistantMessage(content="好的，现在是2026年1月1日 18时15分24秒", name="second"),
        ]
        await context.add_messages(messages)
        await engine.save_contexts(context_ids=["ctx"], session=session)
        await session.post_run()

        engine1 = ContextEngine()
        context1 = await engine1.create_context(context_id="ctx", session=session)
        assert context1.get_messages(with_history=False) == []
        assert context1.get_messages() == history + messages

        result = context1.pop_messages(with_history=True, size=1)
        assert result == messages[-1:]
        assert context1.get_messages() == history + messages[:-1]

        result = context1.pop_messages(with_history=False, size=1)
        assert len(result) == 0
        assert context1.get_messages() == history + messages[:-1]

        messages1 = [
            UserMessage(content="message 1", name="1"),
            AssistantMessage(content="message 2", name="1")
        ]
        context1.set_messages(messages=messages1, with_history=False)
        assert context1.get_messages(with_history=False) == messages1
        assert context1.get_messages() == history + messages[:-1] + messages1