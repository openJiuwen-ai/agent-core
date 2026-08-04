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
from .sample_payloads import build_sample, coerce_logprobs
from .sample_recorder import SampleRecorder

_SINGLE_USER_DEFAULT_ID = "jiuwenclaw-web"
_UNINDEXED_FILTER_SCAN_LIMIT = 1000000


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

    async def batch_create_trajectories(self, payload: dict[str, Any]) -> dict[str, Any]:
        protocol_version = str(payload.get("protocol_version") or "")
        if protocol_version == "rail-v1":
            result = await self.rail_ingestor.ingest_rail_batch(payload)
            return {
                "accepted": result.get("accepted", 0),
                "rejected": result.get("rejected", 0),
                "duplicate": 0,
                "items": [],
                "legacy_result": result,
            }
        if protocol_version not in {"agent-rollout-v1", "online-rl-sample-v1", ""}:
            raise ValueError(f"unsupported protocol_version: {protocol_version}")

        trajectories = payload.get("trajectories")
        if trajectories is None and isinstance(payload.get("samples"), list):
            trajectories = payload.get("samples")
        if not isinstance(trajectories, list):
            raise ValueError("trajectories must be a list")

        accepted = 0
        rejected = 0
        items: list[dict[str, Any]] = []
        for idx, trajectory in enumerate(trajectories):
            if not isinstance(trajectory, dict):
                rejected += 1
                items.append({"index": idx, "status": "rejected", "error": "trajectory must be an object"})
                continue
            try:
                samples = self._trajectory_to_samples(payload, trajectory, idx)
                for sample in samples:
                    await self.record_sample(sample)
                    accepted += 1
                    items.append({"trajectory_id": sample["sample_id"], "status": "pending"})
            except Exception as exc:
                rejected += 1
                items.append({"index": idx, "status": "rejected", "error": str(exc)})
        return {
            "accepted": accepted,
            "rejected": rejected,
            "duplicate": 0,
            "items": items,
        }

    async def list_trajectories(
        self,
        *,
        model_id: str | None = None,
        status: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        source: str | None = None,
        policy_version: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        normalized_limit = max(1, int(limit))
        has_unindexed_filter = any(
            value
            for value in (model_id, session_id, task_id, source, policy_version)
        )
        fetch_limit = _UNINDEXED_FILTER_SCAN_LIMIT if has_unindexed_filter else normalized_limit
        samples = await self._trajectory_store.list_samples(
            user_id=user_id,
            status=status,
            limit=fetch_limit,
        )
        filtered = []
        for sample in samples:
            if self._matches(
                sample,
                model_id=model_id,
                session_id=session_id,
                task_id=task_id,
                source=source,
                policy_version=policy_version,
            ):
                filtered.append(self._sample_summary(sample))
        return {"items": filtered[:normalized_limit], "next_cursor": None}

    async def get_trajectory(self, trajectory_id: str) -> dict[str, Any] | None:
        sample = await self._trajectory_store.get_sample(trajectory_id)
        if sample is None:
            return None
        return self._sample_detail(sample)

    async def patch_trajectory(self, trajectory_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        sample = await self._trajectory_store.patch_sample(trajectory_id, updates)
        if sample is None:
            return None
        return self._sample_detail(sample)

    async def delete_trajectory(self, trajectory_id: str, *, force: bool = False) -> bool:
        return await self._trajectory_store.delete_sample(trajectory_id, force=force)

    async def trajectory_management_stats(
        self,
        *,
        model_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if hasattr(self._trajectory_store, "management_stats"):
            stats = await self._trajectory_store.management_stats(user_id=user_id, model_id=model_id)
        else:
            samples = await self._trajectory_store.list_samples(user_id=user_id, limit=1000000)
            if model_id:
                samples = [
                    sample
                    for sample in samples
                    if str(sample.get("model") or sample.get("model_id") or "") == model_id
                ]
            stats = {"total": len(samples), "by_status": {}, "by_source": {}}
            for sample in samples:
                item_status = str(sample.get("_store_status") or "pending")
                stats["by_status"][item_status] = stats["by_status"].get(item_status, 0) + 1
                source = str(sample.get("source") or sample.get("mode") or "unknown")
                stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
        return stats

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

    def _trajectory_to_samples(
        self,
        payload: dict[str, Any],
        trajectory: dict[str, Any],
        idx: int,
    ) -> list[dict[str, Any]]:
        if "sample_id" in trajectory and "trajectory" in trajectory:
            sample = dict(trajectory)
            sample.setdefault("user_id", payload.get("user_id") or self._default_user_id)
            sample.setdefault("session_id", payload.get("session_id") or "default")
            sample.setdefault("model", payload.get("model_id") or payload.get("base_model"))
            sample.setdefault("source", payload.get("source") or "manual")
            sample.setdefault("policy_version", payload.get("policy_version") or "base")
            return [sample]

        trajectory_id = str(trajectory.get("trajectory_id") or f"traj-{idx:04d}")
        session_id = str(trajectory.get("session_id") or payload.get("session_id") or "default")
        user_id = str(
            trajectory.get("user_id")
            or payload.get("user_id")
            or payload.get("tenant_id")
            or self._default_user_id
            or ""
        ).strip()
        if not user_id:
            raise ValueError("missing user_id/tenant_id")
        model_id = trajectory.get("model_id") or payload.get("model_id") or payload.get("base_model")
        source = str(trajectory.get("source") or payload.get("source") or "jiuwen_rail")
        policy_version = str(trajectory.get("policy_version") or payload.get("policy_version") or "base")
        reward = trajectory.get("reward")
        judge = None
        if isinstance(reward, dict) and reward.get("score") is not None:
            judge = {
                "score": reward.get("score"),
                "source": reward.get("source"),
                "details": reward.get("details") or {},
            }

        steps = trajectory.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")

        samples: list[dict[str, Any]] = []
        for step_idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if step.get("type", "llm") != "llm":
                continue
            request = step.get("request") if isinstance(step.get("request"), dict) else {}
            response = step.get("response") if isinstance(step.get("response"), dict) else {}
            token_trace = step.get("token_trace") if isinstance(step.get("token_trace"), dict) else {}
            prompt_ids = [int(x) for x in token_trace.get("prompt_ids") or []]
            response_ids = [int(x) for x in token_trace.get("response_ids") or []]
            if not response_ids:
                raise ValueError(f"step {step_idx} missing token_trace.response_ids")
            response_logprobs = coerce_logprobs(token_trace.get("response_logprobs"), len(response_ids))
            messages = request.get("messages") if isinstance(request.get("messages"), list) else []
            assistant_message = {
                "role": "assistant",
                "content": str(response.get("content") or ""),
            }
            if response.get("tool_calls"):
                assistant_message["tool_calls"] = response["tool_calls"]
            sample = build_sample(
                sample_id=f"{trajectory_id}:{step_idx}",
                user_id=user_id,
                session_id=session_id,
                turn_num=int(step.get("turn_num") or step_idx + 1),
                mode=source,
                io_mode="trajectory_api",
                model=model_id,
                messages=messages,
                tools=request.get("tools"),
                assistant_message=assistant_message,
                usage=response.get("usage") or {},
                finish_reason=response.get("finish_reason"),
                prompt_text=str(token_trace.get("prompt_text") or ""),
                prompt_ids=prompt_ids,
                response_text=str(response.get("content") or ""),
                response_ids=response_ids,
                response_logprobs=response_logprobs,
                tool_calls=response.get("tool_calls") or [],
                extra_fields={
                    "trajectory_id": trajectory_id,
                    "rollout_id": trajectory.get("rollout_id"),
                    "task_id": trajectory.get("task_id") or payload.get("task_id"),
                    "source": source,
                    "policy_version": policy_version,
                    "metadata": trajectory.get("metadata") or {},
                },
            )
            if judge is not None:
                sample["judge"] = judge
            samples.append(sample)
        if not samples:
            raise ValueError("trajectory has no llm steps")
        return samples

    @staticmethod
    def _matches(
        sample: dict[str, Any],
        *,
        model_id: str | None,
        session_id: str | None,
        task_id: str | None,
        source: str | None,
        policy_version: str | None,
    ) -> bool:
        expected = {
            "session_id": session_id,
            "task_id": task_id,
            "source": source,
            "policy_version": policy_version,
        }
        for key, value in expected.items():
            if value and str(sample.get(key) or "") != value:
                return False
        if model_id and str(sample.get("model") or sample.get("model_id") or "") != model_id:
            return False
        return True

    @staticmethod
    def _sample_summary(sample: dict[str, Any]) -> dict[str, Any]:
        return {
            "trajectory_id": sample.get("sample_id"),
            "sample_id": sample.get("sample_id"),
            "model_id": sample.get("model") or sample.get("model_id"),
            "source": sample.get("source") or sample.get("mode"),
            "status": sample.get("_store_status") or "pending",
            "user_id": sample.get("user_id"),
            "session_id": sample.get("session_id"),
            "task_id": sample.get("task_id"),
            "policy_version": sample.get("policy_version"),
            "created_at": sample.get("created_at"),
            "updated_at": sample.get("updated_at"),
        }

    @classmethod
    def _sample_detail(cls, sample: dict[str, Any]) -> dict[str, Any]:
        detail = dict(sample)
        detail["trajectory_id"] = sample.get("sample_id")
        detail["status"] = sample.get("_store_status") or "pending"
        return detail
