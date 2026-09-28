# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent parent-session persistence helpers."""

from __future__ import annotations

from openjiuwen.agent_teams.runtime.metadata import TEAMS_KEY, merge_team_namespace
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.subagent_runtime.models import SubagentRecord, SubagentTurn
from openjiuwen.harness.subagent_runtime.persistence import (
    SUBAGENTS_KEY,
    merge_subagent_bucket,
    read_subagent_bucket,
    trim_persisted_bucket,
)


def _record(sid: str, *, closed_at_ms: float | None = None) -> dict:
    return SubagentRecord(
        subagent_id=sid,
        subagent_type="explore",
        display_name=sid,
        role="r",
        task_description="hello",
        created_at_ms=1.0,
        updated_at_ms=2.0,
        closed_at_ms=closed_at_ms,
        closed_reason="manual" if closed_at_ms is not None else None,
    ).to_dict()


def test_subagent_record_round_trip() -> None:
    record = SubagentRecord(
        subagent_id="sid",
        subagent_type="explore",
        display_name="Explorer",
        role="researcher",
        task_description="hello",
        created_at_ms=1.0,
        updated_at_ms=2.0,
        closed_at_ms=None,
        closed_reason=None,
    )
    restored = SubagentRecord.from_dict(record.to_dict())
    assert restored == record
    assert restored.is_closed is False


def test_subagent_record_closed_flag() -> None:
    record = SubagentRecord.from_dict(_record("sid", closed_at_ms=3.0))
    assert record.is_closed is True


def test_subagent_turn_round_trip_and_seq() -> None:
    turn = SubagentTurn(
        subagent_id="sid",
        task_id="task-1",
        seq=2,
        prompt="hello",
        answer="done",
        closed_reason="completed",
        created_at_ms=10.0,
    )
    restored = SubagentTurn.from_dict(turn.to_dict())
    assert restored == turn


def test_merge_subagent_bucket_preserves_other_namespaces() -> None:
    session = Session(session_id="parent")
    merge_team_namespace(session, "team-a", {"spec": {"name": "team-a"}})
    merge_subagent_bucket(
        session,
        {
            "records": {"sid": _record("sid", closed_at_ms=5.0)},
            "revision": 1,
        },
    )

    assert read_subagent_bucket(session)["records"]["sid"]["subagent_id"] == "sid"
    assert session.get_state(TEAMS_KEY)["team-a"]["spec"]["name"] == "team-a"


def test_read_subagent_bucket_returns_empty_for_missing_or_invalid() -> None:
    session = Session(session_id="parent")
    bucket = read_subagent_bucket(session)
    assert bucket["records"] == {}
    assert bucket["turns"] == {}
    assert bucket["revision"] == 0

    session.update_state({SUBAGENTS_KEY: "invalid"})
    bucket = read_subagent_bucket(session)
    assert bucket["records"] == {}


def test_merge_subagent_bucket_increments_revision() -> None:
    session = Session(session_id="parent")
    merge_subagent_bucket(session, {"records": {"a": _record("a")}, "revision": 1})
    merge_subagent_bucket(session, {"records": {"b": _record("b")}, "revision": 2})
    bucket = read_subagent_bucket(session)
    assert set(bucket["records"]) == {"a", "b"}
    assert bucket["revision"] == 2


def test_trim_persisted_bucket_pairs_records_and_turns() -> None:
    records = {f"sid-{index}": _record(f"sid-{index}", closed_at_ms=float(index)) for index in range(3)}
    turns = {f"sid-{index}": [{"seq": 1, "task_id": "t"}] for index in range(3)}
    trimmed_records, trimmed_turns, trimmed_activities = trim_persisted_bucket(
        records,
        turns,
        max_records=2,
    )
    assert len(trimmed_records) == 2
    assert set(trimmed_records) == set(trimmed_turns)


def test_trim_persisted_bucket_pairs_records_turns_and_activities() -> None:
    records = {f"sid-{index}": _record(f"sid-{index}", closed_at_ms=float(index)) for index in range(3)}
    turns = {f"sid-{index}": [{"seq": 1, "task_id": "t"}] for index in range(3)}
    activities = {f"sid-{index}": [{"seq": 1, "kind": "tool_call"}] for index in range(3)}
    trimmed_records, trimmed_turns, trimmed_activities = trim_persisted_bucket(
        records,
        turns,
        max_records=2,
        activities=activities,
    )
    assert len(trimmed_records) == 2
    assert set(trimmed_records) == set(trimmed_turns)
    assert set(trimmed_records) == set(trimmed_activities)
