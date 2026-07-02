# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deterministic naming helpers for team-owned worktrees."""

from __future__ import annotations

import hashlib
import re

from openjiuwen.harness.tools.worktree.slug import validate_slug

_TEAM_PART_LENGTH = 14
_MEMBER_PART_LENGTH = 16


def _slug_part(value: str | None, fallback: str, *, max_length: int) -> str:
    raw = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("._-")
    slug = (slug or fallback)[:max_length].strip("._-")
    return slug or fallback


def build_teammate_worktree_name(
    *,
    team_name: str,
    member_name: str,
    session_id: str,
    mode_namespace: str,
    project_hash: str,
) -> str:
    """Build a deterministic worktree slug for a session-scoped team owner."""
    team = _slug_part(team_name, "team", max_length=_TEAM_PART_LENGTH)
    member = _slug_part(member_name, "member", max_length=_MEMBER_PART_LENGTH)
    seed = f"{team_name}:{member_name}:{session_id}:{mode_namespace}:{project_hash}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
    slug = f"agent-{team}-{member}-{digest}"
    validate_slug(slug)
    return slug


__all__ = ["build_teammate_worktree_name"]
