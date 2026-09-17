# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Redis-backed RL Task lifecycle and capture metadata registry."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import WatchError

_KEY_PREFIX = "rl:v1"
_ACTIVE_TASKS_KEY = f"{_KEY_PREFIX}:tasks:active"
_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


class RewardMode(str, Enum):
    """Reward timing fixed for one RL Task."""

    TERMINAL = "terminal"
    DELAYED_FEEDBACK = "delayed_feedback"


class TaskStatus(str, Enum):
    """Persisted RL Task lifecycle states."""

    ACTIVE = "active"
    FINALIZED = "finalized"
    ABORTED = "aborted"


class FinishReason(str, Enum):
    """Reasons a Task can leave the active state."""

    USER_STOPPED = "user_stopped"
    SERVICE_STOPPED = "service_stopped"
    CAPTURE_FAILED = "capture_failed"
    TIMEOUT = "timeout"
    SERVICE_RESTARTED = "service_restarted"


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Identity and fixed policy for one Agent session capture window."""

    rl_task_id: str
    agent_session_id: str
    model_id: str
    policy_lora_name: str
    reward_mode: RewardMode
    policy_model: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("rl_task_id", "agent_session_id", "model_id", "policy_lora_name"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if _TASK_ID_PATTERN.fullmatch(str(self.rl_task_id)) is None:
            raise ValueError("rl_task_id must contain 1-64 letters, digits, underscores, or hyphens")
        if not isinstance(self.reward_mode, RewardMode):
            object.__setattr__(self, "reward_mode", RewardMode(self.reward_mode))
        if self.policy_model is None:
            object.__setattr__(self, "policy_model", self.policy_lora_name)
        elif not self.policy_model.strip():
            raise ValueError("policy_model is required")


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """Public durable state of an RL Task."""

    rl_task_id: str
    agent_session_id: str
    model_id: str
    policy_lora_name: str
    reward_mode: RewardMode
    status: TaskStatus
    created_at: str
    finished_at: str | None = None
    finish_reason: FinishReason | None = None
    policy_model: str | None = None

    def __post_init__(self) -> None:
        if self.policy_model is None:
            object.__setattr__(self, "policy_model", self.policy_lora_name)
        elif not self.policy_model.strip():
            raise ValueError("policy_model is required")

    @classmethod
    def create(cls, spec: TaskSpec) -> "TaskRecord":
        """Create an active durable record from immutable Task identity."""

        return cls(
            **asdict(spec),
            status=TaskStatus.ACTIVE,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record into its Redis representation."""

        values = asdict(self)
        values["reward_mode"] = self.reward_mode.value
        values["status"] = self.status.value
        values["finish_reason"] = self.finish_reason.value if self.finish_reason else None
        return values

    @classmethod
    def from_json(cls, payload: str | bytes) -> "TaskRecord":
        """Restore a record from its Redis JSON representation."""

        if isinstance(payload, bytes):
            payload = payload.decode()
        values = json.loads(payload)
        return cls(
            rl_task_id=str(values["rl_task_id"]),
            agent_session_id=str(values["agent_session_id"]),
            model_id=str(values["model_id"]),
            policy_lora_name=str(values["policy_lora_name"]),
            reward_mode=RewardMode(values["reward_mode"]),
            policy_model=str(values.get("policy_model", values["policy_lora_name"])),
            status=TaskStatus(values["status"]),
            created_at=str(values["created_at"]),
            finished_at=values.get("finished_at"),
            finish_reason=FinishReason(values["finish_reason"]) if values.get("finish_reason") else None,
        )


@dataclass(frozen=True, slots=True)
class TaskStartResult:
    """Task start outcome used to distinguish create from idempotent reuse."""

    task: TaskRecord
    created: bool


class TaskConflictError(RuntimeError):
    """The requested Task operation conflicts with durable state."""


class TaskNotFoundError(TaskConflictError):
    """The requested RL Task does not exist."""


class TurnClosedError(TaskConflictError):
    """A delayed-feedback capture arrived after its turn was closed."""


@dataclass(frozen=True, slots=True)
class _TurnTransition:
    previous_turn_id: str
    next_turn_id: str


class TaskRegistry:
    """Atomically own RL Tasks and their active-session index in Redis."""

    def __init__(self, *, redis: Redis) -> None:
        if redis is None:
            raise ValueError("TaskRegistry requires redis client")
        self._redis = redis

    async def start(self, spec: TaskSpec) -> TaskStartResult:  # pylint: disable=too-many-locals
        """Atomically create or reuse the active Task for a session."""

        task_key = self._task_key(spec.rl_task_id)
        active_key = self._active_key(spec.agent_session_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(active_key, task_key)
                    existing_raw = await pipe.get(task_key)
                    if existing_raw is not None:
                        existing = TaskRecord.from_json(existing_raw)
                        if self._matches_spec(existing, spec):
                            return TaskStartResult(existing, created=False)
                        raise TaskConflictError(f"rl_task_id already exists: {spec.rl_task_id}")

                    active_id = await pipe.get(active_key)
                    if active_id is not None:
                        active_id = self._text(active_id)
                        active_task_key = self._task_key(active_id)
                        await pipe.watch(active_task_key)
                        active_raw = await pipe.get(active_task_key)
                        if active_raw is not None:
                            active = TaskRecord.from_json(active_raw)
                            if active.status is TaskStatus.ACTIVE:
                                return TaskStartResult(active, created=False)

                    record = TaskRecord.create(spec)
                    pipe.multi()
                    pipe.set(task_key, json.dumps(record.to_dict(), sort_keys=True))
                    pipe.set(active_key, record.rl_task_id)
                    pipe.sadd(_ACTIVE_TASKS_KEY, record.rl_task_id)
                    await pipe.execute()
                    return TaskStartResult(record, created=True)
                except WatchError:
                    continue

    async def get(self, rl_task_id: str) -> TaskRecord | None:
        """Return a Task by ID, or ``None`` when it does not exist."""

        payload = await self._redis.get(self._task_key(rl_task_id))
        return None if payload is None else TaskRecord.from_json(payload)

    async def get_active(self, agent_session_id: str) -> TaskRecord | None:
        """Return the session's active Task, if any."""

        active_id = await self._redis.get(self._active_key(agent_session_id))
        if active_id is None:
            return None
        task = await self.get(self._text(active_id))
        return task if task is not None and task.status is TaskStatus.ACTIVE else None

    async def finalize(self, rl_task_id: str, reason: FinishReason) -> TaskRecord:
        """Idempotently finalize a Task that has no open captures."""

        return await self._finish(rl_task_id, TaskStatus.FINALIZED, reason)

    async def abort(self, rl_task_id: str, reason: FinishReason) -> TaskRecord:
        """Idempotently abort a Task and discard unpublished samples."""

        return await self._finish(rl_task_id, TaskStatus.ABORTED, reason)

    async def active_tasks(self) -> list[TaskRecord]:
        """Return active Tasks from the versioned recovery index."""

        task_ids = sorted(self._text(value) for value in await self._redis.smembers(_ACTIVE_TASKS_KEY))
        tasks: list[TaskRecord] = []
        stale_ids: list[str] = []
        for task_id in task_ids:
            task = await self.get(task_id)
            if task is not None and task.status is TaskStatus.ACTIVE:
                tasks.append(task)
            else:
                stale_ids.append(task_id)
        if stale_ids:
            await self._redis.srem(_ACTIVE_TASKS_KEY, *stale_ids)
        return tasks

    async def recover_active(self) -> list[TaskRecord]:
        """Abort Tasks left active by a previous Service process."""

        return [await self.abort(task.rl_task_id, FinishReason.SERVICE_RESTARTED) for task in await self.active_tasks()]

    # pylint: disable-next=too-many-locals
    async def _begin_capture(
        self,
        rl_task_id: str,
        capture_id: str,
        agent_turn_id: str | None,
        request_fingerprint: str,
    ) -> TaskRecord | _TurnTransition:
        task_key = self._task_key(rl_task_id)
        capture_key = self._capture_key(rl_task_id, capture_id)
        current_turn_key = self._current_turn_key(rl_task_id)
        transition_key = self._turn_transition_key(rl_task_id)
        closed_turns_key = self._closed_turns_key(rl_task_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(task_key, capture_key, current_turn_key, transition_key, closed_turns_key)
                    task_payload = await pipe.get(task_key)
                    if task_payload is None:
                        raise TaskNotFoundError(f"unknown RL Task: {rl_task_id}")
                    task = TaskRecord.from_json(task_payload)
                    if task.status is not TaskStatus.ACTIVE:
                        raise TaskConflictError(f"RL Task is not active: {rl_task_id}")
                    existing_payload = await pipe.get(capture_key)
                    if existing_payload is not None:
                        existing = self._json(existing_payload)
                        if (
                            existing.get("agent_turn_id") != agent_turn_id
                            or existing.get("request_fingerprint") != request_fingerprint
                        ):
                            raise TaskConflictError(f"capture_id already belongs to different input: {capture_id}")
                        return task

                    if task.reward_mode is RewardMode.DELAYED_FEEDBACK:
                        turn_id = str(agent_turn_id or "")
                        if await pipe.sismember(closed_turns_key, turn_id):
                            raise TurnClosedError(f"turn is already closed: {turn_id}")
                        publish_key = self._turn_publish_key(rl_task_id, turn_id)
                        await pipe.watch(publish_key)
                        if await pipe.get(publish_key) is not None:
                            raise TurnClosedError(f"turn is already closing: {turn_id}")
                        current_value = await pipe.get(current_turn_key)
                        current_turn_id = self._text(current_value) if current_value is not None else None
                        transition_value = await pipe.get(transition_key)
                        next_turn_id = self._text(transition_value) if transition_value is not None else None
                        if current_turn_id is not None and current_turn_id != turn_id:
                            if next_turn_id is not None and next_turn_id != turn_id:
                                raise TaskConflictError(
                                    f"turn {next_turn_id} is already waiting for {current_turn_id}",
                                )
                            if next_turn_id is None:
                                pipe.multi()
                                pipe.set(transition_key, turn_id)
                                await pipe.execute()
                            return _TurnTransition(current_turn_id, turn_id)
                        if current_turn_id == turn_id and next_turn_id is not None:
                            raise TurnClosedError(f"turn is closing: {turn_id}")

                    metadata = {
                        "capture_id": capture_id,
                        "agent_turn_id": agent_turn_id,
                        "request_fingerprint": request_fingerprint,
                        "response_fingerprint": None,
                        "status": "open",
                    }
                    pipe.multi()
                    if task.reward_mode is RewardMode.DELAYED_FEEDBACK:
                        pipe.set(current_turn_key, str(agent_turn_id), nx=True)
                    pipe.set(capture_key, json.dumps(metadata, sort_keys=True))
                    pipe.sadd(self._capture_ids_key(rl_task_id), capture_id)
                    pipe.sadd(self._open_capture_ids_key(rl_task_id), capture_id)
                    await pipe.execute()
                    return task
                except WatchError:
                    continue

    async def _discard_capture(self, rl_task_id: str, capture_id: str, agent_turn_id: str | None) -> None:
        task_key = self._task_key(rl_task_id)
        capture_key = self._capture_key(rl_task_id, capture_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(task_key, capture_key)
                    task_payload = await pipe.get(task_key)
                    if task_payload is None:
                        raise TaskNotFoundError(f"unknown RL Task: {rl_task_id}")
                    task = TaskRecord.from_json(task_payload)
                    capture_payload = await pipe.get(capture_key)
                    if capture_payload is None or task.status is not TaskStatus.ACTIVE:
                        return
                    metadata = self._json(capture_payload)
                    if metadata.get("agent_turn_id") != agent_turn_id:
                        raise TaskConflictError(f"capture turn mismatch: {capture_id}")
                    if metadata.get("status") != "open":
                        raise TaskConflictError(f"capture is already committed: {capture_id}")
                    pipe.multi()
                    pipe.delete(capture_key)
                    pipe.srem(self._capture_ids_key(rl_task_id), capture_id)
                    pipe.srem(self._open_capture_ids_key(rl_task_id), capture_id)
                    await pipe.execute()
                    return
                except WatchError:
                    continue

    async def _current_turn(self, rl_task_id: str) -> str | None:
        value = await self._redis.get(self._current_turn_key(rl_task_id))
        return None if value is None else self._text(value)

    async def _claim_turn_publish(self, rl_task_id: str, agent_turn_id: str) -> str:
        open_captures_key = self._open_capture_ids_key(rl_task_id)
        publish_key = self._turn_publish_key(rl_task_id, agent_turn_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(open_captures_key, publish_key)
                    if await pipe.scard(open_captures_key):
                        return "waiting"
                    state = await pipe.get(publish_key)
                    if state is not None:
                        return self._text(state)
                    pipe.multi()
                    pipe.set(publish_key, "publishing")
                    await pipe.execute()
                    return "claimed"
                except WatchError:
                    continue

    async def _turn_pending_samples(self, rl_task_id: str, agent_turn_id: str) -> list[dict[str, Any]]:
        values = await self._redis.smembers(self._turn_capture_ids_key(rl_task_id, agent_turn_id))
        capture_ids = sorted(map(self._text, values))
        return await self._pending_samples(rl_task_id, capture_ids)

    async def _complete_turn_publish(self, rl_task_id: str, agent_turn_id: str) -> None:
        task_key = self._task_key(rl_task_id)
        publish_key = self._turn_publish_key(rl_task_id, agent_turn_id)
        capture_ids_key = self._turn_capture_ids_key(rl_task_id, agent_turn_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(task_key, publish_key, capture_ids_key)
                    task_payload = await pipe.get(task_key)
                    if task_payload is None:
                        raise TaskNotFoundError(f"unknown RL Task: {rl_task_id}")
                    task = TaskRecord.from_json(task_payload)
                    if task.status is not TaskStatus.ACTIVE:
                        raise TaskConflictError(f"RL Task is not active: {rl_task_id}")
                    publish_state = await pipe.get(publish_key)
                    if publish_state is None or self._text(publish_state) != "publishing":
                        raise TaskConflictError(f"turn is not publishing: {agent_turn_id}")
                    capture_ids = await pipe.smembers(capture_ids_key)
                    pipe.multi()
                    pipe.set(publish_key, "published")
                    pipe.sadd(self._closed_turns_key(rl_task_id), agent_turn_id)
                    for capture_id in capture_ids:
                        pipe.delete(self._pending_sample_key(rl_task_id, self._text(capture_id)))
                    await pipe.execute()
                    return
                except WatchError:
                    continue

    async def _fail_turn_publish(self, rl_task_id: str, agent_turn_id: str) -> None:
        await self._redis.set(self._turn_publish_key(rl_task_id, agent_turn_id), "failed")

    async def _advance_turn(self, rl_task_id: str, previous_turn_id: str, next_turn_id: str) -> None:
        current_key = self._current_turn_key(rl_task_id)
        transition_key = self._turn_transition_key(rl_task_id)
        publish_key = self._turn_publish_key(rl_task_id, previous_turn_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(current_key, transition_key, publish_key)
                    current = await pipe.get(current_key)
                    transition = await pipe.get(transition_key)
                    publish = await pipe.get(publish_key)
                    if publish is None or self._text(publish) != "published":
                        raise TaskConflictError(f"turn is not published: {previous_turn_id}")
                    if current is not None and self._text(current) == next_turn_id:
                        return
                    if current is None or self._text(current) != previous_turn_id:
                        raise TaskConflictError("current turn changed during transition")
                    if transition is None or self._text(transition) != next_turn_id:
                        raise TaskConflictError("next turn changed during transition")
                    pipe.multi()
                    pipe.set(current_key, next_turn_id)
                    pipe.delete(transition_key)
                    await pipe.execute()
                    return
                except WatchError:
                    continue

    async def _commit_capture(
        self,
        rl_task_id: str,
        capture_id: str,
        agent_turn_id: str | None,
        response_fingerprint: str,
        sample: dict[str, Any],
    ) -> bool:
        task_key = self._task_key(rl_task_id)
        capture_key = self._capture_key(rl_task_id, capture_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(task_key, capture_key)
                    task_payload = await pipe.get(task_key)
                    if task_payload is None:
                        raise TaskNotFoundError(f"unknown RL Task: {rl_task_id}")
                    task = TaskRecord.from_json(task_payload)
                    capture_payload = await pipe.get(capture_key)
                    if capture_payload is None:
                        raise TaskConflictError(f"capture has no matching before: {capture_id}")
                    metadata = self._json(capture_payload)
                    if metadata.get("agent_turn_id") != agent_turn_id:
                        raise TaskConflictError(f"capture turn mismatch: {capture_id}")
                    if metadata.get("status") == "committed":
                        if metadata.get("response_fingerprint") != response_fingerprint:
                            raise TaskConflictError(f"capture_id already has a different response: {capture_id}")
                        return False
                    if task.status is not TaskStatus.ACTIVE:
                        raise TaskConflictError(f"RL Task is not active: {rl_task_id}")

                    metadata["status"] = "committed"
                    metadata["response_fingerprint"] = response_fingerprint
                    pipe.multi()
                    pipe.set(capture_key, json.dumps(metadata, sort_keys=True))
                    pipe.set(self._pending_sample_key(rl_task_id, capture_id), json.dumps(sample, ensure_ascii=False))
                    pipe.srem(self._open_capture_ids_key(rl_task_id), capture_id)
                    pipe.sadd(self._committed_capture_ids_key(rl_task_id), capture_id)
                    if agent_turn_id is not None:
                        pipe.sadd(self._turn_capture_ids_key(rl_task_id, agent_turn_id), capture_id)
                    await pipe.execute()
                    return True
                except WatchError:
                    continue

    async def _open_capture_count(self, rl_task_id: str) -> int:
        return int(await self._redis.scard(self._open_capture_ids_key(rl_task_id)) or 0)

    async def _pending_samples(self, rl_task_id: str, capture_ids: list[str] | None = None) -> list[dict[str, Any]]:
        if capture_ids is None:
            values = await self._redis.smembers(self._committed_capture_ids_key(rl_task_id))
            capture_ids = sorted(map(self._text, values))
        if not capture_ids:
            return []
        rows = await self._redis.mget([self._pending_sample_key(rl_task_id, capture_id) for capture_id in capture_ids])
        return [self._json(row) for row in rows if row is not None]

    async def _claim_terminal_reward(self, rl_task_id: str, reward: float) -> tuple[list[dict[str, Any]], int, bool]:
        task = await self.get(rl_task_id)
        if task is None:
            raise TaskNotFoundError(f"unknown RL Task: {rl_task_id}")
        if task.reward_mode is not RewardMode.TERMINAL:
            raise TaskConflictError("terminal reward is not valid for delayed_feedback Task")
        if task.status is not TaskStatus.FINALIZED:
            raise TaskConflictError("terminal reward requires a finalized Task")
        values = await self._redis.smembers(self._committed_capture_ids_key(rl_task_id))
        capture_ids = sorted(map(self._text, values))
        if not capture_ids:
            raise TaskConflictError("terminal reward requires at least one capture")

        state_key = self._reward_key(rl_task_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(state_key)
                    state_payload = await pipe.get(state_key)
                    if state_payload is not None:
                        state = self._json(state_payload)
                        if float(state["reward"]) != reward:
                            raise TaskConflictError("RL Task already has a different terminal reward")
                        if state.get("status") == "published":
                            return [], int(state["sample_count"]), True
                    else:
                        state = {"reward": reward, "status": "projecting", "sample_count": len(capture_ids)}
                        pipe.multi()
                        pipe.set(state_key, json.dumps(state, sort_keys=True))
                        await pipe.execute()
                    return await self._pending_samples(rl_task_id, capture_ids), len(capture_ids), False
                except WatchError:
                    continue

    async def _complete_terminal_reward(self, rl_task_id: str, reward: float, sample_count: int) -> None:
        state_key = self._reward_key(rl_task_id)
        state = {"reward": reward, "status": "published", "sample_count": sample_count}
        capture_ids = await self._redis.smembers(self._committed_capture_ids_key(rl_task_id))
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(state_key, json.dumps(state, sort_keys=True))
        for capture_id in capture_ids:
            pipe.delete(self._pending_sample_key(rl_task_id, self._text(capture_id)))
        await pipe.execute()

    async def _finish(
        self,
        rl_task_id: str,
        status: TaskStatus,
        reason: FinishReason,
    ) -> TaskRecord:
        if not isinstance(reason, FinishReason):
            reason = FinishReason(reason)
        task_key = self._task_key(rl_task_id)
        capture_ids_key = self._capture_ids_key(rl_task_id)
        open_captures_key = self._open_capture_ids_key(rl_task_id)
        committed_captures_key = self._committed_capture_ids_key(rl_task_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(task_key, open_captures_key, committed_captures_key)
                    payload = await pipe.get(task_key)
                    if payload is None:
                        raise TaskNotFoundError(f"unknown RL Task: {rl_task_id}")
                    record = TaskRecord.from_json(payload)
                    if record.status is not TaskStatus.ACTIVE:
                        return record
                    if status is TaskStatus.FINALIZED and await pipe.scard(open_captures_key):
                        raise TaskConflictError("RL Task still has open captures")

                    active_key = self._active_key(record.agent_session_id)
                    await pipe.watch(active_key)
                    active_id = await pipe.get(active_key)
                    unpublished_ids = (
                        await pipe.smembers(committed_captures_key) if status is TaskStatus.ABORTED else ()
                    )
                    open_capture_ids = await pipe.smembers(open_captures_key) if status is TaskStatus.ABORTED else ()
                    finished = replace(
                        record,
                        status=status,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        finish_reason=reason,
                    )
                    pipe.multi()
                    pipe.set(task_key, json.dumps(finished.to_dict(), sort_keys=True))
                    if active_id is not None and self._text(active_id) == rl_task_id:
                        pipe.delete(active_key)
                    pipe.srem(_ACTIVE_TASKS_KEY, rl_task_id)
                    for capture_id in unpublished_ids:
                        pipe.delete(self._pending_sample_key(rl_task_id, self._text(capture_id)))
                    for capture_id in open_capture_ids:
                        pipe.delete(self._capture_key(rl_task_id, self._text(capture_id)))
                    if open_capture_ids:
                        pipe.srem(capture_ids_key, *open_capture_ids)
                    if status is TaskStatus.ABORTED:
                        pipe.delete(open_captures_key)
                    await pipe.execute()
                    return finished
                except WatchError:
                    continue

    @staticmethod
    def _matches_spec(record: TaskRecord, spec: TaskSpec) -> bool:
        return (
            record.rl_task_id == spec.rl_task_id
            and record.agent_session_id == spec.agent_session_id
            and record.model_id == spec.model_id
            and record.policy_lora_name == spec.policy_lora_name
            and record.policy_model == spec.policy_model
            and record.reward_mode is spec.reward_mode
        )

    @staticmethod
    def _task_key(rl_task_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}"

    @staticmethod
    def _active_key(agent_session_id: str) -> str:
        return f"{_KEY_PREFIX}:active_session:{agent_session_id}"

    @staticmethod
    def _capture_key(rl_task_id: str, capture_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:capture:{capture_id}"

    @staticmethod
    def _capture_ids_key(rl_task_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:captures"

    @staticmethod
    def _open_capture_ids_key(rl_task_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:captures:open"

    @staticmethod
    def _committed_capture_ids_key(rl_task_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:captures:committed"

    @staticmethod
    def _turn_capture_ids_key(rl_task_id: str, agent_turn_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:turn:{agent_turn_id}:captures"

    @staticmethod
    def _current_turn_key(rl_task_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:turn:current"

    @staticmethod
    def _turn_transition_key(rl_task_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:turn:transition"

    @staticmethod
    def _closed_turns_key(rl_task_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:turns:closed"

    @staticmethod
    def _turn_publish_key(rl_task_id: str, agent_turn_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:turn:{agent_turn_id}:publish"

    @staticmethod
    def _pending_sample_key(rl_task_id: str, capture_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:pending:{capture_id}"

    @staticmethod
    def _reward_key(rl_task_id: str) -> str:
        return f"{_KEY_PREFIX}:task:{rl_task_id}:reward"

    @staticmethod
    def _text(value: str | bytes) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    @staticmethod
    def _json(value: str | bytes) -> dict[str, Any]:
        if isinstance(value, bytes):
            value = value.decode()
        return json.loads(value)


__all__ = [
    "FinishReason",
    "RewardMode",
    "TaskConflictError",
    "TaskNotFoundError",
    "TaskRecord",
    "TaskRegistry",
    "TaskSpec",
    "TaskStartResult",
    "TaskStatus",
    "TurnClosedError",
]
