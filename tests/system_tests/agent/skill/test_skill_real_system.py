# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Skill 系统能力测试（结合 Mock SysOperation + 可选真实大模型 E2E）

本测试用例面向 skill 子系统的关键能力做“系统级”验证：
- SkillManager：扫描/注册/解析 skill.md（含异常分支）
- SkillUtil：将 skill 注册到 agent，并生成 prompt
- 可选 End-to-end：在 RUN_REAL_LLM_TESTS=1 时，用真实 LLM + sys_operation 原生工具走完整调用链

## 迁移说明（重要）
本文件已适配“移除 SkillToolKit 三个封装工具”的新方案：
- 不再使用 view_file / execute_python_code / run_command
- 改为直接使用 sys_operation 原生工具：
  - fs.read_file
  - code.execute_code
  - shell.execute_cmd
- E2E 用例通过 Runner.resource_mgr.get_sys_op_tool_cards(...) 获取工具并挂到 agent

## 环境变量
- API_BASE / API_KEY / MODEL_NAME / MODEL_PROVIDER：用于真实 LLM（仅 E2E）
- RUN_REAL_LLM_TESTS=1：开启真实 LLM 端到端测试；否则自动 skip
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
    内存文件系统（用于 SkillManager 路径分支）

    关键点：
    - 用字符串 dict 模拟目录树、文件列表与文件内容
    - 为兼容 Windows/Unix 混用路径，统一将 '\\' 归一化成 '/'
    - 对于 `list_directories`，返回一个真实存在的 root_path（real_dir_for_isdir_check）
      以保证 Path(...).is_dir() 为 True
    """

    def __init__(self, real_dir_for_isdir_check: str):
        self._real_dir_for_isdir_check = real_dir_for_isdir_check
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

        if path in self.dirs:
            resolved_root = self._real_dir_for_isdir_check
            subs = self.dirs.get(path, [])
            items = [_MockItem(name=Path(p).name, path=p) for p in subs]
            return _MockRes(code=0, data=_MockData(root_path=resolved_root, list_items=items))

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
    伪代码执行器：模拟 sys_operation.code().execute_code()
    """

    async def execute_code(self, code: str, language: str = "python", **kwargs):
        if language != "python":
            return _MockRes(code=1, message=f"unsupported language: {language}", data=_MockData(stdout="", stderr=""))

        # 这里不真正 exec，做最小模拟，满足 SkillManager 之外的链路兜底
        if "123 + 456" in code:
            return _MockRes(code=0, data=_MockData(stdout="579\n", stderr=""))
        return _MockRes(code=0, data=_MockData(stdout="", stderr=""))


class MockShell:
    """
    伪命令执行器：模拟 sys_operation.shell().execute_cmd()
    """

    async def execute_cmd(self, command: str, **kwargs):
        cmd = (command or "").strip()
        if cmd.lower().startswith("echo "):
            return _MockRes(code=0, data=_MockData(stdout=cmd[5:].lstrip() + "\n", stderr=""))
        return _MockRes(code=0, data=_MockData(stdout="", stderr=""))


class MockSysOperation:
    """
    SysOperation Mock 聚合对象
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
    构造一份最小 skill.md 内容（含 front matter）
    """
    if description is None:
        return "---\n" "foo: bar\n" "---\n" + body
    return "---\n" f"description: {description}\n" "---\n" + body


class TestSkillCapability(unittest.IsolatedAsyncioTestCase):
    """
    Skill 子系统系统测试集合（已去除 SkillToolKit 依赖）
    """

    async def asyncSetUp(self):
        await Runner.start()

        self._tmp = tempfile.TemporaryDirectory()
        self.real_dir = self._tmp.name

        self.sys_operation_id = f"ut_skill_sysop_{uuid.uuid4().hex}"

        # skill 根目录：OK / BAD
        self.skills_root_ok = "/virtual/skills_ok"
        self.skills_root_bad = "/virtual/skills_bad"

        # 单文件 skill
        self.single_skill_dir = "/virtual/single_skill"
        self.single_skill_md = f"{self.single_skill_dir}/skill.md"
        self.single_skill_name = Path(self.single_skill_dir).name

        # OK skill（目录 + skill.md）
        self.mock_skill_name = "good_skill"
        self.good_skill_dir = f"{self.skills_root_ok}/{self.mock_skill_name}"
        self.good_skill_md = f"{self.good_skill_dir}/skill.md"

        # BAD skill（缺少 description）
        self.bad_skill_name = "bad_skill"
        self.bad_skill_dir = f"{self.skills_root_bad}/{self.bad_skill_name}"
        self.bad_skill_md = f"{self.bad_skill_dir}/skill.md"

        # 额外文件（E2E 文案里可用）
        self.files_dir = "/virtual/files"
        self.sample_txt = f"{self.files_dir}/a.txt"

        self.mock_fs = MockFS(real_dir_for_isdir_check=self.real_dir)

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
        创建用于 E2E 的 ReActAgent（真实 LLM 才会用到）

        已适配原生 sys_operation 工具：
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

        # 直接使用 sys_operation 原生工具（与你当前 main 函数一致）
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

        # 仅在真实 resource_mgr 支持并返回成功时才挂载
        #（mock 场景下通常不会走这个 E2E）
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
        真实 LLM 端到端测试（可选）

        覆盖点（原生 sys_operation 工具）：
        - register_skill：从 skills_root_ok 注册 skill
        - read_file：读取文本文件
        - execute_code：执行 python 并返回输出
        - execute_cmd：执行命令并返回 stdout
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
        """验证 SkillManager：扫描目录注册 skill（正常分支）"""
        mgr = SkillManager(self.sys_operation_id)
        await mgr.register(Path(self.skills_root_ok))

        self.assertTrue(mgr.has(self.mock_skill_name))
        sk = mgr.get(self.mock_skill_name)
        self.assertIsNotNone(sk)
        self.assertEqual(sk.description, "UT mock skill description")
        self.assertEqual(sk.directory.name, self.mock_skill_name)

    @pytest.mark.asyncio
    async def test_skill_manager_register_single_file_ok(self):
        """验证 SkillManager：直接注册 skill.md 文件（正常分支）"""
        mgr = SkillManager(self.sys_operation_id)
        await mgr.register(Path(self.single_skill_md))

        self.assertTrue(mgr.has(self.single_skill_name))
        sk = mgr.get(self.single_skill_name)
        self.assertIsNotNone(sk)
        self.assertEqual(sk.description, "SINGLE desc")

    @pytest.mark.asyncio
    async def test_skill_manager_register_duplicate_overwrite(self):
        """验证 SkillManager：重复注册时 overwrite 参数行为"""
        mgr = SkillManager(self.sys_operation_id)
        await mgr.register(Path(self.single_skill_md))

        with self.assertRaises(ValueError):
            await mgr.register(Path(self.single_skill_md), overwrite=False)

        await mgr.register(Path(self.single_skill_md), overwrite=True)
        self.assertTrue(mgr.has(self.single_skill_name))

    @pytest.mark.asyncio
    async def test_skill_manager_registry_ops(self):
        """验证 SkillManager：count / get_names / unregister / clear 等注册表操作"""
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
        """验证 SkillManager：skill.md 缺少 description 时抛 KeyError"""
        mgr = SkillManager(self.sys_operation_id)
        with self.assertRaises(KeyError):
            await mgr.register(Path(self.skills_root_bad))

    @pytest.mark.asyncio
    async def test_skill_manager_yaml_missing_front_matter_raises_keyerror(self):
        """验证 SkillManager：skill.md 无 front matter 时抛 KeyError"""
        mgr = SkillManager(self.sys_operation_id)
        self.mock_fs.content[self.mock_fs.normalize(self.single_skill_md)] = "no front matter"
        with self.assertRaises(KeyError):
            await mgr.register(Path(self.single_skill_md))

    @pytest.mark.asyncio
    async def test_skill_manager_read_file_code_nonzero_raises_filenotfound(self):
        """验证 SkillManager：read_file 返回非 0 code 时抛 FileNotFoundError"""
        mgr = SkillManager(self.sys_operation_id)
        self.mock_fs.fail_read.add(self.mock_fs.normalize(self.single_skill_md))
        with self.assertRaises(FileNotFoundError):
            await mgr.register(Path(self.single_skill_md))

    @pytest.mark.asyncio
    async def test_skill_manager_read_file_content_none_raises_filenotfound(self):
        """验证 SkillManager：read_file content=None 时抛 FileNotFoundError"""
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
        验证 SkillUtil：
        - register_skills：将 skills_root_ok 注册进 agent
        - has_skill：注册后应为 True
        - get_skill_prompt：prompt 中应包含 skill name / description，且提示使用 read_file
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