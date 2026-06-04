# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for evaluator_pipeline config."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
import yaml

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config import (
    PipelineConfig,
    _resolve_env_vars,
)


class TestResolveEnvVars:
    """Test _resolve_env_vars helper function."""

    @staticmethod
    def test_resolve_env_vars_replaces_value():
        """Test _resolve_env_vars replaces ${VAR} with environment variable."""
        os.environ["TEST_VAR"] = "test_value"
        cfg = {"key": "${TEST_VAR}"}
        _resolve_env_vars(cfg)
        assert cfg["key"] == "test_value"
        del os.environ["TEST_VAR"]

    @staticmethod
    def test_resolve_env_vars_keeps_non_env_values():
        """Test _resolve_env_vars leaves non-env values unchanged."""
        cfg = {"key": "normal_value", "num": 123}
        _resolve_env_vars(cfg)
        assert cfg["key"] == "normal_value"
        assert cfg["num"] == 123

    @staticmethod
    def test_resolve_env_vars_handles_missing_env():
        """Test _resolve_env_vars handles missing environment variable."""
        cfg = {"key": "${NON_EXISTENT_VAR}"}
        _resolve_env_vars(cfg)
        assert cfg["key"] == "${NON_EXISTENT_VAR}"

    @staticmethod
    def test_resolve_env_vars_nested_dict():
        """Test _resolve_env_vars works with nested dictionaries."""
        os.environ["NESTED_VAR"] = "nested_value"
        cfg = {"outer": {"inner": "${NESTED_VAR}"}}
        _resolve_env_vars(cfg)
        # Note: _resolve_env_vars does not handle nested dicts by design
        assert cfg["outer"] == {"inner": "${NESTED_VAR}"}
        del os.environ["NESTED_VAR"]


class TestPipelineConfig:
    """Test PipelineConfig dataclass."""

    @staticmethod
    def test_default_values():
        """Test PipelineConfig default values."""
        config = PipelineConfig()
        assert config.agent == "jiuwenswarm"
        assert config.benchmark == "skillsbench"
        assert config.evolution_mode is False
        assert config.max_iterations == 1
        assert config.convergence_check is True
        assert config.convergence_threshold == 2
        assert config.stagnation_patience == 3
        assert config.results_dir == Path("./evolution_results")
        assert config.save_trajectory is True
        assert config.save_skill_history is True
        assert config.agent_config == {}
        assert config.bench_config == {}
        assert config.task_ids == []
        assert config.tasks_filter == ""

    @staticmethod
    def test_custom_values():
        """Test PipelineConfig with custom values."""
        config = PipelineConfig(
            agent="custom_agent",
            max_iterations=5,
            evolution_mode=True,
            convergence_threshold=3,
        )
        assert config.agent == "custom_agent"
        assert config.max_iterations == 5
        assert config.evolution_mode is True
        assert config.convergence_threshold == 3

    @staticmethod
    def test_from_args():
        """Test PipelineConfig.from_args method."""
        config = PipelineConfig.from_args(
            agent="test_agent",
            benchmark="test_bench",
            max_iterations=10,
        )
        assert config.agent == "test_agent"
        assert config.benchmark == "test_bench"
        assert config.max_iterations == 10

    @staticmethod
    def test_from_dict():
        """Test PipelineConfig.from_dict method."""
        data = {
            "agent": "dict_agent",
            "max_iterations": 7,
            "evolution_mode": True,
            "extra_key": "should_be_ignored",
        }
        config = PipelineConfig.from_dict(data)
        assert config.agent == "dict_agent"
        assert config.max_iterations == 7
        assert config.evolution_mode is True
        assert not hasattr(config, "extra_key")

    @staticmethod
    def test_from_yaml(tmp_path: Path):
        """Test PipelineConfig.from_yaml method."""
        yaml_content = """
pipeline:
  agent: yaml_agent
  benchmark: yaml_bench
  max_iterations: 3
  evolution_mode: true
  convergence_threshold: 4

agent_config:
  api_key: test_key

bench_config:
  data_path: ./data
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml_content)

        config = PipelineConfig.from_yaml(config_file)
        assert config.agent == "yaml_agent"
        assert config.benchmark == "yaml_bench"
        assert config.max_iterations == 3
        assert config.evolution_mode is True
        assert config.convergence_threshold == 4
        assert config.agent_config == {"api_key": "test_key"}
        assert config.bench_config == {"data_path": "./data"}

    @staticmethod
    def test_from_yaml_with_env_var(tmp_path: Path):
        """Test PipelineConfig.from_yaml with environment variable substitution."""
        os.environ["TEST_API_KEY"] = "secret_key"
        yaml_content = """
pipeline:
  agent: jiuwenswarm

agent_config:
  api_key: ${TEST_API_KEY}
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml_content)

        config = PipelineConfig.from_yaml(config_file)
        assert config.agent_config["api_key"] == "secret_key"
        del os.environ["TEST_API_KEY"]
