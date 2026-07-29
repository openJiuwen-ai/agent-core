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

The tracker is not thread- or task-safe and deliberately holds no lock: one
member's rail hook, CLI send and event handler all run on the same coroutine, so
there is no concurrent entry to guard against. Introducing a second entry point
that can interleave means revisiting this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import render_event, render_team_context
from openjiuwen.agent_teams.prompts.messages import (
    build_identity_text,
    build_roster_delta_text,
    build_roster_snapshot_text,
    build_team_info_text,
    diff_roster,
)
from openjiuwen.agent_teams.schema.team import TeamRole
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
        role: This member's team role; gates the ``[human]`` roster tag.
        display_name: This member's human-readable label.
        member_workspace_path: This member's own artifact directory.
        member_prompt: This member's private working agreement.
        team_workspace_mount: Agent-relative mount of the shared workspace.
        team_workspace_path: Absolute path of the shared workspace.
        expose_human_agents_to_teammates: Team switch letting teammates see the
            ``[human]`` tag (leaders and human agents always see it).
        language: Rendering language ('cn' or 'en').
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
        team_workspace_mount: str | None = None,
        team_workspace_path: str | None = None,
        expose_human_agents_to_teammates: bool = False,
        language: str = "cn",
    ) -> None:
        self._team_backend = team_backend
        self._member_name = member_name
        self._role = role
        self._display_name = display_name
        self._member_workspace_path = member_workspace_path
        self._member_prompt = member_prompt
        self._team_workspace_mount = team_workspace_mount
        self._team_workspace_path = team_workspace_path
        self._mark_humans = role in (TeamRole.LEADER, TeamRole.HUMAN_AGENT) or expose_human_agents_to_teammates
        self._language = language
        # Baseline computed by the last pending_text call, held until the caller
        # confirms delivery. None means there is nothing awaiting a commit.
        self._uncommitted: dict[str, Any] | None = None

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
        blocks: list[str] = []

        # Identity and team info are both standing facts about the team, so when
        # they surface together they belong in one <team-context> rather than two
        # adjacent ones saying the same kind of thing.
        standing: list[str] = []
        identity_body = await self._identity_body(baseline, updated)
        if identity_body:
            standing.append(identity_body)
        info_body = await self._team_info_body(baseline, updated)
        if info_body:
            standing.append(info_body)
        if standing:
            blocks.append(render_team_context(body="\n".join(standing)))

        roster_block = await self._roster_block(baseline, updated)
        if roster_block:
            blocks.append(roster_block)

        if blocks:
            self._uncommitted = updated
            return "\n\n".join(blocks)
        if updated != baseline:
            await self._persist(session, updated)
        return None

    async def commit(self, session: Any) -> None:
        """Persist the baseline for the text the caller just delivered.

        A no-op when the last :meth:`pending_text` returned nothing, so callers
        never have to guard the call.

        Args:
            session: The member's own child ``AgentSession``.
        """
        pending = self._uncommitted
        self._uncommitted = None
        if pending is None or session is None:
            return
        await self._persist(session, pending)

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def _identity_body(self, baseline: dict[str, Any], updated: dict[str, Any]) -> str | None:
        """Render the one-shot identity body, or None when already delivered.

        Constant for the lifetime of the member, so it has no probe: the
        baseline flag alone decides once it has gone out.

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
            return None
        display_name = self._display_name
        if self._team_backend is not None and self._member_name:
            member = await self._team_backend.get_member(self._member_name)
            if member is None:
                return None
            display_name = member.display_name or ""
        updated[_IDENTITY_EMITTED] = True
        return build_identity_text(
            member_name=self._member_name,
            display_name=display_name,
            member_workspace_path=self._member_workspace_path,
            member_prompt=self._member_prompt,
            language=self._language,
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
        """
        if self._team_backend is None:
            return None
        mtime = await self._team_backend.get_team_updated_at()
        if mtime == baseline.get(_TEAM_INFO_MTIME):
            return None
        updated[_TEAM_INFO_MTIME] = mtime
        info = await self._team_backend.get_team_info()
        if info is None:
            return None
        return build_team_info_text(
            team_info={
                "team_name": info.team_name,
                "display_name": info.display_name,
                "desc": info.desc or "",
            },
            team_workspace_mount=self._team_workspace_mount,
            team_workspace_path=self._team_workspace_path,
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
        members = await self._team_backend.list_members()
        roster = [
            {
                "member_name": member.member_name,
                "display_name": member.display_name,
                "desc": member.desc or "",
                "role": member.role,
            }
            for member in members
        ]
        previous = baseline.get(_ROSTER)
        if previous is None:
            kind = ROSTER_EVENT_KIND
            body = build_roster_snapshot_text(
                members=roster,
                mark_humans=self._mark_humans,
                language=self._language,
            )
        else:
            kind = ROSTER_CHANGE_EVENT_KIND
            body = build_roster_delta_text(
                delta=diff_roster(previous, roster),
                mark_humans=self._mark_humans,
                language=self._language,
            )
        updated[_ROSTER_MTIME] = mtime
        if body is None:
            # Nothing worth announcing (no peers yet, or the probe moved on a
            # field the member does not see). Only record the roster once there
            # is a delivered snapshot to diff against, so the first peer that
            # does show up is announced as a full roster rather than a delta.
            if previous is not None:
                updated[_ROSTER] = roster
            return None
        updated[_ROSTER] = roster
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


__all__ = [
    "ROSTER_CHANGE_EVENT_KIND",
    "ROSTER_EVENT_KIND",
    "ROSTER_NOTE_KIND",
    "TEAM_CONTEXT_STATE_KEY",
    "TeamContextTracker",
]
