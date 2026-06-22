# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamPolicyRail injects team policy as ordered PromptSections.

Decomposes the team's system prompt into one PromptSection per content
category (role, workflow, lifecycle, persona, ...) and registers them
on the agent's shared ``SystemPromptBuilder`` before every model call,
so team-specific slices line up with the harness sections (safety,
tools, memory, workspace, ...) by priority.

Section layout owned by this rail (see ``prompts/sections.py`` for
builders):

  P:11  team_role        - member id + role policy (always)
  P:12  team_hitt        - HITT collaboration rules (dynamic; refreshed
                           from DB when member roster mtime changes)
  P:13  team_workflow    - leader workflow (LEADER only)
  P:14  team_lifecycle   - team lifecycle policy (LEADER only)
  P:15  team_persona     - current persona (when persona is set)
  P:16  team_extra       - user-supplied base prompt (when set)
  P:65  team_info        - team metadata (after capabilities)
  P:66  team_members     - relationships with peers
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_teams.prompts import (
    MtimeSectionCache,
    TeamSectionName,
    build_team_hitt_section,
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


_DYNAMIC_SECTION_NAMES: tuple[str, ...] = (
    TeamSectionName.HITT,
    TeamSectionName.INFO,
    TeamSectionName.MEMBERS,
)


class TeamPolicyRail(DeepAgentRail):
    """Inject team-specific PromptSections into the system prompt builder.

    Sections fall into two buckets:

      * **Static** -- role, workflow, lifecycle, persona, extra. Built
        once at ``__init__`` from constructor arguments and re-added to
        the builder on every ``before_model_call`` (cheap dict insert).
      * **Dynamic** -- ``team_hitt``, ``team_info`` and
        ``team_members``. Backed by :class:`MtimeSectionCache` instances
        that probe the team database for an ``updated_at`` change before
        re-running the full fetch. This lets the rail pick up newly
        spawned human agents or members on the next LLM call without
        paying for a full table read on every call.

    When ``team_backend`` is ``None`` (e.g. unit tests that only care
    about static content) the dynamic caches are skipped entirely and
    the rail behaves like the previous static-only implementation.
    """

    priority = 12

    def __init__(
        self,
        *,
        role: TeamRole,
        persona: str,
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

        # Static sections built once and reused on every call. HITT is
        # dynamic and refreshes from DB before model calls; bridge
        # members remain static for one rail instance.
        bridge_names: list[str] = sorted(team_backend.bridge_agent_names()) if team_backend else []
        self._static_sections: list[PromptSection] = self._build_static_sections(
            role=role,
            persona=persona,
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
        self._cached_hitt_section: Optional[PromptSection] = None
        self._cached_members_section: Optional[PromptSection] = None
        if team_backend is not None:
            self._info_cache = MtimeSectionCache(
                probe=team_backend.get_team_updated_at,
                fetch_and_build=self._fetch_and_build_info_section,
            )

    def init(self, agent: Any) -> None:
        """Cache the agent's shared prompt builder."""
        super().init(agent)
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        """Remove all team sections from the shared builder."""
        if self.system_prompt_builder is not None:
            for section in self._static_sections:
                self.system_prompt_builder.remove_section(section.name)
            for name in _DYNAMIC_SECTION_NAMES:
                self.system_prompt_builder.remove_section(name)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject static sections and refresh dynamic sections before each call."""
        if self.system_prompt_builder is None:
            return

        for section in self._static_sections:
            self.system_prompt_builder.add_section(section)

        if self._team_backend is not None:
            hitt_section, members_section = await self._refresh_member_sections()
            if hitt_section is not None:
                self.system_prompt_builder.add_section(hitt_section)
            if members_section is not None:
                self.system_prompt_builder.add_section(members_section)

        if self._info_cache is not None:
            info_section = await self._info_cache.refresh()
            if info_section is not None:
                self.system_prompt_builder.add_section(info_section)

    def _build_static_sections(
        self,
        *,
        role: TeamRole,
        persona: str,
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
            persona=persona,
            member_name=member_name,
            lifecycle=lifecycle,
            teammate_mode=teammate_mode,
            team_mode=team_mode,
            base_prompt=base_prompt,
            language=self._language,
            bridge_agent_names=bridge_agent_names,
        )
        team_logger.info(
            "[{}] TeamPolicyRail static sections: section_names={}",
            member_name or "?",
            [s.name for s in sections],
        )
        return sections

    async def _refresh_member_sections(self) -> tuple[Optional[PromptSection], Optional[PromptSection]]:
        """Refresh HITT and members sections with one shared members mtime probe."""
        mtime = await self._team_backend.get_members_max_updated_at()
        if self._members_cache_initialized and mtime == self._members_cached_mtime:
            return self._cached_hitt_section, self._cached_members_section

        self._cached_hitt_section = await self._fetch_and_build_hitt_section()
        self._cached_members_section = await self._fetch_and_build_members_section()
        self._members_cached_mtime = mtime
        self._members_cache_initialized = True
        return self._cached_hitt_section, self._cached_members_section

    async def _fetch_and_build_hitt_section(self) -> Optional[PromptSection]:
        """Reload human-agent roster from DB and rebuild the HITT section."""
        human_names = list(await self._team_backend.human_agent_names())
        team_logger.info(
            "[{}] HITT section refresh: human_agent_names={}",
            self._member_name or "?",
            human_names,
        )
        return build_team_hitt_section(
            role=self._role,
            human_agent_names=human_names,
            language=self._language,
            self_member_name=self._member_name,
            expose_human_agents_to_teammates=self._expose_human_agents_to_teammates,
        )

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
