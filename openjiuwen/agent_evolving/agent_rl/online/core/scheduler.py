# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OnlineTrainingScheduler — polls stored samples and triggers LoRA training."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

from ..abstract.lora import LoRARepositoryProtocol
from ..inference.notifier import InferenceNotifier
from ..scheduler.plugins import RolloutRequest, call_rollouter
from .factory import build_training_executor
from .store_factory import OnlineStoreBundle, build_scheduler_store_bundle

logger = logging.getLogger("online_rl.scheduler")


class OnlineTrainingScheduler:
    """Poll Redis queues and run either PPO or SFT LoRA training."""

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
        lora_repo: Optional[LoRARepositoryProtocol] = None,
        notifier: Optional[InferenceNotifier] = None,
        nproc_per_node: int = 1,
        training_gpu_ids: str = "",
        tmp_root: str = "/tmp/agent_rl_online",
        ppo_config_path: Optional[str] = None,
        drain_pending_on_train: bool = False,
        max_samples_per_run: int = 0,
        ppo_samples_per_step: int = 0,
        allow_partial_last_step: bool = True,
        rollouter: Any | None = None,
        evaler: Any | None = None,
        train_backend: str = "PPO",
        sft_rollouter: str = "multi_turn_supervisor",
        supervisor_url: str = "",
        supervisor_token: str = "",
        supervisor_model: str = "",
        target_model_id: str = "",
        sft_dry_run: bool = False,
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
        self.rollouter = rollouter
        self.evaler = evaler
        self.train_backend = self._normalize_train_backend(train_backend)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._training_count = 0
        self._store_bundle: Optional[OnlineStoreBundle] = None
        self._trajectory_store: Optional[Any] = None
        self._sft_store: Optional[Any] = None
        self._training_task_store: Optional[Any] = None
        self._active_training_task: Optional[asyncio.Task[None]] = None
        self._active_training_user: Optional[str] = None
        self._active_training_task_id: Optional[str] = None
        self._active_stop_requested = False

        self._trainer = build_training_executor(
            train_backend=self.train_backend,
            base_model_path=self.base_model_path,
            lora_repo=self.lora_repo,
            notifier=self.notifier,
            training_gpu_ids=self.training_gpu_ids,
            nproc_per_node=self.nproc_per_node,
            ppo_config_path=self.ppo_config_path,
            ppo_samples_per_step=self.ppo_samples_per_step,
            evaler=self.evaler,
            sft_rollouter=sft_rollouter,
            supervisor_url=supervisor_url,
            supervisor_token=supervisor_token,
            supervisor_model=supervisor_model,
            target_model_id=target_model_id,
            sft_dry_run=sft_dry_run,
        )

    @staticmethod
    def _normalize_train_backend(train_backend: str) -> str:
        normalized = (train_backend or "PPO").strip().upper()
        if normalized not in {"PPO", "SFT"}:
            raise ValueError(f"unsupported train_backend: {train_backend}")
        return normalized

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("OnlineTrainingScheduler already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="OnlineTrainScheduler")
        self._thread.start()
        logger.info(
            "OnlineTrainingScheduler started: backend=%s store=%s redis=%s local_dir=%s min_samples=%d poll=%.0fs "
            "drain_pending=%s max_samples_per_run=%d ppo_samples_per_step=%d "
            "allow_partial_last_step=%s rollouter=%s evaler=%s",
            self.train_backend,
            self.trajectory_store_backend,
            self.redis_url,
            self.local_trajectory_store_dir,
            self.min_samples_for_training,
            self.poll_interval,
            self.drain_pending_on_train,
            self.max_samples_per_run,
            self.ppo_samples_per_step,
            self.allow_partial_last_step,
            type(self.rollouter).__name__ if self.rollouter is not None else "",
            type(self.evaler).__name__ if self.evaler is not None else "",
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
            self._training_task_store = self._store_bundle.training_task_store
            if self.train_backend == "SFT":
                self._sft_store = self._store_bundle.sft_store
            else:
                self._trajectory_store = self._store_bundle.trajectory_store
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
            self._sft_store = None
            self._training_task_store = None
            asyncio.set_event_loop(None)
            loop.close()

    async def _poll_main(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._maybe_request_active_stop()
                await self._reap_training_task()
                await self._poll_once()
            except Exception:
                logger.exception("Error in online training scheduler poll")
            await asyncio.sleep(self.poll_interval)

        await self._maybe_request_active_stop()
        await self._reap_training_task(wait=True)

    async def _poll_once(self) -> None:
        if self._active_training_task is not None:
            return
        if self.train_backend == "SFT":
            await self._poll_sft_once()
            return
        await self._poll_ppo_once()

    async def _poll_ppo_once(self) -> None:
        if self._trajectory_store is None:
            return
        if await self._poll_ppo_training_task_once():
            return
        if self.drain_pending_on_train:
            return

        user_ids = await self._trajectory_store.get_users_above_threshold(self.min_samples_for_training)
        if not user_ids:
            logger.debug("No users above threshold=%d", self.min_samples_for_training)
            return

        for user_id in user_ids:
            fetch_limit = await self._resolve_ppo_fetch_limit(user_id)
            if fetch_limit <= 0:
                logger.debug("No trainable full chunk for user=%s", user_id)
                continue
            samples = await self._trajectory_store.fetch_and_mark_training(user_id, fetch_limit)
            if samples:
                self._start_ppo_training(user_id=user_id, samples=samples)
                return

    async def _poll_ppo_training_task_once(self) -> bool:
        if self._training_task_store is None or self._trajectory_store is None:
            return False
        task = await self._training_task_store.get_active_task()
        if task is None or task.get("status") != "pending":
            return False

        user_id = str(task.get("user_id") or "").strip()
        if user_id:
            fetch_limit = await self._resolve_ppo_fetch_limit(
                user_id,
                require_min_samples=False,
                max_samples_per_run=self._task_positive_int(task, "max_samples_per_run", self.max_samples_per_run),
                ppo_samples_per_step=self._task_positive_int(task, "ppo_samples_per_step", self.ppo_samples_per_step),
                allow_partial_last_step=self._task_bool(task, "allow_partial_last_step", self.allow_partial_last_step),
            )
            if fetch_limit <= 0:
                return False
            samples = await self._trajectory_store.fetch_and_mark_training(user_id, fetch_limit)
        else:
            batches: list[dict[str, Any]] = []
            for candidate in await self._trajectory_store.get_users_above_threshold(1):
                fetch_limit = await self._resolve_ppo_fetch_limit(
                    candidate,
                    require_min_samples=False,
                    max_samples_per_run=self._task_positive_int(task, "max_samples_per_run", self.max_samples_per_run),
                    ppo_samples_per_step=self._task_positive_int(
                        task,
                        "ppo_samples_per_step",
                        self.ppo_samples_per_step,
                    ),
                    allow_partial_last_step=self._task_bool(
                        task,
                        "allow_partial_last_step",
                        self.allow_partial_last_step,
                    ),
                )
                if fetch_limit <= 0:
                    continue
                candidate_samples = await self._trajectory_store.fetch_and_mark_training(candidate, fetch_limit)
                if candidate_samples:
                    batches.append({
                        "user_id": candidate,
                        "samples": candidate_samples,
                        "sample_ids": self._ids(candidate_samples, "sample_id"),
                    })
            if not batches:
                return False
            all_sample_ids = [sample_id for batch in batches for sample_id in batch["sample_ids"]]
            sample_count = sum(len(batch["samples"]) for batch in batches)
            claimed = await self._training_task_store.claim_pending_task(user_id=None, sample_count=sample_count)
            if claimed is None:
                await self._trajectory_store.reset_to_pending(all_sample_ids)
                return False
            self._start_ppo_training_for_users(batches=batches, task_id=str(claimed.get("task_id") or ""))
            return True
        if not samples:
            return False

        sample_ids = [str(sample.get("sample_id")) for sample in samples if sample.get("sample_id")]
        claimed = await self._training_task_store.claim_pending_task(user_id=user_id, sample_count=len(samples))
        if claimed is None:
            await self._trajectory_store.reset_to_pending(sample_ids)
            return False
        self._start_ppo_training(user_id=user_id, samples=samples, task_id=str(claimed.get("task_id") or ""))
        return True

    def _start_ppo_training_for_users(self, *, batches: list[dict[str, Any]], task_id: str = "") -> None:
        self._training_count += 1
        self._active_training_user = ",".join(str(batch["user_id"]) for batch in batches)
        self._active_training_task_id = task_id
        self._active_stop_requested = False
        logger.info(
            "Triggering PPO training sequence #%d task=%s users=%d samples=%d",
            self._training_count,
            task_id,
            len(batches),
            sum(len(batch["samples"]) for batch in batches),
        )
        self._active_training_task = asyncio.create_task(
            self._train_ppo_users(batches=batches, task_id=task_id),
        )

    def _start_ppo_training(self, *, user_id: str, samples: list[dict[str, Any]], task_id: str = "") -> None:
        sample_ids = [str(sample.get("sample_id")) for sample in samples if sample.get("sample_id")]
        self._training_count += 1
        self._active_training_user = user_id
        self._active_training_task_id = task_id
        self._active_stop_requested = False
        logger.info(
            "Triggering PPO training #%d task=%s user=%s samples=%d",
            self._training_count,
            task_id,
            user_id,
            len(samples),
        )
        self._active_training_task = asyncio.create_task(
            self._train_ppo_batch(task_id=task_id, user_id=user_id, samples=samples, sample_ids=sample_ids),
        )

    async def _resolve_ppo_fetch_limit(
        self,
        user_id: str,
        *,
        require_min_samples: bool = True,
        max_samples_per_run: int | None = None,
        ppo_samples_per_step: int | None = None,
        allow_partial_last_step: bool | None = None,
    ) -> int:
        if self._trajectory_store is None:
            return 0
        if not self.drain_pending_on_train:
            return self.min_samples_for_training

        pending_count = await self._trajectory_store.get_pending_count(user_id)
        limit = pending_count
        max_samples = self.max_samples_per_run if max_samples_per_run is None else max(0, int(max_samples_per_run))
        if max_samples > 0:
            limit = min(limit, max_samples)

        step_size = self.ppo_samples_per_step if ppo_samples_per_step is None else max(0, int(ppo_samples_per_step))
        allow_partial = (
            self.allow_partial_last_step
            if allow_partial_last_step is None
            else bool(allow_partial_last_step)
        )
        if step_size > 0 and not allow_partial:
            limit = (limit // step_size) * step_size
        if require_min_samples and limit < self.min_samples_for_training:
            return 0
        return max(0, limit)

    async def _reap_training_task(self, *, wait: bool = False) -> None:
        if self._active_training_task is None:
            return
        if not wait and not self._active_training_task.done():
            return
        user_id = self._active_training_user
        task_id = self._active_training_task_id
        try:
            await self._active_training_task
        except Exception:
            logger.exception("Background %s training task failed for user=%s", self.train_backend, user_id)
        finally:
            self._active_training_task = None
            self._active_training_user = None
            self._active_training_task_id = None
            self._active_stop_requested = False
            if task_id and self._training_task_store is not None:
                task = await self._training_task_store.get_task(task_id)
                if task and task.get("status") == "stopping":
                    await self._training_task_store.update_task_status(task_id, status="canceled")

    async def _maybe_request_active_stop(self) -> None:
        if self._active_training_task is None or not self._active_training_task_id:
            return
        if self._training_task_store is None:
            return
        task = await self._training_task_store.get_task(self._active_training_task_id)
        if not task or task.get("status") != "stopping":
            return
        request_stop = getattr(self._trainer, "request_stop", None)
        if not callable(request_stop):
            if not self._active_stop_requested:
                logger.warning(
                    "Training task=%s is stopping but trainer has no request_stop hook",
                    self._active_training_task_id,
                )
            self._active_stop_requested = True
            return
        result = request_stop()
        action = str(result.get("action") if isinstance(result, dict) else "")
        if not self._active_stop_requested or action not in {"waiting", "none"}:
            logger.info("Requested training stop task=%s result=%s", self._active_training_task_id, result)
        self._active_stop_requested = True

    async def _train_ppo_batch(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        sample_ids: list[str],
        task_id: str = "",
    ) -> bool:
        if self._trajectory_store is None:
            return False

        try:
            samples = await self._maybe_rollout(user_id=user_id, samples=samples)
            await self._trainer.train_batch(
                user_id=user_id,
                samples=samples,
                training_count=self._training_count,
                tmp_root=self.tmp_root,
            )
        except Exception:
            logger.exception("PPO training #%d failed for user=%s", self._training_count, user_id)
            await self._trajectory_store.mark_failed(sample_ids)
            await self._mark_task_finished(task_id, "failed", error="ppo training failed")
            return False
        else:
            await self._trajectory_store.mark_trained(sample_ids)
            await self._mark_task_finished(task_id, "succeeded")
            return True

    async def _train_batch(self, *, user_id: str, samples: list[dict[str, Any]], sample_ids: list[str]) -> None:
        """Compatibility helper used by unit tests for the PPO path."""
        await self._train_ppo_batch(user_id=user_id, samples=samples, sample_ids=sample_ids)

    async def _train_ppo_users(self, *, batches: list[dict[str, Any]], task_id: str = "") -> None:
        if self._trajectory_store is None:
            return
        completed = 0
        try:
            for index, batch in enumerate(batches):
                if await self._is_task_stopping(task_id):
                    await self._trajectory_store.reset_to_pending([
                        sample_id
                        for pending_batch in batches[index:]
                        for sample_id in pending_batch["sample_ids"]
                    ])
                    break
                ok = await self._train_ppo_batch(
                    user_id=str(batch["user_id"]),
                    samples=batch["samples"],
                    sample_ids=batch["sample_ids"],
                    task_id="",
                )
                if not ok:
                    await self._trajectory_store.reset_to_pending([
                        sample_id
                        for pending_batch in batches[index + 1:]
                        for sample_id in pending_batch["sample_ids"]
                    ])
                    break
                completed += 1
            if completed > 0:
                await self._mark_task_finished(task_id, "succeeded")
            else:
                await self._mark_task_finished(task_id, "failed", error="no trainable PPO samples found")
        except Exception:
            logger.exception("PPO training sequence failed")
            await self._mark_task_finished(task_id, "failed", error="ppo training sequence failed")

    async def _maybe_rollout(self, *, user_id: str, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.rollouter is None:
            return samples

        prompts = [self._extract_prompt(sample) for sample in samples]
        request = RolloutRequest(
            user_id=user_id,
            samples=samples,
            prompts=prompts,
            training_count=self._training_count,
            metadata={
                "min_samples_for_training": self.min_samples_for_training,
                "sample_count": len(samples),
            },
        )
        try:
            result = await call_rollouter(self.rollouter, request)
        except Exception:
            logger.exception("Rollouter failed for user=%s; continuing with original samples", user_id)
            return samples

        logger.info(
            "Rollouter completed for user=%s success=%s returned_trajectories=%d reason=%s metrics=%s",
            user_id,
            result.success,
            len(result.trajectories),
            result.reason,
            result.metrics,
        )
        return samples

    async def _poll_sft_once(self) -> None:
        """Handle API-triggered SFT training, then optional automatic SFT rollout."""
        if self._sft_store is None:
            return
        if self.drain_pending_on_train and await self._poll_sft_training_task_once():
            return
        if not self.drain_pending_on_train:
            await self._poll_sft_auto_once()

    async def _poll_sft_training_task_once(self) -> bool:
        if self._training_task_store is None or self._sft_store is None:
            return False
        task = await self._training_task_store.get_active_task()
        if task is None or task.get("status") != "pending":
            return False

        user_id = str(task.get("user_id") or "").strip()
        if not user_id:
            user_ids = await self._collect_sft_training_users()
            if not user_ids:
                return False
            claimed = await self._training_task_store.claim_pending_task(user_id=None, sample_count=len(user_ids))
            if claimed is None:
                return False
            self._start_sft_training_for_users(user_ids=user_ids, task_id=str(claimed.get("task_id") or ""), task=task)
            return True

        return await self._trigger_sft_training_for_user(
            task_id=str(task.get("task_id") or ""),
            user_id=user_id,
            task=task,
        )

    async def _poll_sft_auto_once(self) -> None:
        if self._sft_store is None:
            return
        sample_users = await self._sft_store.get_sample_users_above_threshold(self.min_samples_for_training)
        if sample_users:
            samples = await self._fetch_sft_samples_for_training(sample_users[0])
            if samples:
                self._start_sft_training(user_id=sample_users[0], samples=samples)
                return

        raw_users = await self._sft_store.get_raw_users_above_threshold(1)
        if raw_users:
            raw = await self._fetch_sft_raw_for_rollout(raw_users[0])
            if raw:
                self._start_sft_rollout(
                    user_id=raw_users[0],
                    raw_trajectories=raw,
                    raw_ids=self._ids(raw, "raw_id", "sample_id"),
                    train_after_rollout=False,
                )

    async def _fetch_sft_samples_for_training(
        self,
        user_id: str,
        *,
        max_samples_per_run: int | None = None,
    ) -> list[dict[str, Any]]:
        if self._sft_store is None:
            return []
        fetch_limit = await self._resolve_sft_sample_fetch_limit(user_id, max_samples_per_run=max_samples_per_run)
        if fetch_limit <= 0:
            return []
        return await self._sft_store.fetch_samples_and_mark_training(user_id, fetch_limit)

    async def _fetch_sft_raw_for_rollout(
        self,
        user_id: str,
        *,
        max_samples_per_run: int | None = None,
    ) -> list[dict[str, Any]]:
        if self._sft_store is None:
            return []
        max_samples = self.max_samples_per_run if max_samples_per_run is None else max(0, int(max_samples_per_run))
        limit = max_samples or self.min_samples_for_training
        return await self._sft_store.fetch_raw_and_mark_processing(user_id, limit)

    async def _collect_sft_training_users(self) -> list[str]:
        """Return every user with pending SFT samples or raw rollout inputs."""
        if self._sft_store is None:
            return []
        sample_users = await self._sft_store.get_sample_users_above_threshold(1)
        raw_users = await self._sft_store.get_raw_users_above_threshold(1)
        user_ids: list[str] = []
        seen: set[str] = set()
        for user_id in [*sample_users, *raw_users]:
            normalized = str(user_id or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            user_ids.append(normalized)
        return user_ids

    async def _trigger_sft_training_for_user(
        self,
        *,
        task_id: str,
        user_id: str,
        task: dict[str, Any] | None = None,
    ) -> bool:
        if self._sft_store is None or self._training_task_store is None:
            return False
        max_samples_per_run = self._task_positive_int(task, "max_samples_per_run", self.max_samples_per_run)
        samples = await self._fetch_sft_samples_for_training(user_id, max_samples_per_run=max_samples_per_run)
        if samples:
            sample_ids = self._ids(samples, "sample_id")
            claimed = await self._training_task_store.claim_pending_task(user_id=user_id, sample_count=len(samples))
            if claimed is None:
                await self._sft_store.mark_samples_failed(sample_ids)
                return False
            self._start_sft_training(user_id=user_id, samples=samples, task_id=str(claimed.get("task_id") or task_id))
            return True

        raw = await self._fetch_sft_raw_for_rollout(user_id, max_samples_per_run=max_samples_per_run)
        if not raw:
            return False
        raw_ids = self._ids(raw, "raw_id", "sample_id")
        claimed = await self._training_task_store.claim_pending_task(user_id=user_id, sample_count=len(raw))
        if claimed is None:
            await self._sft_store.mark_raw_failed(raw_ids)
            return False
        self._start_sft_rollout(
            user_id=user_id,
            raw_trajectories=raw,
            raw_ids=raw_ids,
            task_id=str(claimed.get("task_id") or task_id),
            train_after_rollout=True,
        )
        return True

    def _start_sft_rollout(
        self,
        *,
        user_id: str,
        raw_trajectories: list[dict[str, Any]],
        raw_ids: list[str],
        task_id: str = "",
        train_after_rollout: bool = False,
    ) -> None:
        self._active_training_user = user_id
        self._active_training_task_id = task_id
        self._active_stop_requested = False
        logger.info(
            "Triggering SFT rollout task=%s user=%s raw_trajectories=%d train_after_rollout=%s",
            task_id,
            user_id,
            len(raw_trajectories),
            train_after_rollout,
        )
        self._active_training_task = asyncio.create_task(
            self._rollout_sft_raw(
                user_id=user_id,
                raw_trajectories=raw_trajectories,
                raw_ids=raw_ids,
                task_id=task_id,
                train_after_rollout=train_after_rollout,
            ),
        )

    def _start_sft_training(self, *, user_id: str, samples: list[dict[str, Any]], task_id: str = "") -> None:
        self._training_count += 1
        self._active_training_user = user_id
        self._active_training_task_id = task_id
        self._active_stop_requested = False
        logger.info(
            "Triggering SFT training #%d task=%s user=%s samples=%d",
            self._training_count,
            task_id,
            user_id,
            len(samples),
        )
        self._active_training_task = asyncio.create_task(
            self._train_sft_batch(
                user_id=user_id,
                samples=samples,
                sample_ids=self._ids(samples, "sample_id"),
                task_id=task_id,
            ),
        )

    def _start_sft_training_for_users(
        self,
        *,
        user_ids: list[str],
        task_id: str = "",
        task: dict[str, Any] | None = None,
    ) -> None:
        if not user_ids:
            return
        self._training_count += 1
        self._active_training_user = ",".join(user_ids)
        self._active_training_task_id = task_id
        self._active_stop_requested = False
        logger.info(
            "Triggering SFT training sequence #%d task=%s users=%s",
            self._training_count,
            task_id,
            ",".join(user_ids),
        )
        self._active_training_task = asyncio.create_task(
            self._train_sft_users(
                user_ids=user_ids,
                task_id=task_id,
                task=task,
            ),
        )

    async def _resolve_sft_sample_fetch_limit(
        self,
        user_id: str,
        *,
        max_samples_per_run: int | None = None,
    ) -> int:
        if self._sft_store is None:
            return 0
        pending_count = await self._sft_store.get_pending_sample_count(user_id)
        if pending_count <= 0:
            return 0
        max_samples = self.max_samples_per_run if max_samples_per_run is None else max(0, int(max_samples_per_run))
        if max_samples > 0:
            pending_count = min(pending_count, max_samples)
        if not self.drain_pending_on_train:
            return pending_count if pending_count >= self.min_samples_for_training else 0
        return pending_count

    async def _train_sft_users(
        self,
        *,
        user_ids: list[str],
        task_id: str = "",
        task: dict[str, Any] | None = None,
    ) -> None:
        if self._sft_store is None:
            return
        completed = 0
        try:
            for user_id in user_ids:
                if await self._is_task_stopping(task_id):
                    logger.info("Stop requested for SFT training sequence task=%s before user=%s", task_id, user_id)
                    break
                ok = await self._trigger_sft_user_workflow(
                    user_id=user_id,
                    task_id=task_id,
                    task=task,
                    finalize_task=False,
                )
                if ok:
                    completed += 1
                if await self._is_task_stopping(task_id):
                    logger.info("Stop requested for SFT training sequence task=%s after user=%s", task_id, user_id)
                    break
            if completed > 0:
                await self._mark_task_finished(task_id, "succeeded")
            else:
                await self._mark_task_finished(task_id, "failed", error="no trainable SFT samples found")
        except Exception:
            logger.exception("SFT training sequence failed for users=%s", user_ids)
            await self._mark_task_finished(task_id, "failed", error="sft training sequence failed")

    async def _rollout_sft_raw(
        self,
        *,
        user_id: str,
        raw_trajectories: list[dict[str, Any]],
        raw_ids: list[str],
        task_id: str = "",
        train_after_rollout: bool = False,
        finalize_task: bool = True,
    ) -> bool:
        if self._sft_store is None:
            return False
        try:
            samples = await self._trainer.build_samples_from_raw(
                user_id=user_id,
                raw_trajectories=raw_trajectories,
            )
            for sample in samples:
                await self._sft_store.save_sample(sample, user_id=user_id)
            if train_after_rollout and samples:
                fetched = await self._sft_store.fetch_samples_and_mark_training(user_id, len(samples))
                train_ok = await self._train_sft_batch(
                    user_id=user_id,
                    samples=fetched,
                    sample_ids=self._ids(fetched, "sample_id"),
                    task_id=task_id,
                    finalize_task=finalize_task,
                )
            else:
                train_ok = True
        except Exception:
            logger.exception("SFT rollout failed for user=%s raw_count=%d", user_id, len(raw_trajectories))
            await self._sft_store.mark_raw_failed(raw_ids)
            if finalize_task:
                await self._mark_task_finished(task_id, "failed", error="sft rollout failed")
            return False
        else:
            await self._sft_store.mark_raw_processed(raw_ids)
            if task_id and not train_after_rollout and finalize_task:
                await self._mark_task_finished(task_id, "succeeded")
            return train_ok

    async def _train_sft_batch(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        sample_ids: list[str],
        task_id: str = "",
        finalize_task: bool = True,
    ) -> bool:
        if self._sft_store is None:
            return False
        try:
            await self._trainer.train_batch(
                user_id=user_id,
                samples=samples,
                training_count=self._training_count,
                tmp_root=self.tmp_root,
            )
        except Exception:
            logger.exception("SFT training #%d failed for user=%s", self._training_count, user_id)
            await self._sft_store.mark_samples_failed(sample_ids)
            if finalize_task:
                await self._mark_task_finished(task_id, "failed", error="sft training failed")
            return False
        else:
            await self._sft_store.mark_samples_trained(sample_ids)
            if finalize_task:
                await self._mark_task_finished(task_id, "succeeded")
            return True

    async def _trigger_sft_user_workflow(
        self,
        *,
        user_id: str,
        task_id: str = "",
        task: dict[str, Any] | None = None,
        finalize_task: bool = True,
    ) -> bool:
        if self._sft_store is None:
            return False
        max_samples_per_run = self._task_positive_int(task, "max_samples_per_run", self.max_samples_per_run)
        samples = await self._fetch_sft_samples_for_training(user_id, max_samples_per_run=max_samples_per_run)
        if samples:
            return await self._train_sft_batch(
                user_id=user_id,
                samples=samples,
                sample_ids=self._ids(samples, "sample_id"),
                task_id=task_id,
                finalize_task=finalize_task,
            )

        raw = await self._fetch_sft_raw_for_rollout(user_id, max_samples_per_run=max_samples_per_run)
        if not raw:
            return False
        return await self._rollout_sft_raw(
            user_id=user_id,
            raw_trajectories=raw,
            raw_ids=self._ids(raw, "raw_id", "sample_id"),
            task_id=task_id,
            train_after_rollout=True,
            finalize_task=finalize_task,
        )

    async def _mark_task_finished(self, task_id: str, status: str, *, error: str = "") -> None:
        if not task_id or self._training_task_store is None:
            return
        task = await self._training_task_store.get_task(task_id)
        final_status = "canceled" if task and task.get("status") == "stopping" else status
        await self._training_task_store.update_task_status(task_id, status=final_status, error=error)

    async def _is_task_stopping(self, task_id: str) -> bool:
        if not task_id or self._training_task_store is None:
            return False
        task = await self._training_task_store.get_task(task_id)
        return bool(task and task.get("status") == "stopping")

    @staticmethod
    def _task_positive_int(task: dict[str, Any] | None, key: str, default: int) -> int:
        if not task:
            return max(0, int(default))
        try:
            value = int(task.get(key) or 0)
        except (TypeError, ValueError):
            return max(0, int(default))
        return value if value > 0 else max(0, int(default))

    @staticmethod
    def _task_bool(task: dict[str, Any] | None, key: str, default: bool) -> bool:
        if not task or key not in task:
            return bool(default)
        value = task.get(key)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _ids(items: list[dict[str, Any]], *fields: str) -> list[str]:
        out: list[str] = []
        for item in items:
            for field in fields:
                value = item.get(field)
                if value:
                    out.append(str(value))
                    break
        return out

    @staticmethod
    def _extract_prompt(sample: dict[str, Any]) -> Any:
        request = sample.get("request") or {}
        if isinstance(request, dict):
            messages = request.get("messages")
            if messages:
                return messages
            prompt = request.get("prompt")
            if prompt:
                return prompt
        trajectory = sample.get("trajectory") or {}
        if isinstance(trajectory, dict):
            prompt = trajectory.get("prompt")
            if prompt:
                return prompt
            prompt_ids = trajectory.get("prompt_ids") or trajectory.get("input_ids")
            if prompt_ids:
                return prompt_ids
        return sample.get("prompt") or ""
