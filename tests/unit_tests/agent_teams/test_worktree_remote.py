# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Remote worktree nodes keep remote-first base resolution."""

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.worktree_remote import WorktreeRemoteHandler
from openjiuwen.harness.tools.worktree.backend import GitBackend


@pytest.mark.level0
def test_remote_handler_switches_git_backend_to_remote_base():
    """The node's clone never advances local branches, so local-first would pin a stale base."""
    backend = GitBackend()
    assert backend.remote_base is False

    WorktreeRemoteHandler(SimpleNamespace(backend=backend))

    assert backend.remote_base is True


@pytest.mark.level1
def test_remote_handler_leaves_custom_backends_alone():
    backend = SimpleNamespace()
    WorktreeRemoteHandler(SimpleNamespace(backend=backend))
    assert not hasattr(backend, "remote_base")
