# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for JiuWenSwarm agent adapter."""

import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import yaml

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.adapters.agents.jiuwenswarm import (
    JiuWenSwarmAgent,
    _debug_dir,
    _staged_file,
    _write_private,
)
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models import (
    AgentContext,
    ExecResult,
    Task,
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


class TestStagedFile:
    """The staging helper must not expose its content to other local accounts."""

    @staticmethod
    def test_staged_file_is_private_to_its_owner():
        with _staged_file("payload") as path:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    @staticmethod
    def test_staged_file_holds_the_content():
        with _staged_file("payload", suffix=".env") as path:
            assert path.read_text(encoding="utf-8") == "payload"
            assert path.suffix == ".env"

    @staticmethod
    def test_staged_file_lives_in_the_configured_temp_dir():
        with _staged_file("payload") as path:
            assert path.parent == Path(tempfile.gettempdir())

    @staticmethod
    def test_staged_names_are_not_reused():
        with _staged_file("payload") as first, _staged_file("payload") as second:
            assert first != second

    @staticmethod
    def test_staged_file_is_removed_on_success():
        with _staged_file("payload") as path:
            assert path.exists()
        assert not path.exists()

    @staticmethod
    def test_staged_file_is_removed_when_the_body_raises():
        captured: list[Path] = []
        with pytest.raises(RuntimeError):
            with _staged_file("payload") as path:
                captured.append(path)
                raise RuntimeError("copy failed")

        assert captured and not captured[0].exists()

    @staticmethod
    def test_staged_file_is_removed_when_the_write_fails():
        """A failed write must not strand the partially staged content."""
        staged: list[Path] = []
        real_mkstemp = tempfile.mkstemp
        real_fdopen = os.fdopen

        def recording_mkstemp(*args, **kwargs):
            fd, raw_path = real_mkstemp(*args, **kwargs)
            staged.append(Path(raw_path))
            return fd, raw_path

        class FailingHandle:
            def __init__(self, handle):
                self._handle = handle

            def write(self, _content):
                raise OSError("no space left on device")

            def __enter__(self):
                return self

            def __exit__(self, *_exc_info):
                self._handle.close()
                return False

        def failing_fdopen(fd, *args, **kwargs):
            return FailingHandle(real_fdopen(fd, *args, **kwargs))

        with patch.object(tempfile, "mkstemp", recording_mkstemp):
            with patch("os.fdopen", failing_fdopen):
                with pytest.raises(OSError):
                    with _staged_file("payload"):
                        pass

        assert staged and not staged[0].exists()

    @staticmethod
    def test_staged_file_closes_the_descriptor_it_cannot_wrap():
        """``mkstemp`` hands over a descriptor that must not outlive a failed wrap."""
        staged: list[Path] = []
        descriptors: list[int] = []
        real_mkstemp = tempfile.mkstemp

        def recording_mkstemp(*args, **kwargs):
            fd, raw_path = real_mkstemp(*args, **kwargs)
            descriptors.append(fd)
            staged.append(Path(raw_path))
            return fd, raw_path

        def failing_fdopen(*_args, **_kwargs):
            raise MemoryError("cannot allocate the file object")

        with patch.object(tempfile, "mkstemp", recording_mkstemp):
            with patch("os.fdopen", failing_fdopen):
                with pytest.raises(MemoryError):
                    with _staged_file("payload"):
                        pass

        with pytest.raises(OSError):
            os.fstat(descriptors[0])
        assert staged and not staged[0].exists()


class TestDebugOutput:
    """Debug output must not be writable, or readable, by other local accounts."""

    @staticmethod
    def test_debug_dir_is_private_and_unpredictable():
        first = _debug_dir()
        second = _debug_dir()
        try:
            assert stat.S_IMODE(first.stat().st_mode) == 0o700
            assert first.parent == Path(tempfile.gettempdir())
            assert first != second
        finally:
            first.rmdir()
            second.rmdir()

    @staticmethod
    def test_write_private_creates_an_owner_only_file(tmp_path):
        target = tmp_path / "raw_output.txt"
        _write_private(target, "transcript")

        assert target.read_text(encoding="utf-8") == "transcript"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    @staticmethod
    def test_write_private_truncates_an_existing_file(tmp_path):
        target = tmp_path / "raw_output.txt"
        _write_private(target, "long previous transcript")
        _write_private(target, "short")

        assert target.read_text(encoding="utf-8") == "short"

    @staticmethod
    def test_write_private_refuses_to_follow_a_symlink(tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("untouched", encoding="utf-8")
        planted = tmp_path / "raw_output.txt"
        planted.symlink_to(victim)

        with pytest.raises(OSError):
            _write_private(planted, "attacker-chosen content")

        assert victim.read_text(encoding="utf-8") == "untouched"

    @staticmethod
    @pytest.mark.asyncio
    async def test_run_sends_its_output_through_the_private_helpers(tmp_path):
        """The run path itself must not fall back to a fixed, shared directory."""
        env = MagicMock()
        env.exec = AsyncMock(
            return_value=ExecResult(stdout="transcript", stderr="diagnostics", returncode=0)
        )
        env.copy_to = AsyncMock(return_value=True)
        agent = JiuWenSwarmAgent(
            {
                "api_key": "not-a-real-key-0123456789",
                "api_base": "https://api.example.invalid",
            }
        )

        real_mkdtemp = tempfile.mkdtemp

        def redirected_mkdtemp(prefix="", **_kwargs):
            return real_mkdtemp(prefix=prefix, dir=tmp_path)

        with patch.object(tempfile, "mkdtemp", redirected_mkdtemp):
            await agent.run(
                env,
                Task(task_id="task-1", instruction="do the thing"),
                AgentContext(iteration=1, has_skill=False),
            )

        created = [entry for entry in tmp_path.iterdir() if entry.is_dir()]
        assert len(created) == 1
        assert stat.S_IMODE(created[0].stat().st_mode) == 0o700
        for name in ("raw_output.txt", "stderr.txt"):
            written = created[0] / name
            assert stat.S_IMODE(written.stat().st_mode) == 0o600


class TestJiuWenSwarmAgentCredentialStaging:
    """The API key must never rest at a guessable path in the shared temp dir."""

    API_KEY = "not-a-real-key-0123456789"

    def _agent_and_env(self, copy_to):
        async def exec_command(command: str, timeout: int) -> ExecResult:
            stdout = "OK" if "import jiuwenswarm" in command else ""
            return ExecResult(stdout=stdout, returncode=0)

        env = MagicMock()
        env.exec = AsyncMock(side_effect=exec_command)
        env.copy_to = AsyncMock(side_effect=copy_to)
        agent = JiuWenSwarmAgent(
            {
                "api_key": self.API_KEY,
                "api_base": "https://api.example.invalid",
                "model_name": "example-model",
            }
        )
        return agent, env

    @pytest.mark.asyncio
    async def test_env_file_is_staged_privately_and_unpredictably(self):
        staged: dict[str, tuple[Path, int]] = {}

        async def copy_to(source: Path, destination: str) -> bool:
            source = Path(source)
            staged[destination] = (source, stat.S_IMODE(source.stat().st_mode))
            return True

        agent, env = self._agent_and_env(copy_to)
        assert await agent.setup(env) is True

        env_path, mode = staged[f"{agent.CONFIG_DIR}/.env"]

        # Readable and writable by the owner only, for the whole copy window.
        assert mode == 0o600

        # The name must not be derivable from the configuration, so it cannot be
        # pre-created as a symlink pointing somewhere else.
        assert "jiuwenswarm_env" not in env_path.name
        assert self.API_KEY not in env_path.name
        assert "example-model" not in env_path.name

        # And it must not survive the copy.
        assert not env_path.exists()

    @pytest.mark.asyncio
    async def test_env_file_is_removed_when_the_copy_fails(self):
        staged: list[Path] = []

        async def copy_to(source: Path, destination: str) -> bool:
            staged.append(Path(source))
            raise OSError("docker cp failed")

        agent, env = self._agent_and_env(copy_to)
        with pytest.raises(OSError):
            await agent.setup(env)

        assert staged, "the .env file was never staged"
        assert not staged[0].exists()


class TestJiuWenSwarmAgentSkillStaging:
    """Skill payloads must not be staged at names the caller can influence."""

    @staticmethod
    def _env(copy_to):
        env = MagicMock()
        env.exec = AsyncMock(return_value=ExecResult(stdout="", returncode=0))
        env.copy_to = AsyncMock(side_effect=copy_to)
        return env

    @pytest.mark.asyncio
    async def test_skill_payloads_are_staged_privately(self):
        staged: list[tuple[Path, int]] = []

        async def copy_to(source: Path, destination: str) -> bool:
            source = Path(source)
            staged.append((source, stat.S_IMODE(source.stat().st_mode)))
            return True

        agent = JiuWenSwarmAgent()
        loaded = await agent.load_skills(
            self._env(copy_to),
            {"alpha": "skill body"},
            evolutions={"alpha": "{}"},
            evolution_files={"alpha": {"notes.md": "extra body"}},
        )

        assert loaded == 1
        assert len(staged) == 3
        for path, mode in staged:
            assert mode == 0o600
            assert not path.exists()

    @pytest.mark.asyncio
    async def test_skill_name_does_not_reach_the_host_path(self):
        staged: list[Path] = []

        async def copy_to(source: Path, destination: str) -> bool:
            staged.append(Path(source))
            return True

        agent = JiuWenSwarmAgent()
        await agent.load_skills(self._env(copy_to), {"../escape": "skill body"})

        assert staged
        assert staged[0].parent == Path(tempfile.gettempdir())
        assert "escape" not in staged[0].name

    @pytest.mark.asyncio
    async def test_skill_payload_is_removed_when_the_copy_fails(self):
        staged: list[Path] = []

        async def copy_to(source: Path, destination: str) -> bool:
            staged.append(Path(source))
            raise OSError("docker cp failed")

        agent = JiuWenSwarmAgent()
        with pytest.raises(OSError):
            await agent.load_skills(self._env(copy_to), {"alpha": "skill body"})

        assert staged and not staged[0].exists()


class TestJiuWenSwarmAgentSetup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("evolution_enabled", [True, False])
    async def test_setup_writes_canonical_evolution_yaml_without_legacy_env(
        self,
        evolution_enabled,
    ):
        copied: dict[str, str] = {}

        async def exec_command(command: str, timeout: int) -> ExecResult:
            stdout = "OK" if "import jiuwenswarm" in command else ""
            return ExecResult(stdout=stdout, returncode=0)

        async def copy_to(source: Path, destination: str) -> bool:
            copied[destination] = Path(source).read_text(encoding="utf-8")
            return True

        env = MagicMock()
        env.exec = AsyncMock(side_effect=exec_command)
        env.copy_to = AsyncMock(side_effect=copy_to)
        agent = JiuWenSwarmAgent(
            {
                "api_key": "test-key",
                "api_base": "https://api.example.com",
                "evolution_enabled": evolution_enabled,
            }
        )

        assert await agent.setup(env) is True

        env_content = copied[f"{agent.CONFIG_DIR}/.env"]
        assert "EVOLUTION_AUTO_SCAN" not in env_content
        assert "EVOLUTION_AUTO_SAVE" not in env_content

        config = yaml.safe_load(copied[f"{agent.CONFIG_DIR}/config.yaml"])
        evolution = config["react"]["evolution"]
        assert evolution["skill_evolution"] is evolution_enabled
        assert evolution["auto_save"] is evolution_enabled
        assert "enabled" not in evolution
        assert "auto_scan" not in evolution
        assert "skill_base_dir" not in evolution


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
