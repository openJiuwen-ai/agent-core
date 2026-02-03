# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Skill 系统能力测试（结合 Mock SysOperation + 可选真实大模型 E2E）

本测试用例面向 skill 子系统的关键能力做“系统级”验证：
- SkillManager：扫描/注册/解析 skill.md（含异常分支）
- SkillToolKit：构建 view_file / execute_python_code / run_command 工具，并验证分支行为
- SkillUtil：将 skill 注册到 agent，并生成 prompt
- 可选 End-to-end：在 RUN_REAL_LLM_TESTS=1 时，用真实 LLM 走完整调用链

## 测试设计

### 1) 绝大多数用例：不依赖真实文件路径
- 通过 MockFS 在内存中构造 skill 目录树与文件内容
- 通过 patch Runner.resource_mgr.get_sys_operation，把 sys_operation_id 映射到 MockSysOperation
- 这样 SkillManager/SkillToolKit/SkillUtil 都“认为”在访问本地资源，但实际全走 MockFS/MockShell/MockCode

### 2) E2E 用例（可选）：允许真实大模型响应
- 仅 test_end_to_end_real_llm 依赖真实 LLM
- 通过环境变量 RUN_REAL_LLM_TESTS=1 显式开启
- 该用例用于验证：LLM 能按 system prompt 选择工具并正确调用

## 环境变量

- API_BASE / API_KEY / MODEL_NAME / MODEL_PROVIDER：用于真实 LLM（仅 E2E）
- RUN_REAL_LLM_TESTS=1：开启真实 LLM 端到端测试；否则自动 skip
"""

# -------------------------
# Standard library imports
# -------------------------
import contextlib
import io
import os
import shlex
import subprocess
import tempfile
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch
import logging

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
from openjiuwen.core.skills.skill_manager import SkillManager
from openjiuwen.core.skills.skill_tool_kit import SkillToolKit
from openjiuwen.core.skills.skill_util import SkillUtil

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
# LocalFunction compat call
# -------------------------


async def _call_local_function(tool: Any, **kwargs):
    """
    LocalFunction 调用兼容封装。

    说明：
    - openjiuwen 的 LocalFunction 以 `invoke(self, inputs: dict, **kwargs)` 为主入口
    - 但不同版本/封装层可能暴露出不同方法名（ainvoke/run/call/execute/func 等）
    - 该封装以“最符合 LocalFunction 语义”的方式优先尝试，并兼容其他少量调用形态

    目的：
    - 避免测试用例强绑定某个具体方法名（例如 `.func`）
    - 在不改被测代码逻辑的前提下，让 UT 在不同实现差异下仍可稳定运行
    """

    async def _await_if_needed(x):
        # 如果返回值是 coroutine/awaitable，则 await；否则直接返回
        if hasattr(x, "__await__"):
            return await x
        return x

    # 1) 优先走 LocalFunction 的标准入口：invoke(inputs_dict)
    invoke = getattr(tool, "invoke", None)
    if callable(invoke):
        return await _await_if_needed(invoke(kwargs))

    # 2) 少数版本可能提供 ainvoke：尝试 ainvoke(inputs_dict) 或 ainvoke(inputs=inputs_dict)
    ainvoke = getattr(tool, "ainvoke", None)
    if callable(ainvoke):
        try:
            return await _await_if_needed(ainvoke(kwargs))
        except TypeError:
            return await _await_if_needed(ainvoke(inputs=kwargs))

    # 3) 其他常见入口兜底：优先传入 dict；失败再尝试 kwargs
    for name in ("arun", "run", "call", "execute", "func"):
        fn = getattr(tool, name, None)
        if fn and callable(fn):
            try:
                return await _await_if_needed(fn(kwargs))
            except TypeError:
                return await _await_if_needed(fn(**kwargs))

    # 4) 如果 tool 本身可调用，也做最后兜底（很少见，但可容错）
    if callable(tool):
        try:
            return await _await_if_needed(tool(kwargs))
        except TypeError:
            return await _await_if_needed(tool(**kwargs))

    raise AttributeError(
        f"Unsupported tool call style for {type(tool).__name__}: "
        f"no invoke/ainvoke/run/call/execute/func and not callable."
    )


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
    内存文件系统（用于 SkillManager / SkillToolKit.view_file 等路径分支）

    关键点：
    - 用字符串 dict 模拟目录树、文件列表与文件内容
    - 为兼容 Windows/Unix 混用路径，统一将 '\\' 归一化成 '/'
    - 对于 `list_directories`，返回一个真实存在的 root_path（real_dir_for_isdir_check）
      以保证 Path(...).is_dir() 在 Windows 下为 True
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
        """
        对外提供的路径归一化方法（避免测试用例访问受保护成员 `_norm`）。
        """
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

        # 若 path 在 mock tree 内视为目录，则返回一个真实存在目录作为 root_path
        # 以通过 SkillManager 对 is_dir 的检查
        if path in self.dirs:
            resolved_root = self._real_dir_for_isdir_check
            subs = self.dirs.get(path, [])
            items = [_MockItem(name=Path(p).name, path=p) for p in subs]
            return _MockRes(code=0, data=_MockData(root_path=resolved_root, list_items=items))

        # 否则视为“不是目录”，返回空列表
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

    - 仅支持 python
    - 捕获 stdout/stderr 并通过 _MockRes 返回
    """

    async def execute_code(self, code_block: str, language: str = "python"):
        if language != "python":
            return _MockRes(code=1, message=f"unsupported language: {language}", data=_MockData(stdout="", stderr=""))

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        globals_dict: Dict[str, Any] = {"__name__": "__main__"}
        locals_dict: Dict[str, Any] = {}

        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(code_block, globals_dict, locals_dict)
            return _MockRes(code=0, data=_MockData(stdout=stdout_buf.getvalue(), stderr=stderr_buf.getvalue()))
        except Exception as e:
            return _MockRes(code=1, message=str(e), data=_MockData(stdout=stdout_buf.getvalue(), stderr=str(e)))


class MockShell:
    """
    伪命令执行器：模拟 sys_operation.shell().execute_cmd()

    规则要求：
    - 禁止 subprocess.run(shell=True)

    实现策略：
    - 对测试中用到的最常见形式（例如：echo xxx），直接在这里模拟输出（跨平台且无注入风险）
    - 其他命令：使用 shell=False + shlex.split 作为安全兜底
    """

    async def execute_cmd(self, bash_command: str):
        try:
            cmd = (bash_command or "").strip()

            # 1) 显式支持 echo（Windows 下 echo 多为 shell builtin，shell=False 会找不到可执行文件）
            if cmd.lower().startswith("echo "):
                # 保持与常见 shell 行为一致：输出 echo 后的内容并换行
                out = cmd[5:].lstrip()
                return _MockRes(code=0, data=_MockData(stdout=f"{out}\n", stderr=""))

            # 2) 其他命令走安全路径：shell=False + 参数列表
            #    注意：这里只是测试 mock，用于满足静态检查与安全规则。
            args = shlex.split(cmd, posix=os.name != "nt")
            if not args:
                return _MockRes(code=0, data=_MockData(stdout="", stderr=""))

            p = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                check=False,
            )
            return _MockRes(code=p.returncode, data=_MockData(stdout=p.stdout or "", stderr=p.stderr or ""))
        except Exception as e:
            return _MockRes(code=1, message=str(e), data=_MockData(stdout="", stderr=str(e)))


class MockSysOperation:
    """
    SysOperation Mock 聚合对象：
    - fs() 提供 MockFS
    - code() 提供 MockCode
    - shell() 提供 MockShell

    被测的 SkillToolKit / SkillManager 会通过 Runner.resource_mgr.get_sys_operation 获取该对象
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

    - description=None：用来触发“缺少 description”异常分支
    - description!=None：生成正常 skill.md
    """
    if description is None:
        return "---\n" "foo: bar\n" "---\n" + body
    return "---\n" f"description: {description}\n" "---\n" + body


class TestSkillCapability(unittest.IsolatedAsyncioTestCase):
    """
    Skill 子系统系统测试集合

    - asyncSetUp：启动 Runner + 构造 MockFS + patch sys_operation
    - asyncTearDown：清理工具资源 + 停止 Runner
    """

    async def asyncSetUp(self):
        await Runner.start()

        # 在磁盘上创建一个真实存在的目录，用于 Path(...).is_dir() 检查
        self._tmp = tempfile.TemporaryDirectory()
        self.real_dir = self._tmp.name

        # 为本组用例生成唯一 sys_operation_id，并 patch get_sys_operation 走 MockSysOperation
        self.sys_operation_id = f"ut_skill_sysop_{uuid.uuid4().hex}"

        # skill 根目录：分别构造 OK / BAD 两组，用于覆盖正常与异常分支
        self.skills_root_ok = "/virtual/skills_ok"
        self.skills_root_bad = "/virtual/skills_bad"

        # 单文件 skill：覆盖“直接注册 skill.md 文件”的分支
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

        # view_file 的文本/二进制样例文件
        self.files_dir = "/virtual/files"
        self.sample_txt = f"{self.files_dir}/a.txt"
        self.sample_bin = f"{self.files_dir}/b.bin"

        # 构造 MockFS 的目录树与内容
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

        # view_file samples
        self.mock_fs.add_dir(self.files_dir)
        self.mock_fs.add_file(self.files_dir, self.sample_txt, "hello_skill_tool")
        self.mock_fs.add_file(self.files_dir, self.sample_bin, b"\x00\x01\x02")

        self.mock_sysop = MockSysOperation(self.mock_fs, MockCode(), MockShell())

        import openjiuwen.core.runner.runner as runner_mod

        self._orig_get_sysop = runner_mod.Runner.resource_mgr.get_sys_operation

        def _patched_get_sys_operation(sys_id: str, *args, **kwargs):
            # 仅当 sys_operation_id 匹配本 UT 的 id 时，返回 MockSysOperation
            if sys_id == self.sys_operation_id:
                return self.mock_sysop
            return self._orig_get_sysop(sys_id, *args, **kwargs)

        # patch Runner.resource_mgr.get_sys_operation
        self._patcher = patch.object(
            runner_mod.Runner.resource_mgr,
            "get_sys_operation",
            side_effect=_patched_get_sys_operation,
        )
        self._patcher.start()

        # 记录 UT 过程中可能注册的内部工具 id，便于 tearDown 清理
        self._tool_ids_added = [
            "_internal_execute_python_code",
            "_internal_run_command",
            "_internal_view_file",
        ]

    async def asyncTearDown(self):
        # 尽力清理：移除工具、停止 patch、回收 tempdir、停止 Runner
        remove_errors: List[str] = []
        try:
            for tid in self._tool_ids_added:
                try:
                    Runner.resource_mgr.remove_tool(tool_id=tid)
                except Exception as e:
                    # 不静默吞异常：记录下来，避免违反 “Try/Except/Pass” 类规则
                    remove_errors.append(f"remove_tool failed: {tid} -> {e!r}")

            self._patcher.stop()
            self._tmp.cleanup()
        finally:
            await Runner.stop()

        # tearDown 阶段不 raise 以免掩盖主失败；这里输出诊断信息即可
        for msg in remove_errors:
            LOGGER.warning("%s", msg)

    def _create_agent_for_llm(self) -> ReActAgent:
        """
        创建用于 E2E 的 ReActAgent（真实 LLM 才会用到）

        - system_prompt 强制工具调用策略，便于验证 tool routing 是否按预期发生
        - 通过 SkillToolKit.add_skill_tools(agent) 挂载三类工具能力
        """
        system_prompt = (
            "You are an intelligent assistant.\n"
            "You MUST call tools when the user asks you to use them.\n"
            "When asked to read a file, you MUST use view_file.\n"
            "When asked to run Python, you MUST use execute_python_code.\n"
            "When asked to run a command, you MUST use run_command.\n"
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

        toolkit = SkillToolKit(self.sys_operation_id)
        toolkit.add_skill_tools(agent)
        return agent

    # -------------------------
    # End-to-end real LLM (optional)
    # -------------------------
    @pytest.mark.asyncio
    async def test_end_to_end_real_llm(self):
        """
        真实 LLM 端到端测试（可选）

        覆盖点：
        - register_skill：从 skills_root_ok 注册 skill
        - view_file：读取文本文件
        - execute_python_code：执行 python 并返回输出
        - run_command：执行命令并返回 stdout

        注意：
        - 该用例仅在 RUN_REAL_LLM_TESTS=1 时执行
        """
        if os.getenv("RUN_REAL_LLM_TESTS", "0") != "1":
            pytest.skip("Real LLM test skipped. Set RUN_REAL_LLM_TESTS=1 to enable.")

        agent = self._create_agent_for_llm()
        session = create_agent_session(session_id="ut_skill_session_e2e")

        await agent.register_skill(self.skills_root_ok)

        q1 = "Use view_file to read this file and output its content exactly:\n" + self.sample_txt
        r1 = await agent.invoke({"query": q1}, session=session)
        self.assertEqual(r1.get("result_type"), "answer")
        self.assertIn("hello_skill_tool", r1.get("output", ""))

        q2 = "Use execute_python_code to run python code and output the result only:\nprint(123 + 456)"
        r2 = await agent.invoke({"query": q2}, session=session)
        self.assertEqual(r2.get("result_type"), "answer")
        self.assertIn("579", r2.get("output", ""))

        q3 = "Use run_command to execute the following command and output stdout only:\necho hello_cmd"
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
    # SkillToolKit tests
    # -------------------------
    @pytest.mark.asyncio
    async def test_skill_toolkit_sysop_missing_branch(self):
        """
        验证 SkillToolKit：sys_operation 不可用时的兜底分支

        - toolkit 用不存在的 sysop id 创建
        - 预期所有工具调用都返回“sys_operation is not available”
        """
        toolkit = SkillToolKit("missing_sysop_id")

        view_tool = toolkit.create_view_file_tool()
        out = await _call_local_function(view_tool, file_path=self.sample_txt)
        self.assertIn("sys_operation is not available", str(out))

        py_tool = toolkit.create_execute_python_code_tool()
        out2 = await _call_local_function(py_tool, code_block="print(1)")
        self.assertIn("sys_operation is not available", str(out2))

        cmd_tool = toolkit.create_execute_command_tool()
        out3 = await _call_local_function(cmd_tool, bash_command="echo hi")
        self.assertIn("sys_operation is not available", str(out3))

    @pytest.mark.asyncio
    async def test_skill_toolkit_tools_ok_and_binary_branch(self):
        """
        验证 SkillToolKit：三类工具的正常执行与二进制文件分支

        - view_file：读取文本文件返回内容
        - view_file：读取二进制文件触发“Binary file detected”分支
        - execute_python_code：执行 print(123+456) 并返回 579
        - run_command：执行 echo 并返回 stdout
        """
        toolkit = SkillToolKit(self.sys_operation_id)

        view_tool = toolkit.create_view_file_tool()
        view_out = await _call_local_function(view_tool, file_path=self.sample_txt)
        self.assertIn("hello_skill_tool", str(view_out))

        view_bin = await _call_local_function(view_tool, file_path=self.sample_bin)
        self.assertIn("Binary file detected", str(view_bin))

        py_tool = toolkit.create_execute_python_code_tool()
        py_out = await _call_local_function(py_tool, code_block="print(123 + 456)")
        self.assertIn("579", str(py_out))

        cmd_tool = toolkit.create_execute_command_tool()
        cmd_out = await _call_local_function(cmd_tool, bash_command="echo hello_cmd")
        self.assertIn("hello_cmd", str(cmd_out))

    # -------------------------
    # SkillUtil tests
    # -------------------------
    @pytest.mark.asyncio
    async def test_skill_util_register_and_prompt(self):
        """
        验证 SkillUtil：
        - register_skills：将 skills_root_ok 注册进 agent
        - has_skill：注册后应为 True
        - get_skill_prompt：prompt 中应包含 skill name 与 description
        """
        agent = ReActAgent(card=AgentCard(name="ut_agent", description="x"))
        util = SkillUtil(self.sys_operation_id)

        await util.register_skills(self.skills_root_ok, agent)

        self.assertTrue(util.has_skill())

        prompt = util.get_skill_prompt()
        self.assertIn("Skill name:", prompt)
        self.assertIn(self.mock_skill_name, prompt)
        self.assertIn("UT mock skill description", prompt)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
