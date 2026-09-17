# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-facing Claude session helpers over ``openjiuwen.harness_providers.claudecode``."""

from __future__ import annotations

from openjiuwen.harness_providers.claudecode.options import (
    build_claude_session_id as _build_claude_session_id,
    delete_claude_session as _delete_claude_session,
    load_claude_sdk,
    strip_parent_claude_env,
)


def build_claude_session_id(*, team_session_id: str | None, member_name: str) -> str | None:
    """Build the stable Claude UUID for a team member (team naming)."""
    return _build_claude_session_id(host_session_id=team_session_id, agent_name=member_name)


def delete_claude_session(*, team_session_id: str, member_name: str, cwd: str | None) -> bool:
    """Delete the Claude SDK session derived for a team member."""
    return _delete_claude_session(host_session_id=team_session_id, agent_name=member_name, cwd=cwd)


__all__ = [
    "build_claude_session_id",
    "delete_claude_session",
    "load_claude_sdk",
    "strip_parent_claude_env",
]
