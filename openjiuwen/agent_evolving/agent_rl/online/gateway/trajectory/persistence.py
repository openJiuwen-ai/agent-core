# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Trajectory persistence and rail-ingest wiring for gateway runtime."""

from __future__ import annotations

import os
from typing import Any, Optional

from ....storage.redis_trajectory_store import RedisTrajectoryStore
from .judge_dispatcher import JudgeDispatcher
from .pending_judge_store import PendingJudgeStore
from .rail_ingest import RailBatchIngestor
from .sample_recorder import SampleRecorder

_SINGLE_USER_DEFAULT_ID = "jiuwenclaw-web"


class GatewayTrajectoryRuntime:
    """Own scored-sample persistence and rail-v1 ingestion wiring."""

    def __init__(
        self,
        config: Any,
        *,
        redis: Optional[Any] = None,
    ) -> None:
        if redis is None:
            raise ValueError("GatewayTrajectoryRuntime requires redis client")
        os.makedirs(config.record_dir, exist_ok=True)
        self._default_user_id = _SINGLE_USER_DEFAULT_ID if getattr(config, "single_user_default", False) else ""
        self._trajectory_store = RedisTrajectoryStore(redis)
        self._sample_recorder = SampleRecorder(
            sample_file=os.path.join(config.record_dir, "samples.jsonl"),
            dump_token_ids=config.dump_token_ids,
        )
        self._pending_judge_store = PendingJudgeStore(redis=redis)
        self._rail_ingestor: RailBatchIngestor | None = None
        self.set_judge_scorer(None)

    @property
    def store_backend(self) -> str:
        return type(self._trajectory_store).__name__

    @property
    def rail_ingestor(self) -> RailBatchIngestor:
        if self._rail_ingestor is None:
            raise RuntimeError("rail_ingestor is not initialized")
        return self._rail_ingestor

    def set_judge_scorer(self, judge_scorer: Optional[Any]) -> None:
        judge_dispatcher = JudgeDispatcher(
            pending_store=self._pending_judge_store,
            record_sample=self.record_sample,
            judge_scorer=judge_scorer,
        )
        self._rail_ingestor = RailBatchIngestor(
            pending_judge_store=self._pending_judge_store,
            judge_dispatcher=judge_dispatcher,
            default_user_id=self._default_user_id,
        )

    async def record_sample(self, sample: dict[str, Any]) -> None:
        normalized = dict(sample)
        normalized_user_id = str(normalized.get("user_id") or self._default_user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("missing user_id; online training requires a stable user id")
        normalized["user_id"] = normalized_user_id
        await self._trajectory_store.save_sample(normalized, user_id=normalized_user_id)
        await self._sample_recorder.record_sample(normalized)

    async def snapshot_stats(self) -> dict[str, Any]:
        sample_stats = await self._sample_recorder.snapshot_stats()
        train_stats = await self._trajectory_store.stats()
        return {
            "total_samples": sample_stats["total_samples"],
            "trajectory_store_backend": self.store_backend,
            "trajectory_store_total": train_stats["total_samples"],
            "trajectory_store_pending": train_stats["pending_samples"],
            "trajectory_store_training": train_stats["training_samples"],
            "trajectory_store_trained": train_stats["trained_samples"],
            "trajectory_store_failed": train_stats["failed_samples"],
        }
