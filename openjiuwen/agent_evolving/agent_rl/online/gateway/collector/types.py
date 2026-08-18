# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public identity and lifecycle types for gateway trajectory collection."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.task_reward import TaskReward


class _CopyableCodedError:
    """Preserve coded exception constructor arguments when logging copies them."""

    code: Any

    def __reduce__(self) -> tuple[type[Any], tuple[Any, str]]:
        return type(self), (self.code, str(self))


class CollectionMode(str, Enum):
    """Collection mechanism accepted by the public session request."""

    RAIL = "rail"
    GATEWAY = "gateway"


class RewardMode(str, Enum):
    """Reward timing fixed for one collection session."""

    DELAYED_FEEDBACK = "delayed_feedback"
    TERMINAL_TASK = "terminal_task"


class CollectionSessionStatus(str, Enum):
    """Complete persisted lifecycle state of a collection session."""

    ACTIVE = "active"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"
    ABORTED = "aborted"


class CollectionSessionErrorCode(str, Enum):
    """Stable categories for collection-session lifecycle failures."""

    DUPLICATE_CREATE = "duplicate_create"
    UNKNOWN_SESSION = "unknown_session"
    SESSION_TERMINAL = "session_terminal"
    DUPLICATE_FINALIZE = "duplicate_finalize"
    FINALIZE_AFTER_ABORT = "finalize_after_abort"
    DUPLICATE_ABORT = "duplicate_abort"
    ABORT_AFTER_FINALIZE = "abort_after_finalize"
    INVALID_REWARD_MODE = "invalid_reward_mode"
    SESSION_NOT_FINALIZED = "session_not_finalized"
    PERSISTENCE_FAILURE = "persistence_failure"


class CollectionSessionError(_CopyableCodedError, RuntimeError):
    """Lifecycle error carrying a stable adapter-facing category."""

    def __init__(self, code: CollectionSessionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CollectionSessionSpec:
    """Immutable collection and model identity fixed before an online run."""

    session_id: str
    model_id: str
    tokenizer_revision: str
    template_revision: str
    collection_mode: CollectionMode = CollectionMode.GATEWAY
    reward_mode: RewardMode = RewardMode.DELAYED_FEEDBACK

    def __post_init__(self) -> None:
        for field_name, value in asdict(self).items():
            if not str(value).strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.collection_mode, CollectionMode):
            object.__setattr__(self, "collection_mode", CollectionMode(self.collection_mode))
        if not isinstance(self.reward_mode, RewardMode):
            object.__setattr__(self, "reward_mode", RewardMode(self.reward_mode))
        if self.collection_mode is not CollectionMode.GATEWAY:
            raise ValueError("collection sessions require collection_mode=gateway")


@dataclass(frozen=True, slots=True)
class CollectionSessionRecord:
    """Valid durable state for one collection session."""

    spec: CollectionSessionSpec
    status: CollectionSessionStatus = CollectionSessionStatus.ACTIVE

    def __post_init__(self) -> None:
        if not isinstance(self.status, CollectionSessionStatus):
            object.__setattr__(self, "status", CollectionSessionStatus(self.status))
        if (
            self.status is CollectionSessionStatus.FINALIZING
            and self.spec.reward_mode is not RewardMode.DELAYED_FEEDBACK
        ):
            raise ValueError("only delayed-feedback sessions can be finalizing")

    @property
    def accepts_captures(self) -> bool:
        return self.status is CollectionSessionStatus.ACTIVE

    def require_active(self) -> None:
        if not self.accepts_captures:
            raise CollectionSessionError(
                CollectionSessionErrorCode.SESSION_TERMINAL,
                f"collection session is not active: {self.spec.session_id}",
            )

    def begin_finalize(self) -> "CollectionSessionRecord":
        if self.status is CollectionSessionStatus.ABORTED:
            raise CollectionSessionError(
                CollectionSessionErrorCode.FINALIZE_AFTER_ABORT,
                f"collection session already aborted: {self.spec.session_id}",
            )
        if self.status is CollectionSessionStatus.FINALIZED:
            raise CollectionSessionError(
                CollectionSessionErrorCode.DUPLICATE_FINALIZE,
                f"collection session already finalized: {self.spec.session_id}",
            )
        if self.status is CollectionSessionStatus.FINALIZING:
            return self
        target = (
            CollectionSessionStatus.FINALIZING
            if self.spec.reward_mode is RewardMode.DELAYED_FEEDBACK
            else CollectionSessionStatus.FINALIZED
        )
        return replace(self, status=target)

    def complete_finalize(self) -> "CollectionSessionRecord":
        if self.status is not CollectionSessionStatus.FINALIZING:
            raise ValueError(f"collection session is not finalizing: {self.spec.session_id}")
        return replace(self, status=CollectionSessionStatus.FINALIZED)

    def abort(self) -> "CollectionSessionRecord":
        if self.status in (CollectionSessionStatus.FINALIZING, CollectionSessionStatus.FINALIZED):
            raise CollectionSessionError(
                CollectionSessionErrorCode.ABORT_AFTER_FINALIZE,
                f"collection session already finalized: {self.spec.session_id}",
            )
        if self.status is CollectionSessionStatus.ABORTED:
            raise CollectionSessionError(
                CollectionSessionErrorCode.DUPLICATE_ABORT,
                f"collection session already aborted: {self.spec.session_id}",
            )
        return replace(self, status=CollectionSessionStatus.ABORTED)

    def require_task_reward(self) -> None:
        if self.spec.reward_mode is not RewardMode.TERMINAL_TASK:
            raise CollectionSessionError(
                CollectionSessionErrorCode.INVALID_REWARD_MODE,
                "collection session does not accept terminal task rewards",
            )
        if self.status is not CollectionSessionStatus.FINALIZED:
            raise CollectionSessionError(
                CollectionSessionErrorCode.SESSION_NOT_FINALIZED,
                "collection session is not finalized",
            )

    def to_json(self) -> str:
        """Serialize the public transport shape with legacy derived fields."""
        payload = self._payload()
        payload.update(self._legacy_state_fields())
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def to_storage_json(self) -> str:
        """Serialize the canonical persisted state without redundant fields."""
        return json.dumps(self._payload(), ensure_ascii=False, sort_keys=True)

    def _payload(self) -> dict[str, Any]:
        payload = asdict(self.spec)
        payload["status"] = self.status.value
        return payload

    def _legacy_state_fields(self) -> dict[str, Any]:
        if self.status is CollectionSessionStatus.ACTIVE:
            return {
                "phase": "active",
                "terminal_condition": None,
                "terminal_effects_completed": False,
            }
        if self.status is CollectionSessionStatus.ABORTED:
            return {
                "phase": "terminal",
                "terminal_condition": "aborted",
                "terminal_effects_completed": True,
            }
        return {
            "phase": "terminal",
            "terminal_condition": "finalized",
            "terminal_effects_completed": self.status is CollectionSessionStatus.FINALIZED,
        }

    @classmethod
    def from_json(cls, payload: str | bytes) -> "CollectionSessionRecord":
        """Deserialize a lightweight Redis record."""
        if isinstance(payload, bytes):
            payload = payload.decode()
        values = json.loads(payload)
        if values.get("collection_mode", "gateway") != "gateway":
            raise ValueError("collection sessions require collection_mode=gateway")
        spec = CollectionSessionSpec(
            session_id=values["session_id"],
            collection_mode=CollectionMode(values.get("collection_mode", "gateway")),
            model_id=values["model_id"],
            tokenizer_revision=values["tokenizer_revision"],
            template_revision=values["template_revision"],
            reward_mode=RewardMode(values.get("reward_mode", RewardMode.DELAYED_FEEDBACK.value)),
        )
        return cls(spec=spec, status=cls._status_from_payload(values))

    @staticmethod
    def _status_from_payload(values: dict[str, Any]) -> CollectionSessionStatus:
        status = values.get("status")
        if status is not None:
            return CollectionSessionStatus(status)

        # Read sessions persisted before the state model was collapsed to one field.
        phase = values.get("phase")
        terminal = values.get("terminal_condition")
        effects_completed = bool(values.get("terminal_effects_completed", True))
        if phase == "active" and terminal is None:
            return CollectionSessionStatus.ACTIVE
        if phase == "terminal" and terminal == "aborted" and effects_completed:
            return CollectionSessionStatus.ABORTED
        if phase == "terminal" and terminal == "finalized":
            return (
                CollectionSessionStatus.FINALIZED
                if effects_completed
                else CollectionSessionStatus.FINALIZING
            )
        raise ValueError("invalid persisted collection session state")


class CollectionSessionManager(Protocol):
    """Own collection-session lifecycle and terminal reward submission."""

    async def create_session(self, spec: CollectionSessionSpec) -> CollectionSessionRecord:
        pass

    async def get_session(self, session_id: str) -> CollectionSessionRecord | None:
        pass

    async def finalize_session(self, session_id: str) -> CollectionSessionRecord:
        pass

    async def abort_session(self, session_id: str) -> CollectionSessionRecord:
        pass

    async def submit_task_reward(self, session_id: str, reward: "TaskReward") -> int:
        pass
