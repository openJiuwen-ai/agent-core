# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""LoRA repository protocols used by online core code."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class LoRAVersionLike(Protocol):
    """Read-only shape returned by LoRA repository implementations."""

    user_id: str
    version: str
    path: str
    created_at: datetime
    trajectory_count: int
    reward_avg: float
    base_model: str
    parent_lora_id: str
    parent_lora_version: str
    parent_lora_path: str
    availability_status: str
    availability_reason: str
    availability_checked_at: datetime | None
    training_source: str


class LoRARepositoryProtocol(Protocol):
    """Repository operations required by online gateway and trainer code."""

    def publish(self, request: Any, *args: Any, **kwargs: Any) -> LoRAVersionLike:
        """Publish one LoRA artifact and return its version metadata."""

    def get_latest(self, user_id: str) -> LoRAVersionLike | None:
        """Return the latest LoRA version for one user/model."""

    def get_latest_available(self, user_id: str) -> LoRAVersionLike | None:
        """Return the latest LoRA version confirmed available by inference."""

    def list_versions(self, user_id: str) -> list[LoRAVersionLike]:
        """Return all published versions for one user/model."""

    def get_version(self, user_id: str, version: str) -> LoRAVersionLike | None:
        """Return one published LoRA version."""

    def list_users(self) -> list[str]:
        """Return users/models with published LoRA versions."""

    def set_latest(self, user_id: str, version: str) -> LoRAVersionLike:
        """Set one version as latest."""

    def set_availability(
        self,
        user_id: str,
        version: str,
        *,
        available: bool,
        reason: str = "",
        checked_at: datetime | None = None,
    ) -> LoRAVersionLike:
        """Update inference availability state for one version."""


__all__ = ["LoRARepositoryProtocol", "LoRAVersionLike"]
