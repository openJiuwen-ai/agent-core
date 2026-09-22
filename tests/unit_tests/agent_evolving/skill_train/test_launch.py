# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the importable offline-training launcher (``skill_train.launch``)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.skill_train import launch
from openjiuwen.agent_evolving.skill_train.model_compat import set_target_backend

_ENV_KEYS = (
    "TARGET_BACKEND",
    "SKILL_TRAIN_DATA_ROOT",
    "SKILL_TRAIN_OUTPUT",
    "SEARCHQA_SPLIT_DIR",
    "SPLIT_DIR",
    "SKILL_INIT",
    "API_KEY",
    "API_BASE",
    "OPENAI_COMPATIBLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_COMPATIBLE_BASE_URL",
    "OPTIMIZER_OPENAI_COMPATIBLE_API_KEY",
    "OPTIMIZER_OPENAI_COMPATIBLE_BASE_URL",
    "OPTIMIZER_MODEL",
    "OPTIMIZER_API_KEY",
    "OPTIMIZER_API_BASE",
    "TARGET_MODEL",
    "TARGET_API_KEY",
    "TARGET_API_BASE",
    "MODEL_NAME",
    "LIMIT",
    "MAX_TURNS",
    "NUM_EPOCHS",
    "WORKERS",
    "ANALYST_WORKERS",
    "EXEC_TIMEOUT",
    "BATCH_SIZE",
    "TRAIN_SIZE",
    "JIUWENSWARM_GATEWAY_URL",
    "REFLACT_JIUWENSWARM_TRACE_TO_OPTIMIZER",
)


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    data_root = tmp_path / "data"
    (data_root / "searchqa_id_split").mkdir(parents=True)
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("API_BASE", "http://llm")
    yield data_root
    set_target_backend("openai_chat")


class TestBuildTrainConfig:
    def test_exec_backend_skips_target_model(self, isolated_env):
        opts = launch.TrainLaunchOptions(
            env_name="searchqa",
            target_backend="jiuwenswarm_cli_exec",
            data_root=str(isolated_env),
            output_dir=str(isolated_env.parent / "out"),
            optimizer_model="opt-model",
            jiuwenswarm_gateway_url="ws://gw:1/tui",
            limit=5,
            num_epochs=1,
        )
        resolved = launch.build_train_config(opts)
        cfg = resolved.config
        assert resolved.backend == "jiuwenswarm_cli_exec"
        assert resolved.target_model == "" and resolved.target_api_key == ""
        assert resolved.optimizer_model == "opt-model"
        assert cfg.target_backend == "jiuwenswarm_cli_exec"
        assert cfg.jiuwenswarm_gateway_url == "ws://gw:1/tui"
        assert cfg.env_kwargs["limit"] == 5
        assert cfg.env_kwargs["split_dir"] == str((isolated_env / "searchqa_id_split").resolve())
        assert Path(cfg.skill_init).name == "initial.md"
        assert cfg.num_epochs == 1
        assert cfg.env_kwargs["workers"] == launch.DEFAULT_EXEC_WORKERS  # preset 24 capped for exec
        assert os.environ["SKILL_TRAIN_DATA_ROOT"] == str(isolated_env)
        summary = resolved.summary()
        assert summary["target_backend"] == "jiuwenswarm_cli_exec" and summary["target_model"] == ""

    def test_chat_backend_from_env_and_target_model(self, isolated_env, monkeypatch):
        monkeypatch.setenv("TARGET_BACKEND", "openai_chat")
        monkeypatch.setenv("TARGET_MODEL", "tgt")
        opts = launch.TrainLaunchOptions(
            env_name="searchqa", data_root=str(isolated_env), optimizer_model="opt", output_dir=str(isolated_env / "o")
        )
        resolved = launch.build_train_config(opts)
        assert resolved.backend == "openai_chat"
        assert resolved.target_model == "tgt"
        assert resolved.target_api_key == "k"
        assert "search_mode" not in resolved.config.env_kwargs
        assert resolved.config.env_kwargs["workers"] == 24  # chat preset untouched

    def test_unknown_backend_and_env_rejected(self, isolated_env):
        with pytest.raises(ValueError, match="unsupported target_backend"):
            launch.build_train_config(launch.TrainLaunchOptions(target_backend="claude_code_exec"))
        with pytest.raises(ValueError, match="unsupported env"):
            launch.build_train_config(launch.TrainLaunchOptions(env_name="nope"))

    def test_missing_split_dir_is_explicit(self, isolated_env, tmp_path):
        opts = launch.TrainLaunchOptions(
            env_name="searchqa", data_root=str(tmp_path / "empty"), optimizer_model="opt", output_dir="o"
        )
        with pytest.raises(FileNotFoundError, match="split_dir not found"):
            launch.build_train_config(opts)

    def test_missing_credentials(self, isolated_env, monkeypatch):
        monkeypatch.delenv("API_KEY")
        opts = launch.TrainLaunchOptions(env_name="searchqa", data_root=str(isolated_env), optimizer_model="opt")
        with pytest.raises(ValueError, match="API_KEY"):
            launch.build_train_config(opts)


class TestRunOfflineTraining:
    def test_wires_trainer_without_target_llm_for_exec(self, isolated_env, monkeypatch):
        captured = {}

        class _Trainer:
            def __init__(self, **kwargs):
                captured["init"] = kwargs

            def train(self, *, config, adapter):
                captured["config"] = config
                captured["adapter"] = adapter
                return "result"

        monkeypatch.setattr("openjiuwen.agent_evolving.skill_train.trainer.SkillReflACTTrainer", _Trainer)
        monkeypatch.setattr(launch, "_build_model", lambda **kw: ("model", kw["model_name"]))
        monkeypatch.setattr(
            "openjiuwen.agent_evolving.skill_train.registry.get_env_adapter",
            lambda name, **kw: ("adapter", name, kw.get("split_dir")),
        )
        opts = launch.TrainLaunchOptions(
            env_name="searchqa",
            target_backend="jiuwenswarm_cli_exec",
            data_root=str(isolated_env),
            output_dir=str(isolated_env.parent / "out"),
            optimizer_model="opt",
        )
        assert launch.run_offline_training(opts) == "result"
        assert captured["init"]["target_llm"] is None
        assert captured["init"]["optimizer_llm"] == ("model", "opt")
        assert captured["adapter"][1] == "searchqa"
        assert captured["config"].target_backend == "jiuwenswarm_cli_exec"


class TestTrainerBackendWiring:
    def test_configure_target_backend_sets_trace_gate(self, monkeypatch):
        from openjiuwen.agent_evolving.skill_train.trainer import _configure_target_backend

        monkeypatch.delenv("REFLACT_JIUWENSWARM_TRACE_TO_OPTIMIZER", raising=False)
        try:
            assert _configure_target_backend({"target_backend": "jiuwenswarm_cli_exec"}) == "jiuwenswarm_cli_exec"
            assert os.environ["REFLACT_JIUWENSWARM_TRACE_TO_OPTIMIZER"] == "1"
            _configure_target_backend(
                {"target_backend": "jiuwenswarm_cli_exec", "jiuwenswarm_trace_to_optimizer": False}
            )
            assert os.environ["REFLACT_JIUWENSWARM_TRACE_TO_OPTIMIZER"] == "0"
            _configure_target_backend({"target_backend": "openai_chat"})
            assert os.environ["REFLACT_JIUWENSWARM_TRACE_TO_OPTIMIZER"] == "0"
        finally:
            set_target_backend("openai_chat")

    def test_chat_backend_requires_target_llm(self, tmp_path):
        from openjiuwen.agent_evolving.skill_train.config import SkillTrainConfig
        from openjiuwen.agent_evolving.skill_train.trainer import SkillReflACTTrainer

        trainer = SkillReflACTTrainer(optimizer_llm=object(), optimizer_model="opt")  # type: ignore[arg-type]
        cfg = SkillTrainConfig(env_name="searchqa", output_dir=str(tmp_path), target_backend="openai_chat")
        with pytest.raises(ValueError, match="target_llm is required"):
            trainer._prepare(cfg, adapter=None)

    def test_config_defaults_include_backend(self):
        from openjiuwen.agent_evolving.skill_train.config import SkillTrainConfig

        flat = SkillTrainConfig().to_trainer_cfg()
        assert flat["target_backend"] == "openai_chat"
        assert flat["jiuwenswarm_trace_to_optimizer"] is True


class TestReflectAttachment:
    def test_jiuwenswarm_trace_attachment_gated(self, tmp_path, monkeypatch):
        from openjiuwen.agent_evolving.skill_train.reflect import _ATTACHMENTS

        spec = next(a for a in _ATTACHMENTS if a.filename == "jiuwenswarm_trace_steps.txt")
        assert spec.heading == "JiuwenSwarm Trace Steps"
        assert spec.env_flag == "REFLACT_JIUWENSWARM_TRACE_TO_OPTIMIZER"
        monkeypatch.setenv("REFLACT_JIUWENSWARM_TRACE_TO_OPTIMIZER", "0")
        assert not spec.enabled()
        monkeypatch.setenv("REFLACT_JIUWENSWARM_TRACE_TO_OPTIMIZER", "1")
        assert spec.enabled()
        (tmp_path / "jiuwenswarm_trace_steps.txt").write_text("[1] tool_call: read_file task.md\n", encoding="utf-8")
        assert spec.resolve({}, str(tmp_path)) == "[1] tool_call: read_file task.md"
        assert not any("openjiuwen_trace" in a.filename for a in _ATTACHMENTS)
