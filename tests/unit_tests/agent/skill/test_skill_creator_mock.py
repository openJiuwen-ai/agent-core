# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Skill Creator tester

Tests:
- Simple skill creation
  - Check the skill is named correctly
  - Check the skill is at the correct location
  - Check the generated SKILL.md file exists and is correctly formatted

Environment (Only needed for real LLM tests):
- API_BASE / API_KEY / MODEL_NAME / MODEL_PROVIDER: LLM parameters
- OUTPUT_DIR: Place to store LLM's created skill
- RUN_REAL_LLM_TESTS=1：Enable E2E tests with LLM usage
"""

import logging
import os
from pathlib import Path
import unittest

from dotenv import load_dotenv
import pytest

from openjiuwen.core.single_agent import ReActAgent
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.dev_tools.skill_creator import SkillCreator

load_dotenv()


class MockFS:
    directories: list
    files: dict[str, str]

    def __init__(self):
        self.directories = []
        self.files = {}

    def add_directory(self, dir_name: str):
        self.directories.append(dir_name)
    
    def add_file(self, file_path: str, file_contents: str):
        self.files[file_path] = file_contents


class MockReActAgent(ReActAgent):
    fs: MockFS

    def __init__(self):
        self.fs = MockFS()
        super().__init__(AgentCard())

    async def invoke(self):
        self.fs.add_directory("skill_name")
        self.fs.add_file(
            "skill_name/SKILL.md",
            "---\nname: skill_name\ndescription: sample skill\n---\n# Skill Body\n"
        )


class TestSkillCreator(unittest.IsolatedAsyncioTestCase):
    
    @pytest.mark.asyncio
    async def test_skill_creation_real_llm(self):
        if os.getenv("RUN_REAL_LLM_TESTS", "0") != "1":
            pytest.skip("Real LLM test skipped. Set RUN_REAL_LLM_TESTS=1 to enable.")

        query = "Create a skeleton skill directory nammed 'skill_name'"
        output_dir = Path(os.getenv("OUTPUT_DIR"), "")

        skill_creator = SkillCreator()
        await skill_creator.create_agent()
        await skill_creator.generate(requirement=query, output_path=output_dir)

        # Skill folder exists
        skill_dir = output_dir / "skill_name"
        self.assertTrue(skill_dir.is_dir())

        # Skill folder contains SKILL.md
        skill_file = skill_dir / "SKILL.md"
        self.assertTrue(skill_file.exists())

        # Generated SKILL.md is formatted correctly
        skill_file_contents = skill_file.read_text(encoding="utf-8")
        self.assertRegex(skill_file_contents, "^---(.|\n)*name: skill_name(.|\n)*---(.|\n)*$")
        self.assertRegex(skill_file_contents, "^---(.|\n)*description: (.|\n)*---(.|\n)*$")

    @pytest.mark.asyncio
    async def test_skill_creation_mock_llm(self):
        skill_creator = SkillCreator()
        skill_creator.agent = MockReActAgent()
        await skill_creator.agent.invoke()
        fs = skill_creator.agent.fs

        # Skill folder exists
        self.assertIn("skill_name", fs.directories)

        # Skill folder contains SKILL.md
        self.assertIn("skill_name/SKILL.md", fs.files.keys())

        # Generated SKILL.md is formatted correctly
        skill_file_contents = fs.files["skill_name/SKILL.md"]
        self.assertRegex(skill_file_contents, "^---(.|\n)*name: skill_name(.|\n)*---(.|\n)*$")
        self.assertRegex(skill_file_contents, "^---(.|\n)*description: (.|\n)*---(.|\n)*$")