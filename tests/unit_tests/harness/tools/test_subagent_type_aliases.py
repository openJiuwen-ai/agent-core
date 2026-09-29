# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import unittest
from unittest.mock import patch

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig
from openjiuwen.harness.tools import TaskTool
from openjiuwen.harness.tools.subagent.subagent_tools import SubagentSpawnTool
from openjiuwen.harness.tools.subagent.type_aliases import (
    canonicalize_subagent_type,
    subagent_type_allowed,
)


class TestSubagentTypeAliases(unittest.TestCase):
    def test_yaml_key_maps_to_card_name(self) -> None:
        self.assertEqual(
            canonicalize_subagent_type("general_agent"),
            "general-purpose",
        )
        self.assertEqual(
            canonicalize_subagent_type("general-purpose"),
            "general-purpose",
        )
        self.assertEqual(canonicalize_subagent_type("explore_agent"), "explore_agent")

    def test_allowlist_accepts_either_name(self) -> None:
        self.assertTrue(
            subagent_type_allowed("general_agent", {"general-purpose"}),
        )
        self.assertTrue(
            subagent_type_allowed("general-purpose", {"general_agent"}),
        )
        self.assertFalse(
            subagent_type_allowed("general_agent", {"explore_agent"}),
        )
        self.assertTrue(subagent_type_allowed("explore_agent", None))


class TestTaskToolTypeAliases(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await Runner.start()

    async def asyncTearDown(self) -> None:
        await Runner.stop()

    async def test_task_tool_accepts_yaml_key_when_card_is_general_purpose(self) -> None:
        created: list[str] = []

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="general-purpose", description="gp", id="gp")

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                return {"output": "ok"}

        spec = SubAgentConfig(
            agent_card=AgentCard(name="general-purpose", description="gp"),
            system_prompt="sub",
        )
        parent = DeepAgent(AgentCard(name="parent", description="test"))
        parent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[spec],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )

        def _create(subagent_type: str, *_args, **_kwargs):
            created.append(subagent_type)
            return FakeSubAgent()

        tool = TaskTool(
            card=ToolCard(id="task_tool_alias", name="task_tool", description="test"),
            parent_agent=parent,
            allowed_subagent_types=["general-purpose"],
        )
        with patch.object(parent, "create_subagent", side_effect=_create):
            result = await tool.invoke(
                {"subagent_type": "general_agent", "task_description": "write a file"},
                session=Session(session_id="parent_session"),
            )

        self.assertTrue(result.success)
        self.assertEqual(created, ["general-purpose"])

    async def test_task_tool_still_rejects_unknown_alias(self) -> None:
        parent = DeepAgent(AgentCard(name="parent", description="test"))
        parent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_alias_reject", name="task_tool", description="test"),
            parent_agent=parent,
            allowed_subagent_types=["general-purpose"],
        )
        with self.assertRaises(BaseError) as ctx:
            await tool.invoke(
                {"subagent_type": "explore_agent", "task_description": "nope"},
                session=Session(session_id="parent_session"),
            )
        self.assertIn("is not available through task_tool", str(ctx.exception))


class TestFindSpecTypeAliases(unittest.TestCase):
    def test_find_spec_by_yaml_key(self) -> None:
        spec = SubAgentConfig(
            agent_card=AgentCard(name="general-purpose", description="gp"),
            system_prompt="sub",
        )
        parent = DeepAgent(AgentCard(name="parent", description="test"))
        parent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[spec],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )
        found = parent._find_subagent_spec("general_agent")
        self.assertIs(found, spec)


class TestSpawnToolTypeAliases(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_rejects_unaliased_unknown_type(self) -> None:
        parent = DeepAgent(AgentCard(name="parent", description="test"))
        tool = SubagentSpawnTool(
            card=ToolCard(id="spawn_alias", name="subagent_spawn", description="test"),
            parent_agent=parent,
            allowed_subagent_types=["general-purpose"],
        )
        with self.assertRaises(BaseError) as ctx:
            await tool.invoke(
                {
                    "subagent_type": "explore_agent",
                    "task_description": "x",
                    "display_name": "x",
                    "role": "x",
                },
                session=Session(session_id="parent_session"),
            )
        self.assertIn("is not available through subagent_spawn", str(ctx.exception))
