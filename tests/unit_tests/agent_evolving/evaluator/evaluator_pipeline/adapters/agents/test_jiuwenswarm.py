# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for JiuWenSwarm agent adapter."""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.adapters.agents.jiuwenswarm import (
    JiuWenSwarmAgent,
)


class TestJiuWenSwarmAgentInit:
    """Test JiuWenSwarmAgent initialization."""

    @staticmethod
    def test_default_init():
        """Test default initialization."""
        agent = JiuWenSwarmAgent()
        
        assert agent._config == {}
        assert agent._resolved_skill_name == ""
        assert agent._all_skill_names == []

    @staticmethod
    def test_init_with_config():
        """Test initialization with config."""
        config = {"model_name": "gpt-4", "api_key": "test-key"}
        agent = JiuWenSwarmAgent(config)
        
        assert agent._config == config

    @staticmethod
    def test_name_staticmethod():
        """Test name() static method."""
        assert JiuWenSwarmAgent.name() == "jiuwenswarm"

    @staticmethod
    def test_supported_skills_modes():
        """Test supported skills modes."""
        agent = JiuWenSwarmAgent()
        modes = agent.supported_skills_modes()
        
        assert "create" in modes
        assert "read" in modes
        assert "evolve" in modes

    @staticmethod
    def test_default_model():
        """Test default model configuration."""
        agent = JiuWenSwarmAgent()
        assert agent.default_model() == "glm-5"
        
        agent2 = JiuWenSwarmAgent({"model_name": "gpt-4"})
        assert agent2.default_model() == "gpt-4"


class TestJiuWenSwarmAgentValidateConfig:
    """Test JiuWenSwarmAgent config validation."""

    @staticmethod
    def test_validate_config_empty():
        """Test validation with empty config."""
        agent = JiuWenSwarmAgent()
        errors = agent.validate_config()
        
        assert len(errors) == 2
        assert "api_key" in errors[0]
        assert "api_base" in errors[1]

    @staticmethod
    def test_validate_config_complete():
        """Test validation with complete config."""
        agent = JiuWenSwarmAgent({
            "api_key": "test-key",
            "api_base": "https://api.example.com",
        })
        errors = agent.validate_config()
        
        assert len(errors) == 0


class TestJiuWenSwarmAgentGetSourceFiles:
    """Test JiuWenSwarmAgent get_source_files method."""

    @staticmethod
    def test_get_source_files_git_mode():
        """Test get_source_files with git mode."""
        agent = JiuWenSwarmAgent({"install_mode": "git"})
        result = agent.get_source_files()
        
        assert result["mode"] == "git"
        assert result["requires_git"] is True
        assert len(result["packages"]) == 1
        assert "git+" in result["packages"][0]

    @staticmethod
    def test_get_source_files_pypi_mode():
        """Test get_source_files with pypi mode."""
        agent = JiuWenSwarmAgent({"install_mode": "pypi"})
        result = agent.get_source_files()
        
        assert result["mode"] == "pypi"
        assert result["packages"] == ["jiuwenswarm"]

    @staticmethod
    @patch("pathlib.Path.exists")
    def test_get_source_files_local_mode_not_found(mock_exists):
        """Test get_source_files with local mode when source not found."""
        mock_exists.return_value = False
        agent = JiuWenSwarmAgent({"install_mode": "local"})
        result = agent.get_source_files()
        
        assert result["mode"] == "git"  # Falls back to git

    @staticmethod
    def test_get_source_files_auto_mode():
        """Test get_source_files with auto mode."""
        agent = JiuWenSwarmAgent({"install_mode": "auto"})
        result = agent.get_source_files()
        
        assert result["mode"] in ["local", "git"]


class TestJiuWenSwarmAgentSkillContext:
    """Test JiuWenSwarmAgent skill context methods."""

    @staticmethod
    def test_set_skill_context():
        """Test set_skill_context method."""
        agent = JiuWenSwarmAgent()
        agent.set_skill_context("skill1", ["skill1", "skill2"])
        
        assert agent._resolved_skill_name == "skill1"
        assert agent._all_skill_names == ["skill1", "skill2"]


class TestJiuWenSwarmAgentConstants:
    """Test JiuWenSwarmAgent class constants."""

    @staticmethod
    def test_class_constants():
        """Test class constants are defined."""
        assert JiuWenSwarmAgent.SKILL_DIR == "/root/.jiuwenswarm/agent/workspace/skills"
        assert JiuWenSwarmAgent.CONFIG_DIR == "/root/.jiuwenswarm/config"
        assert JiuWenSwarmAgent.WORKSPACE_DIR == "/workspace"
