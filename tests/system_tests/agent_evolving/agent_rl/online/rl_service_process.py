from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import uvicorn
from redis.asyncio import from_url as redis_from_url

from openjiuwen.agent_evolving.agent_rl.online.capture_pipeline import CapturePipeline
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store import PendingJudgeStore
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.persistence import GatewayTrajectoryRuntime
from openjiuwen.agent_evolving.agent_rl.online.lora_client import AIGWLoRAClient
from openjiuwen.agent_evolving.agent_rl.online.service import build_rl_service_app
from openjiuwen.agent_evolving.agent_rl.online.task_registry import TaskRegistry
from openjiuwen.agent_evolving.agent_rl.online.training_runner import TrainingArtifact, TrainingRunner
from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore


async def _wait_control(path: Path) -> str:
    mode = path.read_text(encoding="utf-8").strip()
    if mode == "delay":
        await asyncio.sleep(0.75)
    while mode == "block":
        await asyncio.sleep(0.05)
        mode = path.read_text(encoding="utf-8").strip()
    return mode


class _FakeJudge:
    def __init__(self, control_path: Path) -> None:
        self._control_path = control_path

    async def score(self, request, response, followup_user_message) -> float:
        del request, response, followup_user_message
        if await _wait_control(self._control_path) == "fail":
            raise RuntimeError("fake Judge failed")
        return 0.625


class _ControlledCapturePipeline(CapturePipeline):
    def __init__(self, *, control_path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._control_path = control_path

    async def before(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        await _wait_control(self._control_path.with_name("before-control"))
        return await super().before(*args, **kwargs)

    async def after(self, *args: Any, **kwargs: Any) -> None:
        await _wait_control(self._control_path.with_name("after-control"))
        await super().after(*args, **kwargs)


class _ControlledActivator:
    def __init__(self, client: AIGWLoRAClient, control_path: Path) -> None:
        self._client = client
        self._control_path = control_path

    async def activate(self, **kwargs: Any) -> None:
        if await _wait_control(self._control_path) == "fail":
            raise RuntimeError("fake activation failed")
        await self._client.activate(**kwargs)

    async def active_policy(self):
        return await self._client.active_policy()


class _FakePPO:
    def __init__(self, *, model_id: str, repository: Path, control_path: Path) -> None:
        self._model_id = model_id
        self._repository = repository
        self._control_path = control_path
        self._next_version = 1
        self._canceled: set[str] = set()

    async def train(self, **kwargs: Any) -> TrainingArtifact:
        training_run_id = str(kwargs["training_run_id"])
        mode = self._control_path.read_text(encoding="utf-8").strip()
        if mode == "fail":
            raise RuntimeError("fake PPO failed")
        while mode == "block":
            await asyncio.sleep(0.05)
            mode = self._control_path.read_text(encoding="utf-8").strip()
        await asyncio.sleep(0.05)
        if training_run_id in self._canceled:
            raise RuntimeError("fake PPO canceled")
        version = self._next_version
        self._next_version += 1
        lora_name = f"{self._model_id}:v{version}"
        lora_path = self._repository / self._model_id / f"v{version}"
        lora_path.mkdir(parents=True, exist_ok=True)
        (lora_path / "adapter_config.json").write_text("{}\n", encoding="utf-8")
        return TrainingArtifact(lora_name=lora_name, lora_path=str(lora_path))

    async def cancel(self, training_run_id: str) -> bool:
        self._canceled.add(training_run_id)
        return False


def _build_app(config: dict[str, Any]):
    redis = redis_from_url(config["redis_url"], decode_responses=False)
    store = RedisTrajectoryStore(redis)
    registry = TaskRegistry(redis=redis)
    judge = _FakeJudge(Path(config["judge_control_path"]))
    pipeline = _ControlledCapturePipeline(
        control_path=Path(config["before_control_path"]),
        registry=registry,
        trajectory_store=store,
        judge=judge,
    )
    pending_judge_store = PendingJudgeStore(redis=redis, ttl_sec=3600)
    trajectory_api = GatewayTrajectoryRuntime(
        SimpleNamespace(
            record_dir=config["record_dir"],
            dump_token_ids=False,
            single_user_default=False,
            fixed_user_id=config["model_id"],
            fixed_model_id=config["model_id"],
        ),
        trajectory_store=store,
        pending_judge_store=pending_judge_store,
    )
    trajectory_api.set_judge_scorer(judge)
    http_client = httpx.AsyncClient(timeout=5)
    lora_client = AIGWLoRAClient(
        endpoint=config["aigw_endpoint"],
        model_id=config["model_id"],
        timeout=float(config["lora_activation_timeout"]),
        http_client=http_client,
    )
    activator = _ControlledActivator(lora_client, Path(config["activation_control_path"]))
    runner = TrainingRunner(
        redis=redis,
        trajectory_store=store,
        ppo=_FakePPO(
            model_id=config["model_id"],
            repository=Path(config["lora_repository_path"]),
            control_path=Path(config["ppo_control_path"]),
        ),
        activator=activator,
        model_id=config["model_id"],
        base_model_path=config["base_model_path"],
        min_samples_for_training=2,
        max_samples_per_run=2,
        active_policy=activator.active_policy,
    )

    async def close_resources() -> None:
        await http_client.aclose()
        await redis.aclose()

    return build_rl_service_app(
        model_id=config["model_id"],
        redis=redis,
        trajectory_store=store,
        task_registry=registry,
        capture_pipeline=pipeline,
        training_runner=runner,
        trajectory_api=trajectory_api,
        close_resources=close_resources,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    Path(config["process_pid_path"]).write_text(str(os.getpid()), encoding="utf-8")
    uvicorn.run(
        _build_app(config),
        host="127.0.0.1",
        port=int(config["listen_port"]),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
