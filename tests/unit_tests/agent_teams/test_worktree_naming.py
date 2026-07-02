# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for team teammate worktree naming."""

import re

from openjiuwen.agent_teams.worktree.naming import build_teammate_worktree_name
from openjiuwen.harness.tools.worktree.slug import validate_slug


def test_teammate_worktree_name_uses_session_scoped_agent_format():
    slug = build_teammate_worktree_name(
        team_name="Code Team",
        member_name="Frontend Dev",
        session_id="session-a",
        mode_namespace="code.team",
        project_hash="project123456",
    )

    assert re.match(r"^agent-code-team-frontend-dev-[0-9a-f]{10}$", slug)
    validate_slug(slug)


def test_teammate_worktree_name_changes_across_sessions():
    first = build_teammate_worktree_name(
        team_name="team",
        member_name="dev",
        session_id="session-1",
        mode_namespace="code.team",
        project_hash="project123456",
    )
    second = build_teammate_worktree_name(
        team_name="team",
        member_name="dev",
        session_id="session-2",
        mode_namespace="code.team",
        project_hash="project123456",
    )

    assert first != second
