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
  P:12  team_hitt        - HITT collaboration contract (static rules, gated on
                           hitt_enabled). Human members are tagged ``[human]``
                           in the team_members roster, not listed inline.
  P:12  team_bridge      - bridge-avatar self-contract (BRIDGE_AGENT only)
  P:13  team_workflow    - leader workflow (LEADER only)
  P:14  team_lifecycle   - team lifecycle policy (LEADER only)
  P:16  team_private_prompt  - member-private working agreement (when set)
  P:17  team_extra           - user-supplied base prompt (when set)
  P:65  team_info            - team metadata (attachment, per round)
  P:66  team_members         - unified roster (attachment, per round)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_teams.prompts import (
    MtimeSectionCache,
    TeamSectionName,
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

      * **System-prompt builder** (cache-stable prefix) -- role, HITT
        collaboration contract, bridge self-contract, workflow, dispatch,
        lifecycle, private-prompt, extra. All static: built once at ``__init__`` and
        re-added to the builder on every ``before_model_call`` (cheap dict
        insert). Team-state churn never touches this prefix.
      * **Prompt attachment tail** (per round, disposable) -- ``team_members``
        (the unified roster; human members tagged ``[human]``) and
        ``team_info``. These are the only churning bits; pushing them to the
        DeepAgent's :class:`PromptAttachmentManager` (kind = the section name)
        keeps roster / team-state churn off the system-prompt KV cache. mtime
        caches avoid a full table read on every call.

    When ``team_backend`` is ``None`` (e.g. unit tests that only care about
    static content) the dynamic caches are skipped entirely and the rail
    behaves like a static-only implementation. When no attachment manager is
    available the dynamic sections are skipped.
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
        dispatch_mode: str = "autonomous",
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

        # All team sections are static and built once. The HITT contract is
        # gated on the (sync) HITT capability flag rather than the live human
        # roster, so it is present whenever HITT is enabled even before any
        # human agent is spawned; the human roster itself is a dynamic
        # attachment (team_members, tagged ``[human]``).
        hitt_enabled = team_backend.hitt_enabled() if team_backend is not None else False
        self._static_sections: list[PromptSection] = self._build_static_sections(
            role=role,
            member_prompt=member_prompt,
            member_name=member_name,
            lifecycle=lifecycle,
            teammate_mode=teammate_mode,
            team_mode=team_mode,
            dispatch_mode=dispatch_mode,
            base_prompt=base_prompt,
            hitt_enabled=hitt_enabled,
        )

        # Dynamic attachment caches: keyed on table-level mtime probes so
        # repeated calls pay only for the cheap probe + dict insert.
        self._info_cache: MtimeSectionCache | None = None
        self._members_cached_mtime: int = 0
        self._members_cache_initialized: bool = False
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
        """Remove the team static sections from the shared builder.

        Every builder-bound team section lives in ``_static_sections``; the
        attachment-bound sections (roster / info) live in the prompt attachment
        manager and are torn down with the DeepAgent, so there is nothing to
        strip for them here.
        """
        if self.system_prompt_builder is not None:
            for section in self._static_sections:
                self.system_prompt_builder.remove_section(section.name)
        self.system_prompt_builder = None
        self.attachment_manager = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject static sections into the builder; push churning state to attachments.

        Every team section is static and stays in the system prompt so it
        remains a stable, cacheable prefix. The only churning bits
        (``team_members`` / ``team_info``) go to the prompt attachment manager
        instead, so roster / team-state churn only touches the tail attachment
        block and never invalidates the system-prompt KV cache.
        """
        if self.system_prompt_builder is None:
            return

        for section in self._static_sections:
            self.system_prompt_builder.add_section(section)

        await self._sync_dynamic_sections(ctx)

    async def _sync_dynamic_sections(self, ctx: AgentCallbackContext) -> None:
        """Upsert the dynamic team-state sections as prompt attachments.

        Only ``team_members`` (the unified roster) and ``team_info`` are
        dynamic; both are refreshed from their mtime-backed caches and upserted
        into the attachment tail (cleared when None) so stale state never
        lingers across rounds. This method never touches the system prompt
        builder — every builder section is static. When no attachment manager
        is available (e.g. a minimal unit-test agent) it is a no-op.
        """
        if self.attachment_manager is None:
            return
        members_section = await self._refresh_members_section() if self._team_backend is not None else None
        info_section = await self._info_cache.refresh() if self._info_cache is not None else None
        writer = self.attachment_manager.bind_context(ctx)
        await self._upsert_or_clear(writer, TeamSectionName.MEMBERS, members_section)
        await self._upsert_or_clear(writer, TeamSectionName.INFO, info_section)

    async def _upsert_or_clear(
        self,
        writer: Any,
        section_name: str,
        section: Optional[PromptSection],
    ) -> None:
        """Upsert one dynamic section as an attachment, or clear it when empty.

        The attachment ``kind`` is the section name itself (``team_members`` /
        ``team_info``), which becomes the rendered ``type="..."`` attribute the
        LLM reads (see the attachment-notice section). A missing ``session_id``
        raises ``ValueError`` from the writer; that is swallowed with a warning
        so a transient context glitch never breaks the model call.
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
        dispatch_mode: str,
        base_prompt: str | None,
        hitt_enabled: bool,
    ) -> list[PromptSection]:
        """Construct the never-changing sections once at rail init time."""
        sections = build_team_static_sections(
            role=role,
            member_prompt=member_prompt,
            member_name=member_name,
            lifecycle=lifecycle,
            teammate_mode=teammate_mode,
            team_mode=team_mode,
            dispatch_mode=dispatch_mode,
            base_prompt=base_prompt,
            language=self._language,
            hitt_enabled=hitt_enabled,
            expose_human_agents_to_teammates=self._expose_human_agents_to_teammates,
            include_attachment_notice=True,
        )
        team_logger.info(
            "[{}] TeamPolicyRail static sections: section_names={}",
            member_name or "?",
            [s.name for s in sections],
        )
        return sections

    async def _refresh_members_section(self) -> Optional[PromptSection]:
        """Refresh the unified team_members section behind an mtime probe."""
        mtime = await self._team_backend.get_members_max_updated_at()
        if self._members_cache_initialized and mtime == self._members_cached_mtime:
            return self._cached_members_section
        self._cached_members_section = await self._fetch_and_build_members_section()
        self._members_cached_mtime = mtime
        self._members_cache_initialized = True
        return self._cached_members_section

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
        """Reload the roster from DB and rebuild the unified members section.

        Human members are tagged ``[human]`` only for viewers allowed to see it:
        LEADER / HUMAN_AGENT always, TEAMMATE only when
        ``expose_human_agents_to_teammates`` is set (F_18 privacy default).
        Bridge / external-CLI members are ordinary untagged entries.
        """
        members = await self._team_backend.list_members()
        members_list: list[dict[str, str]] | None = None
        if members:
            members_list = [
                {
                    "member_name": m.member_name,
                    "display_name": m.display_name,
                    "desc": m.desc or "",
                    "role": m.role,
                }
                for m in members
            ]
        mark_humans = self._role in (TeamRole.LEADER, TeamRole.HUMAN_AGENT) or self._expose_human_agents_to_teammates
        return build_team_members_section(
            team_members=members_list,
            self_member_name=self._member_name,
            mark_humans=mark_humans,
            language=self._language,
        )


__all__ = ["TeamPolicyRail"]
