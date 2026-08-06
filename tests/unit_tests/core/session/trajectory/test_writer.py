# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from openjiuwen.core.session.trajectory.writer import TrajectoryWriter, read_trajectory

pytestmark = pytest.mark.asyncio


class _Opaque:
    pass


def _document(steps: list[dict], final_metrics: dict | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "session_id": "s1",
        "trajectory_id": "t1",
        "steps": steps,
        "final_metrics": final_metrics,
    }


async def test_snapshot_and_read_round_trip(tmp_path):
    writer = TrajectoryWriter(tmp_path, "s1")
    await writer.write_snapshot(
        "t1.json",
        _document(
            [{"step_id": 1, "role": "agent", "message": "hello"}],
            final_metrics={"total_tokens": 7, "llm_call_count": 1},
        ),
    )

    path = writer.path_for("t1.json")
    assert path == tmp_path / "s1" / "t1.json"
    trajectory = read_trajectory(path)
    assert trajectory.session_id == "s1"
    assert trajectory.trajectory_id == "t1"
    assert len(trajectory.steps) == 1
    assert trajectory.steps[0].message == "hello"
    assert trajectory.final_metrics.total_tokens == 7


async def test_snapshot_overwrites_previous_state(tmp_path):
    # The file must always reflect the latest snapshot, not accumulate lines.
    writer = TrajectoryWriter(tmp_path, "s1")
    await writer.write_snapshot("t1.json", _document([{"step_id": 1, "role": "agent"}]))
    await writer.write_snapshot(
        "t1.json", _document([{"step_id": 1, "role": "agent"}, {"step_id": 2, "role": "system"}])
    )

    trajectory = read_trajectory(writer.path_for("t1.json"))
    assert [step.step_id for step in trajectory.steps] == [1, 2]


async def test_no_temp_file_left_behind(tmp_path):
    writer = TrajectoryWriter(tmp_path, "s1")
    await writer.write_snapshot("t1.json", _document([{"step_id": 1, "role": "agent"}]))
    assert [p.name for p in (tmp_path / "s1").iterdir()] == ["t1.json"]


async def test_nested_rel_path_creates_parent_dirs(tmp_path):
    # Subagent snapshots live at <run_dir>/subagents/<stem>.json; the writer
    # must create the intermediate directories on demand.
    writer = TrajectoryWriter(tmp_path, "s1")
    await writer.write_snapshot("run-1/subagents/t2.json", _document([{"step_id": 1, "role": "agent"}]))
    assert writer.path_for("run-1/subagents/t2.json") == tmp_path / "s1" / "run-1" / "subagents" / "t2.json"
    assert (tmp_path / "s1" / "run-1" / "subagents" / "t2.json").is_file()


async def test_non_serializable_payload_is_flagged_not_fatal(tmp_path):
    writer = TrajectoryWriter(tmp_path, "s1")
    await writer.write_snapshot(
        "t1.json", _document([{"step_id": 1, "role": "agent", "extra": {"o": _Opaque()}}])
    )
    document = json.loads(writer.path_for("t1.json").read_text(encoding="utf-8"))
    assert document["steps"][0]["extra"]["o"] == "<<non-serializable: _Opaque>>"
