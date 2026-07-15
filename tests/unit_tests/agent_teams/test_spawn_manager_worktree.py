# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Focused tests for session-scoped teammate worktrees."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import (
    configure_openjiuwen_home,
    project_worktree_hash,
    reset_openjiuwen_home,
    team_session_worktrees_dir,
)
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.member_options import (
    MemberWorktreeOptions,
    build_member_options,
    get_member_worktree,
    set_member_worktree_options,
)
from openjiuwen.agent_teams.worktree.naming import build_teammate_worktree_name
from openjiuwen.agent_teams.worktree.lifecycle import (
    MemberWorktreeInfo,
    TeammateWorktreeLifecycle,
    WorktreeContributionState,
)
from openjiuwen.harness.tools.worktree import WorktreeConfig
from openjiuwen.harness.tools.worktree.models import WorktreeChangeSummary


class _FakeMemberDao:
    def __init__(self, member: SimpleNamespace) -> None:
        self.member = member

    async def get_member(self, member_name: str, team_name: str) -> SimpleNamespace | None:
        if self.member.member_name == member_name and self.member.team_name == team_name:
            return self.member
        return None

    async def get_team_members(self, team_name: str) -> list[SimpleNamespace]:
        return [self.member] if self.member.team_name == team_name else []

    async def update_member_worktree(
        self,
        member_name: str,
        team_name: str,
        worktree: MemberWorktreeOptions | None,
    ) -> bool:
        self.member.options = set_member_worktree_options(
            getattr(self.member, "options", None),
            worktree,
        )
        return True

    async def update_member_status(self, member_name: str, team_name: str, status: str) -> bool:
        if self.member.member_name != member_name or self.member.team_name != team_name:
            return False
        self.member.status = status
        return True


class _FakeTeamBackend:
    def __init__(self, member: SimpleNamespace) -> None:
        self.team_name = member.team_name
        self.db = SimpleNamespace(member=_FakeMemberDao(member))

    async def get_member(self, member_name: str) -> SimpleNamespace | None:
        return await self.db.member.get_member(member_name, self.team_name)

    def get_external_cli_agent(self, member_name: str) -> None:
        return None


class _FakeWorktreeManager:
    def __init__(self) -> None:
        self.worktrees_dir: str | None = None
        self.create_calls: list[tuple[str, str | None]] = []
        self.count_changes = AsyncMock(return_value=WorktreeChangeSummary())
        self.remove_worktree = AsyncMock(return_value=True)

    def with_worktrees_dir(self, worktrees_dir: str) -> "_FakeWorktreeManager":
        self.worktrees_dir = worktrees_dir
        return self

    async def create_owner_worktree(self, slug: str, *, source_dir: str | None = None) -> SimpleNamespace:
        self.create_calls.append((slug, source_dir))
        assert self.worktrees_dir is not None
        return SimpleNamespace(
            worktree_path=os.path.join(self.worktrees_dir, slug),
            worktree_branch=f"worktree-{slug}",
            head_commit="base-sha",
        )


@pytest.fixture(autouse=True)
def _session_scope():
    token = set_session_id("session-1")
    yield
    reset_session_id(token)


def _member(**overrides) -> SimpleNamespace:
    data = {
        "member_name": "dev-one",
        "team_name": "code-team",
        "role": TeamRole.TEAMMATE.value,
        "status": MemberStatus.READY.value,
        "desc": "developer",
        "options": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _project(tmp_path) -> str:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return str(project_dir)


def _expected_scope(project_dir: str) -> tuple[str, str, str]:
    project_hash = project_worktree_hash(project_dir)
    root = str(team_session_worktrees_dir("code-team", "session-1"))
    slug = build_teammate_worktree_name(
        team_name="code-team",
        member_name="dev-one",
        session_id="session-1",
        project_hash=project_hash,
    )
    return project_hash, root, slug


def _stored_worktree(
    *,
    path: str,
    project_dir: str,
    session_id: str = "session-1",
    branch: str | None = None,
    head_commit: str = "base-sha",
) -> MemberWorktreeOptions:
    project_hash = project_worktree_hash(project_dir)
    return MemberWorktreeOptions(
        isolation="worktree",
        path=path,
        session_id=session_id,
        project_hash=project_hash,
        managed_root=str(team_session_worktrees_dir("code-team", session_id)),
        worktree_branch=branch,
        head_commit=head_commit,
    )


def _spawn_manager(member: SimpleNamespace, manager: _FakeWorktreeManager, project_dir: str | None) -> SpawnManager:
    team_spec = TeamSpec(
        team_name=member.team_name,
        display_name=member.team_name,
        leader_member_name="leader",
        model_pool=[],
    )
    build_context = BuildContext(project_dir=project_dir)
    build_context.mode = "code.team"
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name=member.team_name,
        worktree=WorktreeConfig(enabled=True),
        build_context=build_context,
        build_context_seed={"project_dir": project_dir, "mode": "code.team"},
    )
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=team_spec)
    configurator = SimpleNamespace(
        team_backend=_FakeTeamBackend(member),
        spec=spec,
        ctx=ctx,
        team_spec=team_spec,
        worktree_manager=manager,
        member_name="leader",
        team_name=member.team_name,
        build_member_messager_config=lambda member_name: None,
    )
    return SpawnManager(
        state=SimpleNamespace(),
        configurator=configurator,
        team_agent_getter=lambda: None,
    )


@pytest.mark.asyncio
async def test_team_clean_skips_member_without_worktree_or_project_dir(tmp_path):
    openjiuwen_home = tmp_path / "openjiuwen-home"
    configure_openjiuwen_home(openjiuwen_home)
    try:
        member = _member()
        manager = _FakeWorktreeManager()
        spawn_manager = _spawn_manager(member, manager, None)

        await spawn_manager.worktree_lifecycle.finalize_all_member_worktrees_for_team_clean()

        manager.count_changes.assert_not_awaited()
        manager.remove_worktree.assert_not_awaited()
    finally:
        reset_openjiuwen_home()


@pytest.mark.asyncio
async def test_team_clean_requires_project_dir_for_existing_worktree(tmp_path):
    project_dir = _project(tmp_path)
    worktree_path = tmp_path / "member-worktree"
    worktree_path.mkdir()
    member = _member(
        options=build_member_options(
            worktree=_stored_worktree(path=str(worktree_path), project_dir=project_dir)
        )
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager, None)

    with pytest.raises(RuntimeError, match="build_context.project_dir"):
        await spawn_manager.worktree_lifecycle.finalize_all_member_worktrees_for_team_clean()

    manager.remove_worktree.assert_not_awaited()


@pytest.mark.asyncio
async def test_creates_teammate_worktree_under_session_system_dir(tmp_path):
    project_dir = _project(tmp_path)
    project_hash, managed_root, slug = _expected_scope(project_dir)
    member = _member(options=build_member_options(worktree_isolation="worktree"))
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager, project_dir)

    ctx = await spawn_manager.build_context_from_db("dev-one")

    assert ctx is not None
    assert ctx.worktree_path == os.path.join(managed_root, slug)
    assert manager.create_calls == [(slug, project_dir)]
    worktree = get_member_worktree(member)
    assert worktree is not None
    assert worktree.path == ctx.worktree_path
    assert worktree.session_id == "session-1"
    assert worktree.project_hash == project_hash
    assert worktree.managed_root == managed_root


@pytest.mark.asyncio
async def test_does_not_reuse_foreign_session_worktree(tmp_path):
    project_dir = _project(tmp_path)
    _, managed_root, slug = _expected_scope(project_dir)
    foreign = tmp_path / "foreign-session-worktree"
    foreign.mkdir()
    member = _member(
        options=build_member_options(
            worktree=_stored_worktree(path=str(foreign), project_dir=project_dir, session_id="old-session")
        )
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager, project_dir)

    ctx = await spawn_manager.build_context_from_db("dev-one")

    assert ctx is not None
    assert ctx.worktree_path == os.path.join(managed_root, slug)
    assert manager.create_calls == [(slug, project_dir)]


@pytest.mark.asyncio
async def test_shutdown_requested_member_context_does_not_recreate_worktree(tmp_path):
    project_dir = _project(tmp_path)
    member = _member(
        status=MemberStatus.SHUTDOWN_REQUESTED.value,
        options=build_member_options(worktree_isolation="worktree"),
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager, project_dir)

    ctx = await spawn_manager.build_context_from_db("dev-one")

    assert ctx is not None
    assert ctx.worktree_path is None
    assert manager.create_calls == []
    worktree = get_member_worktree(member)
    assert worktree is not None
    assert worktree.path is None


@pytest.mark.asyncio
async def test_unhealthy_shutdown_requested_member_is_not_restarted(tmp_path):
    project_dir = _project(tmp_path)
    member = _member(status=MemberStatus.SHUTDOWN_REQUESTED.value)
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager, project_dir)
    spawn_manager.cleanup_teammate = AsyncMock()
    spawn_manager.restart_teammate = AsyncMock()

    await spawn_manager.on_teammate_unhealthy("dev-one")

    spawn_manager.cleanup_teammate.assert_awaited_once_with("dev-one")
    spawn_manager.restart_teammate.assert_not_awaited()
    assert member.status == MemberStatus.SHUTDOWN_REQUESTED.value


@pytest.mark.asyncio
async def test_active_cleanup_removes_clean_non_contributing_worktree(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    _, managed_root, slug = _expected_scope(project_dir)
    worktree_path = tmp_path / "review-worktree"
    worktree_path.mkdir()
    branch = f"worktree-{slug}"
    member = _member(
        options=build_member_options(
            worktree=_stored_worktree(path=str(worktree_path), project_dir=project_dir, branch=branch)
        )
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager, project_dir)

    from openjiuwen.harness.tools.worktree import git as worktree_git

    monkeypatch.setattr(worktree_git, "find_canonical_git_root", AsyncMock(return_value=project_dir))
    monkeypatch.setattr(
        worktree_git,
        "rev_parse",
        AsyncMock(side_effect=lambda ref, cwd: "base-sha" if ref in {branch, "base-sha"} else None),
    )

    await spawn_manager.worktree_lifecycle.finalize_non_contributing_member_worktrees()

    manager.remove_worktree.assert_awaited_once_with(str(worktree_path), project_dir, force=False)
    worktree = get_member_worktree(member)
    assert worktree is not None
    assert worktree.path is None
    assert managed_root.endswith("/worktrees")


@pytest.mark.asyncio
async def test_active_cleanup_keeps_contributing_worktree_until_team_clean(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    _, _, slug = _expected_scope(project_dir)
    worktree_path = tmp_path / "writer-worktree"
    worktree_path.mkdir()
    branch = f"worktree-{slug}"
    member = _member(
        options=build_member_options(
            worktree=_stored_worktree(path=str(worktree_path), project_dir=project_dir, branch=branch)
        )
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager, project_dir)

    from openjiuwen.harness.tools.worktree import git as worktree_git

    monkeypatch.setattr(worktree_git, "find_canonical_git_root", AsyncMock(return_value=project_dir))
    monkeypatch.setattr(
        worktree_git,
        "rev_parse",
        AsyncMock(side_effect=lambda ref, cwd: {"base-sha": "base-sha", branch: "feature-sha"}.get(ref)),
    )
    monkeypatch.setattr(worktree_git, "is_ref_ancestor", AsyncMock(return_value=True))

    await spawn_manager.worktree_lifecycle.finalize_non_contributing_member_worktrees()

    manager.remove_worktree.assert_not_awaited()
    assert get_member_worktree(member).path == str(worktree_path)

    await spawn_manager.worktree_lifecycle.finalize_all_member_worktrees_for_team_clean()

    manager.remove_worktree.assert_awaited_once_with(str(worktree_path), project_dir, force=True)
    assert get_member_worktree(member).path is None


@pytest.mark.asyncio
async def test_team_clean_removes_orphaned_session_worktree(tmp_path, monkeypatch):
    openjiuwen_home = tmp_path / "openjiuwen-home"
    configure_openjiuwen_home(openjiuwen_home)
    try:
        project_dir = _project(tmp_path)
        orphaned_root = team_session_worktrees_dir("code-team", "old-session")
        orphaned_worktree = orphaned_root / "agent-code-team-dev-old"
        orphaned_worktree.mkdir(parents=True)

        member = _member(options=build_member_options(worktree_isolation="worktree"))
        manager = _FakeWorktreeManager()
        spawn_manager = _spawn_manager(member, manager, project_dir)

        from openjiuwen.harness.tools.worktree import git as worktree_git

        monkeypatch.setattr(worktree_git, "find_canonical_git_root", AsyncMock(return_value=project_dir))

        await spawn_manager.worktree_lifecycle.finalize_all_member_worktrees_for_team_clean()

        manager.remove_worktree.assert_awaited_once_with(str(orphaned_worktree), project_dir, force=True)
    finally:
        reset_openjiuwen_home()


@pytest.mark.asyncio
async def test_contribution_detection_uses_actual_worktree_head_when_branch_changes(monkeypatch):
    from openjiuwen.harness.tools.worktree import git as worktree_git

    info = MemberWorktreeInfo(
        worktree_path="/tmp/member-worktree",
        worktree_name="agent-code-team-dev-one",
        worktree_branch="worktree-agent-code-team-dev-one",
        head_commit="base-sha",
    )

    async def fake_rev_parse(ref: str, cwd: str) -> str | None:
        if cwd == "/tmp/member-worktree" and ref == "HEAD":
            return "feature-sha"
        if cwd == "/repo" and ref in {"base-sha", "worktree-agent-code-team-dev-one"}:
            return "base-sha"
        return None

    monkeypatch.setattr(worktree_git, "rev_parse", fake_rev_parse)
    monkeypatch.setattr(worktree_git, "is_ref_ancestor", AsyncMock(return_value=True))

    state = await TeammateWorktreeLifecycle._classify_worktree_contribution(info, "/repo")

    assert state is WorktreeContributionState.CONTRIBUTED
