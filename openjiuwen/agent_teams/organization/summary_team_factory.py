# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-injected callback adapter that turns the Summary Team preset into a Team.

The organization runtime never builds ``TeamAgentSpec`` objects or resolves
model / workspace / storage / transport defaults. ``DefaultSummaryTeamFactory``
implements :class:`~openjiuwen.agent_teams.organization.summary.SummaryTeamFactory`
by delegating the two lifecycle actions to host-supplied callables:

* ``summary_team_builder`` -- build, configure, and start a running Team from a
  :class:`~openjiuwen.agent_teams.organization.summary.SummaryTeamSpec`, then
  return a :class:`~openjiuwen.agent_teams.organization.summary.LaunchedSummaryTeam`.
  The host owns ``TeamRuntimeManager.activate``, ``TeamAgentSpec`` construction,
  and unique ``team_id`` generation; the factory never inspects packages or
  templates.
* ``summary_team_stopper`` -- stop and reclaim the Team that backs a given
  ``SummaryExecution``.

The factory deliberately does not decide summary sources, mutate task state, or
run the aggregation business. Those are owned by the runtime and task pool
(:func:`~openjiuwen.agent_teams.organization.runtime.OrganizationRuntimeManager`
and ``OrgTaskManager``). It is not user-configurable and is never started at
Organization creation; the host injects it through
``OrganizationRuntimeManager.set_summary_team_factory``.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from openjiuwen.agent_teams.organization.summary import (
    LaunchedSummaryTeam,
    SummaryTeamSpec,
)

#: Build-and-start a Team from a Summary Team preset. Host provides the Team
#: runtime activation that produces a unique ``team_id``/``leader_id`` pair.
SummaryTeamBuilder = Callable[
    [SummaryTeamSpec, str, str, str, str],
    Awaitable[LaunchedSummaryTeam],
]

#: Stop and reclaim a previously provisioned Summary Team by execution id.
SummaryTeamStopper = Callable[[str, str], Awaitable[None]]


class DefaultSummaryTeamFactory:
    """Callback-delegating implementation of the Summary Team factory.

    Use it to adapt a host's Team-create/stop primitives onto the framework
    :class:`SummaryTeamFactory` contract without coupling the organization
    subpackage to the team runtime stack.

    Args:
        summary_team_builder: Receives ``(spec, organization_id, root_task_id,
            summary_task_id, session_id)`` and returns the running Team.
        summary_team_stopper: Receives ``(execution_id, session_id)`` and stops /
            reclaims the Team recorded for that execution.
    """

    def __init__(
        self,
        *,
        summary_team_builder: SummaryTeamBuilder,
        summary_team_stopper: SummaryTeamStopper,
    ) -> None:
        self._summary_team_builder = summary_team_builder
        self._summary_team_stopper = summary_team_stopper

    def default_spec(self) -> SummaryTeamSpec:
        """Return the framework preset Summary Team spec."""
        return SummaryTeamSpec()

    async def provision(
        self,
        *,
        organization_id: str,
        root_task_id: str,
        summary_task_id: str,
        session_id: str,
    ) -> LaunchedSummaryTeam:
        """Build and start a task-specific Summary Team via the host builder."""
        spec = self.default_spec()
        return await self._summary_team_builder(
            spec,
            organization_id,
            root_task_id,
            summary_task_id,
            session_id,
        )

    async def release(self, *, execution_id: str, session_id: str) -> None:
        """Stop and reclaim a previously provisioned Summary Team."""
        await self._summary_team_stopper(execution_id, session_id)


__all__ = [
    "DefaultSummaryTeamFactory",
    "SummaryTeamBuilder",
    "SummaryTeamStopper",
]
