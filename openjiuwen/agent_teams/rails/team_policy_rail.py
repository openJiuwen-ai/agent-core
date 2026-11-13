# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamPolicyRail injects team policy as ordered PromptSections.

Decomposes the team's system prompt into one PromptSection per content
category (role, workflow, lifecycle, ...) and registers them on the
agent's shared ``SystemPromptBuilder`` before every model call, so
team-specific slices line up with the harness sections (safety, tools,
memory, workspace, ...) by priority.

Section layout owned by this rail (see ``prompts/sections.py`` for
builders). **The leader takes only two of them** (three under scheduled
dispatch with a bound AgentGroup) — everything else is
disclosed by the ``build_team`` tool result instead (F_76), because the
variant of each convention is not settled until that call is made:

  P:11  team_bootstrap   - LEADER only: identity, the routing guide between
                           build_team and swarmflow (filled only when
                           ``swarmflow_enabled`` — the same signal the tool
                           factory gates the ``swarmflow`` tool on), and the
                           instruction to form the team first.
  P:11  team_role        - role policy + execution mode (non-LEADER roles)
  P:12  team_bound_group - LEADER only, scheduled dispatch with a bound
                           AgentGroup and the expert tools wired: names the
                           group and says to look for experts there first
                           and spawn them on demand (the same signal that
                           scopes ``view_experts`` to the group).
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

import asyncio
from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.extensions.events import build_team_policy_sections
from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildInput, TeamPolicyContext
from openjiuwen.agent_teams.extensions.policy import (
    build_default_policy_sections,
    render_policy_sections,
    select_policy_disclosure_sections,
    select_policy_prefix_sections,
)
from openjiuwen.agent_teams.inbound_render import (
    drop_superseded_snapshots,
    render_clock,
    render_team_policy_inline,
)
from openjiuwen.agent_teams.prompts import (
    build_leader_bootstrap_section,
)
from openjiuwen.agent_teams.prompts.loader import TemplateLoader, load_template
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import (
    TaskOutcomeTracker,
    TaskRevisionTracker,
    TeamContextTracker,
)
from openjiuwen.agent_teams.timefmt import format_absolute_time
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.context_engine.processor.compressor.util import holds_team_policy
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
    from openjiuwen.core.events.protocol import ExtensionEventBusProtocol

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
        extension_bus: "ExtensionEventBusProtocol | None" = None,
        bound_group_name: str | None = None,
        task_event_tracker: TaskOutcomeTracker | TaskRevisionTracker | None = None,
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
        self._team_backend = team_backend
        self._recovered_bootstrap: PromptSection | None = None
        self._continued_policy: bool | None = None

        # All team sections are static and built once. The HITT contract is
        # gated on the (sync) HITT capability flag rather than the live human
        # roster, so it is present whenever HITT is enabled even before any
        # human agent is spawned; the human roster itself rides the state lane
        # (tagged ``[human]``).
        hitt_enabled = team_backend.hitt_enabled() if team_backend is not None else False
        # Scheduled peer messaging (F_126): the same backend gate the tool
        # factory reads to pick the member ``send_message`` variant.
        member_messaging = team_backend.member_messaging_enabled() is True if team_backend is not None else False
        # Member subtasks (F_128): the gate that grants members ``create_task``.
        member_subtasks = team_backend.member_subtasks_enabled() is True if team_backend is not None else False
        # The default section set is assembled by the shared builder; the
        # rail keeps only the build target and the effective-config snapshot
        # that drove this build.
        policy_target = TeamPolicyBuildInput(
            team_name=team_backend.team_name if team_backend is not None else "",
            role=role,
            member_name=member_name,
        )
        policy_context = TeamPolicyContext(
            language=language,
            lifecycle=lifecycle,
            team_mode=team_mode,
            dispatch_mode=dispatch_mode,
            teammate_mode=teammate_mode,
            member_messaging=member_messaging,
            member_subtasks=member_subtasks,
            base_prompt=base_prompt,
            hitt_enabled=hitt_enabled,
            swarmflow_enabled=swarmflow_enabled,
            expose_human_agents_to_teammates=expose_human_agents_to_teammates,
            bootstrap_mode=bootstrap_mode,
            bound_group_name=bound_group_name,
        )
        # Kept for the async policy preparation step: the build target and
        # effective-config snapshot that this rail's sections were built
        # from, ready to be re-run through the extension build entry.
        self._policy_build_input = policy_target
        self._policy_context = policy_context
        # Host extension wiring decides when the sections materialize: with a
        # bus the rail defers to the first model call, where the policy build
        # extension chain assembles them (a failing build fails the member's
        # preparation — no silent fallback); without one the default set is
        # built right here, identical to the no-extension behavior.
        self._extension_bus = extension_bus
        self._policy_sections_ready = extension_bus is None
        self._build_lock: asyncio.Lock | None = asyncio.Lock() if extension_bus is not None else None
        default_sections: tuple[PromptSection, ...] | None = None
        if extension_bus is None:
            default_sections = build_default_policy_sections(
                policy_target,
                policy_context,
                loader=self._loader,
            )
            self._static_sections: list[PromptSection] = list(
                self._prefix_view(default_sections),
            )
        else:
            self._static_sections = []

        # A framework-built team (F_122) has no ``build_team`` result to carry
        # the leader's policy. Its tracker therefore receives the disclosure
        # slice of the same section output that feeds the leader prefix. This
        # matters with an extension bus: handlers must affect the framework
        # policy too, while the leader-only prefix remains out of that block.
        policy_text: str | None = None
        if role == TeamRole.LEADER and bootstrap_mode == "framework":
            if extension_bus is None:
                policy_text = self._render_framework_policy(default_sections or ())

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
        # A task fact the member may have missed rides its next admitted
        # input: under scheduled dispatch a passed review, which does not wake
        # the author (F_119); under autonomous dispatch an edit (F_129). The
        # leader needs neither. The tracker is the member's own, owned by its
        # TeamAgent and borrowed here, so its baseline outlives this rail and
        # is shared with the runtime's event-driven announcement. A second,
        # rail-owned tracker would announce the same edit again, so a missing
        # one disables the notices instead; the configurator always injects
        # one for a non-leader member (covered by its wiring test).
        self._outcome_tracker: TaskOutcomeTracker | TaskRevisionTracker | None = (
            task_event_tracker if role != TeamRole.LEADER else None
        )
        if role != TeamRole.LEADER and task_event_tracker is None and team_backend is not None:
            team_logger.debug("[{}] no task notice tracker injected; task notices are off", member_name or "?")

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

        # ``<team-policy>`` belongs ahead of the first framework-leader input.
        # A bus-wired policy build used to wait until ``before_model_call``;
        # that would be too late for this conversation-state delivery path.
        await self._ensure_sections_built(ctx)

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
        """Prepend the task facts not yet told to the member onto its input."""
        if self._outcome_tracker is None:
            return
        notice = await self._outcome_tracker.claim(session)
        if notice is not None:
            parts.insert(0, notice.text)
            await self._outcome_tracker.persist(session)

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

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Re-evaluate continuation on the first model call of each invocation."""
        self._continued_policy = None

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Allow rebuilding after this invocation explicitly disbands its team."""
        if ctx.inputs.tool_name == "clean_team":
            self._continued_policy = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the static sections, then catch state with no input to ride."""
        await self._ensure_sections_built(ctx)
        if self.system_prompt_builder is not None:
            for section in self._static_sections:
                self.system_prompt_builder.add_section(section)
            if self._continued_policy is None:
                self._continued_policy = await self._continues_with_policy(ctx)
            if self._continued_policy:
                # Same section name, so this replaces the routing to build_team.
                self.system_prompt_builder.add_section(self._recovered_bootstrap_section())

        await self._announce_unattached_state(ctx)

    async def _ensure_sections_built(self, ctx: AgentCallbackContext) -> None:
        """Run the deferred policy build once, before the first model request.

        Only the bus-wired rail enters here: the sections are assembled by
        the ``team.build_policy`` extension chain (CORE default plus any
        registered handlers) using the build target and config snapshot
        saved at construction. The lock plus the ready flag make this
        idempotent under concurrent model-call entries; a failing build
        propagates so the member's preparation fails instead of silently
        falling back to a default policy.

        Args:
            ctx: The model-call context carrying the member's session.
        """
        if self._policy_sections_ready or self._extension_bus is None or self._build_lock is None:
            return
        session = getattr(ctx, "session", None)
        session_id = session.get_session_id() if session is not None else None
        async with self._build_lock:
            if self._policy_sections_ready:
                return
            output = await build_team_policy_sections(
                self._policy_build_input,
                self._policy_context,
                bus=self._extension_bus,
                session_id=session_id,
                loader=self._loader,
            )
            self._static_sections = list(self._prefix_view(output.sections))
            if self._policy_context.bootstrap_mode == "framework" and self._role == TeamRole.LEADER:
                self._tracker.set_policy_text(self._render_framework_policy(output.sections))
            self._policy_sections_ready = True

    def _render_framework_policy(self, sections: tuple[PromptSection, ...] | list[PromptSection]) -> str:
        """Render only the extension-built disclosure sections for a framework leader."""
        disclosure = select_policy_disclosure_sections(tuple(sections))
        return render_team_policy_inline(
            body=render_policy_sections(disclosure, language=self._language),
        )

    def _prefix_view(self, sections: tuple[PromptSection, ...]) -> tuple[PromptSection, ...]:
        """Return this rail's prefix share of a full build output.

        Only the leader splits its sections (F_76): the prefix keeps
        bootstrap + extra, the rest is disclosed through the ``build_team``
        result. Every other role takes the full set as its prefix.
        """
        if self._policy_build_input.role != TeamRole.LEADER:
            return sections
        return select_policy_prefix_sections(sections)

    async def _continues_with_policy(self, ctx: AgentCallbackContext) -> bool:
        """Whether this leader must not be routed to ``build_team``.

        Applies to any run continuing the same session, cold recovered or
        resumed from pause. The switch happens at a run boundary, so the prompt
        prefix stays stable within a run. The transcript is checked, not just
        the flag: a history without the policy clears the flag so ``build_team``
        can still deliver it. Getting this wrong costs at most one round.
        """
        backend = self._team_backend
        if self._role != TeamRole.LEADER or backend is None or not backend.history_restored():
            return False
        messages = ctx.context.get_messages() if ctx.context is not None else []
        if not holds_team_policy(messages):
            backend.clear_history_restored()
            return False
        return await backend.rejects_rebuild()

    def _recovered_bootstrap_section(self) -> PromptSection:
        if self._recovered_bootstrap is None:
            self._recovered_bootstrap = build_leader_bootstrap_section(
                swarmflow_enabled=False,
                language=self._language,
                loader=self._loader,
                recovered=True,
            )
        return self._recovered_bootstrap
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


__all__ = ["TeamPolicyRail"]
