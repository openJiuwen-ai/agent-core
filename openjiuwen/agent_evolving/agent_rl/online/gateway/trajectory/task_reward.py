# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Terminal task rewards and idempotent projection for gateway samples."""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store import PendingJudgeStore

_KEY_PREFIX = "rl:task_reward"


@dataclass(frozen=True, slots=True)
class TaskReward:
    """Terminal verifier reward projected onto every captured policy call."""

    reward_id: str
    attempt_id: str
    task_id: str
    training_key: str
    score: float
    passed: bool
    source: str = "benchmark_verifier"
    termination_reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.reward_id.strip() or not self.attempt_id.strip() or not self.task_id.strip():
            raise ValueError("reward_id, attempt_id, and task_id are required")
        score = float(self.score)
        if not 0.0 <= score <= 1.0:
            raise ValueError("score must be between 0 and 1")
        object.__setattr__(self, "score", score)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TaskReward":
        """Validate and construct a reward received over a transport adapter."""
        return cls(
            reward_id=str(payload["reward_id"]),
            attempt_id=str(payload["attempt_id"]),
            task_id=str(payload["task_id"]),
            training_key=str(payload["training_key"]),
            score=float(payload["score"]),
            passed=bool(payload["passed"]),
            source=str(payload.get("source") or "benchmark_verifier"),
            termination_reason=str(payload.get("termination_reason") or ""),
            details=dict(payload.get("details") or {}),
        )


class TaskRewardProjector:
    """Project one verifier reward onto every pending call in a task session."""

    def __init__(
        self,
        *,
        redis: Any,
        pending_store: PendingJudgeStore,
        record_samples_once: Callable[[Sequence[dict[str, Any]]], Awaitable[set[str]]],
    ) -> None:
        self._redis = redis
        self._pending_store = pending_store
        self._record_samples_once = record_samples_once

    async def project(self, session_id: str, reward: TaskReward) -> int:
        payload = reward.to_payload()
        state_key = f"{_KEY_PREFIX}:{session_id}"
        state = await self._load_or_claim(state_key, session_id, payload)
        projected_ids = set(state.get("projected_sample_ids") or ())
        if state.get("status") == "completed":
            return len(projected_ids)

        samples = await self._pending_store.get_by_session(session_id)
        unprojected: list[dict[str, Any]] = []
        for sample in samples:
            sample_id = str(sample.get("sample_id") or "").strip()
            if sample_id not in projected_ids:
                unprojected.append(self._apply_reward(sample, payload))

        if unprojected:
            await self._record_samples_once(unprojected)
            projected_ids.update(str(sample["sample_id"]) for sample in unprojected)
            await self._save_state(state_key, payload, projected_ids, status="projecting")

        for sample in samples:
            await self._pending_store.pop_one(
                session_id,
                str(sample.get("trajectory_id") or ""),
                int(sample.get("step_index") or 0),
            )

        await self._save_state(state_key, payload, projected_ids, status="completed")
        return len(projected_ids)

    async def _load_or_claim(
        self,
        state_key: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        initial = self._state_payload(payload, set(), status="projecting")
        if await self._redis.set(state_key, json.dumps(initial, sort_keys=True), nx=True):
            return initial
        raw = await self._redis.get(state_key)
        if raw is None:
            return await self._load_or_claim(state_key, session_id, payload)
        if isinstance(raw, bytes):
            raw = raw.decode()
        state = json.loads(raw)
        if state.get("reward") != payload:
            raise ValueError(f"collection session {session_id} already has a different terminal reward")
        return state

    async def _save_state(
        self,
        state_key: str,
        reward: dict[str, Any],
        projected_ids: set[str],
        *,
        status: str,
    ) -> None:
        state = self._state_payload(reward, projected_ids, status=status)
        await self._redis.set(state_key, json.dumps(state, sort_keys=True))

    @staticmethod
    def _state_payload(
        reward: dict[str, Any],
        projected_ids: set[str],
        *,
        status: str,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "reward": reward,
            "projected_sample_ids": sorted(projected_ids),
        }

    @staticmethod
    def _apply_reward(sample: dict[str, Any], reward: dict[str, Any]) -> dict[str, Any]:
        finalized = copy.deepcopy(sample)
        finalized["user_id"] = str(reward["training_key"])
        finalized["judge"] = {
            "score": float(reward["score"]),
            "source": str(reward["source"]),
            "reward_id": str(reward["reward_id"]),
        }
        finalized["task"] = {
            "attempt_id": str(reward["attempt_id"]),
            "task_id": str(reward["task_id"]),
            "passed": bool(reward["passed"]),
            "termination_reason": str(reward.get("termination_reason") or ""),
            "details": copy.deepcopy(reward.get("details") or {}),
        }
        return finalized
