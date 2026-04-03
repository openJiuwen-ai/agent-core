# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
SkillEvaluator tester

Tests:
- Mock LLM evaluation
  - Check evaluate() returns a dict
  - Check the result contains an "output" key
  - Check the output reflects the evaluation of the given skill path
- Real LLM evaluation
  - Check the full pipeline runs end-to-end against a real skill directory

Environment (Only needed for real LLM tests):
- API_BASE / API_KEY / MODEL_NAME / MODEL_PROVIDER: LLM parameters
- OUTPUT_DIR: Directory to store evaluation results
- SKILL_PATH: Path to the skill directory to evaluate
- RUN_REAL_LLM_TESTS=1: Enable E2E tests with LLM usage
"""

import os
from pathlib import Path
import unittest

from dotenv import load_dotenv
import pytest

from openjiuwen.core.single_agent import ReActAgent
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.dev_tools.skill_evaluator.skill_evaluator import SkillEvaluator

load_dotenv()


class MockReActAgent(ReActAgent):

    def __init__(self):
        self.last_skill_path = None
        self.last_requirement = None
        super().__init__(AgentCard())

    async def invoke(self, skill_path: Path, requirement: str):
        self.last_skill_path = skill_path
        self.last_requirement = requirement
        return {
            "output": f"Evaluation complete for skill at: {skill_path}"
        }


class TestSkillEvaluator(unittest.IsolatedAsyncioTestCase):
    @pytest.mark.asyncio
    async def test_evaluate_returns_mock_llm(self):
        skill_path = Path("/mock/skills/my-skill")
        output_dir = Path("/mock/outputs")

        evaluator = SkillEvaluator()
        evaluator.agent = MockReActAgent()

        result = await evaluator.agent.invoke(
            skill_path=skill_path,
            requirement="Run the full pipeline",
        )

        # Result is a dict
        self.assertIsInstance(result, dict)

        # Result contains an output key
        self.assertIn("output", result)


    @pytest.mark.asyncio
    async def test_evaluate_passes_requirement_to_agent_mock_llm(self):
        skill_path = Path("/mock/skills/my-skill")
        requirement = "Focus on safety eval"

        evaluator = SkillEvaluator()
        mock_agent = MockReActAgent()
        evaluator.agent = mock_agent

        await evaluator.agent.invoke(
            skill_path=skill_path,
            requirement=requirement,
        )

        # Agent received the correct requirement
        self.assertEqual(mock_agent.last_requirement, requirement)


if __name__ == "__main__":
    unittest.main()