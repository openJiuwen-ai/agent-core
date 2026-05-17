# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared contracts for evolution apply behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Mapping

from openjiuwen.agent_evolving.trajectory.types import UpdateKey, Updates

from openjiuwen.agent_evolving.protocols import (
    APPEND_MODE,
    EXPERIENCES_TARGET,
    PENDING_CHANGE_EFFECT,
    REPLACE_MODE,
    SKILL_EXPERIENCE_ENTRY,
    STATE_EFFECT,
)

UpdateMode = Literal["replace", "append", "merge"]
UpdateEffect = Literal["state", "pending_change"]


@dataclass(frozen=True)
class UpdateValue:
    """Structured update contract shared by online and offline apply paths."""

    payload: Any
    mode: UpdateMode = REPLACE_MODE
    effect: UpdateEffect = STATE_EFFECT
    change_type: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ApplyResult:
    """Result of applying one normalized update to one evolution target."""

    operator_id: str
    target: str
    applied: bool
    mode: UpdateMode = REPLACE_MODE
    effect: UpdateEffect = STATE_EFFECT
    value: Any = None
    records: list[Any] = field(default_factory=list)
    change_type: str | None = None
    lifecycle_stage: Literal["local_apply_completed"] | None = None
    pending_change_id: str | None = None
    errors: list[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.applied and not self.errors


def normalize_update_value(value: Any, *, target: str | None = None) -> UpdateValue:
    """Wrap legacy updates into the new structured contract.

    Compatibility rules:
    - Structured ``UpdateValue`` passes through unchanged
    - Legacy ``experiences`` payloads normalize to append + pending_change
    - All other legacy values normalize to replace + state
    """

    if isinstance(value, UpdateValue):
        return value
    if target == EXPERIENCES_TARGET:
        return UpdateValue(
            payload=value,
            mode=APPEND_MODE,
            effect=PENDING_CHANGE_EFFECT,
            change_type=SKILL_EXPERIENCE_ENTRY,
            metadata={"change_type": SKILL_EXPERIENCE_ENTRY},
        )
    return UpdateValue(payload=value)


def normalize_updates(updates: Mapping[tuple[str, str], Any]) -> Dict[tuple[str, str], UpdateValue]:
    """Normalize mixed legacy/structured updates for future apply-core use."""

    return {key: normalize_update_value(value, target=key[1]) for key, value in updates.items()}


__all__ = [
    "ApplyResult",
    "UpdateKey",
    "UpdateEffect",
    "UpdateMode",
    "Updates",
    "UpdateValue",
    "normalize_update_value",
    "normalize_updates",
]
