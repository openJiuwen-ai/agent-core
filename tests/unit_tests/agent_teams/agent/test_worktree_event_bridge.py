# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify the worktree -> team workspace event bridge.

``AgentConfigurator.create_worktree_manager`` installs an event handler
on the generic ``WorktreeManager`` so ``WorktreeCreatedEvent`` and
``WorktreeRemovedEvent`` mirror into ``TeamWorkspaceManager.mount_worktree``
and ``unmount_worktree``. Single-agent callers never go through this
path, so the team workspace's ``.worktree/{slug}`` view is team-only by
construction.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.worktree import (
    WorktreeConfig,
    WorktreeCreatedEvent,
    WorktreeRemovedEvent,
)


def _make_configurator(workspace_manager: Any | None) -> AgentConfigurator:
    """Build an ``AgentConfigurator`` with a stubbed workspace manager."""
    configurator = AgentConfigurator(card=AgentCard(name="dummy", description="t"))
    configurator.workspace_manager = workspace_manager
    return configurator


def _spec_with_worktree_enabled() -> Any:
    """Minimal stand-in: ``create_worktree_manager`` only reads ``spec.worktree``."""
    return SimpleNamespace(worktree=WorktreeConfig(enabled=True))


@pytest.mark.level0
@pytest.mark.asyncio
async def test_event_handler_mounts_on_created(monkeypatch):
    """A ``WorktreeCreatedEvent`` must invoke ``mount_worktree`` with slug+path."""
    captured_handler: dict[str, Any] = {}

    def fake_init(self, *, config=None, backend=None, event_handler=None, rails=None):
        _ = self, config, backend, rails
        captured_handler["fn"] = event_handler

    monkeypatch.setattr(
        "openjiuwen.harness.tools.worktree.WorktreeManager.__init__",
        fake_init,
    )

    ws_mgr = MagicMock()
    cfg = _make_configurator(ws_mgr)
    cfg.create_worktree_manager(_spec_with_worktree_enabled())

    handler = captured_handler["fn"]
    assert handler is not None, "team configurator must inject an event handler"

    await handler(
        WorktreeCreatedEvent(
            worktree_name="wt-alpha",
            worktree_path="/tmp/wkspc/.worktrees/wt-alpha",
        ),
    )
    ws_mgr.mount_worktree.assert_called_once_with(
        "wt-alpha",
        "/tmp/wkspc/.worktrees/wt-alpha",
    )
    ws_mgr.unmount_worktree.assert_not_called()


@pytest.mark.level0
@pytest.mark.asyncio
async def test_event_handler_unmounts_on_removed(monkeypatch):
    """A ``WorktreeRemovedEvent`` must invoke ``unmount_worktree`` with slug."""
    captured_handler: dict[str, Any] = {}

    def fake_init(self, *, config=None, backend=None, event_handler=None, rails=None):
        _ = self, config, backend, rails
        captured_handler["fn"] = event_handler

    monkeypatch.setattr(
        "openjiuwen.harness.tools.worktree.WorktreeManager.__init__",
        fake_init,
    )

    ws_mgr = MagicMock()
    cfg = _make_configurator(ws_mgr)
    cfg.create_worktree_manager(_spec_with_worktree_enabled())

    handler = captured_handler["fn"]
    await handler(
        WorktreeRemovedEvent(
            worktree_name="wt-bye",
            worktree_path="/tmp/wkspc/.worktrees/wt-bye",
        ),
    )
    ws_mgr.unmount_worktree.assert_called_once_with("wt-bye")
    ws_mgr.mount_worktree.assert_not_called()


@pytest.mark.level1
def test_no_handler_when_workspace_manager_absent(monkeypatch):
    """Without a team workspace there is no mirror, so no event handler."""
    captured_handler: dict[str, Any] = {}

    def fake_init(self, *, config=None, backend=None, event_handler=None, rails=None):
        _ = self, config, backend, rails
        captured_handler["fn"] = event_handler

    monkeypatch.setattr(
        "openjiuwen.harness.tools.worktree.WorktreeManager.__init__",
        fake_init,
    )

    cfg = _make_configurator(None)
    cfg.create_worktree_manager(_spec_with_worktree_enabled())

    assert captured_handler["fn"] is None
