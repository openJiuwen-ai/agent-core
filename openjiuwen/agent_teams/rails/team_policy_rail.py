# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamPolicyRail injects team policy as ordered PromptSections.

Decomposes the team's system prompt into one PromptSection per content
category (role, workflow, lifecycle, ...) and registers them on the
agent's shared ``SystemPromptBuilder`` before every model call, so
team-specific slices line up with the harness sections (safety, tools,
memory, workspace, ...) by priority.

Section layout owned by this rail (see ``prompts/sections.py`` for
builders):

  P:11  team_role        - role policy + execution mode (always)
  P:12  team_hitt        - HITT collaboration contract (static rules, gated on
                           hitt_enabled). Human members are tagged ``[human]``
                           in the roster message, not listed inline.
  P:12  team_bridge      - bridge-avatar self-contract (BRIDGE_AGENT only)
  P:13  team_workflow    - leader workflow (LEADER only)
  P:14  team_lifecycle   - team lifecycle policy (LEADER only)
  P:15  team_dispatch    - autonomous claim vs scheduled assignment
  P:17  team_extra       - user-supplied base prompt (when set)
  P:18  team_inbound_tags - inbound / event / context XML tag notice

Every one of them is static and identical across the members of a team, so
the prompt prefix stays byte-stable and shareable. Team *state* (this
member's identity, the team metadata, the peer roster) is not a section at
all: it is written into the member's conversation as it appears, driven by
``agent_teams/team_context.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.inbound_render import drop_superseded_snapshots
from openjiuwen.agent_teams.prompts import build_team_static_sections
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TeamContextTracker
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.team import TeamBackend


class TeamPolicyRail(DeepAgentRail):
    """Inject the team's static PromptSections and its evolving team state.

    Two lanes, and the split is the point:

      * **System-prompt builder** (cache-stable prefix) -- role, HITT
        collaboration contract, bridge self-contract, workflow, dispatch,
        lifecycle, extra, inbound-tag notice. All static **and identical across
        the members of a team**: built once at ``__init__`` and re-added to the
        builder on every ``before_model_call`` (cheap dict insert). Neither
        team-state churn nor per-member content ever touches this prefix.
      * **Conversation history** -- this member's own identity, the team
        metadata, and the peer roster. These are per-member and/or appear only
        once the team exists, so they cannot live in the shared prefix.
        :class:`TeamContextTracker` decides what is still unsaid; the rail
        delivers it either on the input that is being admitted
        (``on_user_message``, the normal case) or, when state appears mid
        tool-loop with no input to ride, as a message appended at the tail.
        Nothing already in the history is ever rewritten.

    The state lane used to be a per-round prompt attachment. An attachment
    never invalidates the prefix (it is appended at the tail of the window and
    never persisted) but it is re-encoded on *every* model call and can never be
    served from the cache -- so constant content paid full price forever. Written
    into the conversation once, the same tokens are encoded once.

    ``on_user_message`` carries one more job that is not about team state at
    all: for a non-leader member it drops the queued task boards a later one has
    already superseded. Everything the framework queued for a busy member is
    handed over as one batch, and the board may be in there several times over
    -- full surveys, all but the newest already wrong. Each is one whole entry
    in that batch, so they come out as entries; a step later they are one joined
    history message and can no longer be separated.

    When ``team_backend`` is ``None`` (e.g. unit tests that only care about
    static content) the state lane degrades to the identity channel alone.
    """

    priority = 12

    def __init__(
        self,
        *,
        role: TeamRole,
        member_prompt: str = "",
        member_name: str | None = None,
        display_name: str = "",
        member_workspace_path: str | None = None,
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
        self._role = role
        self._expose_human_agents_to_teammates = expose_human_agents_to_teammates
        self.system_prompt_builder = None

        # All team sections are static and built once. The HITT contract is
        # gated on the (sync) HITT capability flag rather than the live human
        # roster, so it is present whenever HITT is enabled even before any
        # human agent is spawned; the human roster itself rides the state lane
        # (tagged ``[human]``).
        hitt_enabled = team_backend.hitt_enabled() if team_backend is not None else False
        self._static_sections: list[PromptSection] = self._build_static_sections(
            role=role,
            member_prompt=member_prompt,
            member_name=member_name,
            display_name=display_name,
            member_workspace_path=member_workspace_path,
            lifecycle=lifecycle,
            teammate_mode=teammate_mode,
            team_mode=team_mode,
            dispatch_mode=dispatch_mode,
            base_prompt=base_prompt,
            hitt_enabled=hitt_enabled,
        )

        self._tracker = TeamContextTracker(
            team_backend=team_backend,
            member_name=member_name,
            role=role,
            display_name=display_name,
            member_workspace_path=member_workspace_path,
            member_prompt=member_prompt,
            team_workspace_mount=team_workspace_mount,
            team_workspace_path=team_workspace_path,
            expose_human_agents_to_teammates=expose_human_agents_to_teammates,
            language=language,
        )

    def init(self, agent: Any) -> None:
        """Cache the agent's shared prompt builder."""
        super().init(agent)
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        """Remove the team static sections from the shared builder.

        Every builder-bound team section lives in ``_static_sections``; the team
        state lane writes into the conversation, which is the member's own
        history and is never stripped.
        """
        if self.system_prompt_builder is not None:
            for section in self._static_sections:
                self.system_prompt_builder.remove_section(section.name)
        self.system_prompt_builder = None

    async def on_user_message(self, ctx: AgentCallbackContext) -> None:
        """Drop superseded inputs from the batch, then fold in team state.

        Two jobs on the list of inputs about to become one message, in that
        order: the batch is pruned first, and the team state then goes in front
        of what survives.

        Both exist because this is the one moment these are still *inputs*. A
        step later they are one ordinary history message — it can no longer be
        located by position (compaction rewrites the history behind it) and it
        must no longer be edited (rewriting a message invalidates every KV-cache
        entry after it). So the superseded task boards that piled up while the
        member was busy have to be dropped here or not at all, and the pending
        team state has to be folded in here or be appended as a message of its
        own by :meth:`_announce_unattached_state`.

        The state is inserted at the front, so the member reads who it is and
        what team it is on before whatever prompted this round.
        """
        inputs = getattr(ctx, "inputs", None)
        parts = getattr(inputs, "parts", None)
        if parts is None:
            return

        self._drop_superseded(parts)

        session = getattr(ctx, "session", None)
        if session is None:
            return
        text = await self._tracker.pending_text(session)
        if not text:
            return
        parts.insert(0, text)
        await self._tracker.commit(session)

    def _drop_superseded(self, parts: list[str]) -> None:
        """Remove, in place, the queued inputs a later one already supersedes.

        Both input queues hand over everything that piled up while the member
        was busy, so a member woken after a busy stretch sees every task board
        rendered meanwhile — each a full survey, each stale except the last.
        Each board is one whole queued input, so the stale ones come out as
        entries; nothing is parsed and nothing is rewritten.

        **Leader boards are left alone.** A teammate's board is a work queue:
        it lists what is claimable right now, so only the current one is
        actionable. The leader's board is the whole team's incomplete work, and
        it reads the sequence — which task appeared, which moved — to decide
        whether to re-plan or conclude. Pruning that would delete the very
        transitions it is watching for.
        """
        if self._role == TeamRole.LEADER or len(parts) < 2:
            return
        kept = drop_superseded_snapshots(parts)
        if len(kept) == len(parts):
            return
        team_logger.debug(
            "[{}] dropped {} superseded input(s) of {}",
            self._member_name or "?",
            len(parts) - len(kept),
            len(parts),
        )
        parts[:] = kept

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the static sections, then catch state with no input to ride."""
        if self.system_prompt_builder is None:
            return

        for section in self._static_sections:
            self.system_prompt_builder.add_section(section)

        await self._announce_unattached_state(ctx)

    async def _announce_unattached_state(self, ctx: AgentCallbackContext) -> None:
        """Append team state that appeared with no input to ride along with.

        State usually arrives on an input and rides it (see
        :meth:`on_user_message`), but it can also appear mid tool-loop: a leader
        calling ``build_team`` creates the team, its own member row and the
        roster in the middle of a round, and the next input may be far away. So
        whatever is still pending at a model call becomes a message of its own.

        It is **appended**, never inserted: the tail is the only position that
        needs no index and cannot be invalidated by compaction rewriting the
        history behind it.
        """
        context = getattr(ctx, "context", None)
        session = getattr(ctx, "session", None)
        if context is None or session is None:
            return
        text = await self._tracker.pending_text(session)
        if not text:
            return
        await context.add_messages(UserMessage(content=text))
        await self._tracker.commit(session)

    def _build_static_sections(
        self,
        *,
        role: TeamRole,
        member_prompt: str,
        member_name: str | None,
        display_name: str,
        member_workspace_path: str | None,
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
            display_name=display_name,
            member_workspace_path=member_workspace_path,
            lifecycle=lifecycle,
            teammate_mode=teammate_mode,
            team_mode=team_mode,
            dispatch_mode=dispatch_mode,
            base_prompt=base_prompt,
            language=self._language,
            hitt_enabled=hitt_enabled,
            expose_human_agents_to_teammates=self._expose_human_agents_to_teammates,
        )
        team_logger.info(
            "[{}] TeamPolicyRail static sections: section_names={}",
            member_name or "?",
            [s.name for s in sections],
        )
        return sections


__all__ = ["TeamPolicyRail"]
