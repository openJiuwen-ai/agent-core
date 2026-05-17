# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session lifecycle and persistence for TeamAgent."""

from __future__ import annotations

from contextvars import Token
from typing import (
    TYPE_CHECKING,
    Optional,
)

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import Session as AgentTeamSession

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
    from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager
    from openjiuwen.agent_teams.agent.state import TeamAgentState


class SessionManager:
    """Manages session lifecycle and persistence.

    The agent_teams session_id contextvar (see ``agent_teams/context.py``)
    is the single source of truth for "current session id". This manager
    owns a ``contextvars.Token`` so every ``set_session_id`` inside
    ``bind_session`` is paired with a ``reset_session_id`` on the matching
    release / unbind path; that contract is what stops a stale contextvar
    value from bleeding into a sibling spawn that inherits the context.
    """

    def __init__(
        self,
        *,
        state: "TeamAgentState",
        configurator: AgentConfigurator,
        recovery_manager: RecoveryManager,
    ):
        self._state = state
        self._configurator = configurator
        self._recovery_manager = recovery_manager
        # Token returned by the last successful ``set_session_id`` call;
        # held so release/unbind can ``reset`` the contextvar to whatever
        # value was in scope before this manager bound the session.
        self._session_id_token: Optional[Token] = None

    @property
    def team_session(self) -> Optional[AgentTeamSession]:
        return self._state.team_session

    @team_session.setter
    def team_session(self, value: Optional[AgentTeamSession]) -> None:
        self._state.team_session = value

    def _reset_session_id_token(self) -> None:
        """Release the contextvar Token, tolerating cross-context reset.

        ``Token.reset`` can only run in the Context that produced it.
        Spawn / recovery flows occasionally bind on one task and tear
        down on another; in that case we drop the Token silently — the
        next ``set_session_id`` overwrites the current Context anyway.
        """
        token = self._session_id_token
        self._session_id_token = None
        if token is None:
            return
        try:
            reset_session_id(token)
        except (ValueError, LookupError) as exc:
            team_logger.debug(
                "session_id contextvar reset skipped (cross-context token): {}",
                exc,
            )

    async def bind_session(self, session: AgentTeamSession) -> None:
        """Full attach to ``session``.

        Wires session_id into the global contextvar, creates per-session
        DB tables, and persists leader config when the team is leader-side.
        The session must be non-None — for the "no session" / tear-down path
        use :meth:`release_session`.

        Rebinding to a different session resets the prior Token first
        so the contextvar stack stays consistent.
        """
        # Reset any previously held token before overwriting; otherwise the
        # release path would only ever clear the first bind, leaving every
        # subsequent rebind permanently on the stack.
        self._reset_session_id_token()

        self._session_id_token = set_session_id(session.get_session_id())
        self._state.team_session = session if isinstance(session, AgentTeamSession) else None

        team_backend = self._configurator.team_backend
        if team_backend:
            await team_backend.db.create_cur_session_tables()

        spec = self._configurator.spec
        if spec and self._configurator.role == TeamRole.LEADER:
            self._recovery_manager.persist_leader_config(session)

    def release_session(self) -> None:
        """Detach from the current session.

        Resets the contextvar Token from ``bind_session`` and drops the
        live ``AgentTeamSession`` so it cannot be mutated after the round
        ends. Single tear-down path: pause / stop / no-session startup
        all converge here — there is nothing left to "preserve across
        the gap" now that session_id lives only in the contextvar.
        """
        self._reset_session_id_token()
        self._state.team_session = None

    async def resume_for_new_session(self, session: AgentTeamSession) -> None:
        """Switch to a new session and rebind live teammate runtimes.

        Persistent teams keep team rows and old session data intact across
        sessions; only the live runtime needs rebinding so it picks up the
        new session_id.
        """
        recoverable_members = await self._recovery_manager.collect_live_teammates_for_session_switch()
        await self.bind_session(session)

        team_backend = self._configurator.team_backend
        if self._configurator.role != TeamRole.LEADER or not team_backend:
            return

        await self._recovery_manager.restart_for_session_switch(
            recoverable_members,
            cleanup_first=True,
        )

    async def recover_for_existing_session(self, session: AgentTeamSession) -> None:
        """Rebind to a checkpoint-restored session without cleanup.

        Caller must have already torn down coordination (which already
        cleared the live handles) and validated the checkpoint.
        """
        recoverable_members = await self._recovery_manager.collect_live_teammates_for_session_switch()
        await self.bind_session(session)

        team_backend = self._configurator.team_backend
        if self._configurator.role != TeamRole.LEADER or not team_backend:
            return

        await self._recovery_manager.restart_for_session_switch(
            recoverable_members,
            cleanup_first=False,
        )
