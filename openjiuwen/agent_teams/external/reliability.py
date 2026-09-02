# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reliability helpers shared by Claude/Codex SDK member runtimes.

This module owns the *delivery* half of the external-runtime reliability loop:
a per-attempt :class:`RuntimeReliabilityContext` holds one pending failure and
one finalized failure id, and guarantees that each startup or round emits at
most one :class:`ExternalRuntimeFailure` and at most one terminal failed
message.

The *classification* half lives next to each SDK runtime
(``claude/failure_classifier.py`` / ``codex/failure_classifier.py``): pure
functions that turn SDK
structured fields, exceptions and HTTP status into a
``ExternalRuntimeFailureCategory`` plus an ``ExternalRuntimeFailureReason``.

Member status is updated through an injected callback so this layer never
imports ``MemberStatus`` machinery directly. ``ERROR`` is set only on the two
runtime-unavailable paths (startup failure, task abnormal exit); an in-turn
failure leaves the member to settle back to ``READY`` through the normal round
semantics owned by :class:`CliRuntimeBase._drive_turn`.
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Optional

from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    ExternalRuntimeRetryingEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.schema.external_runtime_reliability import (
    ExternalRuntimeAgentKind,
    ExternalRuntimeFailure,
    ExternalRuntimeFailureCategory,
    ExternalRuntimeFailureReason,
    ExternalRuntimePhase,
    user_action_required,
)
from openjiuwen.agent_teams.i18n import t
from openjiuwen.core.common.logging import team_logger

# A status-update callback injected by the owning TeamAgent shell. It receives
# a MemberStatus and is awaited; the runtime stays oblivious to the DB layer.
UpdateStatusCallback = Callable[[Any], Awaitable[None]]


class RuntimeReliabilityContext:
    """Per startup/round reliability state for a Claude/Codex SDK runtime.

    Each ``begin_attempt`` starts a fresh attempt: it clears any pending
    failure and the finalized failure id. Within one attempt:

    * :meth:`record_pending` stores one candidate failure (Claude
      ``AssistantMessage.error`` / Codex ``ErrorNotification(will_retry=False)``)
      without writing a failed message.
    * :meth:`publish_retrying` emits the non-persistent progress event.
    * :meth:`finalize_failure` is the single exit: the first call mints a
      ``failure_id``, persists the failed message to the leader mailbox, and
      records it on the current turn span; subsequent calls reuse the existing
      ``failure_id`` and write nothing. Exactly-once per attempt.
    * :meth:`mark_member_error` flips the member to ``ERROR`` (startup failure
      or task abnormal exit only).

    The context is best-effort on delivery: ``send_message`` / publish failures
    are logged, never raised — an unread mailbox message is still picked up by
    a later sweep.
    """

    def __init__(
        self,
        *,
        member_name: str,
        team_name: str,
        session_id: str,
        agent_kind: ExternalRuntimeAgentKind,
        message_manager: Any,
        messager: Any,
        leader_name: str,
        update_status_cb: UpdateStatusCallback,
        span_bridge: Any = None,
    ) -> None:
        """Bind the delivery and status surface for one member runtime."""
        self._member_name = member_name
        self._team_name = team_name
        self._session_id = session_id
        self._agent_kind = agent_kind
        self._message_manager = message_manager
        self._messager = messager
        self._leader_name = leader_name
        self._update_status_cb = update_status_cb
        self._span_bridge = span_bridge
        # Per-attempt state; see begin_attempt.
        self._phase: Optional[ExternalRuntimePhase] = None
        self._round_id: Optional[int] = None
        self._pending_category: Optional[ExternalRuntimeFailureCategory] = None
        self._pending_reason: Optional[ExternalRuntimeFailureReason] = None
        self._failure_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Attempt lifecycle
    # ------------------------------------------------------------------

    def begin_attempt(self, *, phase: ExternalRuntimePhase, round_id: Optional[int]) -> None:
        """Start a new startup/turn attempt, clearing prior failure state."""
        self._phase = phase
        self._round_id = round_id
        self._pending_category = None
        self._pending_reason = None
        self._failure_id = None

    @property
    def phase(self) -> Optional[ExternalRuntimePhase]:
        """Return the current attempt phase (startup/turn)."""
        return self._phase

    @property
    def round_id(self) -> Optional[int]:
        """Return the current attempt round id (None for startup)."""
        return self._round_id

    @property
    def has_pending(self) -> bool:
        """Return whether a candidate failure is recorded for this attempt."""
        return self._pending_category is not None

    @property
    def has_finalized(self) -> bool:
        """Return whether this attempt already finalized a failure."""
        return self._failure_id is not None

    @property
    def failure_id(self) -> Optional[str]:
        """Return the finalized failure id, if any."""
        return self._failure_id

    @property
    def pending_category(self) -> Optional[ExternalRuntimeFailureCategory]:
        """Return the pending failure category, if any."""
        return self._pending_category

    @property
    def pending_reason(self) -> Optional[ExternalRuntimeFailureReason]:
        """Return the pending failure reason, if any."""
        return self._pending_reason

    # ------------------------------------------------------------------
    # Candidate failure + retrying progress
    # ------------------------------------------------------------------

    def record_pending(
        self,
        *,
        category: ExternalRuntimeFailureCategory,
        reason: ExternalRuntimeFailureReason,
    ) -> None:
        """Record a candidate failure; the turn terminal state finalizes it.

        A later candidate on the same attempt overwrites an earlier one only
        when it carries a more specific signal (non-empty ``http_status`` or
        non-empty ``sdk_error_code``); otherwise the first structured signal
        wins.
        """
        if self._failure_id is not None:
            # Already finalized; a late candidate must not reopen the attempt.
            return
        if self._pending_category is None:
            self._pending_category = category
            self._pending_reason = reason
            return
        # Keep the more specific reason; category follows the structured signal.
        if _more_specific(reason, self._pending_reason):
            self._pending_category = category
        self._pending_reason = _merge_reason(reason, self._pending_reason)

    async def publish_retrying(
        self,
        *,
        category: ExternalRuntimeFailureCategory,
        reason: ExternalRuntimeFailureReason,
        summary: str,
        attempt: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ) -> None:
        """Publish a non-persistent retrying progress event to the team topic.

        Does not end the round and does not change member status. Publish
        failure is logged only (best-effort); this signal is not persisted.
        """
        if self._messager is None:
            team_logger.warning(
                "[{}] external runtime retrying event dropped (no messager): {}",
                self._member_name,
                summary,
            )
            return
        event = ExternalRuntimeRetryingEvent(
            team_name=self._team_name,
            member_name=self._member_name,
            agent_kind=self._agent_kind,
            phase=self._phase or "turn",
            category=category,
            summary=summary,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            round_id=self._round_id,
        )
        try:
            await self._messager.publish(
                topic_id=TeamTopic.TEAM.build(self._session_id, self._team_name),
                message=EventMessage.from_event(event),
            )
        except Exception:  # noqa: BLE001 - progress event; must not break the turn
            team_logger.exception(
                "[{}] failed to publish external runtime retrying event",
                self._member_name,
            )
        team_logger.info(
            "[external-runtime] member {} {} retrying category={} summary={} round_id={}",
            self._member_name,
            self._agent_kind,
            category,
            summary,
            self._round_id,
        )

    # ------------------------------------------------------------------
    # Final failure (exactly-once)
    # ------------------------------------------------------------------

    async def finalize_failure(
        self,
        *,
        category: ExternalRuntimeFailureCategory,
        reason: ExternalRuntimeFailureReason,
        summary: str,
        suggested_action: str = "",
    ) -> Optional[ExternalRuntimeFailure]:
        """Finalize and persist the one failed message for this attempt.

        Returns the finalized :class:`ExternalRuntimeFailure` (same object on
        repeated calls), or ``None`` if delivery could not even be attempted
        (no message manager). Repeated calls reuse the existing ``failure_id``
        and write no second message — exactly one failure per attempt.
        """
        if not suggested_action:
            suggested_action = _default_suggested_action(category)
        if self._failure_id is not None:
            return None
        failure_id = uuid.uuid4().hex
        self._failure_id = failure_id
        failure = ExternalRuntimeFailure(
            failure_id=failure_id,
            team_name=self._team_name,
            member_name=self._member_name,
            agent_kind=self._agent_kind,
            phase=self._phase or "turn",
            category=category,
            user_action_required=user_action_required(category),
            summary=summary,
            suggested_action=suggested_action,
            reason=reason,
            round_id=self._round_id,
        )
        await self._persist_failure(failure)
        self._record_span(failure)
        return failure

    async def _persist_failure(self, failure: ExternalRuntimeFailure) -> None:
        """Write the failed message to the leader mailbox via send_message."""
        if self._message_manager is None:
            team_logger.error(
                "[{}] external runtime failure has no message manager; failure {} undelivered: {}",
                self._member_name,
                failure.failure_id,
                failure.summary,
            )
            return
        content = failure.model_dump_json()
        try:
            await self._message_manager.send_message(
                content=content,
                to_member_name=self._leader_name,
                from_member_name=self._member_name,
                protocol="json",
            )
        except Exception:  # noqa: BLE001 - delivery must not break the turn
            team_logger.exception(
                "[{}] failed to persist external runtime failure {} to mailbox",
                self._member_name,
                failure.failure_id,
            )
        team_logger.error(
            "[external-runtime] member {} {} failed phase={} category={} failure_id={} round_id={} "
            "summary={} user_action_required={}",
            self._member_name,
            self._agent_kind,
            failure.phase,
            failure.category,
            failure.failure_id,
            failure.round_id,
            failure.summary,
            failure.user_action_required,
        )

    def _record_span(self, failure: ExternalRuntimeFailure) -> None:
        """Record the finalized failure on the current turn span, best-effort."""
        bridge = self._span_bridge
        if bridge is None:
            return
        record = getattr(bridge, "record_external_runtime_failure", None)
        if not callable(record):
            return
        try:
            record(
                failure_id=failure.failure_id,
                round_id=failure.round_id,
                phase=failure.phase,
                category=failure.category,
                summary=failure.summary,
            )
        except Exception:  # noqa: BLE001 - observability is best-effort
            team_logger.debug(
                "[{}] span bridge failed to record external runtime failure",
                self._member_name,
            )

    async def mark_member_error(self) -> None:
        """Flip the member to ERROR (runtime-unavailable paths only)."""
        from openjiuwen.agent_teams.schema.status import MemberStatus

        try:
            await self._update_status_cb(MemberStatus.ERROR)
        except Exception:  # noqa: BLE001 - status update must not mask the failure
            team_logger.exception(
                "[{}] failed to mark member ERROR after runtime failure",
                self._member_name,
            )


def _more_specific(
    new: ExternalRuntimeFailureReason,
    old: Optional[ExternalRuntimeFailureReason],
) -> bool:
    """Return whether ``new`` carries a stronger structured signal than ``old``."""
    if old is None:
        return True
    new_score = _reason_score(new)
    old_score = _reason_score(old)
    return new_score > old_score


def _reason_score(reason: ExternalRuntimeFailureReason) -> int:
    """Score a reason by how much structured signal it carries."""
    score = 0
    if reason.http_status is not None:
        score += 2
    if reason.sdk_error_code:
        score += 1
    if reason.sdk_error_type:
        score += 1
    return score


def _merge_reason(
    new: ExternalRuntimeFailureReason,
    old: Optional[ExternalRuntimeFailureReason],
) -> ExternalRuntimeFailureReason:
    """Merge a new candidate reason into the pending one, keeping detail.

    Prefer the new structured fields; fall back to the old message text when
    the new one is empty so the raw SDK context is never lost.
    """
    if old is None:
        return new
    return ExternalRuntimeFailureReason(
        message=new.message or old.message,
        sdk_error_type=new.sdk_error_type or old.sdk_error_type,
        sdk_error_code=new.sdk_error_code or old.sdk_error_code,
        http_status=new.http_status if new.http_status is not None else old.http_status,
    )


def _default_suggested_action(category: ExternalRuntimeFailureCategory) -> str:
    """Return a user-facing suggested action for a failure category."""
    return t(f"reliability.suggested_action.{category}")


__all__ = ["RuntimeReliabilityContext", "UpdateStatusCallback"]
