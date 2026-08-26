# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Trainer interfaces for backend-specific LoRA training executors."""

from __future__ import annotations

from typing import Any, Optional, Protocol


class TrainingExecutor(Protocol):
    """Common async lifecycle expected from online training executors."""

    async def aclose(self) -> None:
        """Release executor resources."""

    def request_stop(self) -> dict[str, object]:
        """Request the active training job to stop."""


class BatchTrainingExecutor(TrainingExecutor, Protocol):
    """Executor capable of training one batch of normalized samples."""

    async def train_batch(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        training_count: int,
        tmp_root: str,
    ) -> Optional[str]:
        """Train one batch and return the published LoRA path when available."""
