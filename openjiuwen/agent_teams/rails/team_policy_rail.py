# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamPolicyRail injects team policy as ordered PromptSections.

Decomposes the team's system prompt into one PromptSection per content
category (role, workflow, lifecycle, ...) and registers them on the
agent's shared ``SystemPromptBuilder`` before every model call, so
team-specific slices line up with the harness sections (safety, tools,
memory, workspace, ...) by priority.

Section layout owned by this rail (see ``prompts/sections.py`` for
builders). **The leader takes only two of them** — everything else is
disclosed by the ``build_team`` tool result instead (F_76), because the
variant of each convention is not settled until that call is made:

  P:11  team_bootstrap   - LEADER only: identity, the routing guide between
                           build_team and swarmflow (filled only when
                           ``swarmflow_enabled`` — the same signal the tool
                           factory gates the ``swarmflow`` tool on), and the
                           instruction to form the team first.
  P:11  team_role        - role policy + execution mode (non-LEADER roles)
  P:12  team_hitt        - HITT collaboration contract (static rules, gated on
                           hitt_enabled). Human members are tagged ``[human]``
                           in the roster message, not listed inline.
  P:12  team_bridge      - bridge-avatar self-contract (BRIDGE_AGENT only)
  P:13  team_workflow    - leader workflow (disclosure only)
  P:14  team_lifecycle   - team lifecycle policy (disclosure only)
  P:15  team_dispatch    - autonomous claim vs scheduled assignment
  P:17  team_extra       - user-supplied base prompt (when set). Stays in the
                           prefix for every role including the leader: it is
                           the caller's instruction, not team policy, and has
                           to hold before the team exists.
  P:18  team_inbound_tags - inbound / event / context XML tag notice

Every one of them is static and identical across the members of a team, so
the prompt prefix stays byte-stable and shareable. Team *state* (this
member's identity, the team metadata, the peer roster) is not a section at
all: it is written into the member's conversation as it appears, driven by
``agent_teams/team_context.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.inbound_render import drop_superseded_snapshots, render_clock, render_team_policy_inline
from openjiuwen.agent_teams.prompts import (
    build_leader_bootstrap_section,
    build_leader_policy_disclosure,
    build_leader_prebuilt_section,
    build_team_extra_section,
    build_team_static_sections,
)
from openjiuwen.agent_teams.prompts.loader import TemplateLoader, load_template
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TaskOutcomeTracker, TeamContextTracker
from openjiuwen.agent_teams.timefmt import format_absolute_time
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PROMPT_ATTACHMENT_COMMIT_CALLBACKS_KEY,
    PromptAttachmentKind,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.team import TeamBackend

# Attachment section holding this member's identity, team metadata and roster.
TEAM_CONTEXT_ATTACHMENT_SECTION = "team.context"


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
        registers it as one dynamic attachment section, and re-registers the
        last delivered content while nothing changed so the section stays
        active across agent rebuilds. ReAct then appends a
        role=system snapshot/delta message either before the admitted user
        turn or after a tool result when state appears mid-loop. Nothing already
        in the history is ever rewritten.

    Two more jobs are not about team state at all, and both are about the same
    thing: what a member coming back from a busy stretch is handed in one turn.
    ``on_user_message`` drops, for a non-leader member, the queued task boards a
    later one has already superseded -- the board may be in the batch several
    times over, full surveys, all but the newest already wrong. Each is one
    whole entry, so they come out as entries; a step later they are one joined
    history message and can no longer be separated.
    ``before_steering_drain`` handles what cannot be dropped: mailbox messages
    each say something of their own, so the batch is capped instead, and the
    surplus stays queued for the model calls that follow.

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
        bootstrap_mode: str = "leader",
        base_prompt: str | None = None,
        team_workspace_path: str | None = None,
        team_outputs_dir: str | None = None,
        team_backend: "TeamBackend | None" = None,
        expose_human_agents_to_teammates: bool = False,
        swarmflow_enabled: bool = False,
        steer_batch_size: int = 2,
        fork_source: str | None = None,
        loader: TemplateLoader = load_template,
    ) -> None:
        super().__init__()
        self._language = language
        self._member_name = member_name
        self._role = role
        self._expose_human_agents_to_teammates = expose_human_agents_to_teammates
        self._steer_batch_size = steer_batch_size
        self.system_prompt_builder = None
        self.attachment_manager = None

        # The loader is bound at construction by the rail factory
        # (elements.py) from the backend's cache; the default is the
        # framework read-only loader (unit tests /
        # no backend). No second fallback here — the factory owns the wiring.
        self._loader = loader

        # All team sections are static and built once. The HITT contract is
        # gated on the (sync) HITT capability flag rather than the live human
        # roster, so it is present whenever HITT is enabled even before any
        # human agent is spawned; the human roster itself rides the state lane
        # (tagged ``[human]``).
        hitt_enabled = team_backend.hitt_enabled() if team_backend is not None else False
        # Scheduled peer messaging (F_126): the same backend gate the tool
        # factory reads to pick the member ``send_message`` variant.
        member_messaging = team_backend.member_messaging_enabled() is True if team_backend is not None else False
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
            member_messaging=member_messaging,
            base_prompt=base_prompt,
            hitt_enabled=hitt_enabled,
            swarmflow_enabled=swarmflow_enabled,
            bootstrap_mode=bootstrap_mode,
        )

        # A framework-built team (F_122) has no ``build_team`` result to carry
        # the leader's policy, so the same disclosure text is rendered here —
        # every variable it depends on is spec-settled (no leader choice is
        # pending) — and handed to the tracker as a ``<team-policy>`` block.
        # ``hitt_enabled`` is the spec ceiling: with no ``build_team`` call it
        # is also the effective value.
        policy_text: str | None = None
        if role == TeamRole.LEADER and bootstrap_mode == "framework":
            policy_text = render_team_policy_inline(
                body=build_leader_policy_disclosure(
                    lifecycle=lifecycle,
                    teammate_mode=teammate_mode,
                    team_mode=team_mode,
                    dispatch_mode=dispatch_mode,
                    member_messaging=member_messaging,
                    language=language,
                    hitt_enabled=hitt_enabled,
                    loader=self._loader,
                ),
            )

        self._tracker = TeamContextTracker(
            team_backend=team_backend,
            member_name=member_name,
            role=role,
            display_name=display_name,
            member_workspace_path=member_workspace_path,
            member_prompt=member_prompt,
            team_workspace_path=team_workspace_path,
            team_outputs_dir=team_outputs_dir,
            expose_human_agents_to_teammates=expose_human_agents_to_teammates,
            language=language,
            fork_source=fork_source,
            policy_text=policy_text,
        )
        # Scheduled dispatch only (F_119): a passed review does not wake the
        # author, so the outcome rides its next admitted input instead.
        # Autonomous dispatch keeps its live verified notice.
        self._outcome_tracker: TaskOutcomeTracker | None = (
            TaskOutcomeTracker(team_backend=team_backend, member_name=member_name)
            if dispatch_mode == "scheduled" and team_backend is not None and role != TeamRole.LEADER
            else None
        )

    def init(self, agent: Any) -> None:
        """Cache the agent's shared prompt builder."""
        super().init(agent)
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

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
        self.attachment_manager = None

    async def on_user_message(self, ctx: AgentCallbackContext) -> None:
        """Drop superseded inputs, stamp the current time, then fold in team state.

        Three jobs on the list of inputs about to become one message, in that
        order: the batch is pruned first, a ``<team-clock>`` anchor goes in
        front of what survives, and the team state then goes in front of that.

        The clock anchor is what makes the absolute ``time`` on every
        ``<team-inbound>`` readable as "how long ago". The inbound blocks carry
        no relative diff because they stay in the history: rendered once, a
        "just now" would still say "just now" when the member reads it again an
        hour later. The anchor is written once per admitted input and never
        refreshed, so each one stays true as a record of when that input
        arrived, and the newest one is the member's current time.

        All three happen here because this is the one moment these are still *inputs*. A
        step later they are one ordinary history message — it can no longer be
        located by position (compaction rewrites the history behind it) and it
        must no longer be edited (rewriting a message invalidates every KV-cache
        entry after it). So the superseded task boards that piled up while the
        member was busy have to be dropped here or not at all, and the pending
        team state has to be folded in here or be appended as a message of its
        own by :meth:`_announce_unattached_state`.

        The state is inserted at the front, so the member reads who it is and
        what team it is on before whatever prompted this round.

        Under scheduled dispatch, review passes the member has not been told
        about yet are folded in as well (F_119). They are events, not standing
        state, so they always go inline into this input — never into the
        ``team.context`` attachment.
        """
        inputs = getattr(ctx, "inputs", None)
        parts = getattr(inputs, "parts", None)
        if parts is None:
            return

        self._drop_superseded(parts)
        if parts:
            parts.insert(0, render_clock(now=format_absolute_time(get_current_time())))

        session = getattr(ctx, "session", None)
        if session is None:
            return
        await self._fold_task_outcomes(parts, session)
        if await self._queue_team_context_attachment(ctx, session):
            return
        text = await self._tracker.pending_text(session)
        if not text:
            return
        parts.insert(0, text)
        await self._tracker.commit(session)

    async def _fold_task_outcomes(self, parts: list[str], session: Any) -> None:
        """Prepend review passes not yet told to the member onto its input."""
        if self._outcome_tracker is None:
            return
        text = await self._outcome_tracker.pending_text(session)
        if not text:
            return
        parts.insert(0, text)
        await self._outcome_tracker.commit(session)

    async def before_steering_drain(self, ctx: AgentCallbackContext) -> None:
        """Cap how much of the steering backlog one model call takes.

        The other half of the same problem :meth:`_drop_superseded` addresses,
        one step earlier. Everything the framework queued for a busy member is
        handed over at once, and what is queued here is mailbox traffic: one
        entry per message, none of them superseding any other, all of them
        having to be read. Dropping is therefore not an option — the only thing
        that can keep the turn from becoming a wall of fused messages is taking
        fewer of them, and letting the rest ride the model calls after this one.

        **The leader is exempt**, for the reason it is exempt from the
        superseded-board pruning: it reads what arrives as a sequence, and a
        sequence it sees in pieces is a sequence it has to reassemble.
        """
        if self._role == TeamRole.LEADER:
            return
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        inputs.limit = self._steer_batch_size

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
        if self.system_prompt_builder is not None:
            for section in self._static_sections:
                self.system_prompt_builder.add_section(section)

        await self._announce_unattached_state(ctx)

    async def _queue_team_context_attachment(self, ctx: AgentCallbackContext, session: Any) -> bool:
        """Register this member's team state as one dynamic attachment section.

        The attachment manager is a *current-state* channel: each boundary
        diffs the registered sections against the state recorded in history,
        and a section missing from the registry reads as withdrawn. The manager
        lives on the agent instance, so a rebuilt member starts with an empty
        registry and ``team.context`` has to be registered at every boundary:

          * history holds no attachment snapshot (first delivery, or compaction
            dropped it) -- the complete current state;
          * the tracker has something new -- that incremental text, so roster
            changes stay join/leave deltas;
          * nothing changed -- the content committed last time, unchanged, so
            its content hash matches the history and nothing is announced.

        The tracker baseline and the registered content are committed only
        after the ReAct history sync succeeds.

        Returns:
            ``True`` when the attachment lane owns delivery, ``False`` (no
            manager installed, or the section could not be registered) to fall
            back to the in-history message lane.
        """
        manager = self.attachment_manager
        context = getattr(ctx, "context", None)
        if manager is None or context is None:
            return False
        try:
            writer = manager.bind_context(ctx)
            has_snapshot = manager.has_history_snapshot(context, writer.session_id)
            text = await self._tracker.pending_text(session)
            last_text = self._tracker.last_delivered_text(session)
            if not has_snapshot:
                content = await self._tracker.snapshot_text()
            elif text:
                content = text
            else:
                # A session delivered before the content was remembered falls
                # back to the full state once, then keeps re-registering it.
                content = last_text or await self._tracker.snapshot_text()
            if not content:
                return True
            await writer.add_section(
                section=TEAM_CONTEXT_ATTACHMENT_SECTION,
                content=content,
                kind=PromptAttachmentKind.RUNTIME,
                source="agent_core.team_policy_rail",
                priority=80,
                content_kind="text/markdown",
            )
        except (TypeError, ValueError) as exc:
            team_logger.warning(
                "[{}] failed to queue team context attachment: {}",
                self._member_name or "?",
                exc,
            )
            return False

        if text or content != last_text:
            callbacks = ctx.extra.setdefault(PROMPT_ATTACHMENT_COMMIT_CALLBACKS_KEY, [])
            if isinstance(callbacks, list):
                callbacks.append(lambda: self._tracker.commit(session, delivered_text=content))
        return True

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
        if await self._queue_team_context_attachment(ctx, session):
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
        member_messaging: bool,
        base_prompt: str | None,
        hitt_enabled: bool,
        swarmflow_enabled: bool,
        bootstrap_mode: str,
    ) -> list[PromptSection]:
        """Construct the never-changing sections once at rail init time.

        The leader takes the progressive-disclosure lane (F_76): its prefix is
        the bootstrap section plus the user's own extra instructions, and
        nothing else. Every collaboration convention it needs is disclosed by
        the ``build_team`` result, which is also the first moment the variant
        of each one is actually settled. Every other role keeps the full static
        set — a teammate's conventions are fixed at spawn and it has no
        ``build_team`` call to hang them off.
        """
        if role == TeamRole.LEADER:
            sections = self._build_leader_sections(
                base_prompt=base_prompt,
                swarmflow_enabled=swarmflow_enabled,
                bootstrap_mode=bootstrap_mode,
            )
        else:
            sections = build_team_static_sections(
                role=role,
                member_name=member_name,
                lifecycle=lifecycle,
                teammate_mode=teammate_mode,
                team_mode=team_mode,
                dispatch_mode=dispatch_mode,
                member_messaging=member_messaging,
                base_prompt=base_prompt,
                language=self._language,
                hitt_enabled=hitt_enabled,
                expose_human_agents_to_teammates=self._expose_human_agents_to_teammates,
                loader=self._loader,
            )
        team_logger.info(
            "[{}] TeamPolicyRail static sections: section_names={}",
            member_name or "?",
            [s.name for s in sections],
        )
        return sections

    def _build_leader_sections(
        self,
        *,
        base_prompt: str | None,
        swarmflow_enabled: bool,
        bootstrap_mode: str,
    ) -> list[PromptSection]:
        """Build the leader's prefix: an opening section, plus the caller's own prompt.

        The opening section is ``team_bootstrap`` (route to ``build_team``) or,
        for a framework-built team (F_122), ``team_prebuilt`` (the team exists,
        the policy arrives as a ``<team-policy>`` block). The extra section
        stays here rather than moving into the policy disclosure because it is
        not team policy: it is what the SDK caller told *this* leader to do,
        and it has to be in force before the team exists — it may well be the
        instruction that decides what team to build.
        """
        if bootstrap_mode == "framework":
            opening = build_leader_prebuilt_section(language=self._language, loader=self._loader)
        else:
            opening = build_leader_bootstrap_section(
                swarmflow_enabled=swarmflow_enabled,
                language=self._language,
                loader=self._loader,
            )
        sections = [opening]
        extra = build_team_extra_section(base_prompt=base_prompt, language=self._language)
        if extra is not None:
            sections.append(extra)
        return sections


__all__ = ["TeamPolicyRail"]
