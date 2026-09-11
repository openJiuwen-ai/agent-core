from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_config import _apply_env_overrides
from openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_executor import PPOTrainingExecutor
from openjiuwen.agent_evolving.agent_rl.online.training_runner import TrainingArtifact
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ExecutionError


@dataclass
class _Version:
    version: str
    path: str


class _LoRARepository:
    def __init__(self) -> None:
        self.latest: _Version | None = None

    def list_versions(self, model_id: str) -> list[_Version]:
        assert model_id == "model-1"
        return [_Version("v1", "/loras/model-1/v1")]

    def get_latest(self, model_id: str) -> _Version | None:
        assert model_id == "model-1"
        return self.latest


class _CancelableExecutor(PPOTrainingExecutor):
    def __init__(self, repository: _LoRARepository) -> None:
        super().__init__(
            base_model_path="/models/base",
            lora_repo=repository,
            nproc_per_node=1,
            training_gpu_ids="",
            ppo_config_path=None,
        )
        self.started = threading.Event()
        self.finished = threading.Event()
        self.release = threading.Event()

    def _run_ppo_training_sync(self, **kwargs):
        del kwargs
        self.started.set()
        self.release.wait(timeout=5)
        self.finished.set()
        raise RuntimeError("actor stopped")

    def close(self) -> None:
        self.release.set()


@pytest.mark.asyncio
async def test_train_uses_parent_snapshot_and_returns_repository_version() -> None:
    repository = _LoRARepository()
    executor = PPOTrainingExecutor(
        base_model_path="/models/base",
        lora_repo=repository,
        nproc_per_node=1,
        training_gpu_ids="",
        ppo_config_path=None,
    )
    calls: list[dict] = []

    async def train_batch(**kwargs):
        calls.append(kwargs)
        repository.latest = _Version("v2", "/loras/model-1/v2")
        return repository.latest.path

    executor.train_batch = train_batch

    artifact = await executor.train(
        training_run_id="run-1",
        model_id="model-1",
        samples=[{"sample_id": "sample-1"}],
        init_lora_name="model-1:v1",
        init_lora_path="/loras/model-1/v1",
    )

    assert artifact == TrainingArtifact("model-1:v2", "/loras/model-1/v2")
    assert calls == [
        {
            "user_id": "model-1",
            "samples": [{"sample_id": "sample-1"}],
            "training_count": 2,
            "tmp_root": "/tmp/agent_rl_online",
            "init_lora_name": "model-1:v1",
            "init_lora_path": "/loras/model-1/v1",
        }
    ]


@pytest.mark.asyncio
async def test_cancel_waits_until_background_ppo_has_stopped() -> None:
    executor = _CancelableExecutor(_LoRARepository())
    training = asyncio.create_task(
        executor.train(
            training_run_id="run-1",
            model_id="model-1",
            samples=[{"sample_id": "sample-1"}],
            init_lora_name="base",
            init_lora_path="",
        )
    )
    assert await asyncio.to_thread(executor.started.wait, 2)

    await executor.cancel("run-1")

    assert executor.finished.is_set()
    with pytest.raises(ExecutionError, match="actor stopped") as exc_info:
        await training
    assert exc_info.value.status is StatusCode.AGENT_RL_PPO_EXECUTION_ERROR
    assert isinstance(exc_info.value.cause, RuntimeError)


def test_online_ppo_preserves_captured_old_logprobs() -> None:
    torch = pytest.importorskip("torch")

    from openjiuwen.agent_evolving.agent_rl.rl_trainer.verl_converter import VerlDataProtoConverter
    from openjiuwen.agent_evolving.agent_rl.rl_trainer.verl_executor import BaseVerlTrainingExecutor

    batch = VerlDataProtoConverter(pad_token_id=0).convert_samples(
        [
            {
                "sample_id": "sample-1",
                "trajectory": {
                    "prompt_ids": [1],
                    "response_ids": [2, 3],
                    "response_logprobs": [-0.25, -0.5],
                },
                "judge": {"score": 1.0},
            }
        ]
    )
    expected = torch.tensor([[-0.25, -0.5]])
    executor = SimpleNamespace(
        actor_rollout_wg=SimpleNamespace(world_size=1),
        pad_size=None,
        preserve_provided_old_log_probs=True,
    )

    result = BaseVerlTrainingExecutor.compute_old_log_prob(executor, batch, {})

    assert torch.equal(result.batch["old_log_probs"], expected)


def test_online_ppo_actor_learning_rate_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("ONLINE_RL_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ONLINE_RL_ACTOR_LEARNING_RATE", "0.0001")
    config = {"actor_rollout_ref": {"actor": {"optim": {"lr": 0.00001}}}}

    _apply_env_overrides(config)

    assert config["actor_rollout_ref"]["actor"]["optim"]["lr"] == pytest.approx(0.0001)
