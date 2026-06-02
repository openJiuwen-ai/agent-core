# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for evaluator_pipeline docker_env."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env import DockerEnvironment
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models import ExecResult


# Mock shutil.which to return a fake docker path for all tests
@pytest.fixture(autouse=True)
def mock_docker_path():
    """Mock shutil.which to return a fake docker path."""
    with patch("shutil.which") as mock_which:
        mock_which.return_value = "/usr/bin/docker"
        yield


class TestDockerEnvironmentInit:
    """Test DockerEnvironment initialization."""

    @staticmethod
    def test_default_values():
        """Test DockerEnvironment with default values."""
        env = DockerEnvironment(image_tag="test-image")
        
        assert env.image_tag == "test-image"
        assert env._cpus == 1
        assert env._memory_mb == 2048
        assert env._timeout == 900
        assert env._container_id is None

    @staticmethod
    def test_custom_values():
        """Test DockerEnvironment with custom values."""
        env = DockerEnvironment(
            image_tag="test-image",
            container_name="test-container",
            cpus=4,
            memory_mb=4096,
            timeout=1800,
        )
        
        assert env._cpus == 4
        assert env._memory_mb == 4096
        assert env._timeout == 1800
        assert env._container_name == "test-container"

    @staticmethod
    def test_is_running_property():
        """Test is_running property."""
        env = DockerEnvironment(image_tag="test-image")
        assert env.is_running is False
        
        env._container_id = "abc123"
        assert env.is_running is True

    @staticmethod
    def test_container_name_property():
        """Test container_name property."""
        env = DockerEnvironment(image_tag="test/image:tag")
        assert env.container_name == "test_image_tag"
        
        env2 = DockerEnvironment(image_tag="simple", container_name="custom-name")
        assert env2.container_name == "custom-name"


class TestDockerEnvironmentBuild:
    """Test DockerEnvironment.build method."""

    @staticmethod
    def test_build_with_missing_dockerfile(tmp_path: Path):
        """Test build raises FileNotFoundError when Dockerfile missing."""
        env = DockerEnvironment(image_tag="test-image")
        missing_path = tmp_path / "nonexistent" / "Dockerfile"
        
        with pytest.raises(FileNotFoundError):
            env.build(missing_path, tmp_path)

    @staticmethod
    @patch("subprocess.run")
    def test_build_success(mock_run, tmp_path: Path):
        """Test build succeeds with valid Dockerfile."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM alpine")
        
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        
        env = DockerEnvironment(image_tag="test-image")
        result = env.build(dockerfile, tmp_path)
        
        assert result == "test-image"
        mock_run.assert_called_once()

    @staticmethod
    @patch("subprocess.run")
    def test_build_failure(mock_run, tmp_path: Path):
        """Test build raises RuntimeError when docker fails."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM alpine")
        
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="build failed"
        )
        
        env = DockerEnvironment(image_tag="test-image")
        
        with pytest.raises(RuntimeError, match="docker build failed"):
            env.build(dockerfile, tmp_path)

    @staticmethod
    @patch("subprocess.run")
    def test_build_timeout(mock_run, tmp_path: Path):
        """Test build raises RuntimeError on timeout."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM alpine")
        
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["docker", "build"], timeout=600)
        
        env = DockerEnvironment(image_tag="test-image")
        
        with pytest.raises(RuntimeError, match="timed out"):
            env.build(dockerfile, tmp_path)


class TestDockerEnvironmentStartStop:
    """Test DockerEnvironment start/stop methods."""

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_start_success(mock_run_command):
        """Test start method succeeds."""
        mock_run_command.return_value = ExecResult(returncode=0, stdout="container-id-12345")
        
        env = DockerEnvironment(image_tag="test-image")
        await env.start()
        
        assert env._container_id == "container-id-12345"
        mock_run_command.assert_called_once()

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_start_failure(mock_run_command):
        """Test start method raises on failure."""
        mock_run_command.return_value = ExecResult(returncode=1, stderr="failed to start")
        
        env = DockerEnvironment(image_tag="test-image")
        
        with pytest.raises(RuntimeError, match="Failed to start container"):
            await env.start()

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_stop_when_not_running(mock_run_command):
        """Test stop method does nothing when no container."""
        env = DockerEnvironment(image_tag="test-image")
        await env.stop()
        
        mock_run_command.assert_not_called()

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_stop_success(mock_run_command):
        """Test stop method succeeds."""
        mock_run_command.return_value = ExecResult(returncode=0)
        
        env = DockerEnvironment(image_tag="test-image")
        env._container_id = "container-id-12345"
        
        await env.stop()
        
        assert env._container_id is None
        assert mock_run_command.call_count == 2


class TestDockerEnvironmentExec:
    """Test DockerEnvironment.exec method."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_exec_no_container():
        """Test exec returns error when no container running."""
        env = DockerEnvironment(image_tag="test-image")
        
        result = await env.exec("echo hello")
        
        assert result.returncode == -1
        assert "No container running" in result.stderr

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_exec_success(mock_run_command):
        """Test exec succeeds."""
        mock_run_command.return_value = ExecResult(returncode=0, stdout="hello world")
        
        env = DockerEnvironment(image_tag="test-image")
        env._container_id = "container-id"
        
        result = await env.exec("echo hello world")
        
        assert result.success is True
        assert result.stdout == "hello world"

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_exec_with_workdir(mock_run_command):
        """Test exec with workdir parameter."""
        mock_run_command.return_value = ExecResult(returncode=0, stdout="result")
        
        env = DockerEnvironment(image_tag="test-image")
        env._container_id = "container-id"
        
        await env.exec("pwd", workdir="/app")
        
        # Verify command includes workdir
        call_args = mock_run_command.call_args[0][0]
        assert "-w" in call_args
        assert "/app" in call_args

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_exec_with_env(mock_run_command):
        """Test exec with environment variables."""
        mock_run_command.return_value = ExecResult(returncode=0, stdout="result")
        
        env = DockerEnvironment(image_tag="test-image")
        env._container_id = "container-id"
        
        await env.exec("env", env={"VAR1": "value1", "VAR2": "value2"})
        
        call_args = mock_run_command.call_args[0][0]
        assert "-e" in call_args
        assert "VAR1=value1" in call_args


class TestDockerEnvironmentCopy:
    """Test DockerEnvironment copy_to and copy_from methods."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_copy_to_no_container():
        """Test copy_to returns False when no container."""
        env = DockerEnvironment(image_tag="test-image")
        result = await env.copy_to(Path("/tmp/file"), "/dest/path")
        assert result is False

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_copy_to_success(mock_run_command):
        """Test copy_to succeeds."""
        mock_run_command.return_value = ExecResult(returncode=0)
        
        env = DockerEnvironment(image_tag="test-image")
        env._container_id = "container-id"
        
        result = await env.copy_to(Path("/tmp/file"), "/dest/path")
        
        assert result is True

    @staticmethod
    @pytest.mark.asyncio
    async def test_copy_from_no_container():
        """Test copy_from returns False when no container."""
        env = DockerEnvironment(image_tag="test-image")
        result = await env.copy_from("/src/path", Path("/tmp/file"))
        assert result is False

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment._run_command")
    @pytest.mark.asyncio
    async def test_copy_from_success(mock_run_command):
        """Test copy_from succeeds."""
        mock_run_command.return_value = ExecResult(returncode=0)
        
        env = DockerEnvironment(image_tag="test-image")
        env._container_id = "container-id"
        
        result = await env.copy_from("/src/path", Path("/tmp/file"))
        
        assert result is True


class TestDockerEnvironmentRunCommand:
    """Test DockerEnvironment._run_command method."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_run_command_exception():
        """Test _run_command handles exceptions."""
        env = DockerEnvironment(image_tag="test-image")
        
        result = await env._run_command(["nonexistent_command_xyz_123"])
        
        assert result.success is False
        assert result.returncode == -1
