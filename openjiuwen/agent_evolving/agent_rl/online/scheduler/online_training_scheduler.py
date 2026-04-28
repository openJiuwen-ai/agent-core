# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OnlineTrainingScheduler — polls trajectory samples and triggers training."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

from ..inference.notifier import InferenceNotifier
from .ppo_executor import PPOTrainingExecutor
from ...storage.lora_repo import LoRARepository
from ...storage.redis_trajectory_store import RedisTrajectoryStore

logger = logging.getLogger("online_rl.scheduler")


class OnlineTrainingScheduler:
    """Poll RedisTrajectoryStore and trigger PPO LoRA training."""

    def __init__(
        self,
        *,
        redis_url: str = "redis://127.0.0.1:6379/0",
        poll_interval: float = 30.0,
        min_samples_for_training: int = 32,
        base_model_path: str = "",
        lora_repo: Optional[LoRARepository] = None,
        notifier: Optional[InferenceNotifier] = None,
        nproc_per_node: int = 1,
        training_gpu_ids: str = "",
        tmp_root: str = "/tmp/agent_rl_online",
        ppo_config_path: Optional[str] = None,
    ) -> None:
        self.redis_url = redis_url
        self.poll_interval = poll_interval
        self.min_samples_for_training = min_samples_for_training
        self.base_model_path = base_model_path
        self.lora_repo = lora_repo
        self.notifier = notifier
        self.nproc_per_node = nproc_per_node
        self.training_gpu_ids = training_gpu_ids
        self.tmp_root = tmp_root
        self.ppo_config_path = ppo_config_path

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._training_count = 0
        self._redis = None
        self._trajectory_store: Optional[RedisTrajectoryStore] = None
        self._active_training_task: Optional[asyncio.Task[None]] = None
        self._active_training_user: Optional[str] = None
        self._trainer = PPOTrainingExecutor(
            base_model_path=self.base_model_path,
            lora_repo=self.lora_repo,
            notifier=self.notifier,
            nproc_per_node=self.nproc_per_node,
            training_gpu_ids=self.training_gpu_ids,
            ppo_config_path=self.ppo_config_path,
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("OnlineTrainingScheduler already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="OnlineTrainScheduler")
        self._thread.start()
        logger.info(
            "OnlineTrainingScheduler started: redis=%s min_samples=%d poll=%.0fs",
            self.redis_url, self.min_samples_for_training, self.poll_interval,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                logger.warning("OnlineTrainingScheduler stop timed out while training is still in progress")
        self._trainer.close()
        logger.info("OnlineTrainingScheduler stopped")

    def _poll_loop(self) -> None:
        if not self.redis_url:
            logger.warning("OnlineTrainingScheduler disabled: redis_url is empty")
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from redis.asyncio import from_url as redis_from_url

            self._redis = redis_from_url(self.redis_url, decode_responses=False)
            self._trajectory_store = RedisTrajectoryStore(self._redis)
            loop.run_until_complete(self._poll_main())
        finally:
            loop.run_until_complete(self._trainer.aclose())
            if self._redis is not None:
                try:
                    loop.run_until_complete(self._redis.aclose())
                except Exception as exc:
                    logger.debug("Failed to close Redis client: %s", exc)
            self._redis = None
            self._trajectory_store = None
            asyncio.set_event_loop(None)
            loop.close()

    async def _poll_main(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._reap_training_task()
                await self._poll_once()
            except Exception:
                logger.exception("Error in online training scheduler poll")
            await asyncio.sleep(self.poll_interval)

        await self._reap_training_task(wait=True)

    async def _poll_once(self) -> None:
        """Pull trainable samples from RedisTrajectoryStore."""
        if self._trajectory_store is None:
            return
        if self._active_training_task is not None:
            return

        user_ids = await self._trajectory_store.get_users_above_threshold(self.min_samples_for_training)
        if not user_ids:
            logger.debug("No users above threshold=%d", self.min_samples_for_training)
            return

        for user_id in user_ids:
            samples = await self._trajectory_store.fetch_and_mark_training(
                user_id,
                self.min_samples_for_training,
            )
            if not samples:
                continue
            sample_ids = [str(sample.get("sample_id")) for sample in samples if sample.get("sample_id")]
            self._training_count += 1
            logger.info(
                "Triggering PPO training #%d for user=%s samples=%d",
                self._training_count,
                user_id,
                len(samples),
            )
            self._active_training_user = user_id
            self._active_training_task = asyncio.create_task(
                self._train_batch(user_id=user_id, samples=samples, sample_ids=sample_ids),
            )
            return

    async def _reap_training_task(self, *, wait: bool = False) -> None:
        if self._active_training_task is None:
            return
        if not wait and not self._active_training_task.done():
            return
        user_id = self._active_training_user
        try:
            await self._active_training_task
        except Exception:
            logger.exception("Background PPO training task failed for user=%s", user_id)
        finally:
            self._active_training_task = None
            self._active_training_user = None

    async def _train_batch(self, *, user_id: str, samples: list[dict[str, Any]], sample_ids: list[str]) -> None:
        if self._trajectory_store is None:
            return

        try:
            await self._trainer.train_batch(
                user_id=user_id,
                samples=samples,
                training_count=self._training_count,
                tmp_root=self.tmp_root,
            )
        except Exception:
            logger.exception("PPO training #%d failed for user=%s", self._training_count, user_id)
            await self._trajectory_store.mark_failed(sample_ids)
        else:
            await self._trajectory_store.mark_trained(sample_ids)
