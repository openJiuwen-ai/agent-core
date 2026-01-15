#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from typing import List
import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig, ModelContext
from openjiuwen.core.foundation.llm import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage
)


class TestModelContext:
    async def create_context(self, history: List[BaseMessage] = None, context_message_limit: int = 100) -> ModelContext:
        context_engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=context_message_limit)
        )
        session = None
        return await context_engine.create_context("test_context", session, history_messages=history)

    @pytest.mark.asyncio
    async def test_model_context_add_one_messages(self):
        context = await self.create_context()
        message = await context.add_messages(UserMessage(content="test"))
        assert message == [UserMessage(content="test")]
        assert context.get_messages() == [UserMessage(content="test")]
        assert context.get_messages(with_history=True) == [UserMessage(content="test")]
        assert len(context) == 1
        context.pop_messages()

    @pytest.mark.asyncio
    async def test_model_context_add_invalid_messages(self):
        context = await self.create_context()
        try:
            await context.add_messages(123)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.CONTEXT_MESSAGE_INVALID.code

        try:
            invalid_messages = [UserMessage(content="test"), {"role": "user", "content": "test"}]
            await context.add_messages(invalid_messages)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.CONTEXT_MESSAGE_INVALID.code

    @pytest.mark.asyncio
    async def test_model_context_add_batch_messages(self):
        context = await self.create_context()
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        message = await context.add_messages(message_list)
        assert message == message_list
        assert context.get_messages() == message_list
        assert context.get_messages(with_history=False) == message_list
        assert len(context) == len(message_list)
        context.pop_messages()

    @pytest.mark.asyncio
    async def test_model_context_add_one_messages_with_history(self):
        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        message = await context.add_messages(UserMessage(content="test"))
        assert message == [UserMessage(content="test")]
        assert context.get_messages() == history_list + [UserMessage(content="test")]
        assert context.get_messages(with_history=False) == [UserMessage(content="test")]
        assert len(context) == len(history_list) + 1
        context.pop_messages()

    @pytest.mark.asyncio
    async def test_model_context_add_batch_messages_with_history(self):
        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        message = await context.add_messages(message_list)
        assert message == message_list
        assert context.get_messages() == history_list + message_list
        assert context.get_messages(with_history=False) == message_list
        assert len(context) == len(history_list) + len(message_list)
        context.pop_messages()

    @pytest.mark.asyncio
    async def test_model_context_get_empty_messages(self):
        context = await self.create_context()
        messages = context.get_messages(with_history=True)
        assert len(context) == 0
        assert messages == []
        messages = context.get_messages(with_history=False)
        assert messages == []
        messages = context.get_messages(0)
        assert messages == []
        messages = context.get_messages(10)
        assert messages == []

    @pytest.mark.asyncio
    async def test_model_context_get_messages_with_invalid_size(self):
        context = await self.create_context()
        try:
            await context.get_messages(size=-1)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.CONTEXT_EXECUTION_ERROR.code

    @pytest.mark.asyncio
    async def test_model_context_get_empty_messages_with_history(self):
        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        assert len(context) == 100
        messages = context.get_messages(with_history=True)
        assert messages == history_list
        messages = context.get_messages(with_history=False)
        assert messages == []
        messages = context.get_messages(0)
        assert messages == []
        messages = context.get_messages(10)
        assert messages == history_list[90:]
        messages = context.get_messages(100)
        assert messages == history_list
        messages = context.get_messages(101)
        assert messages == history_list
        messages = context.get_messages(0, with_history=False)
        assert messages == []
        messages = context.get_messages(10, with_history=False)
        assert messages == []

    @pytest.mark.asyncio
    async def test_model_context_get_messages(self):
        context = await self.create_context()
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        await context.add_messages(message_list)
        assert len(context) == 100
        messages = context.get_messages(with_history=True)
        assert messages == message_list
        messages = context.get_messages(with_history=False)
        assert messages == message_list
        messages = context.get_messages(0)
        assert messages == []
        messages = context.get_messages(10)
        assert messages == message_list[90:]
        messages = context.get_messages(100)
        assert messages == message_list
        messages = context.get_messages(101)
        assert messages == message_list

        messages = context.get_messages(0, with_history=False)
        assert messages == []
        messages = context.get_messages(10, with_history=False)
        assert messages == message_list[90:]
        messages = context.get_messages(100, with_history=False)
        assert messages == message_list
        messages = context.get_messages(101, with_history=False)
        assert messages == message_list

    @pytest.mark.asyncio
    async def test_model_context_get_messages_with_history(self):
        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        await context.add_messages(message_list)
        assert len(context) == 200
        messages = context.get_messages(with_history=True)
        assert messages == history_list + message_list
        messages = context.get_messages(with_history=False)
        assert messages == message_list
        messages = context.get_messages(0)
        assert messages == []
        messages = context.get_messages(10)
        assert messages == message_list[90:]
        messages = context.get_messages(100)
        assert messages == message_list
        messages = context.get_messages(101)
        assert messages == [history_list[-1]] + message_list
        messages = context.get_messages(150)
        assert messages == history_list[50:] + message_list
        messages = context.get_messages(200)
        assert messages == history_list + message_list
        messages = context.get_messages(201)
        assert messages == history_list + message_list

        messages = context.get_messages(0, with_history=False)
        assert messages == []
        messages = context.get_messages(10, with_history=False)
        assert messages == message_list[90:]
        messages = context.get_messages(100, with_history=False)
        assert messages == message_list
        messages = context.get_messages(101, with_history=False)
        assert messages == message_list
        messages = context.get_messages(200, with_history=False)
        assert messages == message_list

    @pytest.mark.asyncio
    async def test_model_context_pop_empty_messages(self):
        context = await self.create_context()
        messages = context.pop_messages(len(context))
        assert len(context) == 0
        assert messages == []
        messages = context.pop_messages(len(context), with_history=False)
        assert len(context) == 0
        assert messages == []
        messages = context.pop_messages(100)
        assert len(context) == 0
        assert messages == []

    @pytest.mark.asyncio
    async def test_model_context_pop_empty_messages_with_history(self):
        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        messages = context.pop_messages(len(context))
        assert len(context) == 0
        assert messages == history_list

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        messages = context.pop_messages(len(context), with_history=False)
        assert len(context) == 100
        assert messages == []

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        messages = context.pop_messages(0)
        assert len(context) == 100
        assert messages == []
        for i in range(1, 101):
            messages = context.pop_messages(1)
            assert len(context) == 100 - i
            assert messages == [history_list[100 - i]]
        messages = context.pop_messages(1)
        assert len(context) == 0
        assert messages == []

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        messages = context.pop_messages(10)
        assert len(context) == 90
        assert messages == history_list[90:]
        messages = context.pop_messages(100)
        assert len(context) == 0
        assert messages == history_list[:90]

    @pytest.mark.asyncio
    async def test_model_context_pop_messages(self):
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context()
        await context.add_messages(message_list)
        messages = context.pop_messages(len(context))
        assert len(context) == 0
        assert messages == message_list

        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context()
        await context.add_messages(message_list)
        messages = context.pop_messages(len(context), with_history=False)
        assert len(context) == 0
        assert messages == message_list

        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context()
        await context.add_messages(message_list)
        messages = context.pop_messages(0)
        assert len(context) == 100
        assert messages == []
        for i in range(1, 101):
            messages = context.pop_messages(1)
            assert len(context) == 100 - i
            assert messages == [message_list[100 - i]]
        messages = context.pop_messages(1)
        assert len(context) == 0
        assert messages == []

        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context()
        await context.add_messages(message_list)
        messages = context.pop_messages(10)
        assert len(context) == 90
        assert messages == message_list[90:]
        messages = context.pop_messages(100)
        assert len(context) == 0
        assert messages == message_list[:90]

    @pytest.mark.asyncio
    async def test_model_context_pop_messages_with_invalid_size(self):
        context = await self.create_context()
        try:
            context.pop_messages(size=-1)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.CONTEXT_EXECUTION_ERROR.code

    @pytest.mark.asyncio
    async def test_model_context_pop_messages_with_history(self):
        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        await context.add_messages(message_list)
        messages = context.pop_messages(len(context))
        assert len(context) == 0
        assert messages == history_list + message_list

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        await context.add_messages(message_list)
        messages = context.pop_messages(len(context), with_history=False)
        assert len(context) == 100
        assert messages == message_list

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        await context.add_messages(message_list)
        messages = context.pop_messages(0)
        assert len(context) == 200
        assert messages == []
        for i in range(1, 101):
            messages = context.pop_messages(1)
            assert len(context) == 200 - i
            assert messages == [message_list[100 - i]]
        for i in range(1, 101):
            messages = context.pop_messages(1)
            assert len(context) == 100 - i
            assert messages == [history_list[100 - i]]
        messages = context.pop_messages(1)
        assert len(context) == 0
        assert messages == []

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        await context.add_messages(message_list)
        messages = context.pop_messages(10)
        assert len(context) == 190
        assert messages == message_list[90:]
        messages = context.pop_messages(100)
        assert len(context) == 90
        assert messages == history_list[90:] + message_list[:90]
        messages = context.pop_messages(10)
        assert len(context) == 80
        assert messages == history_list[80:90]
        messages = context.pop_messages(100)
        assert len(context) == 0
        assert messages == history_list[:80]

    @pytest.mark.asyncio
    async def test_model_context_set_messages(self):
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context()
        context.set_messages(message_list)
        assert len(context) == 100

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        context.set_messages(message_list)
        assert len(context) == 100
        messages = context.get_messages()
        assert messages == message_list

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        context.set_messages(message_list, with_history=False)
        assert len(context) == 200
        messages = context.get_messages()
        assert messages == history_list + message_list

        history_list = [UserMessage(content=f"history-{i}") for i in range(100)]
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        context = await self.create_context(history_list)
        context.pop_messages(50)
        context.set_messages(message_list, with_history=False)
        assert len(context) == 150
        messages = context.get_messages()
        assert messages == history_list[:50] + message_list

    @pytest.mark.asyncio
    async def test_model_context_set_invalid_messages(self):
        context = await self.create_context()
        try:
            context.set_messages(123)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.CONTEXT_MESSAGE_INVALID.code

        try:
            invalid_messages = [UserMessage(content="test"), {"role": "user", "content": "test"}]
            await context.set_messages(invalid_messages)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.CONTEXT_MESSAGE_INVALID.code

    @pytest.mark.asyncio
    async def test_model_context_set_empty_context_window(self):
        context = await self.create_context()
        window = await context.get_context_window()
        assert window.context_messages == []
        assert window.system_messages == []
        assert window.tools == []

    @pytest.mark.asyncio
    async def test_model_context_get_context_window_with_invalid_size(self):
        context = await self.create_context()
        try:
            await context.get_context_window(window_size=-1)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.CONTEXT_EXECUTION_ERROR.code

    @pytest.mark.asyncio
    async def test_model_context_set_context_window_with_system_messages(self):
        system_messages = [SystemMessage(content="system message")]
        context = await self.create_context()
        window = await context.get_context_window(system_messages=system_messages)
        assert window.context_messages == []
        assert window.system_messages == system_messages
        assert window.tools == []

    @pytest.mark.asyncio
    async def test_model_context_set_context_window_with_context_messages(self):
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        system_messages = [SystemMessage(content="system message")]
        context = await self.create_context(context_message_limit=10)
        await context.add_messages(message_list)
        window = await context.get_context_window(system_messages=system_messages)
        assert window.context_messages == message_list[91:]
        assert window.system_messages == system_messages
        assert window.tools == []

    @pytest.mark.asyncio
    async def test_model_context_set_context_window_with_limited_size(self):
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        system_messages = [SystemMessage(content="system message")]
        context = await self.create_context(context_message_limit=1)
        await context.add_messages(message_list)
        window = await context.get_context_window(system_messages=system_messages)
        assert window.context_messages == []
        assert window.system_messages == system_messages
        assert window.tools == []

        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        system_messages = [
            SystemMessage(content="system message-1"),
            SystemMessage(content="system message-2")
        ]
        context = await self.create_context(context_message_limit=1)
        await context.add_messages(message_list)
        window = await context.get_context_window(system_messages=system_messages)
        assert window.context_messages == []
        assert window.system_messages == system_messages[:1]
        assert window.tools == []

    @pytest.mark.asyncio
    async def test_model_context_statistic(self):
        def generate_message(idx: int):
            if idx % 4 == 0:
                return UserMessage(content=f"use message-{idx}")
            if idx % 4 == 1:
                return SystemMessage(content=f"system message-{idx}")
            if idx % 4 == 2:
                return AssistantMessage(content=f"ai message-{idx}")
            if idx % 4 == 3:
                return ToolMessage(content=f"tool message-{idx}", tool_call_id="")
            return None
        message_list = [
            generate_message(i) for i in range(100)
        ]
        context = await self.create_context()
        await context.add_messages(message_list)
        stat = context.statistic()
        assert stat.total_messages == 100
        assert stat.system_messages == 25
        assert stat.assistant_messages == 25
        assert stat.tool_messages == 25
        assert stat.user_messages == 25

        context_window = await context.get_context_window()
        stat = context_window.statistic
        assert stat.total_messages == 100
        assert stat.system_messages == 25
        assert stat.assistant_messages == 25
        assert stat.tool_messages == 25
        assert stat.user_messages == 25

    @pytest.mark.asyncio
    async def test_model_context_window_validation(self):
        # 1. Build a context with 10 human messages
        tool_msgs = [
            ToolMessage(content='tool-0', tool_call_id='tc-0'),
            ToolMessage(content='tool-1', tool_call_id='tc-1'),
            ToolMessage(content='tool-2', tool_call_id='tc-2'),
        ]
        message_list = tool_msgs + [UserMessage(content=f"human-{i}") for i in range(10)]
        context = await self.create_context(context_message_limit=20)
        await context.add_messages(message_list)

        # 2. Fetch the window – validation should drop the leading ToolMessages
        system_msgs = [SystemMessage(content="sys")]
        window = await context.get_context_window(system_messages=system_msgs)

        # 3. Assertions
        assert window.system_messages == system_msgs
        # Ensure no ToolMessage remains in the returned list
        assert not any(isinstance(m, ToolMessage) for m in window.context_messages)