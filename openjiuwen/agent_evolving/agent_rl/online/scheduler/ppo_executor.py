# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Training Run adapter for shared PPO batch execution."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error

from ...storage.lora_repo import LoRARepository
from ..abstract.lora import LoRAVersionLike
from ..backends.rl.ppo_engine import PPOBatchEngine
from ..training_runner import TrainingArtifact


class PPOTrainingExecutor(PPOBatchEngine):
    """Adapt PPO batch execution to the durable Training Run lifecycle."""

    def __init__(
        self,
        *,
        base_model_path: str,
        lora_repo: Optional[LoRARepository],
        nproc_per_node: int,
        training_gpu_ids: str,
        ppo_config_path: Optional[str],
        ppo_samples_per_step: int = 0,
    ) -> None:
        super().__init__(
            base_model_path=base_model_path,
            lora_repo=lora_repo,
            nproc_per_node=nproc_per_node,
            training_gpu_ids=training_gpu_ids,
            ppo_config_path=ppo_config_path,
            ppo_samples_per_step=ppo_samples_per_step,
        )
        self._active_training_run_id: str | None = None
        self._active_training_task: asyncio.Task[Optional[LoRAVersionLike]] | None = None
        self._cancel_requested = False
        self._health_error: Exception | None = None

    async def aclose(self) -> None:
        """Close the Ray-backed runner from async lifecycle code."""

        self.close()

    async def cancel(self, training_run_id: str) -> bool:
        """Stop the active Ray actor and wait for its worker thread to exit."""

        if training_run_id != self._active_training_run_id:
            return False
        self._cancel_requested = True
        training_task = self._active_training_task
        if training_task is not None and not training_task.done():
            await asyncio.to_thread(self.close)
            try:
                await training_task
            except (asyncio.CancelledError, Exception):
                pass
        return True

    def check_health(self) -> None:
        """Raise when the Ray scheduler or actor cannot continue."""

        if self._health_error is not None:
            raise build_error(
                StatusCode.AGENT_RL_PPO_SCHEDULER_RUNTIME_ERROR,
                cause=self._health_error,
                error_msg="online PPO scheduler failed",
            ) from self._health_error

    async def train_batch(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        training_count: int,
        tmp_root: str,
        init_lora_name: str | None = None,
        init_lora_path: str | None = None,
    ) -> Optional[str]:
        """Train and publish one immutable sample batch."""

        run_dir = Path(tmp_root) / f"run_{training_count}_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            if not samples:
                raise build_error(
                    StatusCode.AGENT_RL_TRAINING_SAMPLES_INVALID,
                    error_msg="PPO training requires at least one sample",
                )
            if init_lora_name is None:
                (
                    init_lora_adapter_path,
                    training_source,
                    parent_lora_id,
                    parent_lora_version,
                ) = self._resolve_init_lora_adapter_path(user_id)
            else:
                init_lora_adapter_path = str(init_lora_path or "")
                training_source = "base_model" if init_lora_name == "base" else "active_lora"
                parent_lora_id = "" if init_lora_name == "base" else init_lora_name
                parent_lora_version = "" if init_lora_name == "base" else init_lora_name.rsplit(":", 1)[-1]
            training_task = asyncio.create_task(
                asyncio.to_thread(
                    self._run_ppo_training_sync,
                    user_id=user_id,
                    samples=samples,
                    run_dir=run_dir,
                    init_lora_adapter_path=init_lora_adapter_path,
                    training_source=training_source,
                    parent_lora_id=parent_lora_id,
                    parent_lora_version=parent_lora_version,
                )
            )
            self._active_training_task = training_task
            version = await asyncio.shield(training_task)
            return version.path if version is not None else None
        except Exception as exc:
            if not self._cancel_requested and self._is_scheduler_failure(exc):
                self._health_error = exc
            await asyncio.to_thread(self.close)
            if isinstance(exc, BaseError):
                raise
            raise build_error(
                StatusCode.AGENT_RL_PPO_EXECUTION_ERROR,
                cause=exc,
                error_msg=str(exc),
            ) from exc
        finally:
            if self._active_training_task is not None and self._active_training_task.done():
                self._active_training_task = None
            shutil.rmtree(str(run_dir / "fsdp_ckpt"), ignore_errors=True)

    async def train(self, **kwargs: Any) -> TrainingArtifact:
        """Return the repository-assigned artifact for one Training Run."""

        if self.lora_repo is None:
            raise build_error(
                StatusCode.AGENT_RL_PPO_EXECUTION_ERROR,
                error_msg="online PPO requires a LoRA repository",
            )
        model_id = str(kwargs["model_id"])
        training_run_id = str(kwargs["training_run_id"])
        self._active_training_run_id = training_run_id
        try:
            versions_before = len(self.lora_repo.list_versions(model_id))
            path = await self.train_batch(
                user_id=model_id,
                samples=kwargs["samples"],
                training_count=versions_before + 1,
                tmp_root=str(kwargs.get("tmp_root") or "/tmp/agent_rl_online"),
                init_lora_name=str(kwargs.get("init_lora_name") or "base"),
                init_lora_path=str(kwargs.get("init_lora_path") or ""),
            )
            latest = self.lora_repo.get_latest(model_id)
            if latest is None or path is None:
                raise build_error(
                    StatusCode.AGENT_RL_PPO_EXECUTION_ERROR,
                    error_msg="PPO completed without a published LoRA artifact",
                )
            return TrainingArtifact(lora_name=f"{model_id}:{latest.version}", lora_path=path)
        finally:
            if self._active_training_run_id == training_run_id:
                self._active_training_run_id = None
                self._cancel_requested = False

    @staticmethod
    def _is_scheduler_failure(exc: Exception) -> bool:
        try:
            from ray.exceptions import RayActorError, RaySystemError
        except ImportError:
            return False
        return isinstance(exc, (RayActorError, RaySystemError))
