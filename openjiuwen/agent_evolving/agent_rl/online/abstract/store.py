# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Storage protocols used by online gateway and scheduler flows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class TrajectorySampleStore(Protocol):
    """Stateful queue for scored RL samples waiting for training."""

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        """Save a sample as pending for ``user_id``."""

    async def save_samples_once(
        self,
        samples: Sequence[dict[str, Any]],
        *,
        user_id: str = "online",
    ) -> set[str]:
        """Atomically publish samples not already present and return their IDs."""

    async def get_pending_count(self, user_id: str) -> int:
        """Return pending sample count for ``user_id``."""

    async def get_users_above_threshold(self, threshold: int) -> list[str]:
        """Return users whose pending sample count reaches ``threshold``."""

    async def fetch_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Atomically move pending samples to training and return them."""

    async def mark_trained(self, sample_ids: list[str]) -> None:
        """Mark training samples as trained."""

    async def mark_failed(self, sample_ids: list[str]) -> None:
        """Mark training samples as failed."""

    async def reset_to_pending(self, sample_ids: list[str]) -> None:
        """Move training samples back to pending."""

    async def stats(self) -> dict[str, int]:
        """Return store counters."""

    async def delete_sample(self, sample_id: str, *, force: bool = False) -> bool:
        """Delete a sample and return whether it existed."""


__all__ = ["TrajectorySampleStore"]
