# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Remote Skill tester

Tests:
- Finds skills from GitHub
- Downloads skills from GitHub

Environment:
- GITHUB_API_KEY： To access files from GitHub. 
    - Technically, you do not need GitHub API key to access repos on GitHub
      However, the rate limit is very low.
- RUN_GITHUB_TEST=1：Enable GitHub tests (GITHUB_API_KEY is optional, but rate limits may cause failures)
"""

import os
from pathlib import Path
import unittest

import pytest
from dotenv import load_dotenv

from openjiuwen.core.single_agent.skills import GitHubTree, RemoteSkillUtil

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
RUN_GITHUB_TEST = os.getenv("RUN_GITHUB_TEST", "0")


class TestRemoteSkill(unittest.IsolatedAsyncioTestCase):

    @pytest.mark.asyncio
    async def test_fetch_skill_from_github(self):
        if RUN_GITHUB_TEST != "1":
            pytest.skip("GitHub remote skill test skipped, set RUN_GITHUB_TEST=1 to enable.")
        
        remote_skill_util = RemoteSkillUtil("github-skill-id")
        file_list, skill_paths = remote_skill_util.search_github_for_skills(
            GitHubTree(
                repo_owner="dreamofapsychiccat",
                repo_name="remote-skills-test",
            ), token=GITHUB_TOKEN
        )
        file_paths = [file['path'] for file in file_list]

        self.assertEqual(len(file_list), 2)
        self.assertIn(Path("skills/example-skill/SKILL.md"), file_paths)
        self.assertIn(Path("skills/example-skill/references/example-reference.md"), file_paths)
        self.assertNotIn(Path("README.md"), file_paths)

        self.assertEqual(len(skill_paths), 1)
        self.assertIn(Path("example-skill"), skill_paths)

    @pytest.mark.asyncio
    async def test_download_skill_from_github(self):
        if RUN_GITHUB_TEST != "1":
            pytest.skip("GitHub remote skill test skipped, set RUN_GITHUB_TEST=1 to enable.")
        
        remote_skill_util = RemoteSkillUtil("github-skill-id")
        reference_file = remote_skill_util.download_file_from_github(
            GitHubTree(
                repo_owner="dreamofapsychiccat",
                repo_name="remote-skills-test",
            ), 
            file_path="skills/example-skill/references/example-reference.md", 
            token=GITHUB_TOKEN
        )
        self.assertEqual(reference_file, "# Example Reference\n\nExample Reference".encode("UTF-8"))
        