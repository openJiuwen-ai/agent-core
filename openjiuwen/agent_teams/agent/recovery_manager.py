# coding: utf-8
"""Fault tolerance and team recovery for TeamAgent."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
)

from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import Session as AgentTeamSession

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
    from openjiuwen.agent_teams.agent.session_manager import SessionManager
    from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager


class RecoveryManager:
    """Handles fault tolerance and team recovery.

    Responsibilities:
    - Team recovery from database state
    - Member context rebuilding
    - Allocator state persistence
    - Leader configuration persistence
    """

    def __init__(
        self,
        configurator: AgentConfigurator,
        spawn_manager: SpawnManager,
    ):
        self._configurator = configurator
        self._spawn_manager = spawn_manager

    async def recover_team(self) -> list[str]:
        team_backend = self._configurator.team_backend
        if not team_backend:
            return []

        member_name = self._configurator.member_name
        team_logger.info("[{}] recovering team", member_name or "?")
        all_members = await team_backend.list_members()
        restarted: list[str] = []

        for member in all_members:
            if member.member_name == member_name:
                continue
            team_name = self._configurator.team_name
            if team_name:
                await team_backend.db.update_member_status(
                    member.member_name,
                    team_name,
                    MemberStatus.RESTARTING.value,
                )
            if await self._spawn_manager.restart_teammate(member.member_name):
                restarted.append(member.member_name)

        return restarted

    def persist_leader_config(self, session) -> None:
        spec = self._configurator.spec
        ctx = self._configurator.ctx
        if spec is None or ctx is None:
            return

        payload: dict[str, Any] = {
            "spec": spec.model_dump(mode="json"),
            "context": ctx.model_dump(mode="json"),
            "team_name": self._configurator.team_name,
        }
        allocator = self._configurator.model_allocator
        if allocator is not None:
            payload["model_allocator_state"] = allocator.state_dict()
        session.update_state(payload)

    async def _mark_teammate_restarting_for_session_switch(
        self,
        member_name: str,
        current_status: MemberStatus,
    ) -> bool:
        """Move a live teammate into a restartable status for session rebind.

        Session switching must only tear down the live runtime and re-spawn it
        under the new session; team rows and old session data stay intact.
        ``READY``/``BUSY`` cannot transition directly to ``RESTARTING``, so we
        normalize through ``ERROR`` first when needed.
        """
        team_backend = self._configurator.team_backend
        if not team_backend:
            return False

        team_name = self._configurator.team_name
        if team_name is None:
            return False

        db = team_backend.db

        if current_status == MemberStatus.RESTARTING:
            return True

        if current_status not in {MemberStatus.ERROR, MemberStatus.SHUTDOWN}:
            updated = await db.member.update_member_status(
                member_name,
                team_name,
                MemberStatus.ERROR.value,
            )
            if not updated:
                team_logger.warning(
                    "Failed to move teammate {} from {} to ERROR before session rebind",
                    member_name,
                    current_status.value,
                )
                return False
            current_status = MemberStatus.ERROR

        if current_status == MemberStatus.RESTARTING:
            return True

        updated = await db.member.update_member_status(
            member_name,
            team_name,
            MemberStatus.RESTARTING.value,
        )
        if not updated:
            team_logger.warning(
                "Failed to move teammate {} into RESTARTING during session rebind",
                member_name,
            )
            return False
        return True

    async def collect_live_teammates_for_session_switch(self) -> list[tuple[str, MemberStatus]]:
        """Snapshot live teammates that need rebinding when the session switches.

        Returns the (member_name, current_status) pairs of teammates whose
        process handles are still alive and whose DB status is not
        UNSTARTED/SHUTDOWN. The leader is excluded.
        """
        team_backend = self._configurator.team_backend
        if self._configurator.role != TeamRole.LEADER or not team_backend:
            return []

        members = await team_backend.list_members()
        leader_member_name = self._configurator.member_name
        spawned = self._spawn_manager.spawned_handles
        live_teammates = {
            member_name
            for member_name, handle in spawned.items()
            if member_name != leader_member_name and handle is not None
        }
        result: list[tuple[str, MemberStatus]] = []
        for member in members:
            if member.member_name not in live_teammates:
                continue
            member_status = MemberStatus(member.status)
            if member_status in {MemberStatus.UNSTARTED, MemberStatus.SHUTDOWN}:
                continue
            result.append((member.member_name, member_status))
        return result

    async def restart_for_session_switch(
        self,
        recoverable_members: list[tuple[str, MemberStatus]],
        *,
        cleanup_first: bool,
    ) -> None:
        """Restart the given teammates after a session switch.

        ``cleanup_first=True`` tears down the old handles before restart;
        ``False`` assumes coordination teardown already cleared them.
        """
        for member_name, member_status in recoverable_members:
            if cleanup_first:
                await self._spawn_manager.cleanup_teammate(member_name)
            if not await self._mark_teammate_restarting_for_session_switch(
                member_name,
                member_status,
            ):
                continue
            await self._spawn_manager.restart_teammate(member_name)

    def persist_allocator_state(self, team_session) -> None:
        allocator = self._configurator.model_allocator
        if team_session is None or allocator is None:
            return
        try:
            team_session.update_state(
                {"model_allocator_state": allocator.state_dict()},
            )
        except Exception as e:
            team_logger.error(
                "[{}] failed to persist allocator state: {}",
                self._configurator.member_name or "?",
                e,
            )
