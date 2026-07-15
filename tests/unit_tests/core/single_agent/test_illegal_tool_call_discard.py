# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for illegal tool_call discard before the execute loop."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.single_agent.ability_manager import (
    AbilityManager,
    illegal_tool_call_reason,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


class TestIllegalToolCallReason(unittest.TestCase):
    def test_empty_name(self):
        self.assertEqual(
            illegal_tool_call_reason(SimpleNamespace(id="call_1", name="")),
            "empty_tool_name",
        )
        self.assertEqual(
            illegal_tool_call_reason(SimpleNamespace(id="call_1", name="  ")),
            "empty_tool_name",
        )

    def test_empty_id(self):
        self.assertEqual(
            illegal_tool_call_reason(SimpleNamespace(id="", name="bash")),
            "empty_tool_call_id",
        )

    def test_empty_name_takes_priority_over_empty_id(self):
        self.assertEqual(
            illegal_tool_call_reason(SimpleNamespace(id="", name="")),
            "empty_tool_name",
        )

    def test_legal(self):
        self.assertIsNone(
            illegal_tool_call_reason(SimpleNamespace(id="call_1", name="bash"))
        )


class TestExecuteSingleDiscardsIllegalToolCall(unittest.IsolatedAsyncioTestCase):
    async def test_empty_name_returns_error_without_railed_execute(self):
        manager = AbilityManager()
        parent_ctx = AgentCallbackContext(agent=MagicMock())
        tool_call = ToolCall(
            id="call_empty",
            type="function",
            name="",
            arguments="{}",
        )
        session = MagicMock()

        with patch.object(
            manager,
            "_railed_execute_single_tool_call",
            new_callable=AsyncMock,
        ) as railed:
            result, tool_msg, tool_ctx = await manager.execute_single(
                parent_ctx=parent_ctx,
                tool_call=tool_call,
                session=session,
            )

        railed.assert_not_called()
        self.assertFalse(getattr(result, "success", True))
        self.assertIsInstance(tool_msg, ToolMessage)
        self.assertIn("illegal_tool_call", tool_msg.content)
        self.assertEqual(tool_msg.metadata.get("illegal_tool_call"), "empty_tool_name")
        self.assertIs(tool_ctx.inputs.tool_msg, tool_msg)

    async def test_empty_id_returns_error_without_railed_execute(self):
        manager = AbilityManager()
        parent_ctx = AgentCallbackContext(agent=MagicMock())
        tool_call = ToolCall(
            id="",
            type="function",
            name="bash",
            arguments="{}",
        )
        session = MagicMock()

        with patch.object(
            manager,
            "_railed_execute_single_tool_call",
            new_callable=AsyncMock,
        ) as railed:
            result, tool_msg, _tool_ctx = await manager.execute_single(
                parent_ctx=parent_ctx,
                tool_call=tool_call,
                session=session,
            )

        railed.assert_not_called()
        self.assertIsInstance(tool_msg, ToolMessage)
        self.assertEqual(tool_msg.metadata.get("illegal_tool_call"), "empty_tool_call_id")
        self.assertIn("empty_tool_call_id", tool_msg.content)


class TestStripIllegalToolCallsFromAssistant(unittest.TestCase):
    """Mirrors ReActAgent pre-context strip using the shared reason helper."""

    def test_strip_keeps_only_legal_tool_calls(self):
        tool_calls = [
            ToolCall(id="ok", type="function", name="bash", arguments="{}"),
            ToolCall(id="", type="function", name="todo_list", arguments="{}"),
            ToolCall(id="x", type="function", name="", arguments="{}"),
        ]
        kept = [tc for tc in tool_calls if illegal_tool_call_reason(tc) is None]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].name, "bash")
        self.assertEqual(kept[0].id, "ok")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
