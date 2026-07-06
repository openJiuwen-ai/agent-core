# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamPolicyRail injects team policy as ordered PromptSections.

Decomposes the team's system prompt into one PromptSection per content
category (role, workflow, lifecycle, private-prompt, ...) and registers them
on the agent's shared ``SystemPromptBuilder`` before every model call,
so team-specific slices line up with the harness sections (safety,
tools, memory, workspace, ...) by priority.

Section layout owned by this rail (see ``prompts/sections.py`` for
builders):

  P:11  team_role        - member id + role policy (always)
  P:12  team_hitt        - HITT collaboration contract (static rules; added
                           to the system-prompt builder). The human-member
                           roster splits out into the team_hitt_roster
                           attachment, refreshed from the member roster mtime.
  P:13  team_workflow    - leader workflow (LEADER only)
  P:14  team_lifecycle   - team lifecycle policy (LEADER only)
  P:16  team_private_prompt  - member-private working agreement (when set)
  P:17  team_extra           - user-supplied base prompt (when set)
  P:65  team_info            - team metadata (after capabilities)
  P:66  team_members         - relationships with peers
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_teams.prompts import (
    MtimeSectionCache,
    TeamSectionName,
    build_team_hitt_contract_section,
    build_team_hitt_roster_section,
    build_team_info_section,
    build_team_members_section,
    build_team_static_sections,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.team import TeamBackend


# Source tag stamped on every dynamic attachment this rail writes, so the
# attachment manager attributes them to one owner and ``clear_source`` could
# wipe them in one shot if ever needed.
_ATTACHMENT_SOURCE = "agent_teams.team_policy_rail"


class TeamPolicyRail(DeepAgentRail):
    """Inject team-specific PromptSections into the system prompt builder.

    Sections fall into two delivery lanes:

      * **System-prompt builder** (cache-stable prefix) -- role, workflow,
        lifecycle, private-prompt, extra (built once at ``__init__``), plus the
        static ``team_hitt`` collaboration contract. The contract is refreshed
        per round from the member-roster mtime, but its text only changes when
        a human agent is added, so the prefix stays byte-stable in the common
        case.
      * **Prompt attachment tail** (per round, disposable) -- ``team_members``,
        ``team_info`` and ``team_hitt_roster`` (the human-member names). These
        are the genuinely churning bits; pushing them to the DeepAgent's
        :class:`PromptAttachmentManager` (kind = the section name) keeps roster
        / team-state churn off the system-prompt KV cache. The mtime caches
        still avoid a full table read on every call.

    When ``team_backend`` is ``None`` (e.g. unit tests that only care
    about static content) the dynamic caches are skipped entirely and
    the rail behaves like the previous static-only implementation. When
    no attachment manager is available the dynamic sections are skipped.
    """

    priority = 12

    def __init__(
        self,
        *,
        role: TeamRole,
        member_prompt: str = "",
        member_name: str | None = None,
        lifecycle: str = "temporary",
        teammate_mode: str = "build_mode",
        language: str = "cn",
        team_mode: str = "default",
        base_prompt: str | None = None,
        team_workspace_mount: str | None = None,
        team_workspace_path: str | None = None,
        team_backend: "TeamBackend | None" = None,
        expose_human_agents_to_teammates: bool = False,
    ) -> None:
        super().__init__()
        self._language = language
        self._member_name = member_name
        self._team_backend = team_backend
        self._team_workspace_mount = team_workspace_mount
        self._team_workspace_path = team_workspace_path
        self._role = role
        self._expose_human_agents_to_teammates = expose_human_agents_to_teammates
        self.system_prompt_builder = None
        # The DeepAgent's prompt attachment manager, resolved at ``init``.
        # Dynamic team-state sections are pushed here instead of into the
        # system prompt builder so the prompt prefix stays cache-stable.
        self.attachment_manager: Any = None

        # Static sections built once and reused on every call. HITT is
        # dynamic and refreshes from DB before model calls; bridge
        # members remain static for one rail instance.
        bridge_names: list[str] = sorted(team_backend.bridge_agent_names()) if team_backend else []
        self._static_sections: list[PromptSection] = self._build_static_sections(
            role=role,
            member_prompt=member_prompt,
            member_name=member_name,
            lifecycle=lifecycle,
            teammate_mode=teammate_mode,
            team_mode=team_mode,
            base_prompt=base_prompt,
            bridge_agent_names=bridge_names,
        )

        # Dynamic section caches: keyed on table-level mtime probes so
        # repeated calls pay only for the cheap probe + dict insert.
        self._info_cache: MtimeSectionCache | None = None
        self._members_cached_mtime: int = 0
        self._members_cache_initialized: bool = False
        self._cached_hitt_contract: Optional[PromptSection] = None
        self._cached_hitt_roster: Optional[PromptSection] = None
        self._cached_members_section: Optional[PromptSection] = None
        if team_backend is not None:
            self._info_cache = MtimeSectionCache(
                probe=team_backend.get_team_updated_at,
                fetch_and_build=self._fetch_and_build_info_section,
            )

    def init(self, agent: Any) -> None:
        """Cache the agent's shared prompt builder and attachment manager."""
        super().init(agent)
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

    def uninit(self, agent: Any) -> None:
        """Remove team static sections + the HITT contract from the shared builder.

        The attachment-bound sections (roster / info / members) live in the
        prompt attachment manager (session scoped, refreshed per round) and are
        torn down with the DeepAgent, so there is nothing to strip for them. The
        HITT collaboration contract, however, is builder-bound (added per round,
        outside ``_static_sections``), so it must be removed explicitly here.
        """
        if self.system_prompt_builder is not None:
            for section in self._static_sections:
                self.system_prompt_builder.remove_section(section.name)
            self.system_prompt_builder.remove_section(TeamSectionName.HITT)
        self.system_prompt_builder = None
        self.attachment_manager = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject static sections + HITT contract into the builder; churn to attachments.

        Static sections and the HITT collaboration contract stay in the system
        prompt so it remains a stable, cacheable prefix. The genuinely churning
        bits (team_members / team_info / team_hitt_roster) go to the prompt
        attachment manager instead, so roster / team-state churn only touches
        the tail attachment block and never invalidates the system-prompt KV
        cache.
        """
        if self.system_prompt_builder is None:
            return

        for section in self._static_sections:
            self.system_prompt_builder.add_section(section)

        await self._sync_dynamic_sections(ctx)

    async def _sync_dynamic_sections(self, ctx: AgentCallbackContext) -> None:
        """Route the per-round team-state sections to builder vs attachment.

        Each section is refreshed from its mtime-backed cache. The static HITT
        contract goes to the system-prompt builder (added when present, removed
        when absent) so it rides the cache-stable prefix; the churning
        ``team_hitt_roster`` / ``team_members`` / ``team_info`` go to the prompt
        attachment tail (upserted when present, cleared when None) so stale
        state never lingers across rounds. When no attachment manager is
        available (e.g. a minimal unit-test agent) only the builder-bound
        contract is synced — a real DeepAgent always provides a manager.
        """
        contract_section: Optional[PromptSection] = None
        roster_section: Optional[PromptSection] = None
        members_section: Optional[PromptSection] = None
        if self._team_backend is not None:
            contract_section, roster_section, members_section = await self._refresh_member_sections()
        info_section = await self._info_cache.refresh() if self._info_cache is not None else None

        # HITT contract rides the cache-stable system-prompt prefix.
        if self.system_prompt_builder is not None:
            if contract_section is not None:
                self.system_prompt_builder.add_section(contract_section)
            else:
                self.system_prompt_builder.remove_section(TeamSectionName.HITT)

        if self.attachment_manager is None:
            return
        writer = self.attachment_manager.bind_context(ctx)
        await self._upsert_or_clear(writer, TeamSectionName.HITT_ROSTER, roster_section)
        await self._upsert_or_clear(writer, TeamSectionName.MEMBERS, members_section)
        await self._upsert_or_clear(writer, TeamSectionName.INFO, info_section)

    async def _upsert_or_clear(
        self,
        writer: Any,
        section_name: str,
        section: Optional[PromptSection],
    ) -> None:
        """Upsert one dynamic section as an attachment, or clear it when empty.

        The attachment ``kind`` is the section name itself
        (``team_hitt_roster`` / ``team_info`` / ``team_members``), which becomes
        the rendered ``type="..."`` attribute the LLM reads (see the
        attachment-notice section). A missing ``session_id`` raises
        ``ValueError`` from the writer; that is swallowed with a warning so a
        transient context glitch never breaks the model call.
        """
        try:
            if section is not None:
                await writer.add_from_prompt_section(
                    prompt_section=section,
                    kind=section_name,
                    source=_ATTACHMENT_SOURCE,
                    language=self._language,
                )
            else:
                await writer.clear_section(section_name)
        except ValueError as exc:
            team_logger.warning(
                "[{}] TeamPolicyRail skip dynamic attachment section={}: {}",
                self._member_name or "?",
                section_name,
                exc,
            )

    def _build_static_sections(
        self,
        *,
        role: TeamRole,
        member_prompt: str,
        member_name: str | None,
        lifecycle: str,
        teammate_mode: str,
        team_mode: str,
        base_prompt: str | None,
        bridge_agent_names: list[str],
    ) -> list[PromptSection]:
        """Construct the never-changing sections once at rail init time."""
        sections = build_team_static_sections(
            role=role,
            member_prompt=member_prompt,
            member_name=member_name,
            lifecycle=lifecycle,
            teammate_mode=teammate_mode,
            team_mode=team_mode,
            base_prompt=base_prompt,
            language=self._language,
            bridge_agent_names=bridge_agent_names,
            include_attachment_notice=True,
        )
        team_logger.info(
            "[{}] TeamPolicyRail static sections: section_names={}",
            member_name or "?",
            [s.name for s in sections],
        )
        return sections

    async def _refresh_member_sections(
        self,
    ) -> tuple[Optional[PromptSection], Optional[PromptSection], Optional[PromptSection]]:
        """Refresh HITT contract / roster / members with one shared members mtime probe."""
        mtime = await self._team_backend.get_members_max_updated_at()
        if self._members_cache_initialized and mtime == self._members_cached_mtime:
            return self._cached_hitt_contract, self._cached_hitt_roster, self._cached_members_section

        self._cached_hitt_contract, self._cached_hitt_roster = await self._fetch_and_build_hitt_sections()
        self._cached_members_section = await self._fetch_and_build_members_section()
        self._members_cached_mtime = mtime
        self._members_cache_initialized = True
        return self._cached_hitt_contract, self._cached_hitt_roster, self._cached_members_section

    async def _fetch_and_build_hitt_sections(
        self,
    ) -> tuple[Optional[PromptSection], Optional[PromptSection]]:
        """Reload the human-agent roster from DB and rebuild the HITT contract + roster.

        The contract (static rules) rides the system-prompt prefix; the roster
        (human-member names) rides the attachment tail. Both derive from one
        roster read so the split costs no extra query.
        """
        human_names = list(await self._team_backend.human_agent_names())
        team_logger.info(
            "[{}] HITT section refresh: human_agent_names={}",
            self._member_name or "?",
            human_names,
        )
        contract = build_team_hitt_contract_section(
            role=self._role,
            human_agent_names=human_names,
            language=self._language,
            self_member_name=self._member_name,
            expose_human_agents_to_teammates=self._expose_human_agents_to_teammates,
        )
        roster = build_team_hitt_roster_section(
            role=self._role,
            human_agent_names=human_names,
            language=self._language,
            expose_human_agents_to_teammates=self._expose_human_agents_to_teammates,
        )
        return contract, roster

    async def _fetch_and_build_info_section(self) -> Optional[PromptSection]:
        """Reload team metadata from DB and rebuild the info section."""
        info = await self._team_backend.get_team_info()
        info_dict: dict[str, Any] | None = None
        if info is not None:
            info_dict = {
                "team_name": info.team_name,
                "display_name": info.display_name,
                "desc": info.desc or "",
            }
        return build_team_info_section(
            team_info=info_dict,
            team_workspace_mount=self._team_workspace_mount,
            team_workspace_path=self._team_workspace_path,
            language=self._language,
        )

    async def _fetch_and_build_members_section(self) -> Optional[PromptSection]:
        """Reload member roster from DB and rebuild the members section."""
        members = await self._team_backend.list_members()
        members_list: list[dict[str, str]] | None = None
        if members:
            members_list = [
                {
                    "member_name": m.member_name,
                    "display_name": m.display_name,
                    "desc": m.desc or "",
                }
                for m in members
            ]
        return build_team_members_section(
            team_members=members_list,
            self_member_name=self._member_name,
            language=self._language,
        )


__all__ = ["TeamPolicyRail"]
