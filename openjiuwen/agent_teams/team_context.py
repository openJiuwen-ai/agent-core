# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Decide what team state a member still needs to be told, and remember it.

Team state -- the member's own identity, the team metadata, the peer roster --
used to ride the per-round prompt attachment: re-collected, re-rendered and
re-appended to the model input on *every* model call. An attachment sits at the
tail of the window and is never written to the conversation, so it never
invalidates the prefix cache -- but it is also re-encoded every single call and
can never be served *from* that cache. For content that is constant (identity),
near-constant (team metadata) or unchanged most of the time (the roster), that
is pure waste.

This module drives the replacement: state is delivered **into the conversation**
as it appears, and only the part that is new. Two things fall out of that:

* **Timing is data-driven, not startup-driven.** A leader has no team on its
  first model call -- ``build_team`` has not run yet -- so there is nothing to
  announce until it does. Each channel fires on the call where its own probe
  first yields content, and again whenever that probe advances.
* **The baseline has to be persisted.** ``TeamPolicyRail`` is rebuilt on *every*
  round (the native harness terminates at round end and is reconstructed on the
  next start), never mind pause/resume, so an in-memory baseline would re-announce
  everything every round. It lives in the member's own child ``AgentSession``
  state instead, which is checkpointed per ``agent_id`` and restored on
  ``pre_run`` -- the same bucket the member's conversation history is saved into,
  so the two can never drift apart.

Delivery itself is *not* here: an in-process member folds the text into its
context (``rails/team_policy_rail.py``), an external CLI member folds it into
the next message sent to its SDK (``external/runtime.py``). Both use the same
two calls:

    text = await tracker.pending_text(session)
    if text:
        ...place it...
        await tracker.commit(session)

Committing only *after* delivery is the load-bearing part of that order: advance
the baseline first and a failed delivery loses the announcement for good.

A member whose agent owns a prompt attachment manager delivers through a
*current-state* channel: every boundary must re-register the section, and one
that is missing reads as withdrawn. The incremental text is still what gets
registered when something changed, so roster changes keep their join/leave
form. When nothing changed, the content committed last time
(``commit(..., delivered_text=...)``) is registered again unchanged, so the
content hash holds and nothing is announced. When the history holds no
attachment snapshot at all (first delivery, or compaction dropped it),
:meth:`TeamContextTracker.snapshot_text` supplies the complete current state.

The tracker is not thread- or task-safe and deliberately holds no lock: one
member's rail hook, CLI send and event handler all run on the same coroutine, so
there is no concurrent entry to guard against. Introducing a second entry point
that can interleave means revisiting this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import (
    render_event,
    render_team_context,
    render_team_context_with_identity,
)
from openjiuwen.agent_teams.prompts.messages import (
    build_identity_conversion,
    build_identity_prompt_delta,
    build_identity_text,
    build_roster_delta_text,
    build_roster_snapshot_text,
    build_team_info_text,
    diff_roster,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.team import TeamBackend

# Session state key holding this member's delivery baseline.
TEAM_CONTEXT_STATE_KEY = "team_prompt_context"

# Baseline fields.
_IDENTITY_EMITTED = "identity_emitted"
_TEAM_INFO_MTIME = "team_info_mtime"
_ROSTER_MTIME = "roster_mtime"
_ROSTER = "roster"
_MEMBER_PROMPT_MTIME = "member_prompt_mtime"
# Content last registered in a current-state channel (the attachment section).
_LAST_DELIVERED_TEXT = "last_delivered_text"

# Stable contract tokens for the <team-event> ``kind`` attribute.
ROSTER_EVENT_KIND = "roster"
ROSTER_CHANGE_EVENT_KIND = "roster-change"

# <team-note> kind carried by every roster message.
ROSTER_NOTE_KIND = "announcement-only"


class TeamContextTracker:
    """Render the team state one member has not been told about yet.

    Args:
        team_backend: Backend used for the metadata / roster probes and reads.
            ``None`` degrades the tracker to the identity channel only (unit
            tests that only care about the static content).
        member_name: This member's semantic identifier.
        role: This member's team role; gates the ``[human]`` roster tag
            (the tag itself covers both human flavors — ``human_agent``
            avatars and ``passive_human`` members).
        display_name: This member's human-readable label.
        member_workspace_path: This member's own artifact directory.
        member_prompt: This member's private working agreement.
        team_workspace_path: Absolute path of the shared workspace.
        team_outputs_dir: Absolute path of the shared final-deliverables
            directory, surfaced in the team info body for projectless members
            only. Members bound to a project pass ``None`` and the bullet is
            suppressed (they keep deliverables in the project).
        expose_human_agents_to_teammates: Team switch letting teammates see the
            ``[human]`` tag (leaders and human members always see it; the tag
            marks both the avatar and passive flavors).
        language: Rendering language ('cn' or 'en').
        fork_source: When this member was forked from another (spawned with
            ``fork="..."``), the source member's name. ``None`` for a normal
            spawn. Rendered into the identity body as a conversion notice.
    """

    def __init__(
        self,
        *,
        team_backend: "TeamBackend | None",
        member_name: str | None,
        role: TeamRole,
        display_name: str = "",
        member_workspace_path: str | None = None,
        member_prompt: str = "",
        team_workspace_path: str | None = None,
        team_outputs_dir: str | None = None,
        expose_human_agents_to_teammates: bool = False,
        language: str = "cn",
        fork_source: str | None = None,
    ) -> None:
        self._team_backend = team_backend
        self._member_name = member_name
        self._role = role
        self._display_name = display_name
        self._member_workspace_path = member_workspace_path
        self._member_prompt = member_prompt
        self._team_workspace_path = team_workspace_path
        self._team_outputs_dir = team_outputs_dir
        self._mark_humans = role in (TeamRole.LEADER, TeamRole.HUMAN_AGENT) or expose_human_agents_to_teammates
        self._language = language
        self._fork_source = fork_source
        # ``enable_fork`` decides whether the identity body carries the
        # conversion capability statement AND whether the block is wrapped in a
        # nested ``<identity>`` element. When off, the render path must stay
        # byte-identical to the pre-fork one (``render_team_context``) so
        # non-fork teams keep their KV prefix and prompt bytes untouched.
        # ``getattr`` keeps backends without the fork surface (test fakes,
        # external runtimes) on the off path.
        self._fork_capable = bool(
            team_backend and getattr(team_backend, "fork_enabled", lambda: False)()
        )
        # Baseline computed by the last pending_text call, held until the caller
        # confirms delivery. None means there is nothing awaiting a commit.
        self._uncommitted: dict[str, Any] | None = None

    async def snapshot_text(self) -> str | None:
        """Render this member's complete current team state, ignoring any baseline.

        The incremental :meth:`pending_text` only says what is new since the
        persisted baseline. When the conversation no longer holds what was said
        before (a first delivery, or compaction dropped the earlier snapshot),
        a current-state channel needs the whole state instead.

        Reads nothing persisted and writes nothing.

        Returns:
            The full rendered state, or ``None`` when there is nothing to say
            yet (the member's own row or the team does not exist).
        """
        identity_body = await self._full_identity_body()
        info_body = await self._full_team_info_body()
        roster_block = None
        if self._team_backend is not None:
            roster_block = self._render_roster_snapshot(await self._list_roster())
        blocks = self._compose_blocks(identity_body, info_body, roster_block)
        return "\n\n".join(blocks) if blocks else None

    async def pending_text(self, session: Any) -> str | None:
        """Render everything this member has not been told yet.

        Advances nothing: the caller must call :meth:`commit` once the returned
        text has actually been delivered. When a probe moved but produced no
        text to say (an empty roster, or a team that does not exist yet), the baseline
        is advanced right here instead -- there is nothing that could fail to
        deliver, and leaving it unadvanced would re-read the DB on every call.

        Args:
            session: The member's own child ``AgentSession``. ``None`` disables
                the tracker (nothing is rendered and nothing is persisted).

        Returns:
            The rendered text, or ``None`` when the member is already up to date.
        """
        self._uncommitted = None
        if session is None:
            return None
        baseline = self._read_baseline(session)
        updated = dict(baseline)
        identity_body = await self._identity_body(baseline, updated)
        info_body = await self._team_info_body(baseline, updated)
        roster_block = await self._roster_block(baseline, updated)
        blocks = self._compose_blocks(identity_body, info_body, roster_block)

        if blocks:
            self._uncommitted = updated
            return "\n\n".join(blocks)
        if updated != baseline:
            await self._persist(session, updated)
        return None

    async def commit(self, session: Any, *, delivered_text: str | None = None) -> None:
        """Persist the baseline for the text the caller just delivered.

        A no-op when the last :meth:`pending_text` returned nothing and no
        ``delivered_text`` is given, so callers never have to guard the call.

        Args:
            session: The member's own child ``AgentSession``.
            delivered_text: Content the caller registered in a current-state
                channel, remembered so a rebuilt member can register the very
                same content again (see :meth:`last_delivered_text`).
        """
        pending = self._uncommitted
        self._uncommitted = None
        if session is None:
            return
        if delivered_text is not None:
            if pending is None:
                pending = dict(self._read_baseline(session))
            pending[_LAST_DELIVERED_TEXT] = delivered_text
        if pending is None:
            return
        await self._persist(session, pending)

    def last_delivered_text(self, session: Any) -> str | None:
        """Return the content last committed through ``delivered_text``.

        A current-state channel reads a section that is not re-registered as
        withdrawn, while :meth:`pending_text` has nothing to say once the member
        is up to date. Re-registering this exact content keeps the section
        active with an unchanged content hash, so no update is announced.

        Args:
            session: The member's own child ``AgentSession``.

        Returns:
            The remembered content, or ``None`` when nothing was recorded.
        """
        if session is None:
            return None
        value = self._read_baseline(session).get(_LAST_DELIVERED_TEXT)
        return value if isinstance(value, str) else None

    def _compose_blocks(
        self,
        identity_body: str | None,
        info_body: str | None,
        roster_block: str | None,
    ) -> list[str]:
        """Assemble rendered channel bodies into the delivered blocks, in order."""
        blocks: list[str] = []
        # Identity and team info are both standing facts about the team, so when
        # they surface together they belong in one <team-context> rather than two
        # adjacent ones saying the same kind of thing.
        if identity_body or info_body:
            if self._fork_capable and identity_body:
                # Fork-enabled teams get a nested <identity> element (with an
                # optional <identity-conversion> child) so the current identity
                # is distinguishable from an inherited, earlier one. Only when
                # the identity body itself is present: a team-info-only update
                # (identity already emitted) must not render an empty
                # <identity>, so it falls through to the plain path below.
                blocks.append(
                    render_team_context_with_identity(
                        identity_body=identity_body,
                        identity_conversion=(
                            build_identity_conversion(
                                source=self._fork_source,
                                member_name=self._member_name,
                                language=self._language,
                            )
                            if self._fork_source
                            else None
                        ),
                        info_body=info_body,
                    )
                )
            else:
                # Byte-identical to the pre-fork path: non-fork teams, and
                # fork-enabled teams on a team-info-only update, keep their KV
                # prefix and prompt bytes untouched.
                standing = [part for part in (identity_body, info_body) if part]
                blocks.append(render_team_context(body="\n".join(standing)))

        if roster_block:
            blocks.append(roster_block)
        return blocks

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def _identity_body(self, baseline: dict[str, Any], updated: dict[str, Any]) -> str | None:
        """Render the identity body, re-announcing only the prompt on change.

        The constant fields (member_name / display_name / member_workspace_path)
        are fixed at spawn and never change, so they are delivered exactly once
        by :func:`build_identity_text` and gated by the ``identity_emitted``
        flag. The private working agreement (``member_prompt.md``) is the one
        identity field that *can* be hand-evolved mid-session, and its only
        model-side channel is this body — so once emitted, the body probes the
        member_prompt ``updated_at`` and re-announces **only** the prompt
        subsection (via :func:`build_identity_prompt_delta`) when that mtime
        moves. The constants are never restated. A backend without the single-
        member probe (older fakes, evolution off) keeps the one-shot behaviour
        — ``getattr`` returns ``None`` and nothing is re-announced.

        **Waits for the member's own DB row**, because that row is what
        ``display_name`` has to come from. The constructor value is only a
        spec-time default: a leader is registered by ``build_team`` under the
        label the caller passed *there*, so telling it the spec default would
        tell it a name the rest of the team does not use. A teammate's row
        already exists when it spawns, so this gate costs it nothing; a leader
        is told who it is on the call right after it builds the team, alongside
        the team info that appears at the same moment.

        Without a backend at all (unit tests that only exercise static content)
        the constructor values are used as-is.
        """
        if baseline.get(_IDENTITY_EMITTED):
            # Re-announce an evolved prompt without restating the constants.
            if self._team_backend is None or not self._member_name:
                return None
            mtime, present = await self._team_backend.get_member_updated_at_state(
                self._member_name, "prompt"
            )
            if present:
                # A stamped ``updated_at`` keeps the wall-clock comparison: only
                # a moved mtime re-fires, and the baseline records the probe
                # value so a stable file does not loop.
                if mtime == baseline.get(_MEMBER_PROMPT_MTIME):
                    return None
                baseline_mtime = mtime
            else:
                # ``present=False`` (a blank ``updated_at`` — the evolution
                # party edited the body without stamping the field) is an
                # explicit "must update" signal: re-announce regardless of the
                # baseline. A single timestamp T is then stamped into the file
                # (meta only, the evolved body is preserved) and recorded as
                # the new baseline — both share T, so the next probe reads
                # ``present=True, mtime=T`` → ``T == baseline`` → no re-fire:
                # a blank field forces exactly one re-delivery, never a loop.
                baseline_mtime = get_current_time()
                await self._team_backend.stamp_member_prompt_updated_at(
                    self._member_name, baseline_mtime
                )
            member = await self._team_backend.get_member(self._member_name)
            if member is None:
                return None
            delta = build_identity_prompt_delta(
                member_prompt=member.prompt,
                language=self._language,
            )
            if delta is None:
                return None
            updated[_MEMBER_PROMPT_MTIME] = baseline_mtime
            return delta
        body = await self._full_identity_body()
        if body is None:
            return None
        updated[_IDENTITY_EMITTED] = True
        # Record the prompt mtime so a later hand-evolve moves the probe and the
        # delta path above re-announces only the prompt subsection.
        if self._team_backend is not None and self._member_name:
            mtime, _ = await self._team_backend.get_member_updated_at_state(
                self._member_name, "prompt"
            )
            updated[_MEMBER_PROMPT_MTIME] = mtime
        return body

    async def _full_identity_body(self) -> str | None:
        """Render the complete identity body from the member's current DB row.

        Returns ``None`` while the member's own row does not exist yet; without
        a backend the constructor values are used as-is.
        """
        display_name = self._display_name
        # The constructor snapshot is the spec-time DB baseline (pre-evolution).
        # ``get_member`` returns the overlay-merged row, whose ``prompt`` may
        # carry an evolved value (``member_prompt.md`` with ``evolved: true``).
        # Prefer that over the snapshot so an evolved prompt reaches the
        # identity body instead of being dropped at the last step (F_84 gap #1).
        # ``display_name`` is read from the same row one line up.
        member_prompt = self._member_prompt
        if self._team_backend is not None and self._member_name:
            member = await self._team_backend.get_member(self._member_name)
            if member is None:
                return None
            display_name = member.display_name or ""
            if member.prompt:
                member_prompt = member.prompt
        return build_identity_text(
            member_name=self._member_name,
            display_name=display_name,
            member_workspace_path=self._member_workspace_path,
            member_prompt=member_prompt,
            language=self._language,
            fork_capable=self._fork_capable,
        )

    async def _team_info_body(self, baseline: dict[str, Any], updated: dict[str, Any]) -> str | None:
        """Render team metadata when its ``updated_at`` probe has moved.

        Re-announced rather than replaced on change: the previous block stays in
        history as the fact it was at the time.

        **Nothing is announced until the team row exists.** A leader has no team
        on its first model calls -- ``build_team`` has not run yet -- while the
        workspace paths are constructor arguments and are always available. Left
        ungated, that renders a "team info" block with no team in it, and the
        real one lands moments later: the member is told the same thing twice,
        the first time wrongly. The probe reads 0 while the row is missing, so it
        moves on its own once the team is created.

        Probe + stamp fallback are symmetric with :meth:`_identity_body`: a
        stamped ``updated_at`` keeps the wall-clock comparison (only a moved
        mtime re-fires), while a *blank* field (``present=False`` — the
        evolution party edited ``team_card.md`` without stamping it) is an
        explicit "must update" signal that re-announces regardless of the
        baseline and stamps a single timestamp back into the file + baseline
        in one move (next probe is stable, no re-fire). Without this the
        member_prompt/roster channels absorb a blank field fine but team_card
        alone never re-delivered: ``max(db, 0)`` floored on the DB column and
        never moved, so the evolved team desc never reached any member. The
        probe source is the team_card md alone (not the ``get_team_updated_at``
        max that also folds in team_prompt + the DB column): team_prompt's body
        never enters the block, and the DB column advances on its own via a
        ``build_team`` mutation which the team_card md probe stays under.
        """
        if self._team_backend is None:
            return None
        mtime, present = await self._team_backend.get_team_updated_at_state()
        if present:
            # A stamped ``updated_at`` keeps the wall-clock comparison: only a
            # moved mtime re-fires, and the baseline records the probe value so
            # a stable file does not loop.
            if mtime == baseline.get(_TEAM_INFO_MTIME):
                return None
            baseline_mtime = mtime
        else:
            # ``present=False`` (a blank ``updated_at`` — the evolution party
            # edited the ``team_card.md`` body without stamping the field) is an
            # explicit "must update" signal: re-announce regardless of the
            # baseline. A single timestamp T is then stamped into the file (meta
            # only, the evolved body is preserved) and recorded as the new
            # baseline — both share T, so the next probe reads ``present=True,
            # mtime=T`` → ``T == baseline`` → no re-fire: a blank field forces
            # exactly one re-delivery, never a loop.
            baseline_mtime = get_current_time()
            await self._team_backend.stamp_team_card_updated_at(baseline_mtime)
        updated[_TEAM_INFO_MTIME] = baseline_mtime
        return await self._full_team_info_body()

    async def _full_team_info_body(self) -> str | None:
        """Render the team metadata body, or ``None`` while the team row is missing."""
        if self._team_backend is None:
            return None
        info = await self._team_backend.get_team_info()
        if info is None:
            return None
        return build_team_info_text(
            team_info={
                "team_name": info.team_name,
                "display_name": info.display_name,
                "desc": info.desc or "",
            },
            team_workspace_path=self._team_workspace_path,
            team_outputs_dir=self._team_outputs_dir,
            language=self._language,
        )

    async def _roster_block(self, baseline: dict[str, Any], updated: dict[str, Any]) -> str | None:
        """Render a roster snapshot the first time, deltas after that.

        ``TeamBackend.list_members`` already drops the member itself, so the
        stored roster is exactly the peer list the member was last told about
        and the delta is computed against that. A baseline with no ``roster``
        key means nothing has been announced yet -- an empty stored roster is a
        different thing and still takes the delta path.
        """
        if self._team_backend is None:
            return None
        mtime = await self._team_backend.get_members_max_updated_at()
        if mtime == baseline.get(_ROSTER_MTIME):
            return None
        roster = await self._list_roster()
        previous = baseline.get(_ROSTER)
        updated[_ROSTER_MTIME] = mtime
        if previous is None:
            block = self._render_roster_snapshot(roster)
            # Nothing worth announcing (no peers yet). Only record the roster
            # once there is a delivered snapshot to diff against, so the first
            # peer that does show up is announced as a full roster rather than
            # a delta.
            if block is not None:
                updated[_ROSTER] = roster
            return block
        body = build_roster_delta_text(
            delta=diff_roster(previous, roster),
            mark_humans=self._mark_humans,
            language=self._language,
        )
        # A probe that moved on a field the member does not see still advances
        # the stored roster, so the next delta diffs against the latest state.
        updated[_ROSTER] = roster
        if body is None:
            return None
        return self._render_roster_event(ROSTER_CHANGE_EVENT_KIND, body)

    async def _list_roster(self) -> list[dict[str, Any]]:
        """Read the peer roster (the backend already excludes this member)."""
        if self._team_backend is None:
            return []
        members = await self._team_backend.list_members()
        return [
            {
                "member_name": member.member_name,
                "display_name": member.display_name,
                "desc": member.desc or "",
                "role": member.role,
            }
            for member in members
        ]

    def _render_roster_snapshot(self, roster: list[dict[str, Any]]) -> str | None:
        """Render a full roster event, or ``None`` when there are no peers."""
        body = build_roster_snapshot_text(
            members=roster,
            mark_humans=self._mark_humans,
            language=self._language,
        )
        if body is None:
            return None
        return self._render_roster_event(ROSTER_EVENT_KIND, body)

    @staticmethod
    def _render_roster_event(kind: str, body: str) -> str:
        """Wrap a roster body in its event tag with the announcement-only note."""
        return render_event(
            kind=kind,
            body=body,
            note_kind=ROSTER_NOTE_KIND,
            note_text=t("team_context.roster_announcement_note"),
        )

    # ------------------------------------------------------------------
    # Baseline persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _read_baseline(session: Any) -> dict[str, Any]:
        """Read this member's persisted baseline, or an empty one."""
        state = session.get_state(TEAM_CONTEXT_STATE_KEY)
        if not isinstance(state, dict):
            return {}
        return state

    async def _persist(self, session: Any, state: dict[str, Any]) -> None:
        """Write the baseline into the member checkpoint and flush it.

        The commit is what makes a mid-round crash / pause land on a consistent
        pair: the member's conversation and this baseline share one agent-session
        state bucket, so they are written by the same ``AgentStorage.save``.
        """
        try:
            session.update_state({TEAM_CONTEXT_STATE_KEY: state})
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - never break a model call over a checkpoint write
            team_logger.warning(
                "[{}] failed to persist team context baseline: {}",
                self._member_name or "?",
                exc,
            )


# Session state key holding the ids of verified tasks this member was told about.
TASK_OUTCOME_STATE_KEY = "task_outcome_announced"

# Stable contract token for the <team-event> ``kind`` attribute.
TASK_VERIFIED_EVENT_KIND = "task-verified"


class TaskOutcomeTracker:
    """Tell a member, on its next input, which of its submitted tasks passed review.

    Under scheduled dispatch a passed review does not wake the author (F_119):
    it has nothing to act on, and waking it would only delay its retirement.
    The fact still reaches it, late — whatever wakes the member next (a start
    notice, a leader or user message) carries it.

    This is an *event*, not standing state, so it is folded into the admitted
    input itself and never into the ``team.context`` attachment (a
    current-state channel). The source of truth is the task table — the
    member's completed tasks that carry reviewers — and the delivery baseline
    is the set of task ids already told, kept in the member's own session
    state next to the team-context baseline so both are checkpointed with the
    conversation. A task completed without review is never announced: the
    member completed it itself.

    Usage mirrors :class:`TeamContextTracker`: ``pending_text`` → deliver →
    ``commit``. Not task-safe, for the same reason that tracker is not.

    Args:
        team_backend: Backend whose task manager answers the board query.
            ``None`` disables the tracker.
        member_name: This member's semantic identifier.

    The notice text follows the process-global runtime language (``i18n.t``),
    like the roster note.
    """

    def __init__(
        self,
        *,
        team_backend: "TeamBackend | None",
        member_name: str | None,
    ) -> None:
        self._team_backend = team_backend
        self._member_name = member_name
        # Task ids rendered by the last pending_text call, held until delivery
        # is confirmed. None means there is nothing awaiting a commit.
        self._uncommitted: list[str] | None = None

    async def pending_text(self, session: Any) -> str | None:
        """Render the verified tasks this member has not been told about yet.

        Args:
            session: The member's own child ``AgentSession``. ``None`` disables
                the tracker.

        Returns:
            A ``<team-event kind="task-verified">`` block, or ``None``.
        """
        self._uncommitted = None
        if session is None or self._team_backend is None or not self._member_name:
            return None
        task_manager = getattr(self._team_backend, "task_manager", None)
        if task_manager is None:
            return None
        announced = set(_read_announced(session))
        completed = await task_manager.get_tasks_by_assignee(self._member_name, status="completed")
        fresh = [task for task in completed if task.reviewers() and task.task_id not in announced]
        if not fresh:
            return None
        self._uncommitted = [task.task_id for task in fresh]
        lines = [t("team_context.task_verified_intro")]
        lines.extend(f"- {task.task_id} · {task.title or ''}" for task in fresh)
        return render_event(kind=TASK_VERIFIED_EVENT_KIND, body="\n".join(lines))

    async def commit(self, session: Any) -> None:
        """Record the tasks the caller just delivered as told.

        A no-op when the last :meth:`pending_text` returned nothing.

        Args:
            session: The member's own child ``AgentSession``.
        """
        pending = self._uncommitted
        self._uncommitted = None
        if pending is None or session is None:
            return
        announced = list(_read_announced(session))
        announced.extend(task_id for task_id in pending if task_id not in announced)
        try:
            session.update_state({TASK_OUTCOME_STATE_KEY: announced})
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - never break a model call over a checkpoint write
            team_logger.warning(
                "[{}] failed to persist task outcome baseline: {}",
                self._member_name or "?",
                exc,
            )


def _read_announced(session: Any) -> list[str]:
    """Read the ids of verified tasks already announced to this member."""
    state = session.get_state(TASK_OUTCOME_STATE_KEY)
    if not isinstance(state, list):
        return []
    return [str(task_id) for task_id in state]


__all__ = [
    "ROSTER_CHANGE_EVENT_KIND",
    "ROSTER_EVENT_KIND",
    "ROSTER_NOTE_KIND",
    "TASK_OUTCOME_STATE_KEY",
    "TASK_VERIFIED_EVENT_KIND",
    "TEAM_CONTEXT_STATE_KEY",
    "TaskOutcomeTracker",
    "TeamContextTracker",
]
