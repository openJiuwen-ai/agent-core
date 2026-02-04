# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from typing import List, Optional
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig, ModelContext
from openjiuwen.core.foundation.llm import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage, ToolCall
)
from openjiuwen.core.foundation.tool import ToolInfo


class TestModelContext:
    async def create_context(
            self,
            history: List[BaseMessage] = None,
            *,
            window_message_limit: int = 100,
            dialogue_round: int = None,
            max_context_message_num: Optional[int] = None,
            enable_reload: bool = False,
            enable_kv_cache_release: bool = False,
            processors: Optional[List] = None,
            token_counter=None,
    ) -> ModelContext:
        config = ContextEngineConfig(
            default_window_message_num=window_message_limit,
            default_window_round_num=dialogue_round,
            max_context_message_num=max_context_message_num,
            enable_reload=enable_reload,
            enable_kv_cache_release=enable_kv_cache_release,
        )
        context_engine = ContextEngine(config)
        session = None
        return await context_engine.create_context(
            "test_context", session,
            history_messages=history,
            processors=processors,
            token_counter=token_counter,
        )

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
        except BaseError as e:
            assert e.code == StatusCode.CONTEXT_MESSAGE_INVALID.code

        try:
            invalid_messages = [UserMessage(content="test"), {"role": "user", "content": "test"}]
            await context.add_messages(invalid_messages)
        except BaseError as e:
            assert e.code == StatusCode.CONTEXT_MESSAGE_INVALID.code

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
        except BaseError as e:
            assert e.code == StatusCode.CONTEXT_EXECUTION_ERROR.code

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
        except BaseError as e:
            assert e.code == StatusCode.CONTEXT_EXECUTION_ERROR.code

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
        except BaseError as e:
            assert e.code == StatusCode.CONTEXT_MESSAGE_INVALID.code

        try:
            invalid_messages = [UserMessage(content="test"), {"role": "user", "content": "test"}]
            context.set_messages(invalid_messages)
        except BaseError as e:
            assert e.code == StatusCode.CONTEXT_MESSAGE_INVALID.code

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
        except BaseError as e:
            assert e.code == StatusCode.CONTEXT_EXECUTION_ERROR.code

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
        context = await self.create_context(window_message_limit=10)
        await context.add_messages(message_list)
        window = await context.get_context_window(system_messages=system_messages)
        assert window.context_messages == message_list[91:]
        assert window.system_messages == system_messages
        assert window.tools == []

    @pytest.mark.asyncio
    async def test_model_context_set_context_window_with_limited_size(self):
        message_list = [UserMessage(content=f"test-{i}") for i in range(100)]
        system_messages = [SystemMessage(content="system message")]
        context = await self.create_context(window_message_limit=1)
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
        context = await self.create_context(window_message_limit=1)
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
        context = await self.create_context(window_message_limit=20)
        await context.add_messages(message_list)

        # 2. Fetch the window – validation should drop the leading ToolMessages
        system_msgs = [SystemMessage(content="sys")]
        window = await context.get_context_window(system_messages=system_msgs)

        # 3. Assertions
        assert window.system_messages == system_msgs
        # Ensure no ToolMessage remains in the returned list
        assert not any(isinstance(m, ToolMessage) for m in window.context_messages)

    @pytest.mark.asyncio
    async def test_model_context_window_with_dialogue_round(self):
        def create_tool_call_list(tcs: List[str]):
            return [
                ToolCall(id=tc, name="test-tool", type="function", arguments="")
                for tc in tcs
            ]
        context = await self.create_context(dialogue_round=1)
        dialogues = [
            # dialogue-1
            [
                UserMessage(content="user-1"),
                AssistantMessage(content="assistant-1", tool_calls=create_tool_call_list(["tc-1", "tc-2", "tc-3"])),
                ToolMessage(content="tool-1", tool_call_id='tc-1'),
                ToolMessage(content="tool-2", tool_call_id='tc-2'),
                ToolMessage(content="tool-3", tool_call_id='tc-3'),
                AssistantMessage(content="assistant-2"),
            ],
            # dialogue-2
            [
                UserMessage(content="user-2"),
                AssistantMessage(content="assistant-3", tool_calls=create_tool_call_list(["tc-1", "tc-2"])),
                ToolMessage(content="tool-4", tool_call_id='tc-1'),
                ToolMessage(content="tool-5", tool_call_id='tc-2'),
                AssistantMessage(content="assistant-4"),
            ],
            # dialogue-3
            [
                UserMessage(content="user-3"),
                AssistantMessage(content="assistant-3", tool_calls=create_tool_call_list(["tc-1"])),
                ToolMessage(content="tool-6", tool_call_id='tc-1'),
                AssistantMessage(content="assistant-5")
            ]
        ]

        messages = []
        for dialogue in dialogues:
            messages.extend(dialogue)

        await context.add_messages(messages)
        window = await context.get_context_window()
        assert window.context_messages == dialogues[-1]

        await context.add_messages(messages)
        window = await context.get_context_window(dialogue_round=1)
        assert window.context_messages == dialogues[-1]
        context.clear_messages()

        await context.add_messages(messages)
        window = await context.get_context_window(dialogue_round=2)
        assert window.context_messages == dialogues[-2] + dialogues[-1]
        context.clear_messages()

        await context.add_messages(messages)
        window = await context.get_context_window(dialogue_round=3)
        assert window.context_messages == messages
        context.clear_messages()

        await context.add_messages(messages)
        window = await context.get_context_window(dialogue_round=4)
        assert window.context_messages == messages
        context.clear_messages()

    # ---------- max_context_message_num ----------
    @pytest.mark.asyncio
    async def test_max_context_message_num_triggers_resize(self):
        context = await self.create_context(max_context_message_num=50)
        msg_list = [UserMessage(content=f"m-{i}") for i in range(150)]
        await context.add_messages(msg_list)
        assert len(context) <= 50

    @pytest.mark.asyncio
    async def test_max_context_message_num_none_no_limit(self):
        context = await self.create_context(max_context_message_num=None)
        msg_list = [UserMessage(content=f"m-{i}") for i in range(200)]
        await context.add_messages(msg_list)
        assert len(context) == 200

    # ---------- enable_reload ----------
    @pytest.mark.asyncio
    async def test_enable_reload_adds_reloader_prompt(self):
        from openjiuwen.core.context_engine.context.context import _RELOADER_SYSTEM_PROMPT
        context = await self.create_context(enable_reload=True)
        window = await context.get_context_window()
        assert len(window.system_messages) == 1
        assert window.system_messages[0].content == _RELOADER_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_enable_reload_false_no_reloader_prompt(self):
        context = await self.create_context(enable_reload=False)
        window = await context.get_context_window()
        assert window.system_messages == []

    @pytest.mark.asyncio
    async def test_enable_reload_with_custom_system_messages(self):
        from openjiuwen.core.context_engine.context.context import _RELOADER_SYSTEM_PROMPT
        context = await self.create_context(enable_reload=True)
        sys_msgs = [SystemMessage(content="custom sys")]
        window = await context.get_context_window(system_messages=sys_msgs)
        assert len(window.system_messages) == 2
        assert window.system_messages[0].content == "custom sys"
        assert window.system_messages[1].content == _RELOADER_SYSTEM_PROMPT

    # ---------- enable_kv_cache_release ----------
    @pytest.mark.asyncio
    async def test_enable_kv_cache_release_creates_manager(self):
        context = await self.create_context(enable_kv_cache_release=True)
        assert getattr(context, "_kv_cache_manager") is not None

    @pytest.mark.asyncio
    async def test_enable_kv_cache_release_calls_release_on_get_window(self):
        context = await self.create_context(enable_kv_cache_release=True)
        with patch.object(getattr(context, "_kv_cache_manager"), "release", new_callable=AsyncMock) as mock_release:
            await context.get_context_window()
            await context.get_context_window()
            assert mock_release.call_count >= 1

    # ---------- token_counter ----------
    @pytest.mark.asyncio
    async def test_token_counter_none_returns_zero_tokens(self):
        context = await self.create_context()
        await context.add_messages([UserMessage(content="hi")])
        stat = context.statistic()
        assert stat.total_messages == 1
        assert stat.total_tokens == 0
        assert stat.user_message_tokens == 0

    @pytest.mark.asyncio
    async def test_token_counter_mock_returns_tokens(self):
        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=10)
        mock_counter.count_tools = MagicMock(return_value=5)
        context = await self.create_context(token_counter=mock_counter)
        await context.add_messages([UserMessage(content="hi")])
        stat = context.statistic()
        assert stat.user_message_tokens == 10
        tools = [ToolInfo(name="t1", description="d1")]
        window = await context.get_context_window(tools=tools)
        assert window.statistic.tool_tokens == 5

    # ---------- processors ----------
    @pytest.mark.asyncio
    async def test_processors_empty_add_messages_succeeds(self):
        context = await self.create_context(processors=[])
        await context.add_messages([UserMessage(content="hi")])
        assert context.get_messages() == [UserMessage(content="hi")]

    @pytest.mark.asyncio
    async def test_processor_exception_does_not_block_add_messages(self):
        from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
            CurrentRoundCompressorConfig,
        )
        mock_proc = MagicMock()
        mock_proc.processor_type = MagicMock(return_value="MockProcessor")
        mock_proc.trigger_add_messages = AsyncMock(return_value=True)
        mock_proc.on_add_messages = AsyncMock(side_effect=Exception("processor failed"))

        with patch.object(ContextEngine, "_create_processor", return_value=mock_proc):
            engine = ContextEngine(ContextEngineConfig(default_window_message_num=10))
            ctx = await engine.create_context(
                "test", None,
                processors=[("MockProcessor", CurrentRoundCompressorConfig())],
            )
            await ctx.add_messages([UserMessage(content="msg")])
            assert len(ctx) == 1
            assert ctx.get_messages()[0].content == "msg"

    # ---------- reloader_tool ----------
    @pytest.mark.asyncio
    async def test_reloader_tool_offload_then_reload_returns_content(self):
        context = await self.create_context()
        msgs = [UserMessage(content="secret"), AssistantMessage(content="reply")]
        context.offload_messages("handle-1", msgs)
        tool = context.reloader_tool()
        result = await tool.invoke(dict(offload_handle="handle-1", offload_type="in_memory"))
        assert "handle-1" in result
        assert "secret" in result or "reply" in result

    @pytest.mark.asyncio
    async def test_reloader_tool_nonexistent_returns_failure_message(self):
        context = await self.create_context()
        tool = context.reloader_tool()
        result = await tool.invoke(dict(offload_handle="nonexistent", offload_type="in_memory"))
        assert "Failed to reload" in result
        assert "nonexistent" in result

    @pytest.mark.asyncio
    async def test_reloader_tool_card_id_contains_session_and_context(self):
        context = await self.create_context()
        tool = context.reloader_tool()
        assert "default_session_id" in tool.card.id or "test_context" in tool.card.id

    # ---------- offload_messages ----------
    @pytest.mark.asyncio
    async def test_offload_messages_save_state_includes_offload(self):
        context = await self.create_context()
        msgs = [UserMessage(content="x")]
        context.offload_messages("h1", msgs)
        state = context.save_state()
        assert "offload_messages" in state
        assert "h1" in state["offload_messages"] or any(
            "h1" in str(k) for k in state["offload_messages"]
        )

    # ---------- save_state / load_state ----------
    @pytest.mark.asyncio
    async def test_save_state_structure(self):
        context = await self.create_context()
        await context.add_messages([UserMessage(content="a")])
        state = context.save_state()
        assert "messages" in state
        assert "offload_messages" in state
        assert len(state["messages"]) == 1

    @pytest.mark.asyncio
    async def test_load_state_restores_messages(self):
        context = await self.create_context()
        msgs = [UserMessage(content="loaded"), AssistantMessage(content="resp")]
        state = {context.context_id(): {"messages": msgs, "offload_messages": {}}}
        context.load_state(state)
        assert context.get_messages() == msgs

    @pytest.mark.asyncio
    async def test_load_state_restores_offload_messages(self):
        context = await self.create_context()
        offload_msgs = [UserMessage(content="offloaded")]
        state = {
            context.context_id(): {
                "messages": [],
                "offload_messages": {"h1": offload_msgs},
            }
        }
        context.load_state(state)
        tool = context.reloader_tool()
        result = await tool.invoke(dict(offload_handle="h1", offload_type="in_memory"))
        assert "offloaded" in result

    @pytest.mark.asyncio
    async def test_load_state_empty_state_clears_buffer(self):
        context = await self.create_context()
        await context.add_messages([UserMessage(content="x")])
        context.load_state({})
        assert len(context) == 0

    @pytest.mark.asyncio
    async def test_load_state_wrong_context_id_clears_buffer(self):
        context = await self.create_context()
        await context.add_messages([UserMessage(content="x")])
        state = {"other_context": {"messages": [UserMessage(content="other")], "offload_messages": {}}}
        context.load_state(state)
        assert len(context) == 0

    # ---------- get_context_window edge cases ----------
    @pytest.mark.asyncio
    async def test_get_context_window_invalid_dialogue_round(self):
        context = await self.create_context()
        with pytest.raises(BaseError) as exc_info:
            await context.get_context_window(dialogue_round=0)
        assert exc_info.value.code == StatusCode.CONTEXT_EXECUTION_ERROR.code

    @pytest.mark.asyncio
    async def test_get_context_window_with_tools(self):
        context = await self.create_context()
        tools = [ToolInfo(name="my_tool", description="test")]
        window = await context.get_context_window(tools=tools)
        assert window.tools == tools
        assert window.statistic.tools == 1

    @pytest.mark.asyncio
    async def test_dialogue_round_overrides_window_size_when_both_passed(self):
        context = await self.create_context(
            window_message_limit=100,
            dialogue_round=1,
        )
        dialogues = [
            [UserMessage(content="u1"), AssistantMessage(content="a1")],
            [UserMessage(content="u2"), AssistantMessage(content="a2")],
        ]
        messages = dialogues[0] + dialogues[1]
        await context.add_messages(messages)
        window = await context.get_context_window(window_size=100, dialogue_round=1)
        assert window.context_messages == dialogues[-1]

    # ---------- session_id / context_id ----------
    @pytest.mark.asyncio
    async def test_session_id_and_context_id(self):
        context = await self.create_context()
        assert context.session_id() == "default_session_id"
        assert context.context_id() == "test_context"

    # ---------- clear_messages ----------
    @pytest.mark.asyncio
    async def test_clear_messages_with_history_true_clears_all(self):
        history = [UserMessage(content="h1")]
        context = await self.create_context(history=history)
        await context.add_messages([UserMessage(content="n1")])
        context.clear_messages(with_history=True)
        assert len(context) == 0

    @pytest.mark.asyncio
    async def test_clear_messages_with_history_false_keeps_history(self):
        history = [UserMessage(content="h1")]
        context = await self.create_context(history=history)
        await context.add_messages([UserMessage(content="n1")])
        context.clear_messages(with_history=False)
        assert len(context) == 1
        assert context.get_messages()[0].content == "h1"

    @pytest.mark.asyncio
    async def test_model_context_window_with_incomplete_dialogue_round(self):
        def create_tool_call_list(tcs: List[str]):
            return [
                ToolCall(id=tc, name="test-tool", type="function", arguments="")
                for tc in tcs
            ]
        context = await self.create_context(dialogue_round=1)
        dialogues = [
            # dialogue-1
            [
                UserMessage(content="user-1"),
                AssistantMessage(content="assistant-1", tool_calls=create_tool_call_list(["tc-1", "tc-2", "tc-3"])),
                ToolMessage(content="tool-1", tool_call_id='tc-1'),
                ToolMessage(content="tool-2", tool_call_id='tc-2'),
                ToolMessage(content="tool-3", tool_call_id='tc-3'),
                AssistantMessage(content="assistant-2"),
                UserMessage(content="user-1-1"),
            ],
            # dialogue-2
            [
                UserMessage(content="user-2"),
                AssistantMessage(content="assistant-3", tool_calls=create_tool_call_list(["tc-1", "tc-2"])),
                ToolMessage(content="tool-4", tool_call_id='tc-1'),
                ToolMessage(content="tool-5", tool_call_id='tc-2'),
                AssistantMessage(content="assistant-4"),
            ],
            # dialogue-3
            [
                UserMessage(content="user-3"),
            ]
        ]

        messages = []
        for dialogue in dialogues:
            messages.extend(dialogue)

        await context.add_messages(messages)
        window = await context.get_context_window()
        assert window.context_messages == dialogues[-1]

        await context.add_messages(messages)
        window = await context.get_context_window(dialogue_round=1)
        assert window.context_messages == dialogues[-1]
        context.clear_messages()

        await context.add_messages(messages)
        window = await context.get_context_window(dialogue_round=2)
        assert window.context_messages == dialogues[-2] + dialogues[-1]
        context.clear_messages()

        await context.add_messages(messages)
        window = await context.get_context_window(dialogue_round=3)
        assert window.context_messages == messages
        context.clear_messages()

        await context.add_messages(messages)
        window = await context.get_context_window(dialogue_round=4)
        assert window.context_messages == messages
        context.clear_messages()

