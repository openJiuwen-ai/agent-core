# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""单元测试：ExternalMemoryRail"""

from __future__ import annotations

from openjiuwen.harness.rails.memory.external_memory_rail import ExternalMemoryRail
from openjiuwen.core.memory.external.provider import MemoryProvider


class MockInputs:
    """测试用 Mock Inputs"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockCallbackContext:
    """测试用 Mock AgentCallbackContext"""

    def __init__(self, inputs):
        self.inputs = inputs


class MockMemoryProvider(MemoryProvider):
    """测试用 Mock Provider"""

    def __init__(self):
        self._initialized = False
        self.prefetch_calls = []
        self.sync_turn_calls = []

    @property
    def name(self) -> str:
        return "mock_provider"

    def is_available(self) -> bool:
        return self._initialized

    async def initialize(self, **kwargs) -> None:
        self._initialized = True

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_search",
                "description": "Search memory",
                "parameters": {"type": "object", "properties": {}}
            },
        ]

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        return '{"result": "success"}'

    async def prefetch(self, query: str, **kwargs) -> str:
        self.prefetch_calls.append({"query": query, "kwargs": kwargs})
        return f"Memory context for: {query}"

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None:
        self.sync_turn_calls.append({
            "user_msg": user_msg,
            "assistant_msg": assistant_msg,
            "kwargs": kwargs
        })

    def system_prompt_block(self) -> str:
        return "Use memory_search tool."

    @property
    def is_initialized(self) -> bool:
        return self._initialized


class TestResolveUserTextForMemory:
    """Test _resolve_user_text_for_memory method."""

    def test_only_query(self):
        """Scenario 1: Only query field."""
        inputs = MockInputs(query="test query")
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "test query"

    def test_only_messages(self):
        """Scenario 2: Only messages user messages."""      
        inputs = MockInputs(messages=[
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "test message"}
        ])
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "test message"

    def test_both_query_and_messages(self):
        """Scenario 3: Both query and messages, priority query."""
        inputs = MockInputs(
            query="query value",
            messages=[
                {"role": "user", "content": "message value"}
            ]
        )
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "query value"

    def test_both_empty(self):
        """Scenario 4: Both query and messages are empty."""
        inputs = MockInputs()
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == ""

    def test_messages_with_list_content(self):
        """Scenario 5: messages content is a list."""
        inputs = MockInputs(messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "hello world"}
            ]}
        ])
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "hello world"

    def test_messages_with_multiple_user_take_last(self):
        """Scenario 6: Multiple user messages, take last one."""
        inputs = MockInputs(messages=[
            {"role": "user", "content": "first"},
            {"role": "user", "content": "last"}
        ])
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "last"


class TestExtractAssistantOutput:
    """_extract_assistant_output 方法测试"""

    def test_result_with_output_key(self):
        """Scenario 1: result.output format."""
        inputs = MockInputs(result={"output": "assistant response"})
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == "assistant response"

    def test_result_with_message_content(self):
        """Scenario 2: result.message.content format."""
        inputs = MockInputs(result={
            "message": {"content": "assistant response"}
        })
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == "assistant response"

    def test_result_with_content_key(self):
        """Scenario 3: result.content format."""
        inputs = MockInputs(result={"content": "assistant response"})
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == "assistant response"

    def test_result_missing(self):
        """Scenario 4: result is missing."""
        inputs = MockInputs()
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == ""

    def test_result_with_unknown_keys(self):
        """Scenario 5: result has unknown keys."""
        inputs = MockInputs(result={"unknown": "value", "other": 123})
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == ""


class TestBuildMemoryContextBlock:
    """Test _build_memory_context_block method."""

    def test_build_memory_context(self):
        """Scenario 1: Build memory context block."""
        raw = "Previous conversation context"
        result = ExternalMemoryRail._build_memory_context_block(raw)

        assert "<memory-context>" in result
        assert "Previous conversation context" in result
        assert "</memory-context>" in result
        assert "NOT new user input" in result