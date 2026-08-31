# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the external runtime reliability helper."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.schema.status import MemberStatus

import pytest

from openjiuwen.agent_teams.external.reliability import RuntimeReliabilityContext
from openjiuwen.agent_teams.schema.external_runtime_reliability import (
    ExternalRuntimeFailureReason,
)


class _FakeMessageManager:
    """Captures send_message calls so tests assert delivery exactly-once."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(
        self,
        *,
        content: str,
        to_member_name: str,
        from_member_name: str,
        protocol: str = "plain",
    ) -> str | None:
        self.sent.append(
            {
                "content": content,
                "to": to_member_name,
                "from": from_member_name,
                "protocol": protocol,
            }
        )
        return f"mid-{len(self.sent)}"


class _FakeMessager:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, *, topic_id: str, message: Any) -> None:
        self.published.append((topic_id, message))


class _StatusSink:
    def __init__(self) -> None:
        self.statuses: list[Any] = []

    async def __call__(self, status: Any) -> None:
        self.statuses.append(status)


def _build_ctx(*, message_manager=None, messager=None) -> RuntimeReliabilityContext:
    return RuntimeReliabilityContext(
        member_name="worker1",
        team_name="team",
        session_id="session",
        agent_kind="codex",
        message_manager=message_manager,
        messager=messager,
        leader_name="leader",
        update_status_cb=_StatusSink() if messager is not None else _StatusSink(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_finalize_failure_persists_one_message():
    mm = _FakeMessageManager()
    ctx = _build_ctx(message_manager=mm, messager=_FakeMessager())
    ctx.begin_attempt(phase="turn", round_id=1)

    failure = await ctx.finalize_failure(
        category="auth_required",
        reason=ExternalRuntimeFailureReason(message="401"),
        summary="Codex 401",
    )
    assert failure is not None
    assert failure.failure_id == ctx.failure_id
    assert failure.category == "auth_required"
    assert failure.user_action_required is True
    assert failure.round_id == 1
    assert len(mm.sent) == 1
    assert mm.sent[0]["to"] == "leader"
    assert mm.sent[0]["protocol"] == "json"


@pytest.mark.asyncio
async def test_finalize_failure_is_exactly_once_per_attempt():
    mm = _FakeMessageManager()
    ctx = _build_ctx(message_manager=mm, messager=_FakeMessager())
    ctx.begin_attempt(phase="turn", round_id=2)

    first = await ctx.finalize_failure(
        category="sdk_error",
        reason=ExternalRuntimeFailureReason(message="x"),
        summary="s1",
    )
    second = await ctx.finalize_failure(
        category="auth_required",
        reason=ExternalRuntimeFailureReason(message="y"),
        summary="s2",
    )
    assert first is not None
    assert second is None
    assert ctx.has_finalized
    assert len(mm.sent) == 1
    # failure_id is stable across the repeated call.
    assert ctx.failure_id == first.failure_id


@pytest.mark.asyncio
async def test_begin_attempt_clears_prior_failure():
    mm = _FakeMessageManager()
    ctx = _build_ctx(message_manager=mm, messager=_FakeMessager())
    ctx.begin_attempt(phase="turn", round_id=1)
    await ctx.finalize_failure(
        category="sdk_error",
        reason=ExternalRuntimeFailureReason(message="x"),
        summary="s1",
    )
    # New round: state resets, a fresh failure can be written.
    ctx.begin_attempt(phase="turn", round_id=2)
    assert not ctx.has_finalized
    failure = await ctx.finalize_failure(
        category="auth_required",
        reason=ExternalRuntimeFailureReason(message="y"),
        summary="s2",
    )
    assert failure is not None
    assert failure.failure_id != ""
    assert len(mm.sent) == 2


@pytest.mark.asyncio
async def test_record_pending_is_overwritten_by_more_specific_signal():
    ctx = _build_ctx(message_manager=_FakeMessageManager(), messager=_FakeMessager())
    ctx.begin_attempt(phase="turn", round_id=1)
    ctx.record_pending(
        category="sdk_error",
        reason=ExternalRuntimeFailureReason(message="first"),
    )
    ctx.record_pending(
        category="auth_required",
        reason=ExternalRuntimeFailureReason(message="401", http_status=401),
    )
    assert ctx.pending_category == "auth_required"
    assert ctx.pending_reason is not None
    assert ctx.pending_reason.http_status == 401


@pytest.mark.asyncio
async def test_publish_retrying_emits_event_without_persisting():
    msgr = _FakeMessager()
    mm = _FakeMessageManager()
    ctx = _build_ctx(message_manager=mm, messager=msgr)
    ctx.begin_attempt(phase="turn", round_id=3)
    await ctx.publish_retrying(
        category="server_unavailable",
        reason=ExternalRuntimeFailureReason(message="overloaded"),
        summary="retrying",
    )
    assert len(msgr.published) == 1
    assert len(mm.sent) == 0
    # Retrying does not finalize a failure.
    assert not ctx.has_finalized


@pytest.mark.asyncio
async def test_mark_member_error_calls_update_status():
    sink = _StatusSink()
    ctx = RuntimeReliabilityContext(
        member_name="worker1",
        team_name="team",
        session_id="session",
        agent_kind="claude",
        message_manager=_FakeMessageManager(),
        messager=_FakeMessager(),
        leader_name="leader",
        update_status_cb=sink,
    )
    await ctx.mark_member_error()
    assert sink.statuses == [MemberStatus.ERROR]


@pytest.mark.asyncio
async def test_send_message_failure_is_swallowed():
    class _Boom:
        async def send_message(self, **kwargs):
            raise RuntimeError("mailbox down")

    ctx = RuntimeReliabilityContext(
        member_name="worker1",
        team_name="team",
        session_id="session",
        agent_kind="codex",
        message_manager=_Boom(),
        messager=_FakeMessager(),
        leader_name="leader",
        update_status_cb=_StatusSink(),
    )
    ctx.begin_attempt(phase="startup", round_id=None)
    # Must not raise.
    failure = await ctx.finalize_failure(
        category="process_start_failed",
        reason=ExternalRuntimeFailureReason(message="dead"),
        summary="startup failed",
    )
    assert failure is not None
