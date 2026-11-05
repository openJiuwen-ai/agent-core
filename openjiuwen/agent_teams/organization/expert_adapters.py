# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-injected launcher for on-demand expert teams.

Organization runtime never scans AgentGroup packages or builds Team specs.
Hosts inject the package locator through ExpertCatalog and a concrete Launcher here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LaunchedExpertTeam:
    """Result of launching one expert Team from an AgentGroup package."""

    team_id: str
    leader_id: str
    capabilities: tuple[str, ...] = ()
    agent_group_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        return data


class ExpertTeamLauncher(Protocol):
    """Build, activate, and roll back an expert Team for organization invite."""

    async def launch(
        self,
        *,
        organization_id: str,
        agent_group_name: str,
        session_id: str,
        display_name: str | None = None,
        share_db_from_team_id: str | None = None,
    ) -> LaunchedExpertTeam:
        """Activate a new Team from an AgentGroup; team_id must be unique.

        ``share_db_from_team_id`` asks the host to reuse that team's TeamDatabase
        instance so ``invite_team`` can bind the new Team into the organization.
        """

    async def stop(self, *, team_id: str, session_id: str) -> None:
        """Stop a temporary Team after a failed launch/invite or organization dissolution."""


__all__ = [
    "ExpertTeamLauncher",
    "LaunchedExpertTeam",
]
