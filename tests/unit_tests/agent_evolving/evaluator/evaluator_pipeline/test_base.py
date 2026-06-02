# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for evaluator_pipeline base classes."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base import (
    BaseAgentAdapter,
    BaseBenchAdapter,
)
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models import (
    AgentContext,
    AgentRunResult,
    EvalResult,
    SkillDelta,
    Task,
)


class TestBaseAgentAdapter:
    """Test BaseAgentAdapter abstract class."""

    @staticmethod
    def test_default_model_returns_none():
        """Test default_model returns None by default."""
        class TestAdapter(BaseAgentAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def supported_skills_modes(self) -> list[str]:
                return []
            async def setup(self, env):
                return True
            async def run(self, env, task, context):
                return AgentRunResult()

        adapter = TestAdapter()
        assert adapter.default_model() is None

    @staticmethod
    def test_validate_config_returns_empty_list():
        """Test validate_config returns empty list by default."""
        class TestAdapter(BaseAgentAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def supported_skills_modes(self) -> list[str]:
                return []
            async def setup(self, env):
                return True
            async def run(self, env, task, context):
                return AgentRunResult()

        adapter = TestAdapter()
        assert adapter.validate_config() == []

    @staticmethod
    def test_logs_dir_raises_when_not_set():
        """Test logs_dir property raises RuntimeError when not set."""
        class TestAdapter(BaseAgentAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def supported_skills_modes(self) -> list[str]:
                return []
            async def setup(self, env):
                return True
            async def run(self, env, task, context):
                return AgentRunResult()

        adapter = TestAdapter()
        with pytest.raises(RuntimeError, match="logs_dir not set"):
            _ = adapter.logs_dir

    @staticmethod
    def test_set_logs_dir_creates_directory(tmp_path: Path):
        """Test set_logs_dir creates the directory."""
        class TestAdapter(BaseAgentAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def supported_skills_modes(self) -> list[str]:
                return []
            async def setup(self, env):
                return True
            async def run(self, env, task, context):
                return AgentRunResult()

        adapter = TestAdapter()
        logs_path = tmp_path / "logs"
        adapter.set_logs_dir(logs_path)
        
        assert logs_path.exists()
        assert adapter.logs_dir == logs_path

    @staticmethod
    @pytest.mark.asyncio
    async def test_load_skills_returns_zero():
        """Test load_skills returns 0 by default."""
        class TestAdapter(BaseAgentAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def supported_skills_modes(self) -> list[str]:
                return []
            async def setup(self, env):
                return True
            async def run(self, env, task, context):
                return AgentRunResult()

        adapter = TestAdapter()
        env = MagicMock()
        result = await adapter.load_skills(env, {"skill1": "content"})
        assert result == 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_capture_skills_returns_empty_delta():
        """Test capture_skills returns empty SkillDelta by default."""
        class TestAdapter(BaseAgentAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def supported_skills_modes(self) -> list[str]:
                return []
            async def setup(self, env):
                return True
            async def run(self, env, task, context):
                return AgentRunResult()

        adapter = TestAdapter()
        env = MagicMock()
        result = await adapter.capture_skills(env)
        assert isinstance(result, SkillDelta)
        assert result.skills == {}

    @staticmethod
    def test_get_source_files_returns_none():
        """Test get_source_files returns None by default."""
        class TestAdapter(BaseAgentAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def supported_skills_modes(self) -> list[str]:
                return []
            async def setup(self, env):
                return True
            async def run(self, env, task, context):
                return AgentRunResult()

        adapter = TestAdapter()
        assert adapter.get_source_files() is None


class TestBaseBenchAdapter:
    """Test BaseBenchAdapter abstract class."""

    @staticmethod
    def test_clone_repo_returns_true():
        """Test clone_repo returns True by default."""
        class TestBench(BaseBenchAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def load_tasks(self):
                return []
            async def prepare_environment(self, task, env):
                pass
            async def evaluate(self, env, task):
                return EvalResult()

        bench = TestBench()
        assert bench.clone_repo() is True

    @staticmethod
    def test_task_base_path_returns_empty():
        """Test task_base_path returns empty string by default."""
        class TestBench(BaseBenchAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def load_tasks(self):
                return []
            async def prepare_environment(self, task, env):
                pass
            async def evaluate(self, env, task):
                return EvalResult()

        bench = TestBench()
        assert bench.task_base_path() == ""

    @staticmethod
    def test_filter_tasks_no_filter():
        """Test filter_tasks returns all tasks when no filters provided."""
        class TestBench(BaseBenchAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def load_tasks(self):
                return []
            async def prepare_environment(self, task, env):
                pass
            async def evaluate(self, env, task):
                return EvalResult()

        bench = TestBench()
        tasks = [
            Task(task_id="task1", instruction="Test1"),
            Task(task_id="task2", instruction="Test2"),
        ]
        filtered = bench.filter_tasks(tasks)
        assert len(filtered) == 2
        assert filtered[0].task_id == "task1"

    @staticmethod
    def test_filter_tasks_by_task_ids():
        """Test filter_tasks filters by task_ids."""
        class TestBench(BaseBenchAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def load_tasks(self):
                return []
            async def prepare_environment(self, task, env):
                pass
            async def evaluate(self, env, task):
                return EvalResult()

        bench = TestBench()
        tasks = [
            Task(task_id="task1", instruction="Test1"),
            Task(task_id="task2", instruction="Test2"),
            Task(task_id="task3", instruction="Test3"),
        ]
        filtered = bench.filter_tasks(tasks, task_ids=["task1", "task3"])
        assert len(filtered) == 2
        assert {t.task_id for t in filtered} == {"task1", "task3"}

    @staticmethod
    def test_filter_tasks_by_categories():
        """Test filter_tasks filters by categories."""
        class TestBench(BaseBenchAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def load_tasks(self):
                return []
            async def prepare_environment(self, task, env):
                pass
            async def evaluate(self, env, task):
                return EvalResult()

        bench = TestBench()
        tasks = [
            Task(task_id="task1", instruction="Test1", metadata={"category": "cat1"}),
            Task(task_id="task2", instruction="Test2", metadata={"category": "cat2"}),
            Task(task_id="task3", instruction="Test3", metadata={"category": "cat1"}),
        ]
        filtered = bench.filter_tasks(tasks, categories=["cat1"])
        assert len(filtered) == 2
        assert {t.task_id for t in filtered} == {"task1", "task3"}

    @staticmethod
    def test_filter_tasks_by_difficulties():
        """Test filter_tasks filters by difficulties."""
        class TestBench(BaseBenchAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def load_tasks(self):
                return []
            async def prepare_environment(self, task, env):
                pass
            async def evaluate(self, env, task):
                return EvalResult()

        bench = TestBench()
        tasks = [
            Task(task_id="task1", instruction="Test1", metadata={"difficulty": "easy"}),
            Task(task_id="task2", instruction="Test2", metadata={"difficulty": "hard"}),
            Task(task_id="task3", instruction="Test3", metadata={"difficulty": "medium"}),
        ]
        filtered = bench.filter_tasks(tasks, difficulties=["easy", "medium"])
        assert len(filtered) == 2
        assert {t.task_id for t in filtered} == {"task1", "task3"}

    @staticmethod
    def test_aggregate_empty_results():
        """Test aggregate returns default values for empty results."""
        class TestBench(BaseBenchAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def load_tasks(self):
                return []
            async def prepare_environment(self, task, env):
                pass
            async def evaluate(self, env, task):
                return EvalResult()

        bench = TestBench()
        result = bench.aggregate([])
        assert result == {"overall_score": 0.0, "passed": 0, "total": 0}

    @staticmethod
    def test_aggregate_with_results():
        """Test aggregate calculates correct statistics."""
        class TestBench(BaseBenchAdapter):
            @staticmethod
            def name() -> str:
                return "test"
            def load_tasks(self):
                return []
            async def prepare_environment(self, task, env):
                pass
            async def evaluate(self, env, task):
                return EvalResult()

        bench = TestBench()
        results = [
            EvalResult(passed=True, pass_rate=1.0),
            EvalResult(passed=False, pass_rate=0.0),
            EvalResult(passed=True, pass_rate=1.0),
        ]
        result = bench.aggregate(results)
        assert result["total"] == 3
        assert result["passed"] == 2
        assert result["overall_score"] == pytest.approx(2.0 / 3)
