# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Skill system capability tests (Mock SysOperation + optional real-LLM E2E)

This test suite validates key capabilities of the skill subsystem at a system level:
- SkillManager: scan/register/parse skill.md (including error branches)
- SkillUtil: register skills into an agent and generate prompt
- Optional end-to-end: when RUN_REAL_LLM_TESTS=1, run a real LLM flow using native sys_operation tools

Migration notes (important):
This file is adapted to the new approach that removes the SkillToolKit wrapper tools:
- No longer uses view_file / execute_python_code / run_command
- Uses native sys_operation tools directly:
  - fs.read_file
  - code.execute_code
  - shell.execute_cmd
- E2E test obtains tools via Runner.resource_mgr.get_sys_op_tool_cards(...) and mounts them onto the agent

Environment variables:
- API_BASE / API_KEY / MODEL_NAME / MODEL_PROVIDER: for real LLM (E2E only)
- RUN_REAL_LLM_TESTS=1: enable real-LLM E2E; otherwise tests are skipped
"""

# -------------------------
# Standard library imports
# -------------------------
import os
import unittest
import uuid
import tempfile
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

# -------------------------
# Third-party imports
# -------------------------
import pytest
from dotenv import load_dotenv

# -------------------------
# Application imports
# -------------------------
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.skills.skill_manager import SkillManager
from openjiuwen.core.single_agent.skills import SkillUtil

load_dotenv()

# -------------------------
# Real LLM config (E2E only)
# -------------------------
API_BASE = os.getenv("API_BASE", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "z-ai/glm-4.7")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "OpenAI")

LOGGER = logging.getLogger(__name__)


# -------------------------
# MockFS / MockSysOperation
# -------------------------


@dataclass
class _MockItem:
    name: str
    path: str


@dataclass
class _MockData:
    root_path: Optional[str] = None
    list_items: Optional[List[_MockItem]] = None
    content: Any = None
    stdout: str = ""
    stderr: str = ""


@dataclass
class _MockRes:
    code: int = 0
    message: str = ""
    data: Optional[_MockData] = None


class MockFS:
    """
    In-memory filesystem (used for SkillManager path branches)

    Key points:
    - Use dicts to simulate directory trees, file lists, and file contents
    - Normalize mixed Windows/Unix paths by converting '\\\\' to '/'
    - list_directories returns code=1 for known file paths, mirroring real remote API behaviour
    """

    def __init__(self):
        self.dirs: Dict[str, List[str]] = {}
        self.files: Dict[str, List[str]] = {}
        self.content: Dict[str, Any] = {}
        self.fail_read: set[str] = set()
        self.fail_list_dirs: set[str] = set()
        self.fail_list_files: set[str] = set()

    @staticmethod
    def _norm(p: str) -> str:
        return (p or "").replace("\\", "/")

    def normalize(self, p: str) -> str:
        return self._norm(p)

    def add_dir(self, path: str):
        path = self._norm(path)
        self.dirs.setdefault(path, [])
        self.files.setdefault(path, [])

    def add_subdir(self, parent: str, subdir: str):
        parent = self._norm(parent)
        subdir = self._norm(subdir)
        self.add_dir(parent)
        self.add_dir(subdir)
        if subdir not in self.dirs[parent]:
            self.dirs[parent].append(subdir)

    def add_file(self, dir_path: str, file_path: str, content: Any):
        dir_path = self._norm(dir_path)
        file_path = self._norm(file_path)
        self.add_dir(dir_path)
        if file_path not in self.files[dir_path]:
            self.files[dir_path].append(file_path)
        self.content[file_path] = content

    async def list_directories(self, path: str, recursive: bool = False):
        path = self._norm(path)
        if path in self.fail_list_dirs:
            return _MockRes(code=1, message=f"list_directories failed: {path}", data=_MockData())

        # Known file paths are not directories — return an error, mirroring real remote API behaviour
        if path in self.content:
            return _MockRes(code=1, message=f"not a directory: {path}", data=_MockData())

        if path in self.dirs:
            subs = self.dirs.get(path, [])
            items = [_MockItem(name=Path(p).name, path=p) for p in subs]
            return _MockRes(code=0, data=_MockData(root_path=path, list_items=items))

        return _MockRes(code=0, data=_MockData(root_path=path, list_items=[]))

    async def list_files(self, path: str, recursive: bool = False):
        path = self._norm(path)
        if path in self.fail_list_files:
            return _MockRes(code=1, message=f"list_files failed: {path}", data=_MockData())

        files = self.files.get(path, [])
        items = [_MockItem(name=Path(p).name, path=p) for p in files]
        return _MockRes(code=0, data=_MockData(root_path=path, list_items=items))

    async def read_file(self, path: str, mode: str = "text", encoding: str = "utf-8"):
        path = self._norm(path)
        if path in self.fail_read:
            return _MockRes(code=1, message=f"read_file failed: {path}", data=_MockData(content=None))
        return _MockRes(code=0, data=_MockData(content=self.content.get(path, None)))


class MockCode:
    """
    Fake code executor: simulates sys_operation.code().execute_code()
    """

    async def execute_code(self, code: str, language: str = "python", **kwargs):
        if language != "python":
            return _MockRes(code=1, message=f"unsupported language: {language}", data=_MockData(stdout="", stderr=""))

        # Minimal simulation to keep E2E scaffolding stable without executing arbitrary code.
        if "123 + 456" in code:
            return _MockRes(code=0, data=_MockData(stdout="579\n", stderr=""))
        return _MockRes(code=0, data=_MockData(stdout="", stderr=""))


class MockShell:
    """
    Fake command executor: simulates sys_operation.shell().execute_cmd()
    """

    async def execute_cmd(self, command: str, **kwargs):
        cmd = (command or "").strip()
        if cmd.lower().startswith("echo "):
            return _MockRes(code=0, data=_MockData(stdout=cmd[5:].lstrip() + "\n", stderr=""))
        return _MockRes(code=0, data=_MockData(stdout="", stderr=""))


class MockSysOperation:
    """
    Aggregated SysOperation mock
    """

    def __init__(self, fs: MockFS, code: MockCode, shell: MockShell):
        self._fs = fs
        self._code = code
        self._shell = shell

    def fs(self):
        return self._fs

    def code(self):
        return self._code

    def shell(self):
        return self._shell


# -------------------------
# Helpers
# -------------------------
def _make_skill_md(description: Optional[str] = "UT mock skill description", body: str = "body\n") -> str:
    """
    Build a minimal skill.md content (with front matter)
    """
    if description is None:
        return "---\n" "foo: bar\n" "---\n" + body
    return "---\n" f"description: {description}\n" "---\n" + body


class TestSkillCapability(unittest.IsolatedAsyncioTestCase):
    """
    System-level test suite for the skill subsystem (SkillToolKit dependency removed)
    """

    async def asyncSetUp(self):
        await Runner.start()

        self._tmp = tempfile.TemporaryDirectory()
        self.real_dir = self._tmp.name

        self.sys_operation_id = f"ut_skill_sysop_{uuid.uuid4().hex}"

        # Skill roots: OK / BAD
        self.skills_root_ok = "/virtual/skills_ok"
        self.skills_root_bad = "/virtual/skills_bad"

        # Single-file skill
        self.single_skill_dir = "/virtual/single_skill"
        self.single_skill_md = f"{self.single_skill_dir}/skill.md"
        self.single_skill_name = Path(self.single_skill_dir).name

        # OK skill (dir + skill.md)
        self.mock_skill_name = "good_skill"
        self.good_skill_dir = f"{self.skills_root_ok}/{self.mock_skill_name}"
        self.good_skill_md = f"{self.good_skill_dir}/skill.md"

        # BAD skill (missing description)
        self.bad_skill_name = "bad_skill"
        self.bad_skill_dir = f"{self.skills_root_bad}/{self.bad_skill_name}"
        self.bad_skill_md = f"{self.bad_skill_dir}/skill.md"

        # Extra file (used by E2E prompt)
        self.files_dir = "/virtual/files"
        self.sample_txt = f"{self.files_dir}/a.txt"

        self.mock_fs = MockFS()

        # good root
        self.mock_fs.add_dir(self.skills_root_ok)
        self.mock_fs.add_subdir(self.skills_root_ok, self.good_skill_dir)
        self.mock_fs.add_file(self.good_skill_dir, self.good_skill_md, _make_skill_md("UT mock skill description"))

        # bad root
        self.mock_fs.add_dir(self.skills_root_bad)
        self.mock_fs.add_subdir(self.skills_root_bad, self.bad_skill_dir)
        self.mock_fs.add_file(self.bad_skill_dir, self.bad_skill_md, _make_skill_md(description=None))

        # single skill file
        self.mock_fs.add_dir(self.single_skill_dir)
        self.mock_fs.add_file(self.single_skill_dir, self.single_skill_md, _make_skill_md("SINGLE desc"))

        # sample file
        self.mock_fs.add_dir(self.files_dir)
        self.mock_fs.add_file(self.files_dir, self.sample_txt, "hello_skill_tool")

        self.mock_sysop = MockSysOperation(self.mock_fs, MockCode(), MockShell())

        import openjiuwen.core.runner.runner as runner_mod

        self._orig_get_sysop = runner_mod.Runner.resource_mgr.get_sys_operation

        def _patched_get_sys_operation(sys_id: str, *args, **kwargs):
            if sys_id == self.sys_operation_id:
                return self.mock_sysop
            return self._orig_get_sysop(sys_id, *args, **kwargs)

        self._patcher = patch.object(
            runner_mod.Runner.resource_mgr,
            "get_sys_operation",
            side_effect=_patched_get_sys_operation,
        )
        self._patcher.start()

    async def asyncTearDown(self):
        try:
            self._patcher.stop()
            self._tmp.cleanup()
        finally:
            await Runner.stop()

    def _create_agent_for_llm(self) -> ReActAgent:
        """
        Create a ReActAgent for E2E (used only with a real LLM)

        Adapted to native sys_operation tools:
        - read_file
        - execute_code
        - execute_cmd
        """
        system_prompt = (
            "You are an intelligent assistant.\n"
            "You MUST call tools when the user asks you to use them.\n"
            "When asked to read a file, you MUST use read_file.\n"
            "When asked to run Python, you MUST use execute_code.\n"
            "When asked to run a command, you MUST use execute_cmd.\n"
            "Return ONLY the final answer content.\n"
        )

        agent = ReActAgent(card=AgentCard(name="ut_skill_agent", description="Skill Agent UT"))
        cfg = (
            ReActAgentConfig()
            .configure_model_client(
                provider=MODEL_PROVIDER,
                api_key=API_KEY,
                api_base=API_BASE,
                model_name=MODEL_NAME,
                verify_ssl=False,
            )
            .configure_prompt_template([{"role": "system", "content": system_prompt}])
            .configure_max_iterations(10)
            .configure_context_engine(max_context_message_num=None, default_window_round_num=None)
        )
        cfg.sys_operation_id = self.sys_operation_id
        agent.configure(cfg)

        # Use native sys_operation tools directly (aligned with the current main function usage)
        read_file = Runner.resource_mgr.get_sys_op_tool_cards(
            sys_operation_id=self.sys_operation_id,
            operation_name="fs",
            tool_name="read_file",
        )
        execute_code = Runner.resource_mgr.get_sys_op_tool_cards(
            sys_operation_id=self.sys_operation_id,
            operation_name="code",
            tool_name="execute_code",
        )
        execute_cmd = Runner.resource_mgr.get_sys_op_tool_cards(
            sys_operation_id=self.sys_operation_id,
            operation_name="shell",
            tool_name="execute_cmd",
        )

        # Mount tools only if resource_mgr returns valid tool cards (E2E typically runs with real sys_operation)
        if read_file is not None:
            agent.ability_manager.add(read_file)
        if execute_code is not None:
            agent.ability_manager.add(execute_code)
        if execute_cmd is not None:
            agent.ability_manager.add(execute_cmd)

        return agent

    # -------------------------
    # End-to-end real LLM (optional)
    # -------------------------
    @pytest.mark.asyncio
    async def test_end_to_end_real_llm(self):
        """
        Real LLM end-to-end test (optional)

        Coverage (native sys_operation tools):
        - register_skill: register from skills_root_ok
        - read_file: read a text file
        - execute_code: run python and return output
        - execute_cmd: run a command and return stdout
        """
        if os.getenv("RUN_REAL_LLM_TESTS", "0") != "1":
            pytest.skip("Real LLM test skipped. Set RUN_REAL_LLM_TESTS=1 to enable.")

        agent = self._create_agent_for_llm()
        session = create_agent_session(session_id="ut_skill_session_e2e")

        await agent.register_skill(self.skills_root_ok)

        q1 = "Use read_file to read this file and output its content exactly:\n" + self.sample_txt
        r1 = await agent.invoke({"query": q1}, session=session)
        self.assertEqual(r1.get("result_type"), "answer")
        self.assertIn("hello_skill_tool", r1.get("output", ""))

        q2 = "Use execute_code to run python code and output the result only:\nprint(123 + 456)"
        r2 = await agent.invoke({"query": q2}, session=session)
        self.assertEqual(r2.get("result_type"), "answer")
        self.assertIn("579", r2.get("output", ""))

        q3 = "Use execute_cmd to execute the following command and output stdout only:\necho hello_cmd"
        r3 = await agent.invoke({"query": q3}, session=session)
        self.assertEqual(r3.get("result_type"), "answer")
        self.assertIn("hello_cmd", r3.get("output", ""))

    # -------------------------
    # SkillManager tests
    # -------------------------
    @pytest.mark.asyncio
    async def test_skill_manager_register_scan_dir_ok(self):
        """Verify SkillManager: scan a directory and register skills (happy path)"""
        mgr = SkillManager(self.sys_operation_id)
        await mgr.register(Path(self.skills_root_ok))

        self.assertTrue(mgr.has(self.mock_skill_name))
        sk = mgr.get(self.mock_skill_name)
        self.assertIsNotNone(sk)
        self.assertEqual(sk.description, "UT mock skill description")
        self.assertEqual(sk.directory.name, self.mock_skill_name)

    @pytest.mark.asyncio
    async def test_skill_manager_register_single_file_ok(self):
        """Verify SkillManager: register a single skill.md file directly (happy path)"""
        mgr = SkillManager(self.sys_operation_id)
        await mgr.register(Path(self.single_skill_md))

        self.assertTrue(mgr.has(self.single_skill_name))
        sk = mgr.get(self.single_skill_name)
        self.assertIsNotNone(sk)
        self.assertEqual(sk.description, "SINGLE desc")

    @pytest.mark.asyncio
    async def test_skill_manager_register_skill_dir_ok(self):
        """Verify SkillManager: register by passing the skill directory (containing skill.md) directly"""
        mgr = SkillManager(self.sys_operation_id)
        await mgr.register(Path(self.single_skill_dir))

        self.assertTrue(mgr.has(self.single_skill_name))
        sk = mgr.get(self.single_skill_name)
        self.assertIsNotNone(sk)
        self.assertEqual(sk.description, "SINGLE desc")

    @pytest.mark.asyncio
    async def test_skill_manager_register_duplicate_overwrite(self):
        """Verify SkillManager: overwrite behavior when registering duplicates"""
        mgr = SkillManager(self.sys_operation_id)
        await mgr.register(Path(self.single_skill_md))

        with self.assertRaises(ValueError):
            await mgr.register(Path(self.single_skill_md), overwrite=False)

        await mgr.register(Path(self.single_skill_md), overwrite=True)
        self.assertTrue(mgr.has(self.single_skill_name))

    @pytest.mark.asyncio
    async def test_skill_manager_registry_ops(self):
        """Verify SkillManager: registry ops (count / get_names / unregister / clear)"""
        mgr = SkillManager(self.sys_operation_id)
        await mgr.register(Path(self.single_skill_md))

        self.assertEqual(mgr.count(), 1)
        self.assertEqual(set(mgr.get_names()), {self.single_skill_name})

        mgr.unregister(self.single_skill_name)
        self.assertFalse(mgr.has(self.single_skill_name))
        self.assertEqual(mgr.count(), 0)

        mgr.clear()
        self.assertEqual(mgr.count(), 0)

    @pytest.mark.asyncio
    async def test_skill_manager_missing_description_raises_keyerror(self):
        """Verify SkillManager: raise KeyError when description is missing in skill.md"""
        mgr = SkillManager(self.sys_operation_id)
        with self.assertRaises(KeyError):
            await mgr.register(Path(self.skills_root_bad))

    @pytest.mark.asyncio
    async def test_skill_manager_yaml_missing_front_matter_raises_keyerror(self):
        """Verify SkillManager: raise KeyError when skill.md has no front matter"""
        mgr = SkillManager(self.sys_operation_id)
        self.mock_fs.content[self.mock_fs.normalize(self.single_skill_md)] = "no front matter"
        with self.assertRaises(KeyError):
            await mgr.register(Path(self.single_skill_md))

    @pytest.mark.asyncio
    async def test_skill_manager_read_file_code_nonzero_raises_filenotfound(self):
        """Verify SkillManager: raise FileNotFoundError when read_file returns non-zero code"""
        mgr = SkillManager(self.sys_operation_id)
        self.mock_fs.fail_read.add(self.mock_fs.normalize(self.single_skill_md))
        with self.assertRaises(FileNotFoundError):
            await mgr.register(Path(self.single_skill_md))

    @pytest.mark.asyncio
    async def test_skill_manager_read_file_content_none_raises_filenotfound(self):
        """Verify SkillManager: raise FileNotFoundError when read_file returns content=None"""
        mgr = SkillManager(self.sys_operation_id)
        self.mock_fs.content[self.mock_fs.normalize(self.single_skill_md)] = None
        with self.assertRaises(FileNotFoundError):
            await mgr.register(Path(self.single_skill_md))

    # -------------------------
    # SkillUtil tests
    # -------------------------
    @pytest.mark.asyncio
    async def test_skill_util_register_and_prompt(self):
        """
        Verify SkillUtil:
        - register_skills: register skills_root_ok into agent
        - has_skill: should be True after registration
        - get_skill_prompt: prompt should include skill name/description and instruct using read_file
        """
        agent = ReActAgent(card=AgentCard(name="ut_agent", description="x"))
        util = SkillUtil(self.sys_operation_id)

        await util.register_skills(self.skills_root_ok, agent)

        self.assertTrue(util.has_skill())

        prompt = util.get_skill_prompt()
        self.assertIn("Skill name:", prompt)
        self.assertIn(self.mock_skill_name, prompt)
        self.assertIn("UT mock skill description", prompt)
        self.assertIn("using read_file", prompt)
        self.assertNotIn("using view_file", prompt)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])