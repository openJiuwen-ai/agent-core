# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Storage protocols used by online gateway and scheduler flows."""

from __future__ import annotations

from typing import Any, Protocol


class TrajectorySampleStore(Protocol):
    """Stateful queue for scored RL samples waiting for training."""

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        """Save a sample as pending for ``user_id``."""

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


class SFTSampleStore(Protocol):
    """Queue store for ``sft-raw-v1`` inputs and ``sft-sample-v1`` training data."""

    async def save_raw(self, raw: dict[str, Any], *, user_id: str = "online") -> None:
        """Save a raw trajectory as pending rollout input."""

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        """Save an SFT sample as pending training input."""

    async def get_pending_raw_count(self, user_id: str) -> int:
        """Return pending raw trajectory count for ``user_id``."""

    async def get_pending_sample_count(self, user_id: str) -> int:
        """Return pending SFT sample count for ``user_id``."""

    async def get_raw_users_above_threshold(self, threshold: int) -> list[str]:
        """Return users whose pending raw trajectory count reaches ``threshold``."""

    async def get_sample_users_above_threshold(self, threshold: int) -> list[str]:
        """Return users whose pending SFT sample count reaches ``threshold``."""

    async def fetch_raw_and_mark_processing(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Move pending raw trajectories to processing and return them."""

    async def fetch_samples_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Move pending SFT samples to training and return them."""

    async def mark_raw_processed(self, raw_ids: list[str]) -> None:
        """Mark raw trajectories as processed."""

    async def mark_raw_failed(self, raw_ids: list[str]) -> None:
        """Mark raw trajectories as failed."""

    async def mark_samples_trained(self, sample_ids: list[str]) -> None:
        """Mark SFT samples as trained."""

    async def mark_samples_failed(self, sample_ids: list[str]) -> None:
        """Mark SFT samples as failed."""

    async def stats(self) -> dict[str, int]:
        """Return aggregate raw/sample counters."""


__all__ = ["SFTSampleStore", "TrajectorySampleStore"]
