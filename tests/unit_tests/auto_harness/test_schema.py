# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_schema — Auto Harness 数据模型单元测试。"""

from __future__ import annotations

import pytest

from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CycleResult,
    Experience,
    ExperienceType,
    Gap,
    OptimizationTask,
    ResearchContext,
    TaskStatus,
    is_placeholder_local_repo,
    load_auto_harness_config,
)


class TestTaskStatus:
    def test_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.SUCCESS.value == "success"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.TIMEOUT.value == "timeout"
        assert TaskStatus.REVERTED.value == "reverted"

    def test_is_str(self):
        assert isinstance(TaskStatus.PENDING, str)
        assert TaskStatus.PENDING == "pending"


class TestExperienceType:
    def test_values(self):
        assert ExperienceType.OPTIMIZATION.value == "optimization"
        assert ExperienceType.FAILURE.value == "failure"
        assert ExperienceType.INSIGHT.value == "insight"


class TestGap:
    def test_defaults(self):
        g = Gap()
        assert g.id == ""
        assert g.impact == 0.0
        assert g.target_files == []

    def test_priority(self):
        g = Gap(impact=0.8, feasibility=0.5)
        assert g.priority == pytest.approx(0.4)

    def test_priority_zero(self):
        g = Gap(impact=0.0, feasibility=1.0)
        assert g.priority == 0.0


class TestOptimizationTask:
    def test_required_field(self):
        t = OptimizationTask(topic="fix timeout")
        assert t.topic == "fix timeout"
        assert t.status == TaskStatus.PENDING
        assert t.files == []

    def test_status_mutation(self):
        t = OptimizationTask(topic="x")
        t.status = TaskStatus.RUNNING
        assert t.status == TaskStatus.RUNNING


class TestExperience:
    def test_auto_id(self):
        m1 = Experience(topic="a")
        m2 = Experience(topic="b")
        assert m1.id != m2.id
        assert len(m1.id) == 12

    def test_auto_timestamp(self):
        m = Experience()
        assert m.timestamp > 0

    def test_defaults(self):
        m = Experience()
        assert m.type == ExperienceType.OPTIMIZATION
        assert m.files_changed == []


class TestResearchContext:
    def test_defaults(self):
        rc = ResearchContext()
        assert rc.experiences == []
        assert rc.source_files == {}
        assert rc.gap_report is None


class TestCycleResult:
    def test_defaults(self):
        cr = CycleResult()
        assert cr.success is False
        assert cr.pr_url == ""
        assert cr.reverted is False

    def test_success(self):
        cr = CycleResult(success=True, pr_url="http://x")
        assert cr.success is True


class TestAutoHarnessConfig:
    def test_defaults(self):
        cfg = AutoHarnessConfig()
        assert cfg.data_dir == ""
        assert cfg.local_repo == ""
        assert cfg.session_budget_secs == 3600.0
        assert cfg.max_tasks_per_session == 3
        assert cfg.git_remote == ""
        assert cfg.fork_owner == ""
        assert cfg.git_user_name == ""
        assert cfg.gitcode_username == ""
        assert cfg.gitcode_token_env == "GITCODE_ACCESS_TOKEN"
        assert cfg.ci_gate_python_executable == ""
        assert cfg.ci_gate_install_command == ""

    def test_immutable_files_default(self):
        cfg = AutoHarnessConfig()
        assert len(cfg.immutable_files) >= 1

    def test_independent_defaults(self):
        c1 = AutoHarnessConfig()
        c2 = AutoHarnessConfig()
        c1.immutable_files.append("extra.py")
        assert "extra.py" not in c2.immutable_files

    def test_experience_dir_from_data_dir(self):
        cfg = AutoHarnessConfig(data_dir="/tmp/ah")
        assert cfg.resolved_experience_dir == (
            "/tmp/ah/experience"
        )

    def test_explicit_experience_dir_takes_precedence(
        self,
    ):
        cfg = AutoHarnessConfig(
            data_dir="/tmp/ah",
            experience_dir="/tmp/custom-exp",
        )
        assert cfg.resolved_experience_dir == (
            "/tmp/custom-exp"
        )

    def test_worktrees_dir_from_data_dir(self):
        cfg = AutoHarnessConfig(data_dir="/tmp/ah")
        assert cfg.worktrees_dir == "/tmp/ah/worktrees"

    def test_runs_dir_from_data_dir(self):
        cfg = AutoHarnessConfig(data_dir="/tmp/ah")
        assert cfg.runs_dir == "/tmp/ah/runs"

    def test_cache_repo_dir_from_data_dir(self):
        cfg = AutoHarnessConfig(data_dir="/tmp/ah")
        assert cfg.cache_repo_dir == (
            "/tmp/ah/repo/agent-core"
        )

    def test_resolve_gitcode_token_direct(self):
        cfg = AutoHarnessConfig(
            gitcode_token="my-token"
        )
        assert cfg.resolve_gitcode_token() == "my-token"

    def test_resolve_gitcode_token_from_env(
        self, monkeypatch,
    ):
        monkeypatch.setenv(
            "GITCODE_ACCESS_TOKEN", "env-token"
        )
        cfg = AutoHarnessConfig()
        assert cfg.resolve_gitcode_token() == "env-token"

    def test_resolve_gitcode_token_custom_env(
        self, monkeypatch,
    ):
        monkeypatch.setenv("MY_TOKEN", "custom")
        cfg = AutoHarnessConfig(
            gitcode_token_env="MY_TOKEN"
        )
        assert cfg.resolve_gitcode_token() == "custom"

    def test_resolve_gitcode_username_prefers_explicit(
        self,
    ):
        cfg = AutoHarnessConfig(
            gitcode_username="bot-user",
            fork_owner="fallback-owner",
        )
        assert cfg.resolve_gitcode_username() == (
            "bot-user"
        )

    def test_resolve_gitcode_username_falls_back_to_fork_owner(
        self,
    ):
        cfg = AutoHarnessConfig(
            fork_owner="fallback-owner"
        )
        assert cfg.resolve_gitcode_username() == (
            "fallback-owner"
        )

    def test_resolve_ci_gate_python_executable_returns_current(
        self,
    ):
        cfg = AutoHarnessConfig()
        assert cfg.resolve_ci_gate_python_executable()

    def test_resolve_ci_gate_python_executable_prefers_configured(
        self,
    ):
        cfg = AutoHarnessConfig(
            ci_gate_python_executable="/tmp/python3.11"
        )
        assert cfg.resolve_ci_gate_python_executable() == (
            "/tmp/python3.11"
        )


class TestLoadFromDict:
    def test_git_section(self):
        data = {
            "git": {
                "remote": "myfork",
                "base_branch": "main",
                "user_name": "test",
                "user_email": "test@example.com",
                "fork_owner": "TestOwner",
            },
        }
        cfg = AutoHarnessConfig.load_from_dict(data)
        assert cfg.git_remote == "myfork"
        assert cfg.git_base_branch == "main"
        assert cfg.git_user_name == "test"
        assert cfg.git_user_email == "test@example.com"
        assert cfg.fork_owner == "TestOwner"

    def test_budget_section(self):
        data = {
            "budget": {
                "session_secs": 600,
                "cost_limit_usd": 5.0,
                "task_timeout_secs": 300,
                "max_tasks_per_session": 2,
            },
        }
        cfg = AutoHarnessConfig.load_from_dict(data)
        assert cfg.session_budget_secs == 600
        assert cfg.cost_limit_usd == 5.0
        assert cfg.task_timeout_secs == 300
        assert cfg.max_tasks_per_session == 2

    def test_top_level_fields(self):
        data = {
            "local_repo": "/home/user/repo",
            "language": "en",
        }
        cfg = AutoHarnessConfig.load_from_dict(data)
        assert cfg.local_repo == "/home/user/repo"
        assert cfg.language == "en"

    def test_gitcode_section(self):
        data = {
            "gitcode": {
                "username": "bot-user",
                "access_token_env": "AUTO_TOKEN",
                "access_token": "inline-token",
            }
        }
        cfg = AutoHarnessConfig.load_from_dict(data)
        assert cfg.gitcode_username == "bot-user"
        assert cfg.gitcode_token_env == "AUTO_TOKEN"
        assert cfg.gitcode_token == "inline-token"

    def test_ci_gate_section(self):
        data = {
            "ci_gate": {
                "config_path": "/tmp/ci_gate.yaml",
                "python_executable": "/tmp/python3.11",
                "install_command": (
                    "uv sync --active --group dev --extra cli"
                ),
            }
        }
        cfg = AutoHarnessConfig.load_from_dict(data)
        assert cfg.ci_gate_config == "/tmp/ci_gate.yaml"
        assert cfg.ci_gate_python_executable == "/tmp/python3.11"
        assert cfg.ci_gate_install_command == (
            "uv sync --active --group dev --extra cli"
        )

    def test_empty_dict(self):
        cfg = AutoHarnessConfig.load_from_dict({})
        assert cfg.git_remote == ""
        assert cfg.session_budget_secs == 3600.0


class TestLoadAutoHarnessConfig:
    def test_load_from_yaml(self, tmp_path):
        yaml_content = (
            "local_repo: /tmp/repo\n"
            "git:\n"
            "  remote: myfork\n"
            "  fork_owner: TestOwner\n"
            "budget:\n"
            "  session_secs: 900\n"
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        cfg = load_auto_harness_config(str(cfg_file))
        assert cfg.local_repo == "/tmp/repo"
        assert cfg.git_remote == "myfork"
        assert cfg.fork_owner == "TestOwner"
        assert cfg.session_budget_secs == 900

    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load_auto_harness_config(
            str(tmp_path / "nonexistent.yaml")
        )
        assert cfg.git_remote == ""
        assert cfg.session_budget_secs == 3600.0
        assert cfg.config_bootstrapped is True
        assert (tmp_path / "nonexistent.yaml").is_file()

    def test_missing_file_bootstraps_with_detected_local_repo(
        self, tmp_path,
    ):
        repo = tmp_path / "agent-core"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "pyproject.toml").write_text(
            "[project]\nname='x'\n",
            encoding="utf-8",
        )
        (repo / "openjiuwen").mkdir()

        cfg = load_auto_harness_config(
            str(tmp_path / "auto_harness" / "config.yaml"),
            workspace_hint=str(tmp_path),
        )

        assert cfg.suggested_local_repo == str(
            repo.resolve()
        )
        content = (
            tmp_path / "auto_harness" / "config.yaml"
        ).read_text(encoding="utf-8")
        assert (
            '# local_repo: "/home/user/code/agent-core"'
            in content
        )
        assert str(repo.resolve()) not in content

    def test_empty_yaml_returns_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("", encoding="utf-8")
        cfg = load_auto_harness_config(str(cfg_file))
        assert cfg.session_budget_secs == 3600.0


class TestLocalRepoHelpers:
    def test_placeholder_local_repo_detected(self):
        assert is_placeholder_local_repo(
            "/home/user/code/agent-core"
        )
        assert not is_placeholder_local_repo(
            "/home/snape/code/gitcode/agent-core"
        )
