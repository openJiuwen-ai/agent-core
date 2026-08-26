# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for legacy team workspace paths."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.team_workspace.models import ConflictStrategy, WorkspaceMode
from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail


@pytest.mark.asyncio
@pytest.mark.level0
async def test_legacy_hub_path_is_rewritten_before_read_tool_runs():
    team_name = "oc_team_preset-research-insight_officeclaw_1a03cd0f2a9_26b2ec1a2e78"
    workspace = SimpleNamespace(
        workspace_path="/shared/team-workspace",
        team_name=team_name,
        mode=WorkspaceMode.LOCAL,
    )
    rail = TeamWorkspaceRail(workspace_manager=workspace, member_name="researcher")
    tool_args = {"file_path": f".team/{team_name}/artifacts/report.md"}
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(tool_name="read_file", tool_args=tool_args),
    )

    await rail.before_tool_call(ctx)

    assert tool_args["file_path"] == ".team/artifacts/report.md"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_flat_windows_write_path_is_persisted_for_after_tool_call():
    commits = []

    async def auto_commit(path, member_name):
        commits.append((path, member_name))

    workspace = SimpleNamespace(
        workspace_path=r"C:\shared\team-workspace",
        team_name="team-alpha",
        mode=WorkspaceMode.LOCAL,
        config=SimpleNamespace(
            conflict_strategy=ConflictStrategy.MERGE,
            version_control=True,
        ),
        auto_commit=auto_commit,
        publish_event=None,
    )
    rail = TeamWorkspaceRail(workspace_manager=workspace, member_name="researcher")
    tool_args = {"file_path": r".team\artifacts\report.md"}
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(tool_name="write_file", tool_args=tool_args),
        extra={},
    )

    await rail.before_tool_call(ctx)
    await rail.after_tool_call(ctx)

    assert tool_args["file_path"] == ".team/artifacts/report.md"
    assert commits == [("artifacts/report.md", "researcher")]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_non_team_windows_path_is_not_rewritten():
    workspace = SimpleNamespace(
        workspace_path=r"C:\shared\team-workspace",
        team_name="team-alpha",
        mode=WorkspaceMode.LOCAL,
    )
    rail = TeamWorkspaceRail(workspace_manager=workspace, member_name="researcher")
    tool_args = {"file_path": r"docs\report.md"}
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(tool_name="read_file", tool_args=tool_args),
    )

    await rail.before_tool_call(ctx)

    assert tool_args["file_path"] == r"docs\report.md"
