# -*- coding: utf-8 -*-
"""issue 1755：agent_teams 环境作用域跨仓 API（set_env_scope/get_env_scope）。

该 API 由宿主 agent-studio（controller_runner）在创建团队任务前调用，
Core 包内无调用点，故此处以 UT 锁定其存在性与语义（防被误删）。
"""
import pytest

from openjiuwen.agent_teams import context as team_ctx


@pytest.fixture(autouse=True)
def _reset_scope():
    token = team_ctx._env_scope_context.set(None)
    yield
    team_ctx._env_scope_context.reset(token)


def test_set_then_get_roundtrip():
    team_ctx.set_env_scope({"workspace_id": "ws1", "environment_id": "env1", "project_id": "p1"})
    assert team_ctx.get_env_scope() == {"workspace_id": "ws1", "environment_id": "env1", "project_id": "p1"}


def test_empty_scope_keeps_previous():
    team_ctx.set_env_scope({"workspace_id": "ws1"})
    team_ctx.set_env_scope(None)
    team_ctx.set_env_scope({})
    assert team_ctx.get_env_scope() == {"workspace_id": "ws1"}


def test_get_returns_copy():
    team_ctx.set_env_scope({"workspace_id": "ws1"})
    snapshot = team_ctx.get_env_scope()
    snapshot["workspace_id"] = "mutated"
    assert team_ctx.get_env_scope()["workspace_id"] == "ws1"


def test_default_empty():
    assert team_ctx.get_env_scope() == {}
