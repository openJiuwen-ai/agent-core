# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""E2E: auto-harness 配置加载、worktree 隔离、CLI 参数覆盖。

不依赖真实 LLM，通过 mock orchestrator 验证 CLI 集成路径。

Run::

    python -m pytest tests/cli/e2e/test_auto_harness.py -v -s
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    load_auto_harness_config,
)
from openjiuwen.auto_harness.infra.worktree_manager import (
    WorktreeManager,
    _slugify,
)


# ------------------------------------------------------------------
# 配置加载
# ------------------------------------------------------------------


class TestAutoHarnessConfigLoading:
    """验证从 YAML 加载配置，字段正确映射。"""

    def test_load_full_config(self, tmp_path):
        yaml_content = (
            "local_repo: /home/user/agent-core\n"
            "git:\n"
            "  remote: myfork\n"
            "  base_branch: main\n"
            "  user_name: test-user\n"
            "  user_email: test@example.com\n"
            "  fork_owner: TestOwner\n"
            "  upstream_owner: openJiuwen\n"
            "  upstream_repo: agent-core\n"
            "gitcode:\n"
            "  access_token_env: MY_TOKEN\n"
            "budget:\n"
            "  session_secs: 1800\n"
            "  cost_limit_usd: 5.0\n"
            "  task_timeout_secs: 600\n"
            "  max_tasks_per_session: 2\n"
            "fix_loop:\n"
            "  phase1_max_retries: 5\n"
            "  phase2_max_retries: 3\n"
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            yaml_content, encoding="utf-8",
        )

        cfg = load_auto_harness_config(str(cfg_file))

        assert cfg.local_repo == "/home/user/agent-core"
        assert cfg.git_remote == "myfork"
        assert cfg.git_base_branch == "main"
        assert cfg.git_user_name == "test-user"
        assert cfg.git_user_email == "test@example.com"
        assert cfg.fork_owner == "TestOwner"
        assert cfg.gitcode_token_env == "MY_TOKEN"
        assert cfg.session_budget_secs == 1800
        assert cfg.cost_limit_usd == 5.0
        assert cfg.task_timeout_secs == 600
        assert cfg.max_tasks_per_session == 2
        assert cfg.fix_phase1_max_retries == 5
        assert cfg.fix_phase2_max_retries == 3

    def test_missing_config_uses_defaults(
        self, tmp_path,
    ):
        cfg = load_auto_harness_config(
            str(tmp_path / "nonexistent.yaml"),
        )
        assert cfg.git_remote == ""
        assert cfg.fork_owner == ""
        assert cfg.session_budget_secs == 3600.0
        assert cfg.git_base_branch == "develop"

    def test_partial_config_merges(self, tmp_path):
        yaml_content = (
            "git:\n"
            "  remote: partial-fork\n"
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            yaml_content, encoding="utf-8",
        )

        cfg = load_auto_harness_config(str(cfg_file))
        assert cfg.git_remote == "partial-fork"
        # 其他字段保持默认
        assert cfg.git_base_branch == "develop"
        assert cfg.session_budget_secs == 3600.0


# ------------------------------------------------------------------
# Worktree 隔离
# ------------------------------------------------------------------


class TestAutoHarnessWorktreeWithLocalRepo:
    """配置 local_repo 时的 worktree 创建/清理。"""

    @pytest.mark.asyncio
    async def test_worktree_lifecycle(self, tmp_path):
        local_repo = tmp_path / "local_repo"
        local_repo.mkdir()
        data_dir = tmp_path / "data"

        cfg = AutoHarnessConfig(
            data_dir=str(data_dir),
            local_repo=str(local_repo),
            git_base_branch="develop",
            git_user_name="test",
            git_user_email="test@e2e.local",
        )
        mgr = WorktreeManager(cfg)

        created_wt = None

        async def fake_git(*args, cwd):
            nonlocal created_wt
            if (
                args[0] == "worktree"
                and args[1] == "add"
            ):
                # args: worktree add -b branch wt_path base
                created_wt = args[4]
                Path(created_wt).mkdir(
                    parents=True, exist_ok=True,
                )
            if (
                args[0] == "remote"
                and args[1] == "get-url"
            ):
                return 1, ""
            if (
                args[0] == "worktree"
                and args[1] == "remove"
            ):
                wt = Path(args[3])
                if wt.exists():
                    wt.rmdir()
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness"
            ".workspace._run_git",
            side_effect=fake_git,
        ):
            wt_path = await mgr.prepare(
                "optimize-retrieval",
            )

            # worktree 在 data_dir/worktrees/ 下
            assert str(data_dir / "worktrees") in wt_path
            assert Path(wt_path).exists()

            await mgr.cleanup(wt_path)
            assert not Path(wt_path).exists()


class TestAutoHarnessWorktreeWithoutLocalRepo:
    """不配置 local_repo 时自动 clone。"""

    @pytest.mark.asyncio
    async def test_auto_clone(self, tmp_path):
        data_dir = tmp_path / "data"
        cfg = AutoHarnessConfig(
            data_dir=str(data_dir),
            local_repo="",
            git_base_branch="develop",
        )
        mgr = WorktreeManager(cfg)

        clone_called = False

        async def fake_git(*args, cwd):
            nonlocal clone_called
            if args[0] == "clone":
                clone_called = True
                Path(args[-1]).mkdir(
                    parents=True, exist_ok=True,
                )
            if (
                args[0] == "worktree"
                and args[1] == "add"
            ):
                Path(args[3]).mkdir(
                    parents=True, exist_ok=True,
                )
            if (
                args[0] == "remote"
                and args[1] == "get-url"
            ):
                return 1, ""
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness"
            ".workspace._run_git",
            side_effect=fake_git,
        ):
            wt_path = await mgr.prepare("test-clone")

        assert clone_called
        assert str(data_dir / "worktrees") in wt_path


# ------------------------------------------------------------------
# CLI 参数覆盖
# ------------------------------------------------------------------


class TestAutoHarnessConfigCliOverride:
    """CLI 参数覆盖 YAML 配置。"""

    def test_budget_override(self, tmp_path):
        yaml_content = (
            "budget:\n"
            "  session_secs: 3600\n"
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            yaml_content, encoding="utf-8",
        )

        cfg = load_auto_harness_config(str(cfg_file))
        assert cfg.session_budget_secs == 3600

        # 模拟 CLI --budget 600 覆盖
        cfg.session_budget_secs = 600
        cfg.task_timeout_secs = min(
            cfg.task_timeout_secs, 600 * 0.95,
        )
        assert cfg.session_budget_secs == 600
        assert cfg.task_timeout_secs <= 570

    def test_no_push_override(self, tmp_path):
        yaml_content = (
            "git:\n"
            "  remote: myfork\n"
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            yaml_content, encoding="utf-8",
        )

        cfg = load_auto_harness_config(str(cfg_file))
        assert cfg.git_remote == "myfork"

        # 模拟 CLI --no-push
        cfg.git_remote = ""
        assert cfg.git_remote == ""


# ------------------------------------------------------------------
# 数据目录隔离
# ------------------------------------------------------------------


class TestAutoHarnessDataDirIsolation:
    """产物写入 data_dir，不污染 worktree。"""

    def test_paths_under_data_dir(self, tmp_path):
        data_dir = str(tmp_path / "auto_harness")
        cfg = AutoHarnessConfig(data_dir=data_dir)

        assert cfg.memory_dir.startswith(data_dir)
        assert cfg.runs_dir.startswith(data_dir)
        assert cfg.worktrees_dir.startswith(data_dir)
        assert cfg.cache_repo_dir.startswith(data_dir)

        # 所有路径都不包含 worktree 相关内容
        for p in [
            cfg.memory_dir,
            cfg.runs_dir,
        ]:
            assert "worktrees" not in p

    @pytest.mark.asyncio
    async def test_worktree_not_polluted(
        self, tmp_path,
    ):
        """worktree 目录不包含 memory/runs 产物。"""
        data_dir = tmp_path / "data"
        local_repo = tmp_path / "repo"
        local_repo.mkdir()

        cfg = AutoHarnessConfig(
            data_dir=str(data_dir),
            local_repo=str(local_repo),
        )
        mgr = WorktreeManager(cfg)

        wt_created = None

        async def fake_git(*args, cwd):
            nonlocal wt_created
            if (
                args[0] == "worktree"
                and args[1] == "add"
            ):
                wt_created = args[3]
                Path(wt_created).mkdir(
                    parents=True, exist_ok=True,
                )
            if (
                args[0] == "remote"
                and args[1] == "get-url"
            ):
                return 1, ""
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness"
            ".workspace._run_git",
            side_effect=fake_git,
        ):
            wt_path = await mgr.prepare("test-iso")

        # memory 和 runs 在 data_dir 下，不在 wt 下
        assert not (Path(wt_path) / "memory").exists()
        assert not (Path(wt_path) / "runs").exists()
        assert cfg.memory_dir == str(
            data_dir / "memory"
        )
        assert cfg.runs_dir == str(data_dir / "runs")


# ------------------------------------------------------------------
# CLI _subcmd_run 集成测试（mock orchestrator）
# ------------------------------------------------------------------


class TestSubcmdRunIntegration:
    """验证 _subcmd_run 通过 orchestrator 流式执行。"""

    @pytest.mark.asyncio
    async def test_run_with_task(self, tmp_path):
        """--task 传入时 orchestrator 收到对应任务。"""
        from unittest.mock import MagicMock

        from openjiuwen.auto_harness.schema import (
            CycleResult,
        )
        from openjiuwen.core.session.stream.base import (
            OutputSchema,
        )

        received_tasks = None

        async def _fake_stream(tasks=None):
            nonlocal received_tasks
            received_tasks = tasks
            yield OutputSchema(
                type="message", index=0,
                payload={"content": "会话启动"},
            )

        mock_orch = MagicMock()
        mock_orch.run_session_stream = _fake_stream
        mock_orch._results = [
            CycleResult(success=True),
        ]

        with patch(
            "openjiuwen.auto_harness.orchestrator"
            ".create_auto_harness_orchestrator",
            return_value=mock_orch,
        ):
            from rich.console import Console

            from openjiuwen.harness.cli.ui.repl import (
                _subcmd_run,
            )

            console = Console(file=open(
                os.devnull, "w",
            ))
            data_dir = tmp_path / "auto_harness"
            data_dir.mkdir(parents=True)

            await _subcmd_run(
                console,
                ["--task", "fix-lint"],
                str(tmp_path),
            )

        assert received_tasks is not None
        assert len(received_tasks) == 1
        assert received_tasks[0].topic == "fix-lint"

    @pytest.mark.asyncio
    async def test_run_without_task(self, tmp_path):
        """无 --task 时 tasks=None，走 assess→plan。"""
        from unittest.mock import MagicMock

        from openjiuwen.auto_harness.schema import (
            CycleResult,
        )
        from openjiuwen.core.session.stream.base import (
            OutputSchema,
        )

        received_tasks = "NOT_CALLED"

        async def _fake_stream(tasks=None):
            nonlocal received_tasks
            received_tasks = tasks
            yield OutputSchema(
                type="message", index=0,
                payload={"content": "会话启动"},
            )

        mock_orch = MagicMock()
        mock_orch.run_session_stream = _fake_stream
        mock_orch._results = []

        with patch(
            "openjiuwen.auto_harness.orchestrator"
            ".create_auto_harness_orchestrator",
            return_value=mock_orch,
        ):
            from rich.console import Console

            from openjiuwen.harness.cli.ui.repl import (
                _subcmd_run,
            )

            console = Console(file=open(
                os.devnull, "w",
            ))
            data_dir = tmp_path / "auto_harness"
            data_dir.mkdir(parents=True)

            await _subcmd_run(
                console,
                [],
                str(tmp_path),
            )

        assert received_tasks is None

    @pytest.mark.asyncio
    async def test_dry_run_skips_execution(
        self, tmp_path,
    ):
        """--dry-run 只打印任务 JSON，不创建 orch。"""
        from rich.console import Console
        from io import StringIO

        from openjiuwen.harness.cli.ui.repl import (
            _subcmd_run,
        )

        buf = StringIO()
        console = Console(file=buf, force_terminal=False)
        data_dir = tmp_path / "auto_harness"
        data_dir.mkdir(parents=True)

        with patch(
            "openjiuwen.auto_harness.orchestrator"
            ".create_auto_harness_orchestrator",
        ) as mock_create:
            await _subcmd_run(
                console,
                ["--task", "test-task", "--dry-run"],
                str(tmp_path),
            )
            mock_create.assert_not_called()

        output = buf.getvalue()
        assert "test-task" in output
        assert "跳过执行" in output

    @pytest.mark.asyncio
    async def test_budget_and_no_push_override(
        self, tmp_path,
    ):
        """--budget 和 --no-push 覆盖配置。"""
        from unittest.mock import MagicMock

        from openjiuwen.auto_harness.schema import (
            CycleResult,
        )
        from openjiuwen.core.session.stream.base import (
            OutputSchema,
        )

        captured_config = None

        def _capture_create(config):
            nonlocal captured_config
            captured_config = config

            async def _fake_stream(tasks=None):
                yield OutputSchema(
                    type="message", index=0,
                    payload={"content": "ok"},
                )

            mock_orch = MagicMock()
            mock_orch.run_session_stream = _fake_stream
            mock_orch._results = []
            return mock_orch

        with patch(
            "openjiuwen.auto_harness.orchestrator"
            ".create_auto_harness_orchestrator",
            side_effect=_capture_create,
        ):
            from rich.console import Console

            from openjiuwen.harness.cli.ui.repl import (
                _subcmd_run,
            )

            console = Console(file=open(
                os.devnull, "w",
            ))
            data_dir = tmp_path / "auto_harness"
            data_dir.mkdir(parents=True)

            await _subcmd_run(
                console,
                [
                    "--task", "t1",
                    "--budget", "120",
                    "--no-push",
                ],
                str(tmp_path),
            )

        assert captured_config is not None
        assert captured_config.session_budget_secs == 120
        assert captured_config.git_remote == ""



# ------------------------------------------------------------------
# 优化场景 E2E（需要真实 LLM + GitCode token）
# ------------------------------------------------------------------


@pytest.mark.skip(
    reason="需要真实 LLM API 和 GITCODE_ACCESS_TOKEN",
)
class TestAutoHarnessOptimizationScenarios:
    """auto-harness 真实优化场景 E2E 测试。

    这些测试验证 auto-harness 的核心能力：
    在 worktree 中对 agent-core 现有代码做优化改动，
    通过 CI 门禁后提交 PR。

    需要环境变量：
    - OPENJIUWEN_API_KEY / OPENJIUWEN_API_BASE
    - GITCODE_ACCESS_TOKEN
    """

    @pytest.fixture
    def ah_config(self, tmp_path):
        """创建测试用 auto-harness 配置。"""
        data_dir = tmp_path / "auto_harness"
        data_dir.mkdir()
        cfg_content = (
            "local_repo: "
            '"/home/snape/code/gitcode/agent-core"\n'
            "git:\n"
            '  remote: "k00591264"\n'
            '  base_branch: "develop"\n'
            '  user_name: "sally kang"\n'
            '  user_email: "sally@example.com"\n'
            '  fork_owner: "SnapeK"\n'
            "budget:\n"
            "  session_secs: 300\n"
            "  task_timeout_secs: 240\n"
            "  max_tasks_per_session: 1\n"
        )
        cfg_file = data_dir / "config.yaml"
        cfg_file.write_text(
            cfg_content, encoding="utf-8",
        )
        return str(data_dir)

    def test_optimize_add_type_annotations(
        self, ah_config, tmp_path,
    ):
        """优化场景：为现有模块补充类型注解。

        验证 agent 能在 worktree 中修改代码、
        通过 lint 检查、commit + push + 创建 PR。
        """
        from tests.cli.e2e.conftest import (
            CLI_CMD, E2E_ENV,
        )

        result = subprocess.run(
            [
                *CLI_CMD,
                "-w", ah_config,
                "auto-harness", "run",
                "--task",
                "为 openjiuwen/auto_harness/"
                "controller/session_budget.py "
                "补充所有公共方法的返回值类型注解",
                "--budget", "240",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env=E2E_ENV,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0
        assert "成功" in result.stdout or "OK" in result.stdout

    def test_optimize_improve_docstrings(
        self, ah_config, tmp_path,
    ):
        """优化场景：改善现有模块的文档字符串。

        auto-harness 典型任务：提升代码可读性。
        """
        from tests.cli.e2e.conftest import (
            CLI_CMD, E2E_ENV,
        )

        result = subprocess.run(
            [
                *CLI_CMD,
                "-w", ah_config,
                "auto-harness", "run",
                "--task",
                "为 openjiuwen/auto_harness/"
                "memory/store.py 中缺少 docstring "
                "的公共方法补充 Google 风格文档字符串",
                "--budget", "240",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env=E2E_ENV,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0

    def test_optimize_fix_lint_issues(
        self, ah_config, tmp_path,
    ):
        """优化场景：修复 ruff lint 告警。

        auto-harness 典型任务：代码规范治理。
        """
        from tests.cli.e2e.conftest import (
            CLI_CMD, E2E_ENV,
        )

        task_json = json.dumps([{
            "topic": "修复 lint 告警",
            "description": (
                "运行 ruff check openjiuwen/"
                "auto_harness/ 并修复所有可自动修复"
                "的 lint 告警（unused imports、"
                "trailing whitespace 等）"
            ),
            "files": [
                "openjiuwen/auto_harness/",
            ],
        }], ensure_ascii=False)

        task_file = tmp_path / "tasks.json"
        task_file.write_text(
            task_json, encoding="utf-8",
        )

        result = subprocess.run(
            [
                *CLI_CMD,
                "-w", ah_config,
                "auto-harness", "run",
                "--task-file", str(task_file),
                "--budget", "240",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env=E2E_ENV,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0

    def test_optimize_add_error_handling(
        self, ah_config, tmp_path,
    ):
        """优化场景：增强错误处理和日志。

        auto-harness 典型任务：提升代码健壮性。
        """
        from tests.cli.e2e.conftest import (
            CLI_CMD, E2E_ENV,
        )

        result = subprocess.run(
            [
                *CLI_CMD,
                "-w", ah_config,
                "auto-harness", "run",
                "--task",
                "为 openjiuwen/auto_harness/tools/"
                "git_tool.py 的 _create_pr_sync 方法"
                "增加更详细的错误日志，包括 HTTP "
                "状态码和响应体摘要",
                "--budget", "240",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env=E2E_ENV,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0
