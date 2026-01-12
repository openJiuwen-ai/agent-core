#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Simple Test Case for AgentGroup
"""
import asyncio
import json
import os
import unittest
from datetime import datetime
from typing import Dict, AsyncIterator, Any

from openjiuwen.core.single_agent import AgentConfig, BaseAgent
from openjiuwen.core.multi_agent.legacy import (
    AgentGroupConfig,
    AgentGroupSession,
    BaseGroup
)
from openjiuwen.core.session import Session
from openjiuwen.core.runner import Runner


API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class PlanningAgent(BaseAgent):
    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        pass

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        await asyncio.sleep(0.5)
        return {
            "query": inputs["query"],
            "plan": [
                "1. search for recent news",
                "2. make a detailed a report using collected info"
            ]
        }


class ExecuteAgent(BaseAgent):
    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        pass

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        await asyncio.sleep(0.5)
        return {
            "query": inputs["query"],
            "plan": [
                "1. Search for recent news",
                "2. Review collected information and filter unsafety results"
            ],
            "result": "Task is done."
        }

class SummaryAgent(BaseAgent):
    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        pass

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        await asyncio.sleep(0.5)
        return {
            "query": inputs["query"],
            "plan": [
                "1. Search for recent news",
                "2. Review collected information and filter unsafety results"
            ],
            "result": "Task is done",
            "report": "Summary."
        }


class CustomAgentGroup(BaseGroup):
    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        result_plan = await Runner.run_agent(self.agents["planner"], inputs)
        result_execute = await Runner.run_agent(self.agents["executor"], result_plan)
        result_summary = await Runner.run_agent(self.agents["reporter"], result_execute)
        return result_summary

    async def stream(self, inputs: Dict, session: AgentGroupSession = None) -> AsyncIterator[Any]:
        pass


class AgentGroupTest(unittest.IsolatedAsyncioTestCase):
    """Unit tests for AgentGroup"""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @unittest.skip("skip system test")
    async def test_agent_group(self):
        """Test Case for AgentGroup"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        # create single_agent group config
        agent_group_config = AgentGroupConfig(
            group_id="test_agent_group_id",
            max_agents=10,
            max_concurrent_messages=100,
            message_timeout=30.0
        )

        agent_group: CustomAgentGroup = CustomAgentGroup(config=agent_group_config)

        agent_group.add_agent("planner", PlanningAgent(AgentConfig()))
        agent_group.add_agent("executor", ExecuteAgent(AgentConfig()))
        agent_group.add_agent("reporter", SummaryAgent(AgentConfig()))

        result = await agent_group.invoke(inputs={"query": "search for recent news and write a summary"})
        print(f"[AgentGroup] OUTPUT：\n{json.dumps(result, indent=4)}")


if __name__ == "__main__":
    unittest.main()
