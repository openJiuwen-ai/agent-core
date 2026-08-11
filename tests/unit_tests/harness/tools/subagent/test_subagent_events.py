# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent status event helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.subagent_runtime.models import SubagentStatus, SubagentStatusKind
from openjiuwen.harness.tools.subagent.subagent_events import (
    SUBAGENT_UPDATED_EVENT_TYPE,
    build_subagent_updated_payload,
    emit_subagent_updated,
    is_externally_closed,
    is_instance_closed,
    is_turn_finished,
    map_status_to_view,
    resolve_turn_outcome,
)


def test_map_status_running_states() -> None:
    for status in (
        SubagentStatus.pending_init(),
        SubagentStatus.running(),
    ):
        view = map_status_to_view(status)
        assert view["status"] == "running"
        assert view["turn_outcome"] is None
        assert view["closed_reason"] is None
        assert view["error"] is None


def test_map_status_completed() -> None:
    view = map_status_to_view(SubagentStatus.completed("done"))
    assert view == {
        "status": "idle",
        "turn_outcome": "completed",
        "closed_reason": None,
        "error": None,
    }


def test_map_status_interrupted_maps_to_idle_cancelled() -> None:
    view = map_status_to_view(SubagentStatus.interrupted())
    assert view["status"] == "idle"
    assert view["turn_outcome"] == "cancelled"
    assert not SubagentStatus.interrupted().is_final()


def test_map_status_errored_timeout() -> None:
    view = map_status_to_view(SubagentStatus.errored("timed out", code="TIMEOUT"))
    assert view["status"] == "idle"
    assert view["turn_outcome"] == "failed"
    assert view["error"] == {"code": "TIMEOUT", "message": "timed out"}


@pytest.mark.parametrize(
    ("close_reason", "expected"),
    [
        ("manual", "manual"),
        ("evicted", "evicted"),
        ("parent_ended", "parent_ended"),
        ("stream_cancelled", "parent_ended"),
        ("test_cleanup", "parent_ended"),
        ("unknown", "parent_ended"),
    ],
)
def test_map_status_closed_reasons(close_reason: str, expected: str) -> None:
    view = map_status_to_view(SubagentStatus.closed(close_reason))
    assert view["status"] == "closed"
    assert view["closed_reason"] == expected


def test_resolve_turn_outcome() -> None:
    assert resolve_turn_outcome(SubagentStatus.completed()) == "completed"
    assert resolve_turn_outcome(SubagentStatus.interrupted()) == "cancelled"
    assert resolve_turn_outcome(SubagentStatus.errored("x")) == "failed"
    assert resolve_turn_outcome(SubagentStatus.running()) is None


def test_build_subagent_updated_payload_shape() -> None:
    payload = build_subagent_updated_payload(
        subagent_id="sid",
        subagent_type="explore",
        display_name="Explorer",
        role="research",
        parent_session_id="parent",
        task_description="hello",
        created_at_ms=1.0,
        updated_at_ms=2.0,
        closed_at_ms=None,
        status=SubagentStatus.running(),
        revision=3,
    )
    assert payload["subagent_id"] == "sid"
    assert payload["sub_session_id"] == "sid"
    assert payload["status"] == "running"
    assert payload["revision"] == 3
    assert payload["task_description"] == "hello"
    assert payload["can_send_input"] is False
    assert payload["needs_resume"] is False


def test_build_subagent_updated_payload_idle_live() -> None:
    payload = build_subagent_updated_payload(
        subagent_id="sid",
        subagent_type="explore",
        display_name="Explorer",
        role="research",
        parent_session_id="parent",
        task_description="hello",
        created_at_ms=1.0,
        updated_at_ms=2.0,
        closed_at_ms=99.0,
        status=SubagentStatus.completed("done"),
        revision=3,
    )
    assert payload["status"] == "idle"
    assert payload["turn_outcome"] == "completed"
    assert payload["closed_at"] is None
    assert payload["lifecycle"] == "live"
    assert payload["can_send_input"] is True
    assert payload["needs_resume"] is False


def test_is_turn_finished_and_instance_closed() -> None:
    assert is_turn_finished(SubagentStatus.completed()) is True
    assert is_turn_finished(SubagentStatus.running()) is False
    assert is_instance_closed(SubagentStatus.completed()) is False
    assert is_instance_closed(SubagentStatus.closed("manual")) is True


def test_is_externally_closed_means_instance_closed() -> None:
    assert is_externally_closed(SubagentStatus.running()) is False
    assert is_externally_closed(SubagentStatus.completed()) is False
    assert is_externally_closed(SubagentStatus.closed("manual")) is True


@pytest.mark.asyncio
async def test_emit_subagent_updated_writes_stream() -> None:
    session = Session(session_id="parent")
    session.write_stream = AsyncMock()
    projection = {"subagent_id": "sid", "status": "running", "revision": 1}

    await emit_subagent_updated(session, projection=projection)

    session.write_stream.assert_awaited_once()
    chunk = session.write_stream.await_args.args[0]
    assert chunk.type == SUBAGENT_UPDATED_EVENT_TYPE
    assert chunk.payload["subagent_updated"] == projection


@pytest.mark.asyncio
async def test_emit_subagent_updated_swallows_write_errors() -> None:
    session = Session(session_id="parent")
    session.write_stream = AsyncMock(side_effect=RuntimeError("closed"))

    await emit_subagent_updated(session, projection={"subagent_id": "sid"})
