# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workflow contracts for RL/SFT scheduler backends."""

from __future__ import annotations

from typing import Any, Protocol


class OnlineTrainingWorkflow(Protocol):
    """Backend-specific workflow owned by the scheduler core."""

    async def collect_trainable_users(self) -> list[str]:
        """Return users with pending work for this backend."""

    async def run_user_workflow(
        self,
        *,
        user_id: str,
        task_id: str = "",
        finalize_task: bool = True,
    ) -> bool:
        """Run one user's trainable workflow."""

    def request_stop(self) -> dict[str, Any]:
        """Request the active backend workflow to stop."""
