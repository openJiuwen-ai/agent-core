# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Call-scope bindings: isolation, nesting and cleanup."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.extensions.ops import (
    bind_team_scope,
    current_team_name,
)


@pytest.mark.level0
def test_team_scope_binds_and_releases() -> None:
    """The current team is visible inside the scope and gone after it."""
    with bind_team_scope("research-team"):
        assert current_team_name() == "research-team"
    assert current_team_name() is None


@pytest.mark.level0
def test_team_scope_supports_explicit_none() -> None:
    """Binding None is a deliberate no-current-team, not a reset to outer."""
    with bind_team_scope("research-team"):
        with bind_team_scope(None):
            assert current_team_name() is None
        assert current_team_name() == "research-team"


@pytest.mark.level1
def test_team_scope_releases_on_exception() -> None:
    """An exception inside the scope still releases the binding."""
    with pytest.raises(RuntimeError):
        with bind_team_scope("research-team"):
            raise RuntimeError("boom")
    assert current_team_name() is None
