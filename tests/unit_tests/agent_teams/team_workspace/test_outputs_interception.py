# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamWorkspaceRail outputs-directory interception."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail


class _FakeConfig:
    def __init__(self, conflict_strategy, version_control=True):
        self.conflict_strategy = conflict_strategy
        self.version_control = version_control


class _FakeManager:
    def __init__(self, workspace_path, team_name="beta", config=None):
        self.workspace_path = workspace_path
        self.team_name = team_name
        self.config = config or _FakeConfig("lock")
        self.mode = "LOCAL"
        self.publish_event = None
        self.committed = []
        self.locks = {}

    def get_lock(self, path):
        return self.locks.get(path)

    async def auto_commit(self, real_path, member_name):
        self.committed.append((real_path, member_name))


def _ctx(tool_name, file_path):
    inputs = SimpleNamespace(tool_name=tool_name, tool_args={"file_path": file_path})
    return SimpleNamespace(inputs=inputs, extra={})


def _make_rail(tmp_path, outputs_dir=None):
    ws_path = str(tmp_path / "team-workspace")
    os.makedirs(ws_path, exist_ok=True)
    manager = _FakeManager(ws_path)
    return TeamWorkspaceRail(manager, "member-1", outputs_dir=outputs_dir), manager


@pytest.mark.level0
def test_no_outputs_dir_never_intercepts(tmp_path):
    rail, _ = _make_rail(tmp_path, outputs_dir=None)
    # Any path is treated as outside the deliverables dir.
    assert not rail._is_deliverable_path("/anywhere/file.md")
    assert not rail._is_deliverable_path(".team/beta/file.md")


@pytest.mark.level0
def test_outputs_dir_intercepts_inside_only(tmp_path):
    outputs = tmp_path / "team-workspace" / "artifacts" / "2026-09-01" / "chat-1" / "outputs"
    outputs.mkdir(parents=True)
    rail, _ = _make_rail(tmp_path, outputs_dir=str(outputs))
    assert rail._is_deliverable_path(str(outputs / "report.md"))
    # A sibling outside outputs is not intercepted.
    assert not rail._is_deliverable_path(str(outputs.parent / "work" / "draft.md"))


@pytest.mark.asyncio
@pytest.mark.level0
async def test_write_to_outputs_triggers_commit(tmp_path):
    outputs = tmp_path / "team-workspace" / "artifacts" / "2026-09-01" / "chat-1" / "outputs"
    outputs.mkdir(parents=True)
    rail, manager = _make_rail(tmp_path, outputs_dir=str(outputs))
    target = str(outputs / "report.md")
    ctx = _ctx("write_file", target)
    await rail.after_tool_call(ctx)
    assert manager.committed, "auto_commit should run for a write under outputs"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_write_outside_outputs_no_commit(tmp_path):
    outputs = tmp_path / "team-workspace" / "artifacts" / "2026-09-01" / "chat-1" / "outputs"
    outputs.mkdir(parents=True)
    rail, manager = _make_rail(tmp_path, outputs_dir=str(outputs))
    ctx = _ctx("write_file", str(tmp_path / "elsewhere.md"))
    await rail.after_tool_call(ctx)
    assert not manager.committed, "writes outside outputs must not commit"


@pytest.mark.level0
def test_resolve_workspace_relative(tmp_path):
    outputs = tmp_path / "team-workspace" / "artifacts" / "2026-09-01" / "chat-1" / "outputs"
    outputs.mkdir(parents=True)
    rail, manager = _make_rail(tmp_path, outputs_dir=str(outputs))
    target = str(outputs / "report.md")
    rel = rail._resolve_workspace_relative(target)
    assert rel == os.path.join(
        "artifacts", "2026-09-01", "chat-1", "outputs", "report.md"
    )
