# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deterministic naming helpers for team-owned worktrees."""

from __future__ import annotations

import hashlib
import re
import uuid

from openjiuwen.harness.tools.worktree.slug import validate_slug

_MAX_PART_LENGTH = 24


def _slug_part(value: str | None, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("._-")
    slug = (slug or fallback)[:_MAX_PART_LENGTH].strip("._-")
    return slug or fallback


def build_teammate_worktree_name(
    *,
    team_name: str,
    member_name: str,
    nonce: str | None = None,
) -> str:
    """Build the worktree slug for a team teammate."""
    team = _slug_part(team_name, "team")
    member = _slug_part(member_name, "member")
    seed = nonce or uuid.uuid4().hex
    digest = hashlib.sha256(f"{team}:{member}:{seed}".encode()).hexdigest()[:8]
    slug = f"agent-{team}-{member}-{digest}"
    validate_slug(slug)
    return slug


__all__ = ["build_teammate_worktree_name"]
