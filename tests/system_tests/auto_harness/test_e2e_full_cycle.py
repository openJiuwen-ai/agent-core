# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""端到端测试 — auto-harness 全流程（真实 LLM）。

流程：assess → branch → implement → CI → fix → commit → PR

运行方式::

    export API_KEY=xxx
    export API_BASE=xxx
    export MODEL_NAME=GLM-5
    export MODEL_PROVIDER=OpenAI
    python -m pytest tests/system_tests/auto_harness/test_e2e_full_cycle.py -v -s
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from openjiuwen.auto_harness.orchestrator import (
    AutoHarnessOrchestrator,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    OptimizationTask,
    TaskStatus,
)
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner

# ---------------------------------------------------------------------------
# 配置：环境变量 > ~/.openjiuwen/settings.json > 默认值
# ---------------------------------------------------------------------------

_SETTINGS_PATH = Path.home() / ".openjiuwen" / "settings.json"


def _load_settings() -> dict:
    """从 ~/.openjiuwen/settings.json 加载配置。"""
    if _SETTINGS_PATH.is_file():
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


_settings = _load_settings()

API_BASE = (
    os.getenv("API_BASE")
    or os.getenv("OPENJIUWEN_API_BASE")
    or _settings.get("apiBase", "")
)
API_KEY = (
    os.getenv("API_KEY")
    or os.getenv("OPENJIUWEN_API_KEY")
    or _settings.get("apiKey", "")
)
MODEL_NAME = (
    os.getenv("MODEL_NAME")
    or os.getenv("OPENJIUWEN_MODEL")
    or _settings.get("model", "GLM-5")
)
MODEL_PROVIDER = (
    os.getenv("MODEL_PROVIDER")
    or os.getenv("OPENJIUWEN_PROVIDER")
    or _settings.get("provider", "OpenAI")
)
MODEL_TIMEOUT = int(os.getenv("MODEL_TIMEOUT", "120"))

CI_GATE_YAML = str(
    Path(__file__).parent / "ci_gate_e2e.yaml"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _create_model() -> Model:
    """从环境变量创建真实 Model。"""
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            timeout=MODEL_TIMEOUT,
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.2,
            top_p=0.9,
        ),
    )


def _init_git_repo(workspace: str) -> None:
    """在 workspace 中初始化一个干净的 git 仓库。"""
    cmds = [
        ["git", "init"],
        ["git", "config", "user.email", "test@e2e.local"],
        ["git", "config", "user.name", "E2E Test"],
        ["git", "checkout", "-b", "develop"],
    ]
    for cmd in cmds:
        subprocess.run(
            cmd, cwd=workspace, check=True,
            capture_output=True,
        )
    # 创建初始文件并提交
    readme = Path(workspace) / "README.md"
    readme.write_text("# E2E Test Repo\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "."], cwd=workspace,
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=workspace, check=True, capture_output=True,
    )


# ---------------------------------------------------------------------------
# E2E Test
# ---------------------------------------------------------------------------


class TestAutoHarnessE2EFullCycle(
    unittest.IsolatedAsyncioTestCase,
):
    """端到端：implement → CI fail → fix → CI pass → commit。"""

    def _require_llm_config(self) -> None:
        if not API_KEY or not API_BASE:
            self.skipTest(
                "E2E requires API_KEY and API_BASE "
                "in environment."
            )

    async def asyncSetUp(self) -> None:
        await Runner.start()
        self._tmp_dir = tempfile.TemporaryDirectory(
            prefix="auto_harness_e2e_",
        )
        self._workspace = self._tmp_dir.name
        _init_git_repo(self._workspace)

    async def asyncTearDown(self) -> None:
        try:
            self._tmp_dir.cleanup()
        finally:
            await Runner.stop()

    def _make_config(self) -> AutoHarnessConfig:
        """创建 E2E 测试用配置。"""
        return AutoHarnessConfig(
            model=_create_model(),
            workspace=self._workspace,
            experience_dir=str(
                Path(self._workspace) / ".auto_harness/experience"
            ),
            ci_gate_config=CI_GATE_YAML,
            git_remote="",
            git_base_branch="develop",
            session_budget_secs=600.0,
            task_timeout_secs=300.0,
            fix_phase1_max_retries=3,
            fix_phase2_max_retries=0,
        )

    async def test_full_cycle_implement_and_fix(self) -> None:
        """完整流程：implement → CI → fix → commit。

        任务：在 workspace 中创建一个 Python 工具模块。
        agent 写代码后 CI（ruff）可能报错，fix loop 修复。
        最终验证 commit 成功。
        """
        self._require_llm_config()

        config = self._make_config()

        # 创建真实 DeepAgent
        from openjiuwen.auto_harness.agents import (
            create_auto_harness_agent,
        )
        agent = create_auto_harness_agent(config)
        orch = AutoHarnessOrchestrator(config, agent=agent)

        # mock 掉 push 和 create_pr（不访问远程仓库）
        original_git_invoke = orch.git.invoke

        async def _mock_git_invoke(inputs, **kwargs):
            action = inputs.get("action", "")
            if action == "push":
                return {"success": True, "output": "mock push"}
            if action == "create_pr":
                return {
                    "success": True,
                    "pr_url": "https://e2e.test/pr/1",
                }
            return await original_git_invoke(inputs, **kwargs)

        orch.git.invoke = _mock_git_invoke  # type: ignore[assignment]

        task = OptimizationTask(
            topic="创建字符串工具模块",
            description=(
                "在当前工作目录下创建 string_utils.py，"
                "包含一个 reverse_string(s: str) -> str 函数，"
                "返回反转后的字符串。"
                "代码必须符合 ruff 检查规范，"
                "行宽不超过 120 字符。"
            ),
        )

        async for _ in orch.run_session_stream(tasks=[task]):
            pass
        results = orch.results

        self.assertEqual(len(results), 1)
        result = results[0]

        # 验证结果
        print(f"\n=== E2E Result ===")
        print(f"success: {result.success}")
        print(f"pr_url: {result.pr_url}")
        print(f"error: {result.error}")
        print(f"task status: {task.status}")

        self.assertTrue(
            result.success,
            f"E2E cycle failed: {result.error}",
        )
        self.assertEqual(task.status, TaskStatus.SUCCESS)
        self.assertIn("e2e.test/pr/1", result.pr_url)


if __name__ == "__main__":
    unittest.main()
