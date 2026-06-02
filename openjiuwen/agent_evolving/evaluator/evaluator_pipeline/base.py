# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Type

from openjiuwen.core.common.logging import logger

from .docker_env import DockerEnvironment
from .models import AgentContext, AgentRunResult, EvalResult, SkillDelta, Task

# Registry maps - populated by register_agent and register_benchmark decorators
_AGENT_REGISTRY: dict[str, Type["BaseAgentAdapter"]] = {}
_BENCH_REGISTRY: dict[str, Type["BaseBenchAdapter"]] = {}


def register_agent(name: str):
    """Decorator to register an agent adapter."""
    def decorator(cls: Type["BaseAgentAdapter"]) -> Type["BaseAgentAdapter"]:
        _AGENT_REGISTRY[name] = cls
        return cls
    return decorator


def register_benchmark(name: str):
    """Decorator to register a benchmark adapter."""
    def decorator(cls: Type["BaseBenchAdapter"]) -> Type["BaseBenchAdapter"]:
        _BENCH_REGISTRY[name] = cls
        return cls
    return decorator


def create_agent(name: str, config: dict[str, Any]) -> "BaseAgentAdapter":
    """Create an agent adapter instance by name."""
    # First try to auto-discover if not found
    if name not in _AGENT_REGISTRY:
        # First, try explicit import of built-in adapters
        try:
            from .adapters.agents import jiuwenswarm as _jiuwenswarm
        except ImportError:
            pass
        
        # Then try auto-discovery for custom adapters
        _discover_adapters("agents")
    
    cls = _AGENT_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown agent: {name}. Available: {list(_AGENT_REGISTRY.keys())}")
    return cls(config)


def create_benchmark(name: str, config: dict[str, Any]) -> "BaseBenchAdapter":
    """Create a benchmark adapter instance by name."""
    # First try to auto-discover if not found
    if name not in _BENCH_REGISTRY:
        # First, try explicit import of built-in adapters
        try:
            from .adapters.benchmarks import skillsbench as _skillsbench
        except ImportError:
            pass
        
        # Then try auto-discovery for custom adapters
        _discover_adapters("benchmarks")
    
    cls = _BENCH_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown benchmark: {name}. Available: {list(_BENCH_REGISTRY.keys())}")
    return cls(config)


def _discover_adapters(package: str) -> None:
    """Auto-discover adapter modules in the specified package."""
    adapters_dir = Path(__file__).parent / "adapters" / package
    if not adapters_dir.exists():
        return
    
    for _, module_name, is_pkg in pkgutil.iter_modules([str(adapters_dir)]):
        if is_pkg:
            continue
        module_path = f"openjiuwen.agent_evolving.evaluator.evaluator_pipeline.adapters.{package}.{module_name}"
        try:
            importlib.import_module(module_path)
        except Exception as e:
            logger.warning(f"Failed to load {package} adapter module '{module_name}': {e}")


# Explicitly import built-in adapters to ensure they are registered
try:
    from .adapters.agents import jiuwenswarm as _jiuwenswarm
    from .adapters.benchmarks import skillsbench as _skillsbench
except ImportError:
    pass


class BaseAgentAdapter(ABC):
    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._logs_dir: Path | None = None
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0

    @staticmethod
    @abstractmethod
    def name() -> str:
        ...

    @abstractmethod
    def supported_skills_modes(self) -> list[str]:
        ...

    def default_model(self) -> str | None:
        return None

    def validate_config(self) -> list[str]:
        return []

    @property
    def logs_dir(self) -> Path:
        if self._logs_dir is None:
            raise RuntimeError("logs_dir not set, call set_logs_dir() first")
        return self._logs_dir

    def set_logs_dir(self, logs_dir: Path) -> None:
        self._logs_dir = logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    async def setup(self, env: DockerEnvironment) -> bool:
        ...

    @abstractmethod
    async def run(
        self,
        env: DockerEnvironment,
        task: Task,
        context: AgentContext,
    ) -> AgentRunResult:
        ...

    async def load_skills(
        self,
        env: DockerEnvironment,
        skills: dict[str, str],
        evolutions: dict[str, str] | None = None,
        evolution_files: dict[str, dict[str, str]] | None = None,
    ) -> int:
        return 0

    def set_skill_context(self, resolved_name: str, all_names: list[str]) -> None:
        """Set the skill context for the agent."""
        pass

    async def load_skills_from_dir(self, env: DockerEnvironment, skills_dir: Path) -> list[str]:
        """Load skills from a directory."""
        return []

    @property
    def captured_evolution_json(self) -> dict[str, str]:
        """Get captured evolution JSON files."""
        return {}

    async def capture_skills(self, env: DockerEnvironment) -> SkillDelta:
        return SkillDelta()

    def get_source_files(self) -> dict[str, Any] | None:
        """Returns source files or configuration required for Agent installation.

        Returns a dictionary with the following keys:
        - mode: Installation mode, possible values: "local" | "git" | "pypi"
        - sources: dict[name -> path], only valid when mode="local", indicates local source directories
        - packages: list[str], pip package list (valid for mode="git" or "pypi")
          e.g.: ["git+https://gitcode.com/openJiuwen/jiuwenswarm.git@develop"]

        Returns None if no special installation is required.
        Subclasses override this method to provide installation configuration for the Agent.
        """
        return None


class BaseBenchAdapter(ABC):
    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}

    @staticmethod
    @abstractmethod
    def name() -> str:
        ...

    @abstractmethod
    def load_tasks(self) -> list[Task]:
        ...

    @abstractmethod
    async def prepare_environment(self, task: Task, env: DockerEnvironment) -> None:
        ...

    @abstractmethod
    async def evaluate(self, env: DockerEnvironment, task: Task) -> EvalResult:
        ...

    def clone_repo(self) -> bool:
        """Automatically clone or update the benchmark repository.
        
        Returns True if successful or repository already exists, False if failed.
        Subclasses can override this method to implement specific cloning logic.
        """
        return True

    def task_base_path(self) -> str:
        return ""

    def filter_tasks(
        self,
        tasks: list[Task],
        task_ids: list[str] | None = None,
        categories: list[str] | None = None,
        difficulties: list[str] | None = None,
    ) -> list[Task]:
        filtered = tasks
        if task_ids:
            filtered = [t for t in filtered if t.task_id in task_ids]
        if categories:
            filtered = [t for t in filtered if t.metadata.get("category") in categories]
        if difficulties:
            filtered = [t for t in filtered if t.metadata.get("difficulty") in difficulties]
        return filtered

    def aggregate(self, results: list[EvalResult]) -> dict[str, Any]:
        if not results:
            return {"overall_score": 0.0, "passed": 0, "total": 0}
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        avg_rate = sum(r.pass_rate for r in results) / total
        return {"overall_score": avg_rate, "passed": passed, "total": total}
