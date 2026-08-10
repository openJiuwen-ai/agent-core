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
from ...storage.store_factory import OnlineStoreBundle, build_scheduler_store_bundle

logger = logging.getLogger("online_rl.scheduler")


class OnlineTrainingScheduler:
    """Poll trajectory samples and trigger PPO LoRA training."""

    def __init__(
        self,
        *,
        redis_url: str = "redis://127.0.0.1:6379/0",
        trajectory_store_backend: str = "auto",
        local_trajectory_store_dir: str = "",
        record_dir: str = "records",
        poll_interval: float = 30.0,
        min_samples_for_training: int = 32,
        base_model_path: str = "",
        lora_repo: Optional[LoRARepository] = None,
        notifier: Optional[InferenceNotifier] = None,
        nproc_per_node: int = 1,
        training_gpu_ids: str = "",
        tmp_root: str = "/tmp/agent_rl_online",
        ppo_config_path: Optional[str] = None,
        drain_pending_on_train: bool = False,
        max_samples_per_run: int = 0,
        ppo_samples_per_step: int = 0,
        allow_partial_last_step: bool = True,
    ) -> None:
        self.redis_url = redis_url
        self.trajectory_store_backend = trajectory_store_backend
        self.local_trajectory_store_dir = local_trajectory_store_dir
        self.record_dir = record_dir
        self.poll_interval = poll_interval
        self.min_samples_for_training = min_samples_for_training
        self.base_model_path = base_model_path
        self.lora_repo = lora_repo
        self.notifier = notifier
        self.nproc_per_node = nproc_per_node
        self.training_gpu_ids = training_gpu_ids
        self.tmp_root = tmp_root
        self.ppo_config_path = ppo_config_path
        self.drain_pending_on_train = bool(drain_pending_on_train)
        self.max_samples_per_run = max(0, int(max_samples_per_run))
        self.ppo_samples_per_step = max(0, int(ppo_samples_per_step))
        self.allow_partial_last_step = bool(allow_partial_last_step)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._training_count = 0
        self._store_bundle: Optional[OnlineStoreBundle] = None
        self._trajectory_store: Optional[Any] = None
        self._training_task_store: Optional[Any] = None
        self._active_training_task: Optional[asyncio.Task[None]] = None
        self._active_training_user: Optional[str] = None
        self._trainer = PPOTrainingExecutor(
            base_model_path=self.base_model_path,
            lora_repo=self.lora_repo,
            notifier=self.notifier,
            nproc_per_node=self.nproc_per_node,
            training_gpu_ids=self.training_gpu_ids,
            ppo_config_path=self.ppo_config_path,
            ppo_samples_per_step=self.ppo_samples_per_step,
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("OnlineTrainingScheduler already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="OnlineTrainScheduler")
        self._thread.start()
        logger.info(
            "OnlineTrainingScheduler started: store=%s redis=%s local_dir=%s min_samples=%d poll=%.0fs "
            "drain_pending=%s max_samples_per_run=%d ppo_samples_per_step=%d "
            "allow_partial_last_step=%s",
            self.trajectory_store_backend,
            self.redis_url,
            self.local_trajectory_store_dir,
            self.min_samples_for_training,
            self.poll_interval,
            self.drain_pending_on_train,
            self.max_samples_per_run,
            self.ppo_samples_per_step,
            self.allow_partial_last_step,
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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._store_bundle = build_scheduler_store_bundle(
                backend=self.trajectory_store_backend,
                redis_url=self.redis_url,
                local_store_dir=self.local_trajectory_store_dir,
                record_dir=self.record_dir,
            )
            self._trajectory_store = self._store_bundle.trajectory_store
            self._training_task_store = self._store_bundle.training_task_store
            loop.run_until_complete(self._poll_main())
        finally:
            loop.run_until_complete(self._trainer.aclose())
            redis_client = self._store_bundle.redis_client if self._store_bundle is not None else None
            if redis_client is not None and self._store_bundle and self._store_bundle.owns_redis_client:
                try:
                    loop.run_until_complete(redis_client.aclose())
                except Exception as exc:
                    logger.debug("Failed to close Redis client: %s", exc)
            self._store_bundle = None
            self._trajectory_store = None
            self._training_task_store = None
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
        """Pull trainable samples from the configured trajectory store."""
        if self._trajectory_store is None:
            return
        if self._active_training_task is not None:
            return

        if self._training_task_store is not None:
            await self._poll_training_task_once()
            return

        user_ids = await self._trajectory_store.get_users_above_threshold(self.min_samples_for_training)
        if not user_ids:
            logger.debug("No users above threshold=%d", self.min_samples_for_training)
            return

        for user_id in user_ids:
            fetch_limit = await self._resolve_fetch_limit(user_id)
            if fetch_limit <= 0:
                logger.debug("No trainable full chunk for user=%s", user_id)
                continue
            samples = await self._trajectory_store.fetch_and_mark_training(
                user_id,
                fetch_limit,
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

    async def _poll_training_task_once(self) -> None:
        if self._training_task_store is None or self._trajectory_store is None:
            return
        task = await self._training_task_store.get_active_task()
        if task is None or task.get("status") != "pending":
            return

        requested_user_id = str(task.get("user_id") or "").strip() or None
        if requested_user_id:
            fetch_limit = await self._resolve_fetch_limit(requested_user_id)
            if fetch_limit <= 0:
                logger.debug(
                    "No trainable full chunk for task=%s user=%s",
                    task.get("task_id"),
                    requested_user_id,
                )
                return
            samples = await self._trajectory_store.fetch_and_mark_training(requested_user_id, fetch_limit)
            user_id = requested_user_id
        else:
            await self._poll_all_users_training_task_once(task)
            return

        if not samples:
            return

        sample_ids = [str(sample.get("sample_id")) for sample in samples if sample.get("sample_id")]
        claimed = await self._training_task_store.claim_pending_task(user_id=user_id, sample_count=len(samples))
        if claimed is None:
            await self._trajectory_store.reset_to_pending(sample_ids)
            return

        self._training_count += 1
        self._active_training_user = user_id
        self._active_training_task = asyncio.create_task(
            self._train_batch(
                task_id=str(claimed.get("task_id") or ""),
                user_id=user_id,
                samples=samples,
                sample_ids=sample_ids,
            ),
        )

    async def _poll_all_users_training_task_once(self, task: dict[str, Any]) -> None:
        """Claim one manual task and train every currently trainable user once.

        A task without ``user_id`` is treated as an explicit "train all ready
        users" request. Each user keeps its own trajectory batch and publishes
        its own LoRA version; the task status covers the whole sweep.
        """
        if self._training_task_store is None or self._trajectory_store is None:
            return

        user_ids = await self._trajectory_store.get_users_above_threshold(self.min_samples_for_training)
        if not user_ids:
            logger.debug(
                "No users above threshold=%d for pending training task",
                self.min_samples_for_training,
            )
            return

        batches: list[dict[str, Any]] = []
        for user_id in user_ids:
            fetch_limit = await self._resolve_fetch_limit(user_id)
            if fetch_limit <= 0:
                logger.debug("No trainable full chunk for task=%s user=%s", task.get("task_id"), user_id)
                continue
            samples = await self._trajectory_store.fetch_and_mark_training(user_id, fetch_limit)
            if not samples:
                continue
            batches.append({
                "user_id": user_id,
                "samples": samples,
                "sample_ids": [
                    str(sample.get("sample_id"))
                    for sample in samples
                    if sample.get("sample_id")
                ],
            })

        if not batches:
            return

        all_sample_ids = [
            sample_id
            for batch in batches
            for sample_id in batch["sample_ids"]
        ]
        sample_count = sum(len(batch["samples"]) for batch in batches)
        claimed = await self._training_task_store.claim_pending_task(user_id=None, sample_count=sample_count)
        if claimed is None:
            await self._trajectory_store.reset_to_pending(all_sample_ids)
            return

        logger.info(
            "Triggering all-user PPO training task=%s users=%d samples=%d",
            claimed.get("task_id"),
            len(batches),
            sample_count,
        )
        self._active_training_user = "*"
        self._active_training_task = asyncio.create_task(
            self._train_user_batches(
                task_id=str(claimed.get("task_id") or ""),
                batches=batches,
            ),
        )

    async def _resolve_fetch_limit(self, user_id: str) -> int:
        if self._trajectory_store is None:
            return 0
        if not self.drain_pending_on_train:
            return self.min_samples_for_training

        pending_count = await self._trajectory_store.get_pending_count(user_id)
        limit = pending_count
        if self.max_samples_per_run > 0:
            limit = min(limit, self.max_samples_per_run)

        step_size = self.ppo_samples_per_step
        if step_size > 0 and not self.allow_partial_last_step:
            limit = (limit // step_size) * step_size
        if limit < self.min_samples_for_training:
            return 0
        return max(0, limit)

    async def _reap_training_task(self, *, wait: bool = False) -> None:
        if self._active_training_task is None:
            return
        if not wait and not self._active_training_task.done():
            return
        user_id = self._active_training_user
        task_id = ""
        if self._training_task_store is not None:
            task = await self._training_task_store.get_active_task()
            if task is not None:
                task_id = str(task.get("task_id") or "")
        try:
            await self._active_training_task
        except Exception:
            logger.exception("Background PPO training task failed for user=%s", user_id)
        finally:
            self._active_training_task = None
            self._active_training_user = None
            if task_id and self._training_task_store is not None:
                task = await self._training_task_store.get_task(task_id)
                if task and task.get("status") == "stopping":
                    await self._training_task_store.update_task_status(task_id, status="canceled")

    async def _train_user_batches(self, *, task_id: str, batches: list[dict[str, Any]]) -> None:
        """Run one claimed no-user task as a sequence of per-user trainings."""
        if self._trajectory_store is None:
            return

        failed_users: list[str] = []
        for index, batch in enumerate(batches):
            task = await self._training_task_store.get_task(task_id) if self._training_task_store else None
            if task and task.get("status") == "stopping":
                await self._trajectory_store.reset_to_pending([
                    sample_id
                    for pending_batch in batches[index:]
                    for sample_id in pending_batch["sample_ids"]
                ])
                await self._training_task_store.update_task_status(task_id, status="canceled")
                return

            self._training_count += 1
            ok = await self._train_batch(
                user_id=str(batch["user_id"]),
                samples=batch["samples"],
                sample_ids=batch["sample_ids"],
                finalize_task_status=False,
            )
            if not ok:
                failed_users.append(str(batch["user_id"]))
                await self._trajectory_store.reset_to_pending([
                    sample_id
                    for pending_batch in batches[index + 1:]
                    for sample_id in pending_batch["sample_ids"]
                ])
                break

        if self._training_task_store is None:
            return
        task = await self._training_task_store.get_task(task_id)
        if task and task.get("status") == "stopping":
            await self._training_task_store.update_task_status(task_id, status="canceled")
        elif failed_users:
            await self._training_task_store.update_task_status(
                task_id,
                status="failed",
                error=f"ppo training failed for users: {','.join(failed_users)}",
            )
        else:
            await self._training_task_store.update_task_status(task_id, status="succeeded")

    async def _train_batch(
        self,
        *,
        task_id: str = "",
        user_id: str,
        samples: list[dict[str, Any]],
        sample_ids: list[str],
        finalize_task_status: bool = True,
    ) -> bool:
        if self._trajectory_store is None:
            return False

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
            if finalize_task_status and task_id and self._training_task_store is not None:
                await self._training_task_store.update_task_status(
                    task_id,
                    status="failed",
                    error="ppo training failed",
                )
            return False
        else:
            await self._trajectory_store.mark_trained(sample_ids)
            if finalize_task_status and task_id and self._training_task_store is not None:
                task = await self._training_task_store.get_task(task_id)
                final_status = "canceled" if task and task.get("status") == "stopping" else "succeeded"
                await self._training_task_store.update_task_status(task_id, status=final_status)
            return True
