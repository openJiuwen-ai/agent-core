# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for evaluator_pipeline models."""

from datetime import datetime
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models import (
    ExecResult,
    Task,
    AgentContext,
    AgentRunResult,
    EvalResult,
    SkillDelta,
    IterationResult,
    PipelineResult,
)


class TestExecResult:
    """Test ExecResult dataclass."""

    @staticmethod
    def test_success_property():
        """Test success property returns True when returncode is 0."""
        result = ExecResult(stdout="output", returncode=0)
        assert result.success is True

    @staticmethod
    def test_failure_property():
        """Test success property returns False when returncode is non-zero."""
        result = ExecResult(stderr="error", returncode=1)
        assert result.success is False

    @staticmethod
    def test_timeout_property():
        """Test timed_out property works correctly."""
        result = ExecResult(timed_out=True, returncode=-1)
        assert result.timed_out is True


class TestTask:
    """Test Task dataclass."""

    @staticmethod
    def test_task_creation():
        """Test Task creation with minimal parameters."""
        task = Task(task_id="test_task", instruction="Test instruction")
        assert task.task_id == "test_task"
        assert task.instruction == "Test instruction"
        assert task.metadata == {}
        assert task.environment_spec == {}
        assert task.has_skills is False
        assert task.skills == []

    @staticmethod
    def test_task_with_metadata():
        """Test Task with metadata and skills."""
        task = Task(
            task_id="test_task",
            instruction="Test",
            metadata={"key": "value"},
            has_skills=True,
            skills=["skill1", "skill2"],
        )
        assert task.metadata == {"key": "value"}
        assert task.has_skills is True
        assert task.skills == ["skill1", "skill2"]


class TestAgentContext:
    """Test AgentContext dataclass."""

    @staticmethod
    def test_default_context():
        """Test default AgentContext values."""
        ctx = AgentContext()
        assert ctx.iteration == 1
        assert ctx.has_skill is False
        assert ctx.previous_result is None
        assert ctx.evolution_suggestions is None
        assert ctx.evolution_files is None

    @staticmethod
    def test_context_with_values():
        """Test AgentContext with custom values."""
        ctx = AgentContext(
            iteration=3,
            has_skill=True,
            evolution_suggestions="suggestion text",
            n_input_tokens=100,
            n_output_tokens=200,
        )
        assert ctx.iteration == 3
        assert ctx.has_skill is True
        assert ctx.evolution_suggestions == "suggestion text"
        assert ctx.n_input_tokens == 100
        assert ctx.n_output_tokens == 200


class TestAgentRunResult:
    """Test AgentRunResult dataclass."""

    @staticmethod
    def test_result_creation():
        """Test AgentRunResult creation."""
        result = AgentRunResult(
            final_response="response",
            trajectory=[{"step": 1}],
            execution_time=1.5,
            tokens_used=1000,
        )
        assert result.final_response == "response"
        assert result.trajectory == [{"step": 1}]
        assert result.execution_time == 1.5
        assert result.tokens_used == 1000


class TestEvalResult:
    """Test EvalResult dataclass."""

    @staticmethod
    def test_eval_result_passed():
        """Test EvalResult with passed=True."""
        result = EvalResult(
            passed=True,
            pass_rate=1.0,
            test_output="All tests passed",
            returncode=0,
        )
        assert result.passed is True
        assert result.pass_rate == pytest.approx(1.0)
        assert result.returncode == 0

    @staticmethod
    def test_eval_result_failed():
        """Test EvalResult with passed=False."""
        result = EvalResult(
            passed=False,
            pass_rate=0.5,
            failed_tests=["test1", "test2"],
            returncode=1,
        )
        assert result.passed is False
        assert result.pass_rate == pytest.approx(0.5)
        assert result.failed_tests == ["test1", "test2"]


class TestSkillDelta:
    """Test SkillDelta dataclass."""

    @staticmethod
    def test_skill_delta_default():
        """Test default SkillDelta values."""
        delta = SkillDelta()
        assert delta.skills == {}
        assert delta.evolutions == {}
        assert delta.evolution_files == {}
        assert delta.changed is False

    @staticmethod
    def test_skill_delta_with_content():
        """Test SkillDelta with content."""
        delta = SkillDelta(
            skills={"skill1": "content"},
            evolutions={"skill1": "evo json"},
            changed=True,
        )
        assert delta.changed is True
        assert "skill1" in delta.skills


class TestIterationResult:
    """Test IterationResult dataclass."""

    @staticmethod
    def test_iteration_result():
        """Test IterationResult creation."""
        agent_result = AgentRunResult(final_response="test")
        eval_result = EvalResult(passed=True)
        skill_delta = SkillDelta()

        result = IterationResult(
            iteration=1,
            agent_result=agent_result,
            eval_result=eval_result,
            skill_delta=skill_delta,
        )
        assert result.iteration == 1
        assert result.agent_result.final_response == "test"
        assert result.eval_result.passed is True
        assert result.skill_changed is False


class TestPipelineResult:
    """Test PipelineResult dataclass."""

    @staticmethod
    def test_pipeline_result_creation():
        """Test PipelineResult creation."""
        result = PipelineResult(
            task_id="task1",
            agent_name="jiuwenswarm",
            benchmark_name="skillsbench",
            total_iterations=3,
            convergence_achieved=True,
            convergence_type="convergence",
            metrics={"accuracy": 0.8},
        )
        assert result.task_id == "task1"
        assert result.total_iterations == 3
        assert result.convergence_achieved is True
        assert result.metrics == {"accuracy": 0.8}

    @staticmethod
    def test_to_dict():
        """Test PipelineResult.to_dict method."""
        result = PipelineResult(
            task_id="task1",
            agent_name="jiuwenswarm",
            benchmark_name="skillsbench",
            total_iterations=2,
            convergence_achieved=False,
            metrics={"score": 0.7},
            output_dir=Path("./results"),
        )
        result_dict = result.to_dict()

        assert result_dict["task_id"] == "task1"
        assert result_dict["agent_name"] == "jiuwenswarm"
        assert result_dict["total_iterations"] == 2
        assert result_dict["convergence_achieved"] is False
        assert result_dict["metrics"] == {"score": 0.7}
        # Path converts "./results" to "results" when converted to string
        assert result_dict["output_dir"] == "results"
