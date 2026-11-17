# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Delegated subagent approval routing: resolve, timeout, cancel and capacity."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.harness.security.skill_authorization.subagent_approval_registry import (
    SubagentApprovalCancelled,
    SubagentApprovalCapacityError,
    SubagentApprovalKind,
    SubagentApprovalRegistry,
    SubagentApprovalTimeout,
    get_subagent_approval_registry,
)


def _kind() -> SubagentApprovalKind:
    return SubagentApprovalKind.SKILL_LOAD


async def _make_registry_request(
    registry: SubagentApprovalRegistry,
    *,
    scope: str = "main",
    session: str = "session-1",
    timeout: float = 5.0,
    expiry_sender=None,
):
    started = asyncio.Event()

    async def sender(_request) -> None:
        started.set()

    task = asyncio.create_task(
        registry.request(
            kind=_kind(),
            session_id=session,
            agent_scope_id=scope,
            tool_call_id="call-1",
            payload={"k": "v"},
            sender=sender,
            expiry_sender=expiry_sender,
            timeout=timeout,
        ),
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    return task


@pytest.mark.asyncio
async def test_request_waits_for_resolve() -> None:
    registry = SubagentApprovalRegistry()
    task = await _make_registry_request(registry)

    assert registry.pending_count == 1
    pending = registry.pending_requests()
    assert len(pending) == 1
    assert pending[0].payload == {"k": "v"}
    assert pending[0].approval_id.startswith("subagent_skill_load_")

    assert registry.resolve(
        session_id="session-1",
        approval_id=pending[0].approval_id,
        kind=_kind(),
        answer={"approved": True},
    )
    assert await asyncio.wait_for(task, timeout=1.0) == {"approved": True}
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_resolve_rejects_wrong_session_or_kind() -> None:
    registry = SubagentApprovalRegistry()
    task = await _make_registry_request(registry)
    approval_id = registry.pending_requests()[0].approval_id

    assert not registry.resolve(
        session_id="other-session",
        approval_id=approval_id,
        kind=_kind(),
        answer=True,
    )
    assert not registry.resolve(
        session_id="session-1",
        approval_id=approval_id,
        kind=SubagentApprovalKind.TOOL_PERMISSION,
        answer=True,
    )
    assert registry.resolve(
        session_id="session-1",
        approval_id=approval_id,
        kind=_kind(),
        answer=True,
    )
    assert await asyncio.wait_for(task, timeout=1.0) is True


@pytest.mark.asyncio
async def test_response_timeout_notifies_expiry_sender() -> None:
    registry = SubagentApprovalRegistry()
    reasons: list[str] = []

    async def expiry_sender(_request, reason: str) -> None:
        reasons.append(reason)

    with pytest.raises(SubagentApprovalTimeout):
        await registry.request(
            kind=_kind(),
            session_id="session-1",
            agent_scope_id="main",
            tool_call_id="call-1",
            payload={},
            sender=_noop_sender,
            expiry_sender=expiry_sender,
            timeout=0.01,
        )

    assert reasons == ["timeout"]
    assert registry.pending_count == 0


async def _noop_sender(_request) -> None:
    return None


@pytest.mark.asyncio
async def test_cancel_scope_raises_cancelled_and_notifies_expiry() -> None:
    registry = SubagentApprovalRegistry()
    reasons: list[str] = []

    async def expiry_sender(_request, reason: str) -> None:
        reasons.append(reason)

    task = await _make_registry_request(registry, expiry_sender=expiry_sender)
    assert registry.cancel_scope("session-1", "main") == 1

    with pytest.raises(SubagentApprovalCancelled):
        await asyncio.wait_for(task, timeout=1.0)
    assert reasons == ["cancelled"]


@pytest.mark.asyncio
async def test_capacity_per_scope_and_per_session() -> None:
    registry = SubagentApprovalRegistry(max_pending_per_session=1)
    task = await _make_registry_request(registry, scope="main")

    with pytest.raises(SubagentApprovalCapacityError, match="scope already has a pending approval"):
        await registry.request(
            kind=_kind(),
            session_id="session-1",
            agent_scope_id="main",
            tool_call_id="call-2",
            payload={},
            sender=_noop_sender,
            timeout=1.0,
        )
    with pytest.raises(SubagentApprovalCapacityError, match="limit reached"):
        await registry.request(
            kind=_kind(),
            session_id="session-1",
            agent_scope_id="other",
            tool_call_id="call-3",
            payload={},
            sender=_noop_sender,
            timeout=1.0,
        )

    registry.cancel_session("session-1")
    with pytest.raises(SubagentApprovalCancelled):
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_request_validates_required_fields() -> None:
    registry = SubagentApprovalRegistry()
    with pytest.raises(ValueError):
        await registry.request(
            kind=_kind(),
            session_id="",
            agent_scope_id="main",
            tool_call_id="call-1",
            payload={},
            sender=_noop_sender,
            timeout=1.0,
        )
    with pytest.raises(ValueError, match="timeout"):
        await registry.request(
            kind=_kind(),
            session_id="session-1",
            agent_scope_id="main",
            tool_call_id="call-1",
            payload={},
            sender=_noop_sender,
            timeout=0,
        )


def test_constructor_rejects_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        SubagentApprovalRegistry(max_pending_per_session=0)


def test_singleton_lifecycle() -> None:
    SubagentApprovalRegistry.reset_instance_for_tests()
    try:
        assert SubagentApprovalRegistry.peek_instance() is None
        instance = get_subagent_approval_registry()
        assert SubagentApprovalRegistry.peek_instance() is instance
        assert SubagentApprovalRegistry.get_instance() is instance
    finally:
        SubagentApprovalRegistry.reset_instance_for_tests()
