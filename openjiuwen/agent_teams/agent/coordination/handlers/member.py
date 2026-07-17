# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Member-lifecycle coordination events.

Handles all six ``MEMBER_*`` events. Leader observes every member's
transitions; a non-leader only reacts to events targeting itself
(``MEMBER_CANCELED`` cancels the local round; a *forced* ``MEMBER_SHUTDOWN``
tears it down immediately). The on-shutdown mailbox drain — and with it the
graceful teardown of every non-leader role — is **not** this handler's
concern: ``MessageHandler`` registers its own ``MEMBER_SHUTDOWN`` callback and
the framework fans out both, so this handler stays scoped to lifecycle state
only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.coordination.event_bus import CoordinationEvent, InnerEventType
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.prompts import (
    TeamSectionName,
    build_team_info_section,
    build_team_members_section,
)
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachment,
    PromptAttachmentManager,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import DispatcherHost, PollController
    from openjiuwen.agent_teams.external.runtime import CliRuntimeBase


class MemberHandler(BaseCoordinationHandler):
    """Handle MEMBER_* lifecycle events.

    Leader: observe all members' transitions for logging.

    Non-leader: only react to events targeting self. ``MEMBER_CANCELED``
    cancels the local agent task. ``MEMBER_SHUTDOWN`` tears the member down
    only when ``force`` is set; a graceful shutdown does **not** teardown here
    and does **not** drain the mailbox here — that's ``MessageHandler``'s
    fan-out callback, registered on the same event_key.

    Stale-claim nudging is **not** this handler's concern: every member
    sweeps its own claimed tasks on ``POLL_TASK`` and self-nudges via
    ``StaleTaskHandler``. The leader no longer reaches across processes
    to nudge another member about its stale claims.
    """

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        InnerEventType.REFRESH_TEAM_CONTEXT.value: "on_refresh_team_context",
        TeamEvent.MEMBER_SPAWNED: "on_member_event",
        TeamEvent.MEMBER_RESTARTED: "on_member_event",
        TeamEvent.MEMBER_STATUS_CHANGED: "on_member_event",
        TeamEvent.MEMBER_EXECUTION_CHANGED: "on_member_event",
        TeamEvent.MEMBER_SHUTDOWN: "on_member_event",
        TeamEvent.MEMBER_CANCELED: "on_member_event",
    }
    TEAM_CONTEXT_EVENTS: ClassVar[frozenset[str]] = frozenset(
        {
            TeamEvent.MEMBER_SPAWNED,
            TeamEvent.MEMBER_SHUTDOWN,
        }
    )

    def __init__(
        self,
        host: "DispatcherHost",
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: "PollController",
    ) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        self._team_clean_requested = False

    async def on_member_event(self, event: EventMessage) -> None:
        """Handle MEMBER_* lifecycle events.

        Teammate: only react to events targeting self (cancel only —
        on-shutdown drain is MessageHandler's concern).
        Leader: observe all members' lifecycle transitions.
        """
        if self._blueprint.role == TeamRole.LEADER:
            await self._handle_leader_member_event(event)
        else:
            await self._handle_teammate_member_event(event)

    async def _handle_teammate_member_event(self, event: CoordinationEvent) -> None:
        """Handle member events as a non-leader — only react to events targeting self.

        A graceful ``MEMBER_SHUTDOWN`` is not decided here. It rides the mailbox
        drain (``MessageHandler.on_member_shutdown_drain``, registered on the
        same event_key), whose harness-input gate settles an idle member straight
        to SHUTDOWN and steers a running one's final messages into the round it
        is already in — that round's end then closes the stream. This holds for
        every non-leader role, human agents included, so there is no role branch
        left here: a human avatar is a member like any other on the way out.

        ``force`` is the exception, and it is role-agnostic too: it means "tear
        down now", so the member gets no round to finish and no chance to wedge.
        """
        member_name = self._blueprint.member_name
        payload = event.get_payload()
        target_id = payload.member_name
        if target_id is None or target_id != member_name:
            await self._deliver_external_team_context(event)
            return
        if event.event_type == TeamEvent.MEMBER_CANCELED:
            await self._round.cancel_agent()
            return
        if event.event_type == TeamEvent.MEMBER_SHUTDOWN and getattr(payload, "force", False):
            team_logger.info("[{}] forced shutdown; tearing down without a final round", member_name)
            await self._lifecycle.shutdown_self()
            return

    async def _deliver_external_team_context(self, event: CoordinationEvent) -> None:
        """Steer current team metadata into external members after roster changes."""
        if event.event_type not in self.TEAM_CONTEXT_EVENTS:
            return
        await self._deliver_external_team_context_snapshot()

    async def on_refresh_team_context(self, _event: CoordinationEvent) -> None:
        """Refresh this member's team context from current state."""
        await self._deliver_external_team_context_snapshot()

    async def _deliver_external_team_context_snapshot(self) -> None:
        """Steer current team metadata into external members."""
        runtime = self._external_runtime()
        if runtime is None:
            return
        if runtime.state is HarnessState.TERMINATED:
            team_logger.debug(
                "[{}] skip external team context refresh; runtime is terminated",
                self._blueprint.member_name,
            )
            return

        try:
            text = await self._build_team_context_event()
            if text:
                await self._round.deliver_input(text, use_steer=True)
        except Exception as exc:
            team_logger.warning(
                "[{}] failed to refresh external team context: {}",
                self._blueprint.member_name,
                exc,
            )

    def _external_runtime(self) -> "CliRuntimeBase | None":
        """Return the external CLI runtime when this member uses one."""
        runtime = getattr(self._round, "harness", None)
        from openjiuwen.agent_teams.external.runtime import CliRuntimeBase

        if isinstance(runtime, CliRuntimeBase):
            return runtime
        return None

    async def _build_team_context_event(self) -> str | None:
        """Build the external team-context event from native attachment sections."""
        language = self._blueprint.language
        info_section = await self._build_team_info_section(language)
        members_section = await self._build_team_members_section(language)
        attachments = [
            self._prompt_attachment(TeamSectionName.INFO, info_section, language),
            self._prompt_attachment(TeamSectionName.MEMBERS, members_section, language),
        ]
        present_attachments: list[PromptAttachment] = []
        for attachment in attachments:
            if attachment is not None:
                present_attachments.append(attachment)
        return PromptAttachmentManager().render(present_attachments) or None

    async def _build_team_info_section(self, language: str) -> PromptSection | None:
        """Build the same team_info section used by TeamPolicyRail."""
        backend = self._infra.team_backend
        if backend is None:
            return None

        info = await backend.get_team_info()
        info_dict: dict[str, Any] | None = None
        if info is not None:
            info_dict = {
                "team_name": info.team_name,
                "display_name": info.display_name,
                "desc": info.desc or "",
            }

        workspace_manager = self._infra.workspace_manager
        team_workspace_mount = None
        team_workspace_path = None
        if workspace_manager is not None:
            team_workspace_mount = f".team/{backend.team_name}/"
            team_workspace_path = workspace_manager.workspace_path

        return build_team_info_section(
            team_info=info_dict,
            team_workspace_mount=team_workspace_mount,
            team_workspace_path=team_workspace_path,
            language=language,
        )

    async def _build_team_members_section(self, language: str) -> PromptSection | None:
        """Build the same team_members section used by TeamPolicyRail."""
        backend = self._infra.team_backend
        if backend is None:
            return None

        members = await backend.list_members()
        members_list: list[dict[str, str]] | None = None
        if members:
            members_list = [
                {
                    "member_name": member.member_name,
                    "display_name": member.display_name,
                    "desc": member.desc or "",
                    "role": member.role,
                }
                for member in members
            ]

        mark_humans = (
            self._blueprint.role in (TeamRole.LEADER, TeamRole.HUMAN_AGENT)
            or self._blueprint.spec.expose_human_agents_to_teammates
        )
        return build_team_members_section(
            team_members=members_list,
            self_member_name=self._blueprint.member_name,
            mark_humans=mark_humans,
            language=language,
        )

    @staticmethod
    def _prompt_attachment(
        section_name: str,
        section: PromptSection | None,
        language: str,
    ) -> PromptAttachment | None:
        """Convert a PromptSection to the native prompt-attachment model."""
        if section is None:
            return None
        text = section.render(language).strip()
        if not text:
            return None
        return PromptAttachment(
            id=section_name,
            section=section_name,
            kind=section_name,
            content=text,
            priority=getattr(section, "priority", 100),
            session_id="",
        )

    async def _handle_leader_member_event(self, event: CoordinationEvent) -> None:
        """Handle member events as the leader — observe other members' lifecycle."""
        payload = event.payload
        target_id = payload.get("member_name", "")
        event_type = event.event_type
        if event_type == TeamEvent.MEMBER_SPAWNED:
            text = t("dispatcher.member_online", target_id=target_id)
        elif event_type == TeamEvent.MEMBER_RESTARTED:
            restart_count = payload.get("restart_count", 1)
            text = t("dispatcher.member_restarted", target_id=target_id, restart_count=restart_count)
        elif event_type == TeamEvent.MEMBER_STATUS_CHANGED:
            old_status = payload.get("old_status")
            new_status = payload.get("new_status")
            text = t(
                "dispatcher.member_status_changed",
                target_id=target_id,
                old_status=old_status,
                new_status=new_status,
            )
            await self._maybe_clean_team_after_shutdown(new_status)
        elif event_type == TeamEvent.MEMBER_EXECUTION_CHANGED:
            text = t(
                "dispatcher.member_execution_changed",
                target_id=target_id,
                old_status=payload.get("old_status"),
                new_status=payload.get("new_status"),
            )
        elif event_type == TeamEvent.MEMBER_SHUTDOWN:
            text = t("dispatcher.member_shutdown", target_id=target_id)
        elif event_type == TeamEvent.MEMBER_CANCELED:
            text = t("dispatcher.member_canceled", target_id=target_id)
        else:
            return

        team_logger.debug(text)

    async def _maybe_clean_team_after_shutdown(self, new_status: str | None) -> None:
        """Clean the team once every non-leader member has shut down.

        Temporary teams normally rely on the leader calling ``clean_team``
        after ``shutdown_member``. In practice, natural-language "disband
        team" requests can stop after shutting members down, and persistent
        teams do not expose ``clean_team`` as a leader tool. This leader-side
        guard keeps the final cleanup deterministic while still requiring
        every teammate to reach the terminal SHUTDOWN state first.
        """
        if self._team_clean_requested:
            return
        if new_status != MemberStatus.SHUTDOWN.value:
            return

        team_backend = self._infra.team_backend
        if team_backend is None:
            return

        try:
            members = await team_backend.list_members()
        except Exception as e:
            team_logger.warning("Failed to list members before team clean: {}", e)
            return

        leader_name = self._blueprint.member_name
        teammates = [member for member in members if getattr(member, "member_name", None) != leader_name]
        if not teammates:
            return
        if any(getattr(member, "status", None) != MemberStatus.SHUTDOWN.value for member in teammates):
            return

        self._team_clean_requested = True
        team_logger.info(
            "All non-leader members for team {} are SHUTDOWN; invoking clean_team",
            getattr(team_backend, "team_name", "?"),
        )
        try:
            cleaned = await team_backend.clean_team()
        except Exception as e:
            self._team_clean_requested = False
            team_logger.warning("Team clean after member shutdown failed: {}", e)
            return
        if not cleaned:
            self._team_clean_requested = False
