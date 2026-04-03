# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Skill Evaluator tester

Tests:
- Simple skill evaluation
  - Check the evaluation report is saved to the correct location
  - Check the report file exists after evaluation
  - Check the agent is correctly configured

Environment (Only needed for real LLM tests):
- API_BASE / API_KEY / MODEL_NAME / MODEL_PROVIDER: LLM parameters
- SKILLS_DIR: Directory containing skills to evaluate
- OUTPUT_DIR: Place to store evaluation report
- RUN_REAL_LLM_TESTS=1: Enable E2E tests with LLM usage
"""

import os
from pathlib import Path
import unittest

from dotenv import load_dotenv
import pytest

from openjiuwen.core.single_agent import ReActAgent, AgentCard
from openjiuwen.dev_tools.skill_evaluator import SkillEvaluator

load_dotenv()

SAMPLE_SKILL_PATH = Path(__file__).resolve().parent / "skills" / "sample_skill"
SAMPLE_REPORT_FILENAME = "final_report.md"


class MockFS:
    files: dict[str, str]

    def __init__(self):
        self.files = {}

    def add_file(self, file_path: str, file_contents: str):
        self.files[file_path] = file_contents


class MockReActAgent(ReActAgent):
    fs: MockFS

    def __init__(self):
        self.fs = MockFS()
        super().__init__(AgentCard())

    async def invoke(self, inputs: dict = None) -> str:
        self.fs.add_file(
            SAMPLE_REPORT_FILENAME,
            "# Skill Evaluation Report\n\n"
            "## Summary\nSkill evaluated successfully.\n\n"
            "## Score\n9/10\n",
        )
        return "Evaluation complete."


class TestSkillEvaluator(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_evaluate_real_llm(self):
        if os.getenv("RUN_REAL_LLM_TESTS", "0") != "1":
            pytest.skip("Real LLM test skipped. Set RUN_REAL_LLM_TESTS=1 to enable.")

        output_dir = Path(os.getenv("OUTPUT_DIR", "")) / "eval_output"
        skills_dir = Path(os.getenv("SKILLS_DIR", ""))

        evaluator = SkillEvaluator()
        await evaluator.create_agent()
        await evaluator.evaluate(
            skill_path=skills_dir,
            requirement="Provide a detailed evaluation report.",
            output_path=output_dir,
        )

        # Report directory was created
        self.assertTrue(output_dir.is_dir())

        # At least one report file was written
        report_files = list(output_dir.glob("*.md"))
        self.assertGreater(len(report_files), 0, "Expected at least one .md report file")

        # Report contains basic evaluation content
        report_contents = report_files[0].read_text(encoding="utf-8")
        self.assertRegex(report_contents, r"(?i)evaluation|score|summary")


    @pytest.mark.asyncio
    async def test_evaluate_mock_llm(self):
        evaluator = SkillEvaluator()
        evaluator.agent = MockReActAgent()
        await evaluator.agent.invoke()
        fs = evaluator.agent.fs

        # Report file exists in mock FS
        self.assertIn(SAMPLE_REPORT_FILENAME, fs.files)

        # Report has expected sections
        contents = fs.files[SAMPLE_REPORT_FILENAME]
        self.assertIn("# Skill Evaluation Report", contents)
        self.assertIn("## Summary", contents)
        self.assertIn("## Score", contents)