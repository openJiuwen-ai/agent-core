# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for openjiuwen.core.operator.tool_call module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.operator.tool_call import ToolCallOperator


class TestToolCallOperator:
    """Tests for ToolCallOperator class."""

    @pytest.fixture
    def mock_tool(self):
        """Create a mock tool."""
        tool = MagicMock()
        tool.invoke = AsyncMock(return_value={"result": "success"})
        return tool

    @pytest.fixture
    def operator(self, mock_tool):
        """Create a ToolCallOperator instance."""
        return ToolCallOperator(tool=mock_tool)

    @pytest.fixture
    def mock_tool_registry(self):
        """Create a mock tool registry."""
        registry = MagicMock()
        registry.get_tool_defs = MagicMock(
            return_value=[
                {"name": "tool1", "description": "Tool 1"},
                {"name": "tool2", "description": "Tool 2"},
            ]
        )
        registry.set_tool_description = MagicMock()
        return registry

    @staticmethod
    def test_operator_id_default(operator):
        """Test default operator_id."""
        assert operator.operator_id == "tool_call"

    @staticmethod
    def test_operator_id_custom():
        """Test custom operator_id."""
        op = ToolCallOperator(tool=MagicMock(), tool_call_id="custom_tool")
        assert op.operator_id == "custom_tool"

    @staticmethod
    def test_get_tunables(operator):
        """Test get_tunables returns enabled and max_retries without registry."""
        tunables = operator.get_tunables()
        assert "tool_description" not in tunables

    @staticmethod
    def test_get_tunables_with_registry(mock_tool_registry):
        """Test get_tunables returns tool_description with registry."""
        op = ToolCallOperator(tool=MagicMock(), tool_registry=mock_tool_registry)
        tunables = op.get_tunables()
        assert "tool_description" in tunables
        assert tunables["tool_description"].kind == "text"

    @staticmethod
    def test_set_parameter_tool_description(mock_tool_registry):
        """Test set_parameter for tool_description."""
        op = ToolCallOperator(tool=MagicMock(), tool_registry=mock_tool_registry)
        op.set_parameter(
            "tool_description",
            {
                "tool1": "Updated description 1",
                "tool2": "Updated description 2",
            },
        )
        mock_tool_registry.set_tool_description.assert_any_call("tool1", "Updated description 1")
        mock_tool_registry.set_tool_description.assert_any_call("tool2", "Updated description 2")

    @staticmethod
    def test_set_parameter_unknown_target(operator):
        """Test set_parameter ignores unknown targets."""
        # Should not raise
        operator.set_parameter("unknown", "value")

    @staticmethod
    def test_set_parameter_invalid_value(mock_tool_registry):
        """Test set_parameter ignores non-dict values."""
        op = ToolCallOperator(tool=MagicMock(), tool_registry=mock_tool_registry)
        op.set_parameter("tool_description", "not a dict")
        mock_tool_registry.set_tool_description.assert_not_called()

    @staticmethod
    def test_set_parameter_no_registry(operator):
        """Test set_parameter does nothing without registry."""
        operator.set_parameter("tool_description", {"tool1": "desc"})


class TestToolCallOperatorInvoke:
    """Tests for ToolCallOperator invoke functionality."""

    @pytest.fixture
    def mock_tool(self):
        """Create a mock tool."""
        tool = MagicMock()
        tool.invoke = AsyncMock(return_value={"result": "success"})
        return tool

    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        session = MagicMock()
        session.set_current_operator_id = MagicMock()
        return session

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_basic(mock_tool, mock_session):
        """Test basic invoke functionality."""
        op = ToolCallOperator(tool=mock_tool)
        result = await op.invoke(
            inputs={"param": "value"},
            session=mock_session,
        )
        mock_tool.invoke.assert_called_once()
        assert result == {"result": "success"}

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_with_kwargs(mock_tool, mock_session):
        """Test invoke passes additional kwargs."""
        op = ToolCallOperator(tool=mock_tool)
        await op.invoke(
            inputs={},
            session=mock_session,
            extra_arg="test",
        )
        mock_tool.invoke.assert_called_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_no_tool_configured(mock_session):
        """Test invoke raises error when no tool is configured."""
        op = ToolCallOperator()
        with pytest.raises(RuntimeError, match="no tool"):
            await op.invoke(inputs={}, session=mock_session)

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_router_mode(mock_session):
        """Test invoke in router mode with tool_calls."""
        tool_calls = [{"name": "func1", "args": {}}, {"name": "func2", "args": {}}]

        async def mock_executor(tool_call, session):
            return {"result": f"executed {tool_call['name']}"}, None

        op = ToolCallOperator(tool_executor=mock_executor)
        result = await op.invoke(
            inputs={"tool_calls": tool_calls},
            session=mock_session,
        )
        assert len(result) == 2


class TestToolCallOperatorStream:
    """Tests for ToolCallOperator streaming functionality."""

    @pytest.fixture
    def mock_streaming_tool(self):
        """Create a mock tool with streaming support."""
        tool = MagicMock()

        async def mock_stream(*args, **kwargs):
            yield "chunk1"
            yield "chunk2"

        tool.stream = mock_stream
        return tool

    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        session = MagicMock()
        session.set_current_operator_id = MagicMock()
        return session

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_basic(mock_streaming_tool, mock_session):
        """Test basic streaming functionality."""
        op = ToolCallOperator(tool=mock_streaming_tool)
        chunks = []
        async for chunk in op.stream(inputs={}, session=mock_session):
            chunks.append(chunk)
        assert len(chunks) == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_not_implemented(mock_session):
        """Test stream raises NotImplementedError for non-streaming tools."""
        tool = MagicMock()
        del tool.stream  # Remove stream method
        op = ToolCallOperator(tool=tool)
        with pytest.raises(NotImplementedError):
            async for _ in op.stream(inputs={}, session=mock_session):  # noqa: F841
                pass

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_context_cleanup(mock_streaming_tool, mock_session):
        """Test operator context is cleaned up after streaming."""
        op = ToolCallOperator(tool=mock_streaming_tool)
        async for _ in op.stream(inputs={}, session=mock_session):
            pass
        # Context should be cleared
        mock_session.set_current_operator_id.assert_any_call(None)


class TestToolCallOperatorEdgeCases:
    """Tests for edge cases in ToolCallOperator."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        session = MagicMock()
        session.set_current_operator_id = MagicMock()
        return session

    @staticmethod
    @pytest.mark.asyncio
    async def test_set_parameter_calls_registry():
        """Test set_parameter correctly calls registry methods."""
        registry = MagicMock()
        registry.set_tool_description = MagicMock()

        op = ToolCallOperator(tool_registry=registry)
        op.set_parameter("tool_description", {"tool1": "new desc"})

        registry.set_tool_description.assert_called_once_with("tool1", "new desc")

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_router_mode_normal_flow(mock_session):
        """Test router mode with normal flow (no retries)."""
        tool_calls = [{"name": "func1", "args": {}}]

        async def mock_executor(tool_call, session):
            return {"result": "success"}, None

        op = ToolCallOperator(tool_executor=mock_executor)
        result = await op.invoke(
            inputs={"tool_calls": tool_calls},
            session=mock_session,
        )
        assert result[0][0] == {"result": "success"}
