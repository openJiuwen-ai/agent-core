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

from openjiuwen.agent_teams.prompts import build_team_static_sections
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TeamContextTracker
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.team import TeamBackend


def _is_user_message(message: Any) -> bool:
    """Return True for a user-role message, however it was reconstructed."""
    return getattr(message, "role", "") == "user"


def prepend_to_content(content: Any, text: str) -> Any:
    """Return ``content`` with ``text`` inserted at its very front.

    ``BaseMessage.content`` is either a plain string or a list of string / dict
    blocks, so both shapes have to be handled; a list whose first block is not a
    string gets the text as a new leading block rather than being merged into a
    structure it does not belong in.
    """
    if isinstance(content, str):
        return f"{text}\n\n{content}" if content else text
    if isinstance(content, list):
        blocks = list(content)
        if blocks and isinstance(blocks[0], str):
            blocks[0] = f"{text}\n\n{blocks[0]}" if blocks[0] else text
        else:
            blocks.insert(0, text)
        return blocks
    return f"{text}\n\n{content}"


class TeamPolicyRail(DeepAgentRail):
    """Inject the team's static PromptSections and its evolving team state.

    Two lanes, and the split is the point:

      * **System-prompt builder** (cache-stable prefix) -- role, HITT
        collaboration contract, bridge self-contract, workflow, dispatch,
        lifecycle, extra, inbound-tag notice. All static **and identical across
        the members of a team**: built once at ``__init__`` and re-added to the
        builder on every ``before_model_call`` (cheap dict insert). Neither
        team-state churn nor per-member content ever touches this prefix.
      * **Conversation history** -- this member's own identity (``member_name``
        + private working agreement), the team metadata, and the peer roster.
        These are per-member and/or appear only once the team exists, so they
        cannot live in the shared prefix; they are written into the member's
        context by :class:`TeamContextTracker` at the model call where they
        first appear, and never rewritten afterwards.

    The state lane used to be a per-round prompt attachment. An attachment
    never invalidates the prefix (it is appended at the tail of the window and
    never persisted) but it is re-encoded on *every* model call and can never be
    served from the cache -- so constant content paid full price forever. Written
    into the conversation once, the same tokens are encoded once.

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
            member_prompt=member_prompt,
            team_workspace_mount=team_workspace_mount,
            team_workspace_path=team_workspace_path,
            expose_human_agents_to_teammates=expose_human_agents_to_teammates,
            language=language,
        )
        # How far into the conversation this rail has already looked. Describes
        # the live round only, so it is deliberately NOT persisted -- unlike the
        # tracker's baseline, which must survive the per-round rail rebuild.
        self._seen_message_count: int | None = None

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

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the static sections, then tell the member what is new."""
        if self.system_prompt_builder is None:
            return

        for section in self._static_sections:
            self.system_prompt_builder.add_section(section)

        await self._sync_team_context(ctx)

    async def _sync_team_context(self, ctx: AgentCallbackContext) -> None:
        """Write any newly-appeared team state into the conversation.

        Placement only ever touches the messages added since the previous model
        call, so everything before them keeps its cached prefix:

          * when that segment contains a user message, the text goes to the very
            front of the oldest one -- the member reads the team state before
            whatever prompted this call;
          * mid tool-loop the segment is all assistant / tool-result messages,
            so the text becomes a new trailing user message of its own.

        The baseline is committed only after placement succeeds; a failure here
        leaves the state pending and it is re-rendered next call.
        """
        context = getattr(ctx, "context", None)
        session = getattr(ctx, "session", None)
        if context is None or session is None:
            return
        text = await self._tracker.pending_text(session)
        if not text:
            return
        messages = context.get_messages()
        target = self._placement_target(messages)
        if target is not None:
            target.content = prepend_to_content(target.content, text)
        else:
            await context.add_messages(UserMessage(content=text))
        self._seen_message_count = len(context.get_messages())
        await self._tracker.commit(session)

    def _placement_target(self, messages: list[BaseMessage]) -> BaseMessage | None:
        """Return the user message to prepend into, or None to append a new one.

        On the very first call of a rail instance there is no recorded boundary.
        Restored history must not be touched (rewriting an old message throws
        away the cache for everything after it), so the boundary starts at the
        last user message -- the input that triggered this round on a cold start
        as much as after a resume.
        """
        boundary = self._seen_message_count
        if boundary is None:
            boundary = self._last_user_message_index(messages)
        for message in messages[boundary:]:
            if _is_user_message(message):
                return message
        return None

    @staticmethod
    def _last_user_message_index(messages: list[BaseMessage]) -> int:
        """Index of the last user message, or 0 when the history has none."""
        for index in range(len(messages) - 1, -1, -1):
            if _is_user_message(messages[index]):
                return index
        return 0

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
        )
        team_logger.info(
            "[{}] TeamPolicyRail static sections: section_names={}",
            member_name or "?",
            [s.name for s in sections],
        )
        return sections


__all__ = ["TeamPolicyRail", "prepend_to_content"]
