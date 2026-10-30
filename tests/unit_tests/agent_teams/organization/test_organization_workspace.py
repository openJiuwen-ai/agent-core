"""Organization workspace isolation, mounting, locking, and Git coverage."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.organization import workspace as workspace_module
from openjiuwen.agent_teams.organization.workspace import (
    OrganizationWorkspaceConfig,
    OrganizationWorkspaceManager,
    validate_organization_id,
)
from openjiuwen.agent_teams.organization.workspace_rail import OrganizationWorkspaceRail
from openjiuwen.agent_teams.runtime.pool import RuntimeState
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.tools.worktree.git import _run_git


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_summary_submission_only_finishes_on_success(success):
    from openjiuwen.agent_teams.organization.runtime import _SummaryCloseoutRail
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs
    from openjiuwen.harness.tools.base_tool import ToolOutput

    rail = _SummaryCloseoutRail("summary")
    ctx = AgentCallbackContext(event="after_tool_call", agent=None, inputs=ToolCallInputs(
        tool_name="org_summary_complete", tool_result=ToolOutput(success=success),
        tool_args={"output_context": {"description": "verified report"}},
    ))
    await rail.after_tool_call(ctx)
    finish = ctx.consume_force_finish()
    assert (finish is not None) is success
    if finish:
        assert finish.result["output"] == "verified report"


@pytest.mark.asyncio
async def test_summary_drafting_dispatch_is_bounded():
    from openjiuwen.agent_teams.organization.runtime import _SummaryCloseoutRail
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs

    rail = _SummaryCloseoutRail("summary")
    for attempt in range(3):
        ctx = AgentCallbackContext(event="before_tool_call", agent=None, inputs=ToolCallInputs(
            tool_name="send_message", tool_args={"to": "delivery-drafter"},
        ))
        await rail.before_tool_call(ctx)
        assert bool(ctx.extra.get("_skip_tool")) is (attempt == 2)


@pytest.mark.asyncio
async def test_summary_budget_does_not_reset_when_switching_tasks():
    from openjiuwen.agent_teams.organization.runtime import _SummaryCloseoutRail
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs
    from openjiuwen.harness.tools.base_tool import ToolOutput

    rail = _SummaryCloseoutRail("summary")
    for task_id in ("one", "one", "two", "one"):
        read = AgentCallbackContext(event="after_tool_call", agent=None, inputs=ToolCallInputs(
            tool_name="org_summary_get_inputs", tool_args={"summary_task_id": task_id},
            tool_result=ToolOutput(success=True),
        ))
        await rail.after_tool_call(read)
        send = AgentCallbackContext(event="before_tool_call", agent=None, inputs=ToolCallInputs(
            tool_name="send_message", tool_args={"to": "delivery-drafter"},
        ))
        await rail.before_tool_call(send)
    assert send.extra["_skip_tool"] is True


@pytest.mark.asyncio
async def test_summary_failure_wakes_leader_without_settled_board_and_escalates_once():
    from unittest.mock import AsyncMock

    from openjiuwen.agent_teams.organization.runtime import OrganizationRuntimeManager, _SummaryCloseoutRail
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs
    from openjiuwen.harness.tools.base_tool import ToolOutput

    execution = SimpleNamespace(
        execution_id="exec", summary_task_id="summary-task", root_task_id="root",
        summary_team_id="summary", status="RUNNING",
    )
    # Use the actual execution enum value, not a Harness task-completion marker.
    from openjiuwen.agent_teams.organization.schema import OrgSummaryExecutionStatus
    execution.status = OrgSummaryExecutionStatus.RUNNING.value
    manager = SimpleNamespace(
        list_incomplete_summary_executions=AsyncMock(return_value=[execution]),
        get_task=AsyncMock(return_value=SimpleNamespace(task_id="summary-task")),
    )
    messages = SimpleNamespace(send_message=AsyncMock())
    backend = SimpleNamespace(org_task_manager=manager, message_manager=messages, leader_member_name="leader")
    agent = SimpleNamespace(team_backend=backend, spec=SimpleNamespace(metadata={"summary_team": True}, language="cn"))
    rail = _SummaryCloseoutRail("summary")
    agent.harness = SimpleNamespace(find_rails_by_type=lambda _types: [rail])
    pool = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        agent=agent, current_session_id="session", state=RuntimeState.RUNNING,
    )))
    runtime = OrganizationRuntimeManager(SimpleNamespace(pool=pool))
    runtime._notify_summary_blocker = AsyncMock()
    other = SimpleNamespace(**{**vars(execution), "execution_id": "other", "summary_task_id": "other-task"})
    manager.list_incomplete_summary_executions.return_value = [execution, other]
    await runtime.notify_summary_member_failure(
        team_id="summary", session_id="session", member_name="delivery-drafter", reason="error", turn_id="ambiguous",
    )
    messages.send_message.assert_not_awaited()
    for turn in ("error-1", "error-1", "error-2", "error-3"):
        await runtime.notify_summary_member_failure(
            team_id="summary", session_id="session", member_name="delivery-drafter",
            reason="Max iterations reached without completion", turn_id=turn,
            summary_task_id="summary-task",
        )
    assert messages.send_message.await_count == 2
    assert messages.send_message.call_args.kwargs["to_member_name"] == "leader"
    assert "True" in messages.send_message.call_args.kwargs["content"]
    runtime._notify_summary_blocker.assert_awaited_once()
    assert runtime._summary_closeout_attempts[("session", "summary", "exec")] == 2
    assert ("session", "summary", "other") not in runtime._summary_member_failures
    await rail.after_tool_call(AgentCallbackContext(event="after_tool_call", agent=None, inputs=ToolCallInputs(
        tool_name="org_summary_get_inputs", tool_args={"summary_task_id": "summary-task"},
        tool_result=ToolOutput(success=True),
    )))
    send = AgentCallbackContext(event="before_tool_call", agent=None, inputs=ToolCallInputs(
        tool_name="send_message", tool_args={"to": "delivery-drafter"},
    ))
    await rail.before_tool_call(send)
    assert send.extra["_skip_tool"] is True
    agent.spec.metadata = {}
    await runtime.notify_summary_member_failure(
        team_id="summary", session_id="session", member_name="delivery-drafter", reason="error", turn_id="normal",
    )
    assert messages.send_message.await_count == 2


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
    user_name = await _run_git(["config", "--local", "user.name"], cwd=str(root), check=True)
    user_email = await _run_git(["config", "--local", "user.email"], cwd=str(root), check=True)
    assert user_name.stdout == "OpenJiuwen"
    assert user_email.stdout == "openjiuwen@example.invalid"


@pytest.mark.asyncio
async def test_concurrent_organization_workspace_initialize_serializes_git_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent Team bindings must not write the shared Git config together."""
    manager = OrganizationWorkspaceManager(
        organization_id="org-1",
        session_id="session-1",
        config=OrganizationWorkspaceConfig(root_path=str(tmp_path / "organization")),
    )
    await manager.initialize()
    run_git = workspace_module._run_git
    active_configs = 0
    max_active_configs = 0

    async def tracked_run_git(args, **kwargs):
        """Expose overlapping config writes while retaining real Git behavior."""
        nonlocal active_configs, max_active_configs
        if args[0] != "config":
            return await run_git(args, **kwargs)
        active_configs += 1
        max_active_configs = max(max_active_configs, active_configs)
        try:
            await asyncio.sleep(0.01)
            return await run_git(args, **kwargs)
        finally:
            active_configs -= 1

    monkeypatch.setattr(workspace_module, "_run_git", tracked_run_git)
    await asyncio.gather(*(manager.initialize() for _ in range(3)))

    assert max_active_configs == 1


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
async def test_summary_closeout_tool_policy_is_request_local(tmp_path: Path) -> None:
    from openjiuwen.agent_teams.organization.runtime import _summary_closeout_scope, _SummaryCloseoutRail

    rail = _SummaryCloseoutRail("summary")
    tools = [SimpleNamespace(name=name) for name in
             ("read_file", "org_summary_get_inputs", "org_summary_complete", "bash", "create_task", "send_message")]
    model_inputs = SimpleNamespace(tools=list(tools))
    model_ctx = AgentCallbackContext(event="before_model_call", agent=None, inputs=model_inputs)
    with _summary_closeout_scope("other-team"):
        await rail.before_model_call(model_ctx)
        assert model_inputs.tools == tools
    with _summary_closeout_scope("summary"):
        await rail.before_model_call(model_ctx)
        assert [tool.name for tool in model_inputs.tools] == [
            "read_file", "org_summary_get_inputs", "org_summary_complete",
        ]
        for name in ("bash", "create_task", "send_message", "org_create_organization"):
            inputs = SimpleNamespace(tool_name=name, tool_args={}, tool_call=SimpleNamespace(id="call"))
            ctx = AgentCallbackContext(event="before_tool_call", agent=None, inputs=inputs)
            await rail.before_tool_call(ctx)
            assert ctx.extra["_skip_tool"] is True
        inputs = SimpleNamespace(tool_name="org_summary_complete", tool_args={})
        ctx = AgentCallbackContext(event="before_tool_call", agent=None, inputs=inputs)
        await rail.before_tool_call(ctx)
        assert not ctx.extra.get("_skip_tool")
    model_inputs.tools = list(tools)
    await rail.before_model_call(model_ctx)
    assert model_inputs.tools == tools
    inputs = SimpleNamespace(tool_name="bash", tool_args={})
    ctx = AgentCallbackContext(event="before_tool_call", agent=None, inputs=inputs)
    await rail.before_tool_call(ctx)
    assert not ctx.extra.get("_skip_tool")


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
