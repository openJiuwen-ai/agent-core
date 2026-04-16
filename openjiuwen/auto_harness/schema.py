# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness Agent 数据模型。"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjiuwen.core.foundation.llm.model import Model

logger = logging.getLogger(__name__)

_DEFAULT_REPO_URL = (
    "https://gitcode.com/openJiuwen/agent-core.git"
)
_CONFIG_TEMPLATE = (
    Path(__file__).parent / "resources" / "config.yaml"
)


class TaskStatus(str, Enum):
    """优化任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REVERTED = "reverted"


class ExperienceType(str, Enum):
    """经验记录类型。"""

    OPTIMIZATION = "optimization"
    FAILURE = "failure"
    INSIGHT = "insight"


@dataclass
class Gap:
    """竞品差距。"""

    id: str = ""
    competitor: str = ""
    feature: str = ""
    current_state: str = ""
    gap_description: str = ""
    impact: float = 0.0
    feasibility: float = 0.0
    suggested_approach: str = ""
    target_files: List[str] = field(
        default_factory=list
    )

    @property
    def priority(self) -> float:
        """impact x feasibility。"""
        return self.impact * self.feasibility


@dataclass
class OptimizationTask:
    """单个优化任务。"""

    topic: str
    description: str = ""
    files: List[str] = field(default_factory=list)
    issue_ref: Optional[str] = None
    expected_effect: str = ""
    status: TaskStatus = TaskStatus.PENDING


@dataclass
class Experience:
    """经验库记录。"""

    type: ExperienceType = ExperienceType.OPTIMIZATION
    topic: str = ""
    summary: str = ""
    outcome: str = ""
    details: str = ""
    pr_url: str = ""
    files_changed: List[str] = field(
        default_factory=list
    )
    id: str = field(
        default_factory=lambda: uuid.uuid4().hex[:12]
    )
    timestamp: float = field(default_factory=time.time)


@dataclass
class ResearchContext:
    """Research 阶段收集的上下文。"""

    experiences: List[Experience] = field(
        default_factory=list
    )
    source_files: dict[str, str] = field(
        default_factory=dict
    )
    gap_report: Optional[str] = None


@dataclass
class CycleResult:
    """单个 task 的执行结果。"""

    success: bool = False
    summary: str = ""
    pr_url: str = ""
    error: str = ""
    reverted: bool = False
    error_log: str = ""


@dataclass
class CommitFacts:
    """提交阶段的事实快照。"""

    branch_name: str = ""
    task_declared_files: List[str] = field(
        default_factory=list
    )
    preexisting_dirty_files: List[str] = field(
        default_factory=list
    )
    current_dirty_files: List[str] = field(
        default_factory=list
    )
    tracked_modified_files: List[str] = field(
        default_factory=list
    )
    untracked_files: List[str] = field(
        default_factory=list
    )
    edited_files: List[str] = field(
        default_factory=list
    )
    allowed_files: List[str] = field(
        default_factory=list
    )
    derived_test_files: List[str] = field(
        default_factory=list
    )
    legacy_related_test_files: List[str] = field(
        default_factory=list
    )
    verify_related_files: List[str] = field(
        default_factory=list
    )
    diff_stat: str = ""


@dataclass
class AutoHarnessConfig:
    """Auto Harness Agent 配置。

    ``data_dir`` 由宿主 CLI 传入，所有产物（经验库、
    运行记录、clone 缓存、worktree）都存放在此目录下。

    ``local_repo`` 可选，指向本地 agent-core 仓库路径，
    用于加速 worktree 创建。未配置时自动 clone 到
    ``{data_dir}/repo/agent-core``。
    """

    model: Optional[Model] = None

    # ---- 路径 ----
    data_dir: str = ""
    local_repo: str = ""
    repo_url: str = _DEFAULT_REPO_URL

    # ---- 语言 ----
    language: str = "cn"
    optimization_goal: str = ""
    competitor: str = ""

    # ---- 预算 ----
    session_budget_secs: float = 3600.0
    cost_limit_usd: float = 10.0
    task_timeout_secs: float = 1200.0
    max_tasks_per_session: int = 3
    self_driven_slots: int = 1

    # ---- Git ----
    git_remote: str = ""
    git_base_branch: str = "develop"
    git_user_name: str = ""
    git_user_email: str = ""
    fork_owner: str = ""
    upstream_owner: str = "openJiuwen"
    upstream_repo: str = "agent-core"

    # ---- GitCode ----
    gitcode_username: str = ""
    gitcode_token: str = ""
    gitcode_token_env: str = "GITCODE_ACCESS_TOKEN"

    # ---- CI 门控 ----
    ci_gate_config: str = ""
    ci_gate_python_executable: str = ""
    ci_gate_install_command: str = ""

    # ---- Fix Loop ----
    fix_phase1_max_retries: int = 10
    fix_phase2_max_retries: int = 9

    # ---- 安全 ----
    immutable_files: List[str] = field(
        default_factory=lambda: [
            "openjiuwen/auto_harness/prompts/identity.md",
            "openjiuwen/auto_harness/resources/ci_gate.yaml",
            "openjiuwen/harness/rails/security_rail.py",
        ]
    )
    high_impact_prefixes: List[str] = field(
        default_factory=lambda: [
            "openjiuwen/core/",
        ]
    )

    # ---- 兼容旧字段 ----
    # workspace 已废弃，保留以兼容旧调用方
    workspace: str = ""
    config_path: str = ""
    config_bootstrapped: bool = False
    suggested_local_repo: str = ""
    experience_dir: str = ""

    @property
    def resolved_experience_dir(self) -> str:
        """经验库目录，从 data_dir 派生。"""
        if self.experience_dir:
            return self.experience_dir
        if self.data_dir:
            return str(Path(self.data_dir) / "experience")
        return ".auto_harness/experience/"

    @property
    def worktrees_dir(self) -> str:
        """Worktree 根目录，从 data_dir 派生。"""
        if self.data_dir:
            return str(Path(self.data_dir) / "worktrees")
        return ".auto_harness/worktrees/"

    @property
    def runs_dir(self) -> str:
        """运行记录目录，从 data_dir 派生。"""
        if self.data_dir:
            return str(Path(self.data_dir) / "runs")
        return ".auto_harness/runs/"

    @property
    def cache_repo_dir(self) -> str:
        """Clone 缓存目录，从 data_dir 派生。"""
        if self.data_dir:
            return str(
                Path(self.data_dir) / "repo" / "agent-core"
            )
        return ".auto_harness/repo/agent-core"

    def resolve_gitcode_token(self) -> str:
        """解析 GitCode token。

        优先使用 ``gitcode_token``，否则从
        ``gitcode_token_env`` 指定的环境变量读取。

        Returns:
            Token 字符串，未配置时返回空字符串。
        """
        if self.gitcode_token:
            return self.gitcode_token
        return os.getenv(self.gitcode_token_env, "")

    def resolve_gitcode_username(self) -> str:
        """Resolve the GitCode login username for git HTTPS auth."""
        if self.gitcode_username:
            return self.gitcode_username
        if self.fork_owner:
            return self.fork_owner
        return ""

    def resolve_ci_gate_python_executable(self) -> str:
        """Resolve the Python executable used by CI gate commands."""
        if self.ci_gate_python_executable:
            return self.ci_gate_python_executable

        candidates = []
        if self.workspace:
            candidates.append(
                Path(self.workspace) / ".venv" / "bin" / "python"
            )
        if self.local_repo:
            candidates.append(
                Path(self.local_repo) / ".venv" / "bin" / "python"
            )

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

        return sys.executable

    @staticmethod
    def load_from_dict(
        data: Dict[str, Any],
    ) -> "AutoHarnessConfig":
        """从字典构建配置，支持嵌套 YAML 结构。

        Args:
            data: 配置字典，支持顶层和嵌套 key。

        Returns:
            填充后的 AutoHarnessConfig 实例。
        """
        cfg = AutoHarnessConfig()

        # 顶层字段
        if "data_dir" in data:
            cfg.data_dir = str(data["data_dir"])
        if "local_repo" in data:
            cfg.local_repo = str(data["local_repo"])
        if "repo_url" in data:
            cfg.repo_url = str(data["repo_url"])
        if "language" in data:
            cfg.language = str(data["language"])
        # 兼容旧字段
        if "workspace" in data:
            cfg.workspace = str(data["workspace"])
        if "experience_dir" in data:
            cfg.experience_dir = str(
                data["experience_dir"]
            )

        # git section
        git = data.get("git", {})
        if isinstance(git, dict):
            if "remote" in git:
                cfg.git_remote = str(git["remote"])
            if "base_branch" in git:
                cfg.git_base_branch = str(
                    git["base_branch"]
                )
            if "user_name" in git:
                cfg.git_user_name = str(git["user_name"])
            if "user_email" in git:
                cfg.git_user_email = str(git["user_email"])
            if "fork_owner" in git:
                cfg.fork_owner = str(git["fork_owner"])
            if "upstream_owner" in git:
                cfg.upstream_owner = str(
                    git["upstream_owner"]
                )
            if "upstream_repo" in git:
                cfg.upstream_repo = str(
                    git["upstream_repo"]
                )

        # gitcode section
        gc = data.get("gitcode", {})
        if isinstance(gc, dict):
            if "username" in gc:
                cfg.gitcode_username = str(
                    gc["username"]
                )
            if "access_token_env" in gc:
                cfg.gitcode_token_env = str(
                    gc["access_token_env"]
                )
            if "access_token" in gc:
                cfg.gitcode_token = str(
                    gc["access_token"]
                )

        # budget section
        budget = data.get("budget", {})
        if isinstance(budget, dict):
            if "session_secs" in budget:
                cfg.session_budget_secs = float(
                    budget["session_secs"]
                )
            if "cost_limit_usd" in budget:
                cfg.cost_limit_usd = float(
                    budget["cost_limit_usd"]
                )
            if "task_timeout_secs" in budget:
                cfg.task_timeout_secs = float(
                    budget["task_timeout_secs"]
                )
            if "max_tasks_per_session" in budget:
                cfg.max_tasks_per_session = int(
                    budget["max_tasks_per_session"]
                )

        # ci_gate section
        ci = data.get("ci_gate", {})
        if isinstance(ci, dict):
            if "config_path" in ci:
                cfg.ci_gate_config = str(
                    ci["config_path"]
                )
            if "python_executable" in ci:
                cfg.ci_gate_python_executable = str(
                    ci["python_executable"]
                )
            if "install_command" in ci:
                cfg.ci_gate_install_command = str(
                    ci["install_command"]
                )

        # fix_loop section
        fl = data.get("fix_loop", {})
        if isinstance(fl, dict):
            if "phase1_max_retries" in fl:
                cfg.fix_phase1_max_retries = int(
                    fl["phase1_max_retries"]
                )
            if "phase2_max_retries" in fl:
                cfg.fix_phase2_max_retries = int(
                    fl["phase2_max_retries"]
                )

        return cfg


def load_auto_harness_config(
    config_path: str,
    *,
    workspace_hint: str = "",
) -> AutoHarnessConfig:
    """从 YAML 文件加载 AutoHarnessConfig。

    文件不存在时自动生成模板并返回默认配置。

    Args:
        config_path: YAML 配置文件路径。
        workspace_hint: 当前 CLI 工作目录，用于推断
            建议的 ``local_repo``。

    Returns:
        填充后的 AutoHarnessConfig 实例。
    """
    import yaml

    path = Path(config_path)
    if not path.is_file():
        bootstrapped = _bootstrap_config_file(
            path,
            suggested_local_repo=_detect_local_repo(
                workspace_hint
            ),
        )
        logger.info(
            "Config file not found: %s, "
            "using defaults",
            config_path,
        )
        cfg = AutoHarnessConfig()
        cfg.config_path = str(path)
        cfg.config_bootstrapped = bootstrapped
        cfg.suggested_local_repo = _detect_local_repo(
            workspace_hint
        )
        if not cfg.data_dir:
            cfg.data_dir = str(path.parent)
        return cfg

    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        logger.warning(
            "Failed to load config: %s, "
            "using defaults",
            config_path,
            exc_info=True,
        )
        cfg = AutoHarnessConfig()
        cfg.config_path = str(path)
        cfg.suggested_local_repo = _detect_local_repo(
            workspace_hint
        )
        if not cfg.data_dir:
            cfg.data_dir = str(path.parent)
        return cfg

    cfg = AutoHarnessConfig.load_from_dict(data)
    cfg.config_path = str(path)
    cfg.suggested_local_repo = _detect_local_repo(
        workspace_hint
    )

    # data_dir 默认为配置文件所在目录
    if not cfg.data_dir:
        cfg.data_dir = str(path.parent)

    return cfg


def _bootstrap_config_file(
    path: Path,
    *,
    suggested_local_repo: str,
) -> bool:
    """Create a starter config file when missing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        template = _CONFIG_TEMPLATE.read_text(
            encoding="utf-8"
        )
        path.write_text(template, encoding="utf-8")
        return True
    except Exception:
        logger.warning(
            "Failed to bootstrap config: %s",
            path,
            exc_info=True,
        )
        return False


def _detect_local_repo(workspace_hint: str) -> str:
    """Best-effort detection of a local agent-core repo."""
    candidates: list[Path] = []
    if workspace_hint:
        hint = Path(workspace_hint).expanduser()
        candidates.extend([
            hint,
            hint / "agent-core",
        ])
    cwd = Path.cwd()
    candidates.extend([cwd, cwd / "agent-core"])

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if _looks_like_repo_root(resolved):
            return key
    return ""


def _looks_like_repo_root(path: Path) -> bool:
    """Return whether *path* looks like the local agent-core repo."""
    return (
        path.is_dir()
        and (path / ".git").exists()
        and (path / "pyproject.toml").is_file()
        and (path / "openjiuwen").is_dir()
    )


def is_placeholder_local_repo(path: str) -> bool:
    """Return whether *path* is an obvious template/example value."""
    normalized = path.strip()
    return normalized in {
        "/home/user/code/agent-core",
        "/home/user/repo",
    }
