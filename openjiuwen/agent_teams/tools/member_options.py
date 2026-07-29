# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured TeamMember options helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemberModelRef(BaseModel):
    """Lightweight model-pool reference stored on a team member."""

    model_name: str | None = None
    model_index: int | None = None


class MemberWorktreeOptions(BaseModel):
    """Worktree isolation options stored on a team member."""

    isolation: str | None = None
    path: str | None = None
    session_id: str | None = None
    project_hash: str | None = None
    managed_root: str | None = None
    worktree_branch: str | None = None
    head_commit: str | None = None


class TeamMemberOptions(BaseModel):
    """Extensible per-member configuration blob persisted in TeamMember.options."""

    model_config = ConfigDict(extra="allow")

    model_ref: MemberModelRef | None = None
    cli_agent: str | None = None
    worktree: MemberWorktreeOptions | None = None
    permissions_override: dict[str, str] | None = Field(
        default=None,
        description=(
            "Per-member permission narrowing from spawn_teammate.permissions. "
            "Flat {tool_name: level_string} dict fed to narrow_permissions "
            "to tighten the base config. None when no override was specified."
        ),
    )


def load_member_options(raw: str | None) -> TeamMemberOptions:
    """Parse a TeamMember.options JSON string."""
    if not raw:
        return TeamMemberOptions()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return TeamMemberOptions()
    if not isinstance(data, dict):
        return TeamMemberOptions()
    try:
        return TeamMemberOptions.model_validate(data)
    except ValueError:
        return TeamMemberOptions()


def dump_member_options(options: TeamMemberOptions) -> str | None:
    """Serialize options, returning None for an empty config."""
    data = options.model_dump(mode="json", exclude_none=True)
    if not data:
        return None
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _model_ref_from_mapping(value: Mapping[str, Any] | None) -> MemberModelRef | None:
    if not value:
        return None
    return MemberModelRef(
        model_name=value.get("model_name") if isinstance(value.get("model_name"), str) else None,
        model_index=value.get("model_index") if isinstance(value.get("model_index"), int) else None,
    )


def _model_ref_from_json(raw: str | None) -> MemberModelRef | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return _model_ref_from_mapping(data)


def merge_legacy_member_options(
    *,
    options: str | None = None,
    model_ref: Mapping[str, Any] | None = None,
    model_ref_json: str | None = None,
) -> str | None:
    """Build options JSON while preserving the legacy model-ref column input."""
    parsed = load_member_options(options)
    if parsed.model_ref is None:
        parsed.model_ref = _model_ref_from_mapping(model_ref) or _model_ref_from_json(model_ref_json)
    return dump_member_options(parsed)


def build_member_options(
    *,
    model_ref: Mapping[str, Any] | None = None,
    cli_agent: str | None = None,
    worktree: MemberWorktreeOptions | None = None,
    worktree_isolation: str | None = None,
    worktree_path: str | None = None,
    permissions_override: dict[str, str] | None = None,
) -> str | None:
    """Build a TeamMember.options JSON string for new writes."""
    parsed = TeamMemberOptions()
    parsed.model_ref = _model_ref_from_mapping(model_ref)
    parsed.cli_agent = cli_agent
    if worktree is not None:
        parsed.worktree = worktree
    elif worktree_isolation or worktree_path:
        parsed.worktree = MemberWorktreeOptions(
            isolation=worktree_isolation,
            path=worktree_path,
        )
    if permissions_override:
        parsed.permissions_override = permissions_override
    return dump_member_options(parsed)


def set_member_worktree_options(
    raw_options: str | None,
    worktree: MemberWorktreeOptions | None = None,
    *,
    isolation: str | None = None,
    worktree_path: str | None = None,
) -> str | None:
    """Replace the worktree section inside a TeamMember.options JSON string."""
    parsed = load_member_options(raw_options)
    if worktree is not None:
        parsed.worktree = worktree
    elif isolation or worktree_path:
        parsed.worktree = MemberWorktreeOptions(isolation=isolation, path=worktree_path)
    else:
        parsed.worktree = None
    return dump_member_options(parsed)


def set_member_permissions_override(
    raw_options: str | None,
    *,
    permissions_override: dict[str, str] | None,
) -> str | None:
    """Replace the permissions_override section inside a TeamMember.options JSON string."""
    parsed = load_member_options(raw_options)
    parsed.permissions_override = permissions_override
    return dump_member_options(parsed)


def get_member_options(record: object) -> TeamMemberOptions:
    """Read options from a DB-like record."""
    return load_member_options(_record_value(record, "options"))


def get_member_model_ref(record: object) -> MemberModelRef | None:
    """Return the member's model reference from options."""
    return get_member_options(record).model_ref


def get_member_cli_agent(record: object) -> str | None:
    """Return the external CLI backend name from options."""
    return get_member_options(record).cli_agent


def get_member_worktree(record: object) -> MemberWorktreeOptions | None:
    """Return the member's worktree options from options."""
    return get_member_options(record).worktree


def get_member_permissions_override(record: object) -> dict[str, str] | None:
    """Return the member's permissions override from options.

    A flat ``{tool_name: level_string}`` dict — e.g.
    ``{"bash": "deny", "write_file": "ask"}``.  ``None`` when no
    override was specified at spawn time.
    """
    return get_member_options(record).permissions_override


def _record_value(record: object, key: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(key)
    return getattr(record, key, None)
