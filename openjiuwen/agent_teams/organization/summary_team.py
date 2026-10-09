# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host contract for the organization-wide reusable Summary Team."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LaunchedSummaryTeam:
    """Identity returned after the host has started a Summary Team."""

    team_id: str
    leader_id: str


class SummaryTeamLauncher(Protocol):
    """Host-owned launcher for the one shared Summary Team in an organization."""

    async def launch(
        self,
        *,
        organization_id: str,
        session_id: str,
        share_db_from_team_id: str,
    ) -> LaunchedSummaryTeam:
        """Start or recover the stable Summary Team using the owner's shared database."""

    async def stop(self, *, team_id: str, session_id: str) -> None:
        """Stop a Summary Team when organization teardown rolls it back or closes it."""


__all__ = ["LaunchedSummaryTeam", "SummaryTeamLauncher"]
