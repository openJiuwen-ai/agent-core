# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Durable lifecycle for one manually triggered online-RL training run."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from typing import Any, ParamSpec, Protocol, TypeVar

from redis.asyncio import Redis
from redis.exceptions import WatchError

from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import (
    RedisTrajectoryStore,
    trajectory_index_key,
    trajectory_key,
)
from openjiuwen.agent_evolving.agent_rl.storage.trajectory_store import TrajectorySampleStore
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error

_KEY_PREFIX = "rl:v1:training_run"
_ACTIVE_KEY = f"{_KEY_PREFIX}:active"
_ACTIVE_STATUSES = {"pending", "running"}

_StoreParams = ParamSpec("_StoreParams")
_StoreResult = TypeVar("_StoreResult")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _translate_run_store_errors(
    operation: Callable[_StoreParams, Coroutine[Any, Any, _StoreResult]],
) -> Callable[_StoreParams, Coroutine[Any, Any, _StoreResult]]:
    @wraps(operation)
    async def wrapped(*args: _StoreParams.args, **kwargs: _StoreParams.kwargs) -> _StoreResult:
        try:
            return await operation(*args, **kwargs)
        except BaseError:
            raise
        except Exception as exc:
            raise build_error(
                StatusCode.AGENT_RL_TRAJECTORY_RUNTIME_ERROR,
                cause=exc,
                error_msg=f"Training Run persistence failed: {exc}",
            ) from exc

    return wrapped


class RunStatus(str, Enum):
    """Public Training Run lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class RunStage(str, Enum):
    """Durable recovery boundary for a Training Run."""

    QUEUED = "queued"
    TRAINING = "training"
    ACTIVATING = "activating"


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """Active policy captured when a Run is created."""

    lora_name: str = "base"
    lora_path: str = ""


@dataclass(frozen=True, slots=True)
class TrainingArtifact:
    """Versioned LoRA output ready for AIGW activation."""

    lora_name: str
    lora_path: str


@dataclass(frozen=True, slots=True)
class TrainingRunRecord:
    """Public durable state for one fixed sample batch."""

    training_run_id: str
    status: RunStatus
    stage: RunStage
    sample_count: int
    policy_versions: dict[str, int]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    lora_name: str | None = None
    lora_path: str | None = None
    failure_reason: str | None = None
    sample_ids: tuple[str, ...] = ()
    parent_lora_name: str = "base"
    parent_lora_path: str = ""

    def public_dict(self) -> dict[str, Any]:
        """Return the fixed HTTP response shape without recovery-only fields."""

        return {
            "training_run_id": self.training_run_id,
            "status": self.status.value,
            "stage": self.stage.value,
            "sample_count": self.sample_count,
            "policy_versions": dict(self.policy_versions),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "lora_name": self.lora_name,
            "lora_path": self.lora_path,
            "failure_reason": self.failure_reason,
        }

    def to_json(self) -> str:
        """Serialize the complete durable record."""

        values = asdict(self)
        values["status"] = self.status.value
        values["stage"] = self.stage.value
        return json.dumps(values, sort_keys=True)

    @classmethod
    def from_json(cls, payload: str | bytes) -> TrainingRunRecord:
        """Restore a record from Redis JSON."""

        if isinstance(payload, bytes):
            payload = payload.decode()
        values = json.loads(payload)
        values["status"] = RunStatus(values["status"])
        values["stage"] = RunStage(values["stage"])
        values["sample_ids"] = tuple(values.get("sample_ids") or ())
        return cls(**values)


@dataclass(frozen=True, slots=True)
class TrainingRunStartResult:
    """Distinguish a new Run from idempotent active-Run reuse."""

    run: TrainingRunRecord
    created: bool


class _TrainingRunStore:
    """Persist Runs and atomically coordinate their trajectory transitions."""

    def __init__(
        self,
        *,
        redis: Redis,
        trajectory_store: TrajectorySampleStore,
        model_id: str,
        min_samples: int,
        max_samples: int,
    ) -> None:
        self._redis = redis
        self._trajectory_store = trajectory_store
        self._model_id = model_id
        self._min_samples = min_samples
        self._max_samples = max_samples

    @_translate_run_store_errors
    async def get(self, training_run_id: str) -> TrainingRunRecord | None:
        """Return a Run by ID."""

        payload = await self._redis.get(self._run_key(training_run_id))
        return None if payload is None else TrainingRunRecord.from_json(payload)

    @_translate_run_store_errors
    async def get_active(self) -> TrainingRunRecord | None:
        """Return the single active Run, if present."""

        run_id = await self._redis.get(_ACTIVE_KEY)
        if run_id is None:
            return None
        if isinstance(run_id, bytes):
            run_id = run_id.decode()
        run = await self.get(str(run_id))
        return run if run is not None and run.status.value in _ACTIVE_STATUSES else None

    @_translate_run_store_errors
    async def claim(
        self,
        run: TrainingRunRecord,
    ) -> tuple[TrainingRunRecord, list[dict[str, Any]], bool]:
        """Create a Run and claim its fixed sample batch as one operation."""

        if isinstance(self._trajectory_store, RedisTrajectoryStore):
            return await self._claim_redis(run)

        pending = await self._trajectory_store.get_pending_count(self._model_id)
        if pending < self._min_samples:
            raise build_error(
                StatusCode.AGENT_RL_TRAINING_SAMPLES_INVALID,
                error_msg=f"pending samples {pending} is below minimum {self._min_samples}",
            )
        existing = await self._create(run)
        if existing is not None:
            return existing, [], False
        samples = await self._trajectory_store.fetch_and_mark_training(
            self._model_id,
            min(pending, self._max_samples),
        )
        claimed = self._with_samples(run, samples)
        await self.save(claimed)
        return claimed, samples, True

    @_translate_run_store_errors
    async def save(self, run: TrainingRunRecord, *, clear_active: bool = False) -> None:
        """Persist a Run and optionally clear its active marker atomically."""

        pipe = self._redis.pipeline(transaction=True)
        pipe.set(self._run_key(run.training_run_id), run.to_json())
        if clear_active:
            pipe.delete(_ACTIVE_KEY)
        await pipe.execute()

    @_translate_run_store_errors
    async def restore_samples(self, sample_ids: tuple[str, ...]) -> None:
        """Return a Run's training samples to pending."""

        await self._trajectory_store.reset_to_pending(list(sample_ids))

    @_translate_run_store_errors
    async def record_ppo_success(self, run: TrainingRunRecord) -> None:
        """Persist the artifact and trained-sample transition atomically in Redis."""

        sample_ids = list(run.sample_ids)
        if not isinstance(self._trajectory_store, RedisTrajectoryStore):
            await self._trajectory_store.mark_trained(sample_ids)
            await self.save(run)
            return

        training_key = trajectory_index_key(self._model_id, "training")
        trained_key = trajectory_index_key(self._model_id, "trained")
        score = datetime.now(timezone.utc).timestamp()
        pipe = self._redis.pipeline(transaction=True)
        for sample_id in sample_ids:
            sample_key = trajectory_key(sample_id)
            pipe.zrem(training_key, sample_id)
            pipe.zadd(trained_key, {sample_id: score})
            pipe.hset(sample_key, "status", "trained")
        pipe.set(self._run_key(run.training_run_id), run.to_json())
        await pipe.execute()

    async def _claim_redis(  # pylint: disable=too-many-locals
        self,
        run: TrainingRunRecord,
    ) -> tuple[TrainingRunRecord, list[dict[str, Any]], bool]:
        pending_key = trajectory_index_key(self._model_id, "pending")
        training_key = trajectory_index_key(self._model_id, "training")
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(_ACTIVE_KEY, pending_key)
                    active_id = await pipe.get(_ACTIVE_KEY)
                    if active_id is not None:
                        active_id = active_id.decode() if isinstance(active_id, bytes) else str(active_id)
                        active_key = self._run_key(active_id)
                        await pipe.watch(active_key)
                        active_payload = await pipe.get(active_key)
                        if active_payload is not None:
                            active = TrainingRunRecord.from_json(active_payload)
                            if active.status.value in _ACTIVE_STATUSES:
                                return active, [], False

                    raw_ids = await pipe.zrange(pending_key, 0, self._max_samples - 1)
                    if len(raw_ids) < self._min_samples:
                        raise build_error(
                            StatusCode.AGENT_RL_TRAINING_SAMPLES_INVALID,
                            error_msg=f"pending samples {len(raw_ids)} is below minimum {self._min_samples}",
                        )
                    sample_ids = [value.decode() if isinstance(value, bytes) else str(value) for value in raw_ids]
                    sample_keys = [trajectory_key(sample_id) for sample_id in sample_ids]
                    await pipe.watch(*sample_keys)
                    payloads = [await pipe.hget(key, "sample_json") for key in sample_keys]
                    if any(payload is None for payload in payloads):
                        raise build_error(
                            StatusCode.AGENT_RL_TRAJECTORY_RUNTIME_ERROR,
                            error_msg="pending trajectory is missing sample_json",
                        )
                    samples = []
                    for payload in payloads:
                        decoded = payload.decode() if isinstance(payload, bytes) else payload
                        sample = json.loads(decoded)
                        sample["_store_status"] = "training"
                        samples.append(sample)
                    claimed = self._with_samples(run, samples)

                    pipe.multi()
                    pipe.zrem(pending_key, *sample_ids)
                    pipe.zadd(
                        training_key, {sample_id: datetime.now(timezone.utc).timestamp() for sample_id in sample_ids}
                    )
                    for sample_key in sample_keys:
                        pipe.hset(sample_key, "status", "training")
                    pipe.set(self._run_key(claimed.training_run_id), claimed.to_json())
                    pipe.set(_ACTIVE_KEY, claimed.training_run_id)
                    await pipe.execute()
                    return claimed, samples, True
                except WatchError:
                    continue

    async def _create(self, run: TrainingRunRecord) -> TrainingRunRecord | None:
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(_ACTIVE_KEY)
                    active_id = await pipe.get(_ACTIVE_KEY)
                    if active_id is not None:
                        if isinstance(active_id, bytes):
                            active_id = active_id.decode()
                        active = await self.get(str(active_id))
                        if active is not None and active.status.value in _ACTIVE_STATUSES:
                            return active
                    pipe.multi()
                    pipe.set(self._run_key(run.training_run_id), run.to_json())
                    pipe.set(_ACTIVE_KEY, run.training_run_id)
                    await pipe.execute()
                    return None
                except WatchError:
                    continue

    @staticmethod
    def _with_samples(run: TrainingRunRecord, samples: list[dict[str, Any]]) -> TrainingRunRecord:
        sample_ids = tuple(str(sample["sample_id"]) for sample in samples)
        versions = Counter(str(sample.get("policy_version") or "base") for sample in samples)
        return replace(
            run,
            sample_count=len(samples),
            policy_versions=dict(versions),
            sample_ids=sample_ids,
        )

    @staticmethod
    def _run_key(training_run_id: str) -> str:
        return f"{_KEY_PREFIX}:{training_run_id}"


class PPOExecutor(Protocol):
    """Train one immutable sample batch and export its versioned LoRA."""

    async def train(self, **kwargs: Any) -> TrainingArtifact:
        """Return the artifact produced from the supplied sample snapshot."""

        ...


class LoRAActivator(Protocol):
    """Atomically activate an exported LoRA in AIGW."""

    async def activate(self, **kwargs: Any) -> None:
        """Activate or idempotently confirm the supplied artifact."""

        ...


class TrainingRunner:
    """Own sample claim, PPO, activation, cancellation, and crash recovery."""

    def __init__(
        self,
        *,
        redis: Redis,
        trajectory_store: TrajectorySampleStore,
        ppo: PPOExecutor,
        activator: LoRAActivator,
        model_id: str,
        base_model_path: str,
        min_samples_for_training: int,
        max_samples_per_run: int,
        active_policy: Callable[[], PolicySnapshot | Awaitable[PolicySnapshot]] | None = None,
    ) -> None:
        if not model_id.strip():
            raise build_error(StatusCode.AGENT_RL_SERVICE_PARAM_ERROR, error_msg="model_id is required")
        if min_samples_for_training < 1:
            raise build_error(
                StatusCode.AGENT_RL_SERVICE_PARAM_ERROR,
                error_msg="min_samples_for_training must be at least 1",
            )
        if max_samples_per_run < min_samples_for_training:
            raise build_error(
                StatusCode.AGENT_RL_SERVICE_PARAM_ERROR,
                error_msg="max_samples_per_run must be >= min_samples_for_training",
            )
        self._ppo = ppo
        self._activator = activator
        self._model_id = model_id
        self._base_model_path = base_model_path
        self._run_store = _TrainingRunStore(
            redis=redis,
            trajectory_store=trajectory_store,
            model_id=model_id,
            min_samples=min_samples_for_training,
            max_samples=max_samples_per_run,
        )
        self._active_policy = active_policy or PolicySnapshot
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_requests: set[str] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> TrainingRunStartResult:
        """Create one fixed batch, or return the currently active Run."""

        async with self._lock:
            active = await self.get_active()
            if active is not None:
                return TrainingRunStartResult(active, created=False)
            policy = await self._policy_snapshot()
            run = TrainingRunRecord(
                training_run_id=f"run-{uuid.uuid4().hex[:12]}",
                status=RunStatus.PENDING,
                stage=RunStage.QUEUED,
                sample_count=0,
                policy_versions={},
                created_at=_now(),
                parent_lora_name=policy.lora_name or "base",
                parent_lora_path=policy.lora_path,
            )
            run, samples, created = await self._run_store.claim(run)
            if not created:
                return TrainingRunStartResult(run, created=False)
            self._tasks[run.training_run_id] = asyncio.create_task(self._execute(run, samples))
            return TrainingRunStartResult(run, created=True)

    async def get(self, training_run_id: str) -> TrainingRunRecord | None:
        """Return a Run by ID."""

        return await self._run_store.get(training_run_id)

    async def get_active(self) -> TrainingRunRecord | None:
        """Return the Service's single active Run, if present."""

        return await self._run_store.get_active()

    async def wait(self, training_run_id: str) -> TrainingRunRecord:
        """Wait for in-process execution and return its final durable record."""

        task = self._tasks.get(training_run_id)
        if task is not None:
            await task
        run = await self.get(training_run_id)
        if run is None:
            raise build_error(
                StatusCode.AGENT_RL_TRAINING_RUN_NOT_FOUND,
                training_run_id=training_run_id,
            )
        return run

    def check_health(self) -> None:
        """Raise when an execution task escaped the expected Run failure paths."""

        ppo_health = getattr(self._ppo, "check_health", None)
        if callable(ppo_health):
            ppo_health()
        for task in self._tasks.values():
            if not task.done() or task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                raise build_error(
                    StatusCode.AGENT_RL_PPO_SCHEDULER_RUNTIME_ERROR,
                    cause=error,
                    error_msg="training runner failed",
                ) from error

    async def stop(self, training_run_id: str) -> TrainingRunRecord:
        """Cancel an active Run and wait until its sample disposition is durable."""

        async with self._lock:
            run = await self.get(training_run_id)
            if run is None:
                raise build_error(
                    StatusCode.AGENT_RL_TRAINING_RUN_NOT_FOUND,
                    training_run_id=training_run_id,
                )
            if run.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
                return run
            self._stop_requests.add(training_run_id)
            task = self._tasks.get(training_run_id)
            if task is not None and not task.done():
                cancel = getattr(self._ppo, "cancel", None)
                worker_settled = False
                if callable(cancel):
                    worker_settled = await cancel(training_run_id) is True
                if not worker_settled and not task.done():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            current = await self.get(training_run_id)
            if current is None:
                raise build_error(
                    StatusCode.AGENT_RL_TRAINING_RUN_NOT_FOUND,
                    training_run_id=training_run_id,
                )
            if current.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
                return current
            if current.stage in {RunStage.QUEUED, RunStage.TRAINING}:
                await self._run_store.restore_samples(current.sample_ids)
            return await self._finish(current, RunStatus.CANCELED)

    async def recover(self) -> TrainingRunRecord | None:
        """Apply the fixed restart rule to the persisted active Run."""

        async with self._lock:
            run = await self.get_active()
            if run is None:
                return None
            if run.stage in {RunStage.QUEUED, RunStage.TRAINING}:
                await self._run_store.restore_samples(run.sample_ids)
                return await self._finish(
                    run,
                    RunStatus.FAILED,
                    failure_reason="service_restarted",
                )
            try:
                await self._activate(run)
            except Exception as exc:  # Activation is an idempotent external recovery operation.
                return await self._finish(run, RunStatus.FAILED, failure_reason=str(exc))
            return await self._finish(run, RunStatus.SUCCEEDED)

    async def _execute(self, run: TrainingRunRecord, samples: list[dict[str, Any]]) -> None:
        running = replace(run, status=RunStatus.RUNNING, stage=RunStage.TRAINING, started_at=_now())
        await self._run_store.save(running)
        sample_ids = list(running.sample_ids)
        try:
            artifact = await self._ppo.train(
                training_run_id=running.training_run_id,
                model_id=self._model_id,
                base_model_path=self._base_model_path,
                samples=samples,
                init_lora_name=running.parent_lora_name,
                init_lora_path=running.parent_lora_path,
            )
            if re.fullmatch(rf"{re.escape(self._model_id)}:v[1-9][0-9]*", artifact.lora_name) is None:
                raise build_error(
                    StatusCode.AGENT_RL_PPO_EXECUTION_ERROR,
                    error_msg="PPO artifact must use '<model_id>:vN' name",
                )
        except asyncio.CancelledError:
            await self._run_store.restore_samples(tuple(sample_ids))
            await self._finish(running, RunStatus.CANCELED)
            return
        except Exception as exc:  # PPO implementations define their own error hierarchy.
            await self._run_store.restore_samples(tuple(sample_ids))
            if running.training_run_id in self._stop_requests:
                await self._finish(running, RunStatus.CANCELED)
            else:
                await self._finish(running, RunStatus.FAILED, failure_reason=str(exc))
            return

        activating = replace(
            running,
            stage=RunStage.ACTIVATING,
            lora_name=artifact.lora_name,
            lora_path=artifact.lora_path,
        )
        transition = asyncio.create_task(self._run_store.record_ppo_success(activating))
        try:
            await asyncio.shield(transition)
        except asyncio.CancelledError:
            await transition
            await self._finish(activating, RunStatus.CANCELED)
            return
        if activating.training_run_id in self._stop_requests:
            await self._finish(activating, RunStatus.CANCELED)
            return
        try:
            await self._activate(activating)
        except asyncio.CancelledError:
            await self._finish(activating, RunStatus.CANCELED)
            return
        except Exception as exc:  # Activation transport maps its own stable errors at the HTTP boundary.
            await self._finish(activating, RunStatus.FAILED, failure_reason=str(exc))
            return
        await self._finish(activating, RunStatus.SUCCEEDED)

    async def _activate(self, run: TrainingRunRecord) -> None:
        await self._activator.activate(
            training_run_id=run.training_run_id,
            model_id=self._model_id,
            base_model=self._base_model_path,
            lora_name=run.lora_name,
            lora_path=run.lora_path,
            expected_lora_name=run.parent_lora_name,
        )

    async def _finish(
        self,
        run: TrainingRunRecord,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
    ) -> TrainingRunRecord:
        finished = replace(run, status=status, finished_at=_now(), failure_reason=failure_reason)
        await self._run_store.save(finished, clear_active=True)
        self._stop_requests.discard(run.training_run_id)
        return finished

    async def _policy_snapshot(self) -> PolicySnapshot:
        value = self._active_policy()
        if inspect.isawaitable(value):
            value = await value
        return value


__all__ = [
    "LoRAActivator",
    "PPOExecutor",
    "PolicySnapshot",
    "RunStage",
    "RunStatus",
    "TrainingArtifact",
    "TrainingRunRecord",
    "TrainingRunStartResult",
    "TrainingRunner",
]
