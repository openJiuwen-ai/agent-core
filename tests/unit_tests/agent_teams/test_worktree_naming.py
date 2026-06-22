# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for team teammate worktree naming."""

import hashlib
import re

from openjiuwen.agent_teams.worktree.naming import build_teammate_worktree_name
from openjiuwen.harness.tools.worktree.slug import validate_slug


def test_teammate_worktree_name_uses_agent_team_member_hash_format():
    slug = build_teammate_worktree_name(
        team_name="Code Team",
        member_name="Frontend Dev",
        nonce="fixed",
    )
    expected_hash = hashlib.sha256("code-team:frontend-dev:fixed".encode()).hexdigest()[:8]

    assert slug == f"agent-code-team-frontend-dev-{expected_hash}"
    validate_slug(slug)


def test_teammate_worktree_name_sanitizes_and_bounds_parts():
    slug = build_teammate_worktree_name(
        team_name="Team Name With Lots Of Words And Spaces",
        member_name="Dev@One/With Spaces",
        nonce="n",
    )

    assert re.match(r"^agent-[a-z0-9._-]{1,24}-[a-z0-9._-]{1,24}-[0-9a-f]{8}$", slug)
    validate_slug(slug)


def test_teammate_worktree_name_nonce_prevents_reuse_collisions():
    first = build_teammate_worktree_name(team_name="team", member_name="dev", nonce="1")
    second = build_teammate_worktree_name(team_name="team", member_name="dev", nonce="2")

    assert first != second
