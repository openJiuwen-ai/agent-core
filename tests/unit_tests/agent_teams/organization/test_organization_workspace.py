"""Organization workspace isolation, mounting, locking, and Git coverage."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.organization.workspace import (
    OrganizationWorkspaceConfig,
    OrganizationWorkspaceManager,
    validate_organization_id,
)
from openjiuwen.agent_teams.organization.workspace_rail import OrganizationWorkspaceRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


@pytest.mark.asyncio
async def test_organization_workspace_mount_and_local_git(tmp_path: Path) -> None:
    root = tmp_path / "organization"
    member = tmp_path / "member"
    member.mkdir()
    manager = OrganizationWorkspaceManager(
        organization_id="org-1",
        session_id="session-1",
        config=OrganizationWorkspaceConfig(root_path=str(root)),
    )

    await manager.initialize()
    manager.ensure_team_directory("team-a")
    manager.mount_into_workspace(str(member))

    mounted = member / ".organization" / "org-1"
    assert mounted.samefile(root)
    artifact = root / "teams" / "team-a" / "report.md"
    artifact.write_text("result", encoding="utf-8")
    sha = await manager.auto_commit_for_actor(
        "teams/team-a/report.md",
        team_id="team-a",
        member_name="leader",
    )
    assert sha
    assert (root / ".git").is_dir()


def test_organization_workspace_write_boundaries(tmp_path: Path) -> None:
    manager = OrganizationWorkspaceManager(
        organization_id="org-1",
        session_id="session-1",
        config=OrganizationWorkspaceConfig(root_path=str(tmp_path / "organization"), version_control=False),
    )

    assert manager.can_write("teams/team-a/report.md", team_id="team-a", summary_team=False)
    assert not manager.can_write("teams/team-b/report.md", team_id="team-a", summary_team=False)
    assert manager.can_write("shared/input.md", team_id="team-a", summary_team=False)
    assert manager.can_write("summary/final.md", team_id="summary", summary_team=True)
    assert not manager.can_write("teams/team-a/report.md", team_id="summary", summary_team=True)
    with pytest.raises(ValueError, match="invalid organization workspace path"):
        manager.relative_path(".organization/org-1/../outside.txt")


@pytest.mark.parametrize("organization_id", ["../outside", "nested/org", "nested\\org", "C:\\outside", " org"])
def test_organization_id_must_be_a_safe_path_segment(organization_id: str) -> None:
    with pytest.raises(ValueError, match="organization_id must use"):
        validate_organization_id(organization_id)


@pytest.mark.asyncio
async def test_organization_workspace_rail_blocks_cross_team_write(tmp_path: Path) -> None:
    member = tmp_path / "member"
    member.mkdir()
    manager = OrganizationWorkspaceManager(
        organization_id="org-1",
        session_id="session-1",
        config=OrganizationWorkspaceConfig(root_path=str(tmp_path / "organization"), version_control=False),
    )
    rail = OrganizationWorkspaceRail(
        manager,
        team_id="team-a",
        member_name="leader",
    )
    manager.ensure_team_directory("team-b")
    manager.mount_into_workspace(str(member))
    inputs = SimpleNamespace(
        tool_name="write_file",
        tool_args={"file_path": str(member / ".organization" / "org-1" / "teams" / "team-b" / "report.md")},
        tool_call=SimpleNamespace(id="call-1"),
        tool_result=None,
        tool_msg=None,
    )
    ctx = AgentCallbackContext(event="before_tool_call", agent=None, inputs=inputs)

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert "cannot write" in ctx.inputs.tool_result["error"]
