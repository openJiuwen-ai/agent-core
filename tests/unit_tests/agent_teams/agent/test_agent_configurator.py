# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``AgentConfigurator`` workspace cache attach (design-v5, 212).

Covers the configure-side of the lazy cache model: ``_attach_workspace_cache``
creates and attaches an empty ``WorkspaceCache`` — no proactive build, no file
scan. Values fill lazily on the first ``get*`` (miss reads the file once) and
drop on the Runner finally pause path via ``invalidate``. Teammates reuse the
leader's shared manager (and its one cache instance) by reference.

Timing invariant (212 COLD_RECOVER fix): the attach runs **before**
``TeamHarness.build`` so the rail factories mint their A-class loaders against
an already-attached cache — ``_assemble_member_workspace`` is pure disk writes.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.agent_teams.paths import (
    configure_openjiuwen_home,
    reset_openjiuwen_home,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _make_ctx(*, role: TeamRole, member_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        role=role,
        member_name=member_name,
        team_spec=SimpleNamespace(
            team_name="T",
            evolution_enabled=True,
        ),
        desc="the desc",
        prompt="the prompt",
        team_desc=None,
        team_prompt=None,
    )


@pytest.fixture
def isolated_home(tmp_path):
    """Redirect the global agent-teams home so real writes stay in tmp."""
    configure_openjiuwen_home(str(tmp_path / "home"))
    try:
        yield tmp_path
    finally:
        reset_openjiuwen_home()


class TestAttachWorkspaceCacheCreatesEmptyCache:
    """212: configure attaches an empty cache object before the harness build."""

    def test_leader_creates_empty_cache_attached_to_manager(self, isolated_home):
        configurator = AgentConfigurator(AgentCard(name="test-agent", description="test"))
        # Real setter semantics (like TeamWorkspaceManager.attach_workspace_cache):
        # the attached cache becomes the manager's resident instance.
        def _attach(cache):
            ws_mgr.workspace_cache = cache

        ws_mgr = SimpleNamespace(workspace_cache=None, attach_workspace_cache=_attach)
        configurator.workspace_manager = ws_mgr
        backend = SimpleNamespace(attach_workspace_manager=MagicMock())
        configurator.team_backend = backend

        configurator._attach_workspace_cache(
            SimpleNamespace(team_name="T"),
            _make_ctx(role=TeamRole.LEADER, member_name="team-leader"),
            "cn",
        )

        # The cache object exists and is attached...
        cache = ws_mgr.workspace_cache
        assert cache is not None
        backend.attach_workspace_manager.assert_called_once_with(ws_mgr)
        # ...and no file has been read yet: the dicts are empty, values fill
        # lazily on the first read-side get (miss → read file once → hit).
        assert cache.get_template("any_name") is None  # no file → None, no IO surprise
        assert cache.get_member_field("team-leader", "desc") is None

    def test_teammate_reuses_shared_cache_without_creating_a_new_one(self, isolated_home):
        """The reuse branch: a shared cache is reused, no new instance attached."""
        configurator = AgentConfigurator(AgentCard(name="test-agent", description="test"))
        shared_cache = MagicMock()
        ws_mgr = SimpleNamespace(
            workspace_cache=shared_cache,
            attach_workspace_cache=MagicMock(),
        )
        configurator.workspace_manager = ws_mgr
        backend = SimpleNamespace(attach_workspace_manager=MagicMock())
        configurator.team_backend = backend

        configurator._attach_workspace_cache(
            SimpleNamespace(team_name="T"),
            _make_ctx(role=TeamRole.TEAMMATE, member_name="counter-1"),
            "cn",
        )

        # Reuse path: manager's resident cache is kept, no new instance
        # attached, backend routed to the manager.
        assert ws_mgr.workspace_cache is shared_cache
        ws_mgr.attach_workspace_cache.assert_not_called()
        backend.attach_workspace_manager.assert_called_once_with(ws_mgr)
