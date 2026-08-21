# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent_runtime models and ids."""

from __future__ import annotations

import dataclasses
import re

import pytest

from openjiuwen.harness.subagent_runtime.ids import build_subagent_id, new_task_id
from openjiuwen.harness.subagent_runtime.models import (
    ShutdownOp,
    SubagentStatus,
    SubagentStatusKind,
    UserInputOp,
    resolve_presentation,
)


@pytest.mark.parametrize(
    ("kind", "expected_final"),
    [
        (SubagentStatusKind.PENDING_INIT, False),
        (SubagentStatusKind.RUNNING, False),
        (SubagentStatusKind.INTERRUPTED, False),
        (SubagentStatusKind.COMPLETED, True),
        (SubagentStatusKind.ERRORED, True),
        (SubagentStatusKind.CLOSED, True),
        (SubagentStatusKind.NOT_FOUND, True),
    ],
)
def test_is_final(kind: SubagentStatusKind, expected_final: bool) -> None:
    status = SubagentStatus(kind)
    assert status.is_final() is expected_final


def test_status_factory_methods() -> None:
    assert SubagentStatus.pending_init().kind is SubagentStatusKind.PENDING_INIT
    assert SubagentStatus.running().kind is SubagentStatusKind.RUNNING
    assert SubagentStatus.completed("done").message == "done"
    assert SubagentStatus.interrupted().kind is SubagentStatusKind.INTERRUPTED
    assert SubagentStatus.errored("boom", code="TIMEOUT").error_code == "TIMEOUT"
    assert SubagentStatus.closed("manual").close_reason == "manual"
    assert SubagentStatus.not_found().kind is SubagentStatusKind.NOT_FOUND


def test_subagent_status_is_frozen() -> None:
    status = SubagentStatus.running()
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.kind = SubagentStatusKind.COMPLETED  # type: ignore[misc]


@pytest.mark.parametrize(
    "kind",
    list(SubagentStatusKind),
)
def test_subagent_status_kind_serializes_as_snake_case(kind: SubagentStatusKind) -> None:
    assert kind.value == kind.name.lower()


def test_shutdown_op_default_reason() -> None:
    assert ShutdownOp().reason == "manual"


def test_new_task_id_is_unique() -> None:
    assert new_task_id() != new_task_id()


def test_build_subagent_id_non_sticky_generates_distinct_ids() -> None:
    first = build_subagent_id("parent", "explore", sticky=False)
    second = build_subagent_id("parent", "explore", sticky=False)
    assert first != second
    assert first.startswith("parent_sub_explore_")
    assert second.startswith("parent_sub_explore_")


def test_resolve_presentation_whitespace_only_display_name_falls_back() -> None:
    assert resolve_presentation(
        subagent_type="research_agent",
        display_name="   ",
        role="role",
    ) == ("research_agent", "role")


def test_subagent_metadata_fields() -> None:
    from openjiuwen.harness.subagent_runtime.models import SubagentMetadata

    metadata = SubagentMetadata(
        subagent_id="parent_sub_explore_ab12cd34",
        subagent_type="explore",
        display_name="Ethan",
        role="researcher",
        parent_session_id="parent",
        created_at=100.0,
        last_used_at=200.0,
        current_task_id="task-1",
    )
    assert metadata.display_name == "Ethan"
    assert metadata.current_task_id == "task-1"


def test_subagent_activity_round_trip() -> None:
    from openjiuwen.harness.subagent_runtime.models import SubagentActivity

    activity = SubagentActivity(
        subagent_id="sid",
        task_id="task-1",
        seq=2,
        kind="tool_result",
        summary="done",
        tool_name="grep",
        tool_call_id="call-1",
        ok=True,
        at_ms=10.0,
    )
    restored = SubagentActivity.from_dict(activity.to_dict())
    assert restored == activity
    assert activity.is_persistable() is True


def test_subagent_activity_thinking_not_persistable() -> None:
    from openjiuwen.harness.subagent_runtime.models import SubagentActivity

    activity = SubagentActivity(
        subagent_id="sid",
        task_id="task-1",
        seq=1,
        kind="thinking",
        summary="plan",
        at_ms=1.0,
    )
    assert activity.is_persistable() is False


def test_spawn_and_wait_result_shapes() -> None:
    from openjiuwen.harness.subagent_runtime.models import SpawnResult, WaitResult

    status = SubagentStatus.running()
    spawn = SpawnResult(subagent_id="sid", task_id="tid", status=status)
    assert spawn.status is status

    wait = WaitResult(
        statuses={"sid": SubagentStatus.completed("ok")},
        results={"sid": "ok"},
        output_files={"sid": "/tmp/sid/output.md"},
        timed_out=False,
    )
    assert wait.results["sid"] == "ok"
    assert wait.output_files["sid"] == "/tmp/sid/output.md"
    assert wait.timed_out is False


def test_op_types_are_frozen() -> None:
    op = UserInputOp(query="hello", task_id="task-1")
    assert op.query == "hello"
    shutdown = ShutdownOp(reason="parent_ended")
    assert shutdown.reason == "parent_ended"


def test_new_task_id_is_hex() -> None:
    task_id = new_task_id()
    assert len(task_id) == 32
    assert re.fullmatch(r"[0-9a-f]{32}", task_id)


def test_build_subagent_id_sticky() -> None:
    sid = build_subagent_id("parent", "explore", sticky=True)
    assert sid == "parent_sub_explore"


def test_build_subagent_id_non_sticky() -> None:
    sid = build_subagent_id("parent", "explore", sticky=False)
    assert sid.startswith("parent_sub_explore_")
    assert len(sid.split("_")[-1]) == 8


def test_build_subagent_id_strips_type() -> None:
    sid = build_subagent_id("parent", "  explore  ", sticky=True)
    assert sid == "parent_sub_explore"


@pytest.mark.parametrize(
    ("display_name", "role", "expected_display", "expected_role"),
    [
        ("Ethan", "市场研究员", "Ethan", "市场研究员"),
        (None, None, "research_agent", ""),
        ("", "", "research_agent", ""),
        ("  Ada  ", None, "Ada", ""),
        (None, "  coder  ", "research_agent", "coder"),
    ],
)
def test_resolve_presentation(
    display_name: str | None,
    role: str | None,
    expected_display: str,
    expected_role: str,
) -> None:
    resolved = resolve_presentation(
        subagent_type="research_agent",
        display_name=display_name,
        role=role,
    )
    assert resolved == (expected_display, expected_role)
