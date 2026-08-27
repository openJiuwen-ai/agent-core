# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-injected adapters for on-demand expert-group discovery and launch.

Organization runtime never scans AgentGroup packages or builds Team specs.
Hosts (for example JiuwenSwarm) inject concrete Catalog / Launcher implementations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExpertGroupDescriptor:
    """Validated AgentGroup template metadata (not a running Team)."""

    agent_group_name: str
    display_name: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        return data


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


class ExpertGroupCatalog(Protocol):
    """Discover and describe AgentGroup packages without creating Teams."""

    def list(
        self, *, capabilities: set[str] | None = None
    ) -> list[ExpertGroupDescriptor]:
        """Return validated expert-group descriptors; never create a Team."""

    def get(self, name: str) -> ExpertGroupDescriptor:
        """Return one validated descriptor or raise ValueError."""


class ExpertTeamLauncher(Protocol):
    """Build, activate, and roll back an expert Team for organization invite."""

    async def launch(
        self,
        *,
        organization_id: str,
        agent_group_name: str,
        session_id: str,
        display_name: str | None = None,
    ) -> LaunchedExpertTeam:
        """Activate a new Team from an AgentGroup; team_id must be unique."""

    async def stop(self, *, team_id: str, session_id: str) -> None:
        """Stop a temporary Team after a failed invite or aborted launch."""


__all__ = [
    "ExpertGroupCatalog",
    "ExpertGroupDescriptor",
    "ExpertTeamLauncher",
    "LaunchedExpertTeam",
]
