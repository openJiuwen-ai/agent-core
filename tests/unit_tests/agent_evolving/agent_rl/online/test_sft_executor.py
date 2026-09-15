from __future__ import annotations

from dataclasses import dataclass

import pytest

from openjiuwen.agent_evolving.agent_rl.online.core.factory import build_training_executor
from openjiuwen.agent_evolving.agent_rl.online.training_runner import TrainingArtifact
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


def _executor(repository: _LoRARepository, *, dry_run: bool = False):
    return build_training_executor(
        train_backend="SFT",
        base_model_path="/models/base",
        lora_repo=repository,
        notifier=None,
        training_gpu_ids="4,5,6,7",
        target_model_id="model-1",
        sft_dry_run=dry_run,
    )


@pytest.mark.asyncio
async def test_sft_factory_executor_supports_durable_training_run() -> None:
    repository = _LoRARepository()
    executor = _executor(repository)
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
        tmp_root="/tmp/sft-test",
    )

    assert artifact == TrainingArtifact("model-1:v2", "/loras/model-1/v2")
    assert calls == [
        {
            "user_id": "model-1",
            "samples": [{"sample_id": "sample-1"}],
            "training_count": 2,
            "tmp_root": "/tmp/sft-test",
        }
    ]


@pytest.mark.asyncio
async def test_sft_dry_run_cannot_return_publishable_artifact() -> None:
    repository = _LoRARepository()
    executor = _executor(repository, dry_run=True)

    async def train_batch(**kwargs):
        del kwargs
        return "/tmp/sft-test/train.parquet"

    executor.train_batch = train_batch

    with pytest.raises(ExecutionError, match="SFT_DRY_RUN"):
        await executor.train(
            training_run_id="run-1",
            model_id="model-1",
            samples=[{"sample_id": "sample-1"}],
        )


@pytest.mark.asyncio
async def test_sft_cancel_only_stops_matching_active_run() -> None:
    repository = _LoRARepository()
    executor = _executor(repository)
    stop_calls: list[bool] = []
    executor._active_training_run_id = "run-1"
    executor.request_stop = lambda: stop_calls.append(True) or {"active": True}

    assert await executor.cancel("run-other") is False
    assert stop_calls == []
    assert await executor.cancel("run-1") is True
    assert stop_calls == [True]
