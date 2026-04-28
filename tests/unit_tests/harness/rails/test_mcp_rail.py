# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""单元测试：McpRail — 初始化、工具注册、清理。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.harness.rails.mcp_rail import McpRail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(language: str = "cn", agent_id: str | None = "test-agent-id") -> MagicMock:
    agent = MagicMock()
    agent.system_prompt_builder.language = language
    card = MagicMock()
    card.id = agent_id
    agent.card = card
    agent.ability_manager = MagicMock()
    return agent


def _make_tool(name: str, tool_id: str) -> MagicMock:
    tool = MagicMock()
    tool.card = MagicMock()
    tool.card.name = name
    tool.card.id = tool_id
    return tool


# ---------------------------------------------------------------------------
# 1. 构造函数
# ---------------------------------------------------------------------------

class TestMcpRailConstructor:
    def test_tools_initially_none(self):
        assert McpRail().tools is None

    def test_priority_is_95(self):
        assert McpRail.priority == 95


# ---------------------------------------------------------------------------
# 2. init() — 工具注册
# ---------------------------------------------------------------------------

class TestMcpRailInit:
    def _make_tools(self):
        list_tool = _make_tool("list_mcp_resources", "ListMcpResourcesTool_abc")
        read_tool = _make_tool("read_mcp_resource", "ReadMcpResourceTool_abc")
        return list_tool, read_tool

    def test_registers_two_tools_in_resource_manager(self):
        rail = McpRail()
        agent = _make_agent()
        list_tool, read_tool = self._make_tools()

        with patch("openjiuwen.harness.rails.mcp_rail.ListMcpResourcesTool", return_value=list_tool), \
             patch("openjiuwen.harness.rails.mcp_rail.ReadMcpResourceTool", return_value=read_tool), \
             patch("openjiuwen.harness.rails.mcp_rail.Runner") as MockRunner:
            rail.init(agent)

        MockRunner.resource_mgr.add_tool.assert_called_once_with([list_tool, read_tool])

    def test_adds_both_cards_to_ability_manager(self):
        rail = McpRail()
        agent = _make_agent()
        list_tool, read_tool = self._make_tools()

        with patch("openjiuwen.harness.rails.mcp_rail.ListMcpResourcesTool", return_value=list_tool), \
             patch("openjiuwen.harness.rails.mcp_rail.ReadMcpResourceTool", return_value=read_tool), \
             patch("openjiuwen.harness.rails.mcp_rail.Runner"):
            rail.init(agent)

        calls = [c.args[0] for c in agent.ability_manager.add.call_args_list]
        assert list_tool.card in calls
        assert read_tool.card in calls

    def test_tools_attribute_set_after_init(self):
        rail = McpRail()
        agent = _make_agent()
        list_tool, read_tool = self._make_tools()

        with patch("openjiuwen.harness.rails.mcp_rail.ListMcpResourcesTool", return_value=list_tool), \
             patch("openjiuwen.harness.rails.mcp_rail.ReadMcpResourceTool", return_value=read_tool), \
             patch("openjiuwen.harness.rails.mcp_rail.Runner"):
            rail.init(agent)

        assert rail.tools == [list_tool, read_tool]

    def test_tools_constructed_with_agent_language_and_id(self):
        rail = McpRail()
        agent = _make_agent(language="en", agent_id="my-agent")

        with patch("openjiuwen.harness.rails.mcp_rail.ListMcpResourcesTool") as MockList, \
             patch("openjiuwen.harness.rails.mcp_rail.ReadMcpResourceTool") as MockRead, \
             patch("openjiuwen.harness.rails.mcp_rail.Runner"):
            MockList.return_value = _make_tool("list_mcp_resources", "lid")
            MockRead.return_value = _make_tool("read_mcp_resource", "rid")
            rail.init(agent)

        MockList.assert_called_once_with("en", "my-agent")
        MockRead.assert_called_once_with("en", "my-agent")

    def test_agent_without_card_uses_none_id(self):
        rail = McpRail()
        agent = _make_agent()
        del agent.card  # 移除 card 属性

        with patch("openjiuwen.harness.rails.mcp_rail.ListMcpResourcesTool") as MockList, \
             patch("openjiuwen.harness.rails.mcp_rail.ReadMcpResourceTool") as MockRead, \
             patch("openjiuwen.harness.rails.mcp_rail.Runner"):
            MockList.return_value = _make_tool("list_mcp_resources", "lid")
            MockRead.return_value = _make_tool("read_mcp_resource", "rid")
            rail.init(agent)

        MockList.assert_called_once_with("cn", None)
        MockRead.assert_called_once_with("cn", None)


# ---------------------------------------------------------------------------
# 3. uninit() — 清理
# ---------------------------------------------------------------------------

class TestMcpRailUninit:
    def _init_rail(self, rail: McpRail, agent: MagicMock) -> tuple[MagicMock, MagicMock]:
        list_tool = _make_tool("list_mcp_resources", "ListMcpResourcesTool_abc")
        read_tool = _make_tool("read_mcp_resource", "ReadMcpResourceTool_abc")
        with patch("openjiuwen.harness.rails.mcp_rail.ListMcpResourcesTool", return_value=list_tool), \
             patch("openjiuwen.harness.rails.mcp_rail.ReadMcpResourceTool", return_value=read_tool), \
             patch("openjiuwen.harness.rails.mcp_rail.Runner"):
            rail.init(agent)
        return list_tool, read_tool

    def test_removes_tool_names_from_ability_manager(self):
        rail = McpRail()
        agent = _make_agent()
        list_tool, read_tool = self._init_rail(rail, agent)

        with patch("openjiuwen.harness.rails.mcp_rail.Runner"):
            rail.uninit(agent)

        removed = [c.args[0] for c in agent.ability_manager.remove.call_args_list]
        assert "list_mcp_resources" in removed
        assert "read_mcp_resource" in removed

    def test_removes_tool_ids_from_resource_manager(self):
        rail = McpRail()
        agent = _make_agent()
        list_tool, read_tool = self._init_rail(rail, agent)

        with patch("openjiuwen.harness.rails.mcp_rail.Runner") as MockRunner:
            rail.uninit(agent)

        removed_ids = [c.args[0] for c in MockRunner.resource_mgr.remove_tool.call_args_list]
        assert list_tool.card.id in removed_ids
        assert read_tool.card.id in removed_ids

    def test_uninit_without_init_does_not_raise(self):
        rail = McpRail()
        agent = _make_agent()
        rail.uninit(agent)  # tools is None — should not raise

    def test_uninit_skips_ability_manager_if_absent(self):
        rail = McpRail()
        agent = _make_agent()
        list_tool, _ = self._init_rail(rail, agent)
        del agent.ability_manager  # 模拟 ability_manager 不存在

        with patch("openjiuwen.harness.rails.mcp_rail.Runner"):
            rail.uninit(agent)  # should not raise
