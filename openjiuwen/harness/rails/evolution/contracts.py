# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Typed contracts shared by online evolution rails."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, runtime_checkable

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.types import PendingChange
from openjiuwen.agent_evolving.trajectory import Trajectory
from openjiuwen.core.session.stream import OutputSchema

EvolutionEventKind = Literal["approval", "progress", "outcome"]


@dataclass(frozen=True)
class EvolutionHostEventMeta:
    """Canonical metadata carried inside ``OutputSchema.payload['evolution_meta']``."""

    event_kind: EvolutionEventKind
    rail_kind: Optional[str] = None
    stage: Optional[str] = None
    skill_name: Optional[str] = None
    request_id: Optional[str] = None
    signal_type: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None

    def to_payload(self) -> dict[str, str]:
        """Return the legacy JSON payload shape without empty fields."""
        payload: dict[str, str] = {"event_kind": self.event_kind}
        for field_name in (
            "rail_kind",
            "stage",
            "skill_name",
            "request_id",
            "signal_type",
            "source",
            "status",
        ):
            value = getattr(self, field_name)
            if value is not None:
                payload[field_name] = value
        return payload


@dataclass(frozen=True)
class EvolutionSnapshot:
    """Async evolution snapshot captured while callback context is still alive."""

    trajectory: Trajectory
    messages: list[dict]
    skill_name: Optional[str] = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the existing dict shape consumed by rail hooks and tests."""
        snapshot: dict[str, Any] = {
            "trajectory": self.trajectory,
            "messages": self.messages,
        }
        if self.skill_name is not None:
            snapshot["skill_name"] = self.skill_name
        return snapshot

    @classmethod
    def from_legacy_dict(cls, snapshot: dict[str, Any]) -> "EvolutionSnapshot":
        return cls(
            trajectory=snapshot["trajectory"],
            messages=list(snapshot.get("messages", [])),
            skill_name=snapshot.get("skill_name"),
        )


@dataclass(frozen=True)
class EvolutionRequestResult:
    """Structured result returned by active user-triggered evolution APIs."""

    skill_name: str
    request_id: Optional[str] = None
    approval_event: Optional[OutputSchema] = None
    records: list[EvolutionRecord] = field(default_factory=list)
    auto_approved: bool = False
    status: Optional[str] = None
    message: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(self.records or self.approval_event)


@dataclass(frozen=True)
class SimplifyRequestResult:
    """Structured result returned by active simplify request APIs."""

    skill_name: str
    request_id: Optional[str] = None
    approval_event: Optional[OutputSchema] = None
    actions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.actions or self.approval_event)


@runtime_checkable
class ApprovalManagerProtocol(Protocol):
    async def approve_request(
        self,
        request_id: str,
        *,
        approved_record_ids: Optional[list[str]] = None,
    ) -> Any:
        """Persist or apply one staged approval request."""

    async def reject_request(self, request_id: str) -> Any:
        """Reject one staged approval request."""


PendingApprovalSnapshotStore = MutableMapping[str, PendingChange]


__all__ = [
    "ApprovalManagerProtocol",
    "EvolutionHostEventMeta",
    "EvolutionRequestResult",
    "EvolutionSnapshot",
    "PendingApprovalSnapshotStore",
    "SimplifyRequestResult",
]
