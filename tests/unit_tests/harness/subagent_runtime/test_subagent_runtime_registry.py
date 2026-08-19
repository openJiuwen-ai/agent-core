# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime registry."""

from __future__ import annotations

import time

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.common.exception.status_mapping import build_status_exception_map
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.errors import (
    build_subagent_runtime_error,
    raise_subagent_capacity_invalid,
    raise_subagent_not_found,
)
from openjiuwen.harness.subagent_runtime.models import SubagentMetadata
from openjiuwen.harness.subagent_runtime.registry import SubagentRegistry


def _metadata(subagent_id: str, *, last_used_at: float | None = None) -> SubagentMetadata:
    now = time.time()
    mono = last_used_at if last_used_at is not None else time.monotonic()
    return SubagentMetadata(
        subagent_id=subagent_id,
        subagent_type="explore",
        display_name="Explorer",
        role="researcher",
        parent_session_id="parent-1",
        created_at=now,
        last_used_at=mono,
    )


def test_subagent_status_codes_map_to_expected_exception_classes() -> None:
    mapping = build_status_exception_map()
    from openjiuwen.core.common.exception.errors import AgentError, ExecutionError, ValidationError

    assert mapping[StatusCode.DEEPAGENT_SUBAGENT_NOT_FOUND] is AgentError
    assert mapping[StatusCode.DEEPAGENT_SUBAGENT_CAPACITY_INVALID] is ValidationError
    assert mapping[StatusCode.DEEPAGENT_SUBAGENT_RUNTIME_ERROR] is ExecutionError


def test_raise_subagent_not_found() -> None:
    from openjiuwen.core.common.exception.errors import AgentError

    with pytest.raises(AgentError) as exc_info:
        raise_subagent_not_found("sid-1")
    assert exc_info.value.status is StatusCode.DEEPAGENT_SUBAGENT_NOT_FOUND


def test_raise_subagent_capacity_invalid() -> None:
    with pytest.raises(ValidationError) as exc_info:
        raise_subagent_capacity_invalid(used=10, limit=10)
    assert exc_info.value.status is StatusCode.DEEPAGENT_SUBAGENT_CAPACITY_INVALID


def test_build_subagent_runtime_error() -> None:
    from openjiuwen.core.common.exception.errors import ExecutionError

    exc = build_subagent_runtime_error("worker failed")
    assert isinstance(exc, ExecutionError)
    assert exc.status is StatusCode.DEEPAGENT_SUBAGENT_RUNTIME_ERROR


def test_reserve_commit_registers_metadata() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=2))
    reservation = registry.reserve_slot()
    metadata = _metadata("sid-1")

    reservation.commit(metadata)

    assert registry.count == 1
    assert registry.find_metadata("sid-1") == metadata


def test_reserve_rollback_releases_quota() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=2))
    reservation = registry.reserve_slot()

    reservation.rollback()

    assert registry.count == 0
    assert registry.find_metadata("sid-1") is None


def test_rollback_is_idempotent() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=2))
    reservation = registry.reserve_slot()

    reservation.rollback()
    reservation.rollback()

    assert registry.count == 0


def test_commit_after_rollback_is_noop() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=2))
    reservation = registry.reserve_slot()
    reservation.rollback()

    reservation.commit(_metadata("sid-1"))

    assert registry.count == 0
    assert registry.find_metadata("sid-1") is None


def test_reserve_slot_raises_when_full() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=1))
    registry.reserve_slot().commit(_metadata("sid-1"))

    with pytest.raises(ValidationError) as exc_info:
        registry.reserve_slot()
    assert exc_info.value.status is StatusCode.DEEPAGENT_SUBAGENT_CAPACITY_INVALID


def test_pending_reservations_count_toward_quota() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=2))
    registry.reserve_slot()
    registry.reserve_slot()

    with pytest.raises(ValidationError):
        registry.reserve_slot()


def test_release_returns_quota() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=1))
    registry.reserve_slot().commit(_metadata("sid-1"))
    registry.release("sid-1")

    reservation = registry.reserve_slot()
    assert reservation._active is True
    assert registry.count == 1


def test_release_unknown_id_is_noop() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=1))
    registry.release("missing")
    assert registry.count == 0


def test_touch_updates_last_used_at_for_lru() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=3))
    older = _metadata("older", last_used_at=1.0)
    newer = _metadata("newer", last_used_at=2.0)
    registry.register(older)
    registry.register(newer)

    time.sleep(0.01)
    registry.touch("older")

    assert registry.lru_candidates() == ["newer", "older"]


def test_find_metadata_returns_none_when_missing() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig())
    assert registry.find_metadata("missing") is None


def test_list_live_returns_all_metadata_sorted_by_created_at() -> None:
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=3))
    first = _metadata("first")
    first.created_at = 1.0
    second = _metadata("second")
    second.created_at = 2.0
    registry.register(first)
    registry.register(second)

    assert registry.list_live() == [first, second]


# ---------------------------------------------------------------------------
# Flow tests: multi-step quota lifecycle
# ---------------------------------------------------------------------------


def test_flow_quota_reserve_commit_release_cycle() -> None:
    """占满 → release → 再占满，完整配额循环。"""
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=1))

    first = registry.reserve_slot()
    first.commit(_metadata("sid-1"))
    assert registry.count == 1

    with pytest.raises(ValidationError):
        registry.reserve_slot()

    registry.release("sid-1")
    assert registry.count == 0

    second = registry.reserve_slot()
    second.commit(_metadata("sid-2"))
    assert registry.find_metadata("sid-2") is not None
    assert registry.count == 1


def test_flow_failed_create_rollback_then_succeed() -> None:
    """创建失败 rollback → 再次 reserve + commit 成功。"""
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=1))

    failed = registry.reserve_slot()
    failed.rollback()
    assert registry.count == 0

    succeeded = registry.reserve_slot()
    succeeded.commit(_metadata("sid-1"))
    assert registry.count == 1
    assert registry.find_metadata("sid-1") is not None


def test_flow_pending_blocks_until_commit_or_rollback() -> None:
    """未决 reservation 占住配额，commit 与 rollback 分别释放。"""
    registry = SubagentRegistry(SubagentRuntimeConfig(max_subagents=1))

    pending = registry.reserve_slot()
    assert registry.count == 1

    with pytest.raises(ValidationError):
        registry.reserve_slot()

    pending.rollback()
    assert registry.count == 0

    reservation = registry.reserve_slot()
    reservation.commit(_metadata("sid-1"))
    assert registry.count == 1
