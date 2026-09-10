# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Framework preset and host-injected factory for on-demand Summary Teams.

The organization runtime never builds Summary Team specs or scans packages.
``SummaryTeamSpec`` is the single framework preset; ``SummaryTeamFactory`` is a
host-injected provider that turns that preset into a running, task-specific Team.
The Task Pool and Runtime drive provisioning/release through the factory contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from openjiuwen.agent_teams.organization.schema import ORG_SUMMARY_CAPABILITY, ORG_SUMMARY_TASK_TYPE

_SUMMARY_DEFAULT_MODEL_POLICY: dict[str, Any] = {"strategy": "default"}


@dataclass(frozen=True)
class SummaryTeamSpec:
    """Framework preset for the on-demand Summary Team (not user-configurable)."""

    name: str = "summary-team-preset"
    display_name: str = "Summary Team"
    capabilities: tuple[str, ...] = (ORG_SUMMARY_CAPABILITY,)
    tool_set: tuple[str, ...] = ()
    prompt: str = (
        f"You are the Summary Team for organization task {ORG_SUMMARY_TASK_TYPE!r}. "
        "Integrate the bound source-task outputs into one final result. "
        "If content is missing or conflicts, create supplementary tasks with the "
        "organization task tools instead of fabricating output."
    )
    model_policy: dict[str, Any] = field(default_factory=lambda: dict(_SUMMARY_DEFAULT_MODEL_POLICY))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        data["tool_set"] = list(self.tool_set)
        return data


@dataclass(frozen=True)
class LaunchedSummaryTeam:
    """Result of provisioning one task-specific Summary Team instance."""

    team_id: str
    leader_id: str
    root_task_id: str
    summary_task_id: str
    spec: SummaryTeamSpec

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "leader_id": self.leader_id,
            "root_task_id": self.root_task_id,
            "summary_task_id": self.summary_task_id,
            "spec": self.spec.to_dict(),
        }


class SummaryTeamFactory(Protocol):
    """Host-injected adapter that creates, recreates, and releases task-specific Summary Teams."""

    def default_spec(self) -> SummaryTeamSpec:
        """Return the framework preset Summary Team spec."""

    async def provision(
        self,
        *,
        organization_id: str,
        root_task_id: str,
        summary_task_id: str,
        session_id: str,
    ) -> LaunchedSummaryTeam:
        """Create and start a Summary Team; raise on failure."""

    async def recover(
        self,
        *,
        execution_id: str,
        organization_id: str,
        root_task_id: str,
        summary_task_id: str,
        session_id: str,
    ) -> LaunchedSummaryTeam:
        """Re-attach or recreate a Summary Team for an interrupted execution (§8).

        Called after a process restart for a SummaryExecution that never bound a
        ``summary_team_id``.  The host decides whether the previously started Team
        still exists (reuse its id) or must be launched fresh; it must return the
        team that should now back the execution.
        """

    async def release(
        self,
        *,
        execution_id: str,
        summary_team_id: str,
        session_id: str,
    ) -> None:
        """Stop and reclaim a previously provisioned Summary Team instance.

        ``summary_team_id`` is the Team to stop (the id returned by ``provision``
        / ``recover``).  ``execution_id`` identifies the ``SummaryExecution`` row
        being released, for hosts that want to correlate the stop with their own
        bookkeeping.
        """


__all__ = [
    "LaunchedSummaryTeam",
    "SummaryTeamFactory",
    "SummaryTeamSpec",
]
