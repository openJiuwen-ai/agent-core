# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for evaluator_pipeline pipeline."""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config import PipelineConfig
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models import (
    Task,
    AgentContext,
    AgentRunResult,
    EvalResult,
    SkillDelta,
    IterationResult,
    PipelineResult,
)


class TestPipelineInitialization:
    """Test EvolutionPipeline initialization."""

    @staticmethod
    def test_pipeline_with_default_config():
        """Test pipeline initialization with default config."""
        config = PipelineConfig()
        
        with patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.create_agent") as mock_create_agent:
            with patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.create_bench") as mock_create_bench:
                from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import EvolutionPipeline
                
                mock_create_agent.return_value = MagicMock()
                mock_create_bench.return_value = MagicMock()
                
                pipeline = EvolutionPipeline(config)
                
                assert pipeline.config == config
                mock_create_agent.assert_called_once()
                mock_create_bench.assert_called_once()

    @staticmethod
    def test_pipeline_with_custom_config():
        """Test pipeline initialization with custom config."""
        config = PipelineConfig(
            agent="custom_agent",
            benchmark="custom_bench",
            max_iterations=5,
            evolution_mode=True,
        )
        
        with patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.create_agent") as mock_create_agent:
            with patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.create_bench") as mock_create_bench:
                from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import EvolutionPipeline
                
                mock_create_agent.return_value = MagicMock()
                mock_create_bench.return_value = MagicMock()
                
                pipeline = EvolutionPipeline(config)
                
                assert pipeline.config.agent == "custom_agent"
                assert pipeline.config.max_iterations == 5
                assert pipeline.config.evolution_mode is True


class TestPipelineMetrics:
    """Test EvolutionPipeline metrics computation."""

    @staticmethod
    def test_compute_evolution_metrics_empty():
        """Test _compute_evolution_metrics with empty results."""
        from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import EvolutionPipeline
        
        metrics = EvolutionPipeline._compute_evolution_metrics([])
        
        assert metrics == {}

    @staticmethod
    def test_compute_evolution_metrics_with_data():
        """Test _compute_evolution_metrics with iteration results."""
        from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import EvolutionPipeline
        
        results = [
            IterationResult(
                iteration=1,
                agent_result=AgentRunResult(tokens_used=1000),
                eval_result=EvalResult(passed=False, pass_rate=0.5),
                skill_delta=SkillDelta(),
            ),
            IterationResult(
                iteration=2,
                agent_result=AgentRunResult(tokens_used=2000),
                eval_result=EvalResult(passed=True, pass_rate=1.0),
                skill_delta=SkillDelta(),
            ),
        ]
        
        metrics = EvolutionPipeline._compute_evolution_metrics(results)
        
        assert metrics["total_iterations"] == 2
        assert metrics["final_pass_rate"] == pytest.approx(1.0)
        assert metrics["best_pass_rate"] == pytest.approx(1.0)
        assert metrics["improvement"] == pytest.approx(0.5)


class TestBuildEvolutionSuggestions:
    """Test _build_evolution_suggestions static method."""

    @staticmethod
    def test_build_evolution_suggestions_passed():
        """Test _build_evolution_suggestions when previous test passed."""
        from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import EvolutionPipeline
        
        prev_result = IterationResult(
            iteration=1,
            agent_result=AgentRunResult(),
            eval_result=EvalResult(passed=True, pass_rate=1.0),
            skill_delta=SkillDelta(),
        )
        
        suggestions = EvolutionPipeline._build_evolution_suggestions(prev_result)
        
        assert "All tests passed" in suggestions
        assert "No changes needed" in suggestions

    @staticmethod
    def test_build_evolution_suggestions_failed():
        """Test _build_evolution_suggestions when previous test failed."""
        from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import EvolutionPipeline
        
        prev_result = IterationResult(
            iteration=1,
            agent_result=AgentRunResult(),
            eval_result=EvalResult(passed=False, pass_rate=0.5),
            skill_delta=SkillDelta(),
        )
        
        suggestions = EvolutionPipeline._build_evolution_suggestions(prev_result)
        
        assert "pass rate" in suggestions.lower()
        assert "NOT modified" in suggestions


class TestPrintSummary:
    """Test _print_summary static method."""

    @staticmethod
    def test_print_summary_empty():
        """Test _print_summary with empty results."""
        from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import EvolutionPipeline
        
        # Should not raise exception
        EvolutionPipeline._print_summary([])

    @staticmethod
    def test_print_summary_with_results():
        """Test _print_summary with results."""
        from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import EvolutionPipeline
        
        results = [
            PipelineResult(
                task_id="task1",
                agent_name="test_agent",
                benchmark_name="test_bench",
                total_iterations=2,
                convergence_achieved=True,
                metrics={"score": 0.8},
                output_dir=Path("./results"),
            ),
        ]
        
        # Should not raise exception
        EvolutionPipeline._print_summary(results)


class TestCreateAgentAndBench:
    """Test create_agent and create_bench functions."""

    @staticmethod
    def test_create_agent_default():
        """Test create_agent with default agent type."""
        from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import create_agent
        
        config = {"model": "test-model"}
        
        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_module.JiuWenSwarmAdapter = MagicMock(return_value=MagicMock())
            mock_import.return_value = mock_module
            
            agent = create_agent("jiuwenswarm", config)
            
            assert agent is not None

    @staticmethod
    def test_create_bench_default():
        """Test create_bench with default benchmark type."""
        from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline import create_bench
        
        config = {"data_path": "./data"}
        
        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_module.SkillsBenchAdapter = MagicMock(return_value=MagicMock())
            mock_import.return_value = mock_module
            
            bench = create_bench("skillsbench", config)
            
            assert bench is not None
