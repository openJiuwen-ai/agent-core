# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Worktree isolation behavior in SpawnManager."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.models.pool import ModelPoolEntry
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.member_options import (
    build_member_options,
    get_member_worktree,
    set_member_worktree_options,
)
from openjiuwen.harness.tools.worktree import WorktreeConfig
from openjiuwen.harness.tools.worktree.models import WorktreeChangeSummary


class _FakeMemberDao:
    def __init__(self, member: SimpleNamespace) -> None:
        self.member = member
        self.updates: list[dict[str, str | None]] = []

    async def get_member(self, member_name: str, team_name: str) -> SimpleNamespace | None:
        if self.member.member_name == member_name and self.member.team_name == team_name:
            return self.member
        return None

    async def update_member_worktree(
        self,
        member_name: str,
        team_name: str,
        *,
        isolation: str | None = None,
        worktree_path: str | None = None,
    ) -> bool:
        self.member.options = set_member_worktree_options(
            getattr(self.member, "options", None),
            isolation=isolation,
            worktree_path=worktree_path,
        )
        self.updates.append(
            {
                "isolation": isolation,
                "worktree_path": worktree_path,
            }
        )
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
    def __init__(self, path: str = "/tmp/worktree") -> None:
        self.path = path
        self.create_calls: list[str] = []
        self.count_changes = AsyncMock(return_value=WorktreeChangeSummary())
        self.remove_worktree = AsyncMock(return_value=True)

    async def create_owner_worktree(self, slug: str) -> SimpleNamespace:
        self.create_calls.append(slug)
        return SimpleNamespace(
            worktree_path=self.path,
            worktree_branch=f"worktree-{slug}",
            head_commit="base-sha",
        )


def _member(**overrides) -> SimpleNamespace:
    data = {
        "member_name": "dev-one",
        "team_name": "code-team",
        "role": TeamRole.TEAMMATE.value,
        "desc": "developer",
        "options": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _spawn_manager(
    member: SimpleNamespace,
    manager: _FakeWorktreeManager,
    *,
    model_pool: list[ModelPoolEntry] | None = None,
) -> SpawnManager:
    team_spec = TeamSpec(
        team_name=member.team_name,
        display_name=member.team_name,
        leader_member_name="leader",
        model_pool=model_pool or [],
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name=member.team_name,
        worktree=WorktreeConfig(enabled=True),
    )
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=team_spec)
    backend = _FakeTeamBackend(member)
    configurator = SimpleNamespace(
        team_backend=backend,
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
async def test_build_context_does_not_create_worktree_without_isolation():
    member = _member()
    manager = _FakeWorktreeManager(path="/tmp/code-team-dev-one")
    spawn_manager = _spawn_manager(member, manager)

    ctx = await spawn_manager.build_context_from_db("dev-one")

    assert ctx is not None
    assert ctx.worktree_path is None
    assert get_member_worktree(member) is None
    assert manager.create_calls == []


@pytest.mark.asyncio
async def test_build_context_creates_and_persists_explicit_teammate_worktree():
    member = _member(options=build_member_options(worktree_isolation="worktree"))
    manager = _FakeWorktreeManager(path="/tmp/code-team-dev-one")
    spawn_manager = _spawn_manager(member, manager)

    ctx = await spawn_manager.build_context_from_db("dev-one")

    assert ctx is not None
    assert ctx.worktree_path == "/tmp/code-team-dev-one"
    worktree = get_member_worktree(member)
    assert worktree is not None
    assert worktree.isolation == "worktree"
    assert worktree.path == "/tmp/code-team-dev-one"
    assert len(manager.create_calls) == 1
    assert manager.create_calls[0].startswith("agent-code-team-dev-one-")


@pytest.mark.asyncio
async def test_build_context_reuses_persisted_worktree_without_recreating():
    member = _member(
        options='{"worktree": {"isolation": "worktree", "path": "/tmp/existing"}}',
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager)

    ctx = await spawn_manager.build_context_from_db("dev-one")

    assert ctx is not None
    assert ctx.worktree_path == "/tmp/existing"
    assert manager.create_calls == []


@pytest.mark.asyncio
async def test_build_context_reuses_options_worktree_without_recreating():
    member = _member(
        options='{"worktree": {"isolation": "worktree", "path": "/tmp/options-existing"}}',
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager)

    ctx = await spawn_manager.build_context_from_db("dev-one")

    assert ctx is not None
    assert ctx.worktree_path == "/tmp/options-existing"
    assert manager.create_calls == []


@pytest.mark.asyncio
async def test_build_context_resolves_model_ref_from_options():
    member = _member(
        options=build_member_options(
            model_ref={"model_name": "gpt-4", "model_index": 1},
            worktree_isolation="worktree",
            worktree_path="/tmp/options-existing",
        ),
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(
        member,
        manager,
        model_pool=[
            ModelPoolEntry(
                model_name="gpt-4",
                api_key="key-0",
                api_base_url="https://one.example",
                api_provider="openai",
            ),
            ModelPoolEntry(
                model_name="gpt-4",
                api_key="key-1",
                api_base_url="https://two.example",
                api_provider="openai",
            ),
        ],
    )

    ctx = await spawn_manager.build_context_from_db("dev-one")

    assert ctx is not None
    assert ctx.member_model is not None
    assert ctx.member_model.model_request_config is not None
    assert ctx.member_model.model_request_config.model_name == "gpt-4"
    assert ctx.member_model.model_client_config.api_key == "key-1"
    assert manager.create_calls == []


@pytest.mark.asyncio
async def test_finalize_removes_clean_worktree_and_clears_metadata(tmp_path, monkeypatch):
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    member = _member(options=build_member_options(worktree_isolation="worktree"))
    manager = _FakeWorktreeManager(path=str(worktree_path))
    spawn_manager = _spawn_manager(member, manager)
    await spawn_manager.build_context_from_db("dev-one")

    from openjiuwen.harness.tools.worktree import git as worktree_git

    monkeypatch.setattr(
        worktree_git,
        "find_canonical_git_root",
        AsyncMock(return_value=str(tmp_path)),
    )

    await spawn_manager.worktree_lifecycle.finalize_member_worktree("dev-one")

    manager.count_changes.assert_awaited_once()
    manager.remove_worktree.assert_awaited_once()
    worktree = get_member_worktree(member)
    assert worktree is not None
    assert worktree.isolation == "worktree"
    assert worktree.path is None


@pytest.mark.asyncio
async def test_finalize_keeps_worktree_when_host_metadata_is_missing(tmp_path, monkeypatch):
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    member = _member(
        options=f'{{"worktree": {{"isolation": "worktree", "path": "{worktree_path}"}}}}'
    )
    manager = _FakeWorktreeManager()
    spawn_manager = _spawn_manager(member, manager)

    from openjiuwen.harness.tools.worktree import git as worktree_git

    monkeypatch.setattr(
        worktree_git,
        "find_canonical_git_root",
        AsyncMock(return_value=str(tmp_path)),
    )

    await spawn_manager.worktree_lifecycle.finalize_member_worktree("dev-one")

    manager.count_changes.assert_not_awaited()
    manager.remove_worktree.assert_not_awaited()
    worktree = get_member_worktree(member)
    assert worktree is not None
    assert worktree.path == str(worktree_path)
