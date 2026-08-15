# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""In-process coordination helpers for autonomous Team debates."""

import asyncio
import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable


_DEBATE_META_ARG = "_team_debate_meta"
_DEBATE_META_KIND = "team_debate"


class DebateMessageRole(str, Enum):
    """Internal role of a message within one debate round."""

    INVITE = "invite"
    PEER = "peer"
    FINAL_REPORT = "final_report"


@dataclass(frozen=True, slots=True)
class _DebateInvocationMeta:
    round_id: str
    message_role: DebateMessageRole


LeaderWakeup = Callable[[str], Awaitable[None]]


class DebateRunState:
    """Process-local convergence state for one autonomous debate round."""

    def __init__(self, *, language: str = "cn") -> None:
        self.language = (language or "cn").lower()
        self.round_id: str | None = None
        self.invitation_calls: dict[str, frozenset[str]] = {}
        self.pending_invitation_calls: set[str] = set()
        self.expected_participants: set[str] = set()
        self.failed_participants: set[str] = set()
        self.reports: dict[str, str] = {}
        self.finalizing = False
        self.finalized = False
        self.participant_round_id: str | None = None
        self._leader_wakeup: LeaderWakeup | None = None
        self._lock = asyncio.Lock()

    def bind_leader_wakeup(self, callback: LeaderWakeup) -> None:
        """Bind the single Leader wake-up used after convergence."""
        self._leader_wakeup = callback

    def reset_leader_round(self) -> None:
        """Clear Leader-local state when a new harness run cycle is built."""
        self.round_id = None
        self.invitation_calls.clear()
        self.pending_invitation_calls.clear()
        self.expected_participants.clear()
        self.failed_participants.clear()
        self.reports.clear()
        self.finalizing = False
        self.finalized = False

    def reset_participant_round(self) -> None:
        """Clear teammate-local state when a new harness run cycle is built."""
        self.participant_round_id = None

    async def begin_round(self, invitation_calls: dict[str, set[str]]) -> str:
        """Replace Leader state with a newly enrolled invitation batch."""
        async with self._lock:
            self.round_id = uuid.uuid4().hex
            self.invitation_calls = {
                call_id: frozenset(targets)
                for call_id, targets in invitation_calls.items()
            }
            self.pending_invitation_calls = set(self.invitation_calls)
            self.expected_participants.clear()
            self.failed_participants.clear()
            self.reports.clear()
            self.finalizing = False
            self.finalized = False
            return self.round_id

    async def activate_participant(self, round_id: str) -> bool:
        """Activate a teammate's local debate state from a tagged invite."""
        if not round_id:
            return False
        async with self._lock:
            changed = self.participant_round_id != round_id
            self.participant_round_id = round_id
            return changed

    async def complete_participant(self, round_id: str) -> bool:
        """Clear a teammate's active round after a successful final report."""
        async with self._lock:
            if self.participant_round_id != round_id:
                return False
            self.participant_round_id = None
            return True

    async def invitation_meta(self, call_id: str) -> _DebateInvocationMeta | None:
        """Return hidden invite metadata for an enrolled pending tool call."""
        async with self._lock:
            if not self.round_id or call_id not in self.pending_invitation_calls:
                return None
            return make_debate_invocation_meta(self.round_id, DebateMessageRole.INVITE)

    async def settle_invitation(
        self,
        call_id: str,
        *,
        succeeded: bool,
        delivered_participants: set[str] | None = None,
    ) -> None:
        """Record one invitation outcome and finalize when the set is terminal."""
        delivery = None
        async with self._lock:
            if call_id not in self.pending_invitation_calls:
                return
            self.pending_invitation_calls.remove(call_id)
            registered = set(self.invitation_calls.get(call_id, ()))
            if delivered_participants is not None:
                self.expected_participants.update(registered & delivered_participants)
            elif succeeded:
                self.expected_participants.update(registered)
            delivery = self._claim_finalization_locked()
        await self._deliver_finalization(delivery)

    async def capture_report(self, round_id: str, sender: str, content: str) -> bool:
        """Capture the first final report from a participant without waking early."""
        delivery = None
        async with self._lock:
            if not self.round_id or round_id != self.round_id or self.finalized:
                return False
            if sender in self.failed_participants:
                return False
            self.reports.setdefault(sender, content)
            delivery = self._claim_finalization_locked()
        await self._deliver_finalization(delivery)
        return True

    async def mark_failed(self, sender: str) -> bool:
        """Mark a participant as explicitly failed for the active Leader round."""
        delivery = None
        async with self._lock:
            if not self.round_id or self.finalized:
                return False
            if sender in self.reports:
                return False
            self.failed_participants.add(sender)
            delivery = self._claim_finalization_locked()
        await self._deliver_finalization(delivery)
        return True

    async def retry_finalization(self) -> bool:
        """Retry a ready Leader wake-up from an existing coordination poll."""
        async with self._lock:
            delivery = self._claim_finalization_locked()
        await self._deliver_finalization(delivery)
        return delivery is not None

    def _claim_finalization_locked(
        self,
    ) -> tuple[str, str, LeaderWakeup] | None:
        if (
            self.finalized
            or self.finalizing
            or not self.round_id
            or self.pending_invitation_calls
            or self._leader_wakeup is None
        ):
            return None
        terminal = set(self.reports) | self.failed_participants
        if not self.expected_participants.issubset(terminal):
            return None
        self.finalizing = True
        return self.round_id, self._finalization_prompt(), self._leader_wakeup

    async def _deliver_finalization(
        self,
        delivery: tuple[str, str, LeaderWakeup] | None,
    ) -> None:
        if delivery is None:
            return
        round_id, prompt, callback = delivery
        try:
            await callback(prompt)
        except BaseException:
            async with self._lock:
                if self.round_id == round_id:
                    self.finalizing = False
            raise
        async with self._lock:
            if self.round_id == round_id:
                self.finalizing = False
                self.finalized = True

    def _finalization_prompt(self) -> str:
        reports = [
            f"- {member}: {self.reports[member]}"
            for member in sorted(self.expected_participants & set(self.reports))
        ]
        failed = sorted(self.expected_participants & self.failed_participants)
        if self.language.startswith("zh") or self.language == "cn":
            sections = ["本轮团队讨论已收束。请基于以下成员最终汇报，仅向用户综合总结一次，不要再次召集成员。"]
            if reports:
                sections.extend(["成员最终汇报：", *reports])
            if failed:
                sections.append("未能完成汇报的成员：" + "、".join(failed))
            return "\n".join(sections)
        sections = [
            "The team debate has converged. Synthesize the following final reports for the user exactly once; do not invite members again."
        ]
        if reports:
            sections.extend(["Final member reports:", *reports])
        if failed:
            sections.append("Members that failed to report: " + ", ".join(failed))
        return "\n".join(sections)


def make_debate_invocation_meta(
    round_id: str,
    message_role: DebateMessageRole | str,
) -> _DebateInvocationMeta:
    """Create metadata that only an in-process framework rail can supply."""
    if not isinstance(round_id, str) or not round_id:
        raise ValueError("round_id must be a non-empty string")
    return _DebateInvocationMeta(
        round_id=round_id,
        message_role=DebateMessageRole(message_role),
    )


def normalize_debate_meta(value: Any) -> dict[str, str] | None:
    """Return the canonical internal debate metadata shape, if valid."""
    if not isinstance(value, _DebateInvocationMeta):
        return None
    return {
        "kind": _DEBATE_META_KIND,
        "round_id": value.round_id,
        "message_role": value.message_role.value,
    }


def parse_debate_coordination_meta(value: Any) -> dict[str, str] | None:
    """Validate persisted coordination metadata from a mailbox row."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict) or value.get("kind") != _DEBATE_META_KIND:
        return None
    round_id = value.get("round_id")
    try:
        message_role = DebateMessageRole(value.get("message_role"))
    except (TypeError, ValueError):
        return None
    if not isinstance(round_id, str) or not round_id:
        return None
    return {
        "kind": _DEBATE_META_KIND,
        "round_id": round_id,
        "message_role": message_role.value,
    }


__all__ = [
    "DebateMessageRole",
    "DebateRunState",
    "make_debate_invocation_meta",
    "normalize_debate_meta",
    "parse_debate_coordination_meta",
]
