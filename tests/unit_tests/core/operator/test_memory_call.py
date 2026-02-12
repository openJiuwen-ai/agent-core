# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for openjiuwen.core.operator.memory_call module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.operator.memory_call import MemoryCallOperator


class TestMemoryCallOperator:
    """Tests for MemoryCallOperator class."""

    @pytest.fixture
    def mock_memory(self):
        """Create a mock memory instance."""
        memory = MagicMock()
        memory.invoke = AsyncMock(return_value={"retrieved": "data"})
        return memory

    @pytest.fixture
    def operator(self, mock_memory):
        """Create a MemoryCallOperator instance."""
        return MemoryCallOperator(memory=mock_memory)

    @staticmethod
    def test_operator_id_default(operator):
        """Test default operator_id."""
        assert operator.operator_id == "memory_call"

    @staticmethod
    def test_operator_id_custom():
        """Test custom operator_id."""
        op = MemoryCallOperator(memory=MagicMock(), memory_call_id="custom_memory")
        assert op.operator_id == "custom_memory"

    @staticmethod
    def test_get_tunables(operator):
        """Test get_tunables returns enabled and max_retries."""
        tunables = operator.get_tunables()
        assert "enabled" in tunables
        assert "max_retries" in tunables
        assert tunables["enabled"].kind == "discrete"
        assert tunables["max_retries"].kind == "discrete"

    @staticmethod
    def test_get_tunables_constraints(operator):
        """Test tunable constraints are correctly set."""
        tunables = operator.get_tunables()
        assert tunables["enabled"].constraint == {"type": "bool"}
        assert tunables["max_retries"].constraint == {"type": "int", "min": 0, "max": 5}

    @staticmethod
    def test_set_parameter_enabled(operator):
        """Test set_parameter for enabled."""
        operator.set_parameter("enabled", False)
        assert operator.get_state()["enabled"] is False
        operator.set_parameter("enabled", True)
        assert operator.get_state()["enabled"] is True

    @staticmethod
    def test_set_parameter_max_retries(operator):
        """Test set_parameter for max_retries."""
        operator.set_parameter("max_retries", 3)
        assert operator.get_state()["max_retries"] == 3

    @staticmethod
    def test_set_parameter_max_retries_clamped(operator):
        """Test set_parameter clamps max_retries to 0-5."""
        operator.set_parameter("max_retries", 10)
        assert operator.get_state()["max_retries"] == 5
        operator.set_parameter("max_retries", -1)
        assert operator.get_state()["max_retries"] == 0

    @staticmethod
    def test_get_state(operator):
        """Test get_state returns enabled and max_retries."""
        state = operator.get_state()
        assert "enabled" in state
        assert "max_retries" in state
        assert state["enabled"] is True
        assert state["max_retries"] == 0

    @staticmethod
    def test_get_state_with_custom_values():
        """Test get_state with custom values."""
        op = MemoryCallOperator(memory=MagicMock())
        op.load_state({"enabled": False, "max_retries": 3})
        state = op.get_state()
        assert state["enabled"] is False
        assert state["max_retries"] == 3

    @staticmethod
    def test_load_state(operator):
        """Test load_state restores state."""
        operator.load_state({"enabled": False, "max_retries": 2})
        state = operator.get_state()
        assert state["enabled"] is False
        assert state["max_retries"] == 2

    @staticmethod
    def test_load_state_partial(operator):
        """Test load_state with partial state."""
        operator.load_state({"enabled": False})
        state = operator.get_state()
        assert state["enabled"] is False
        assert state["max_retries"] == 0

    @staticmethod
    def test_load_state_clamped_retries():
        """Test load_state clamps max_retries to 0-5."""
        op = MemoryCallOperator(memory=MagicMock())
        op.load_state({"max_retries": 10})
        assert op.get_state()["max_retries"] == 5
        op.load_state({"max_retries": -1})
        assert op.get_state()["max_retries"] == 0


class TestMemoryCallOperatorInvoke:
    """Tests for MemoryCallOperator invoke functionality."""

    @pytest.fixture
    def mock_memory(self):
        """Create a mock memory instance."""
        memory = MagicMock()
        memory.invoke = AsyncMock(return_value={"retrieved": "data"})
        return memory

    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        session = MagicMock()
        session.set_current_operator_id = MagicMock()
        return session

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_basic(mock_memory, mock_session):
        """Test basic invoke functionality."""
        op = MemoryCallOperator(memory=mock_memory)
        result = await op.invoke(
            inputs={"query": "test query"},
            session=mock_session,
        )
        mock_memory.invoke.assert_called_once()
        assert result == {"retrieved": "data"}

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_with_kwargs(mock_memory, mock_session):
        """Test invoke passes kwargs to memory."""
        op = MemoryCallOperator(memory=mock_memory)
        await op.invoke(
            inputs={},
            session=mock_session,
            extra_param="value",
        )
        mock_memory.invoke.assert_called_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_disabled_operator(mock_session):
        """Test invoke raises error when operator is disabled."""
        op = MemoryCallOperator(memory=MagicMock())
        op.set_parameter("enabled", False)
        with pytest.raises(RuntimeError, match="disabled"):
            await op.invoke(inputs={}, session=mock_session)

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_no_memory_configured(mock_session):
        """Test invoke raises error when no memory is configured."""
        op = MemoryCallOperator()
        with pytest.raises(RuntimeError, match="no memory"):
            await op.invoke(inputs={}, session=mock_session)

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_with_retries_success_first(mock_memory, mock_session):
        """Test invoke retries and succeeds on first attempt."""
        op = MemoryCallOperator(memory=mock_memory)
        op.set_parameter("max_retries", 3)
        await op.invoke(inputs={}, session=mock_session)
        mock_memory.invoke.assert_called_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_with_retries_failure(mock_memory, mock_session):
        """Test invoke retries and then raises on failure."""
        mock_memory.invoke = AsyncMock(side_effect=ValueError("error"))
        op = MemoryCallOperator(memory=mock_memory)
        op.set_parameter("max_retries", 2)
        with pytest.raises(ValueError):
            await op.invoke(inputs={}, session=mock_session)
        # Should be called 3 times (initial + 2 retries)
        assert mock_memory.invoke.call_count == 3


class TestMemoryCallOperatorCustomInvoke:
    """Tests for MemoryCallOperator with custom memory_invoke."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        session = MagicMock()
        session.set_current_operator_id = MagicMock()
        return session

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_with_custom_callback(mock_session):
        """Test invoke uses custom memory_invoke callback."""
        callback = AsyncMock(return_value={"custom": "result"})

        op = MemoryCallOperator(memory_invoke=callback)
        result = await op.invoke(inputs={}, session=mock_session)

        callback.assert_called_once()
        assert result == {"custom": "result"}

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_callback_takes_precedence(mock_session):
        """Test memory_invoke callback takes precedence over memory.invoke."""
        callback = AsyncMock(return_value={"callback": "result"})
        mock_memory = MagicMock()
        mock_memory.invoke = AsyncMock(return_value={"memory": "result"})

        op = MemoryCallOperator(memory=mock_memory, memory_invoke=callback)
        result = await op.invoke(inputs={}, session=mock_session)

        callback.assert_called_once()
        mock_memory.invoke.assert_not_called()
        assert result == {"callback": "result"}


class TestMemoryCallOperatorStream:
    """Tests for MemoryCallOperator streaming functionality."""

    @pytest.fixture
    def mock_streaming_memory(self):
        """Create a mock memory with streaming support."""
        memory = MagicMock()

        async def mock_stream(*args, **kwargs):
            yield "chunk1"
            yield "chunk2"

        memory.stream = mock_stream
        return memory

    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        session = MagicMock()
        session.set_current_operator_id = MagicMock()
        return session

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_basic(mock_streaming_memory, mock_session):
        """Test basic streaming functionality."""
        op = MemoryCallOperator(memory=mock_streaming_memory)
        chunks = []
        async for chunk in op.stream(inputs={}, session=mock_session):
            chunks.append(chunk)
        assert len(chunks) == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_not_implemented(mock_session):
        """Test stream raises NotImplementedError for non-streaming memory."""
        memory = MagicMock()
        del memory.stream  # Remove stream method
        op = MemoryCallOperator(memory=memory)
        with pytest.raises(NotImplementedError):
            async for _ in op.stream(inputs={}, session=mock_session):  # noqa: F841
                pass

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_context_cleanup(mock_streaming_memory, mock_session):
        """Test operator context is cleaned up after streaming."""
        op = MemoryCallOperator(memory=mock_streaming_memory)
        async for _ in op.stream(inputs={}, session=mock_session):
            pass
        # Context should be cleared
        mock_session.set_current_operator_id.assert_any_call(None)


class TestMemoryCallOperatorEdgeCases:
    """Tests for edge cases in MemoryCallOperator."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        session = MagicMock()
        session.set_current_operator_id = MagicMock()
        return session

    @staticmethod
    @pytest.mark.asyncio
    async def test_set_parameter_unknown_target():
        """Test set_parameter ignores unknown targets."""
        op = MemoryCallOperator(memory=MagicMock())
        # Should not raise
        op.set_parameter("unknown", "value")

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_with_disabled_memory_invoke_mode(mock_session):
        """Test invoke in memory_invoke mode when operator is disabled."""
        callback = AsyncMock(return_value={})
        op = MemoryCallOperator(memory_invoke=callback)
        op.set_parameter("enabled", False)
        with pytest.raises(RuntimeError, match="disabled"):
            await op.invoke(inputs={}, session=mock_session)
        callback.assert_not_called()

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_empty_inputs(mock_session):
        """Test invoke with empty inputs dict."""
        memory = MagicMock()
        memory.invoke = AsyncMock(return_value={})
        op = MemoryCallOperator(memory=memory)
        await op.invoke(inputs={}, session=mock_session)
        memory.invoke.assert_called_once()
