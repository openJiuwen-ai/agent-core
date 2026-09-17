# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Legacy scheduler adapter for shared PPO batch execution."""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from ...abstract.lora import LoRARepositoryProtocol, LoRAVersionLike
from ...core.training_process import release_accelerator_memory
from ...scheduler.plugins import EvalRequest, call_evaler
from .ppo_engine import PPOBatchEngine

logger = logging.getLogger("online_rl.scheduler")


class PPOTrainingExecutor(PPOBatchEngine):
    """Add evaluation and inference notification to PPO batch execution."""

    def __init__(
        self,
        *,
        base_model_path: str,
        lora_repo: Optional[LoRARepositoryProtocol],
        notifier: Any | None,
        nproc_per_node: int,
        training_gpu_ids: str,
        ppo_config_path: Optional[str],
        ppo_samples_per_step: int = 0,
        evaler: Any | None = None,
    ) -> None:
        super().__init__(
            base_model_path=base_model_path,
            lora_repo=lora_repo,
            nproc_per_node=nproc_per_node,
            training_gpu_ids=training_gpu_ids,
            ppo_config_path=ppo_config_path,
            ppo_samples_per_step=ppo_samples_per_step,
        )
        self.notifier = notifier
        self.evaler = evaler

    async def aclose(self) -> None:
        if self.notifier is not None:
            try:
                await self.notifier.close()
            except Exception as exc:
                logger.debug("Failed to close inference notifier: %s", exc)
        self.close()

    def close(self) -> None:
        super().close()
        release_accelerator_memory()

    def request_stop(self) -> dict[str, object]:
        """Request stop for the current PPO actor and release accelerator memory."""

        had_runner = self._ppo_runner is not None
        self.close()
        return {"active": had_runner, "action": "ray.kill" if had_runner else "none", "name": "ppo"}

    async def train_batch(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        training_count: int,
        tmp_root: str,
    ) -> Optional[str]:
        run_dir = Path(tmp_root) / f"run_{training_count}_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            (
                init_lora_adapter_path,
                training_source,
                parent_lora_id,
                parent_lora_version,
            ) = self._resolve_init_lora_adapter_path(user_id)
            published_lora = await asyncio.to_thread(
                self._run_ppo_training_sync,
                user_id=user_id,
                samples=samples,
                run_dir=run_dir,
                init_lora_adapter_path=init_lora_adapter_path,
                training_source=training_source,
                parent_lora_id=parent_lora_id,
                parent_lora_version=parent_lora_version,
            )
            if published_lora is not None:
                published_lora = await self._maybe_eval_lora(
                    user_id=user_id,
                    samples=samples,
                    training_count=training_count,
                    version=published_lora,
                )
            published_lora_path = published_lora.path if published_lora is not None else None
            if self.notifier and published_lora_path:
                try:
                    await self.notifier.notify_update(user_id, published_lora_path)
                except Exception:
                    logger.warning("Failed to notify vLLM for LoRA hot-load (non-fatal)")
            return published_lora_path
        finally:
            shutil.rmtree(str(run_dir / "fsdp_ckpt"), ignore_errors=True)

    async def _maybe_eval_lora(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        training_count: int,
        version: LoRAVersionLike,
    ) -> LoRAVersionLike:
        if self.evaler is None or self.lora_repo is None:
            return version

        request = EvalRequest(
            user_id=user_id,
            lora_id=f"{user_id}:{version.version}",
            lora_version=version.version,
            lora_path=version.path,
            base_model_path=self.base_model_path,
            samples=samples,
            training_count=training_count,
            metadata={
                "trajectory_count": version.trajectory_count,
                "reward_avg": version.reward_avg,
            },
        )
        try:
            result = await call_evaler(self.evaler, request)
        except Exception as exc:
            logger.exception("Evaler failed for LoRA user=%s version=%s", user_id, version.version)
            return self.lora_repo.set_availability(
                user_id,
                version.version,
                available=False,
                reason=f"evaler exception: {exc}",
            )

        reason = result.reason or (
            f"score={result.score} target={result.target_score}"
            if result.score is not None or result.target_score is not None
            else "eval passed"
        )
        evaluated = self.lora_repo.set_availability(
            user_id,
            version.version,
            available=result.passed,
            reason=reason,
        )
        logger.info(
            "Evaler completed for LoRA user=%s version=%s passed=%s score=%s target=%s reason=%s metrics=%s",
            user_id,
            version.version,
            result.passed,
            result.score,
            result.target_score,
            result.reason,
            result.metrics,
        )
        return evaluated
