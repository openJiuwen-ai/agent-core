# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-facing identity, lineage, and range metadata for KV-cache management."""

from dataclasses import dataclass
from typing import Any, Sequence, TypeVar


T = TypeVar("T")

KV_CACHE_AFFINITY_SESSION_ID_ENV = "kv_cache_affinity_session_id"
KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV = "kv_cache_affinity_parent_session_id"


@dataclass(frozen=True, slots=True)
class KVCacheIdentity:
    """Provider-facing KV cache lineage identity."""

    cache_id: str
    parent_cache_id: str


def self_parent_kwargs(cache_id: str) -> dict:
    return {"session_id": cache_id, "parent_session_id": cache_id}


def team_member_cache_identity(
    team_session_id: str,
    team_id: str,
    member_id: str,
) -> str:
    """Return the stable KV cache identity for one Team member."""
    if not team_session_id or not team_id or not member_id:
        raise ValueError("team_session_id, team_id and member_id are required")
    return f"team:{team_session_id}:team:{team_id}:member:{member_id}"


def resolve_session_lineage(session: Any) -> tuple[str | None, str | None]:
    """Resolve current and parent session ids for AscendAffinity hints."""
    if session is None or not hasattr(session, "get_session_id"):
        return None, None

    def _normalize_identity(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    session_id = _normalize_identity(session.get_session_id())
    get_cache_identity = getattr(session, "get_cache_identity", None)
    if callable(get_cache_identity):
        identity = get_cache_identity()
        cache_id = _normalize_identity(getattr(identity, "cache_id", None))
        parent_cache_id = _normalize_identity(
            getattr(identity, "parent_cache_id", None)
        )
        if cache_id:
            return cache_id, parent_cache_id or cache_id
    cache_session_id = None
    if hasattr(session, "get_env"):
        cache_session_id = _normalize_identity(
            session.get_env(KV_CACHE_AFFINITY_SESSION_ID_ENV)
        )
    parent_session_id = None
    get_parent_session_id = getattr(session, "get_parent_session_id", None)
    if callable(get_parent_session_id):
        parent_session_id = _normalize_identity(get_parent_session_id())
    if not parent_session_id and hasattr(session, "get_env"):
        parent_session_id = _normalize_identity(
            session.get_env(KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV)
        )
    resolved_session_id = cache_session_id or session_id
    return resolved_session_id, parent_session_id or resolved_session_id


def first_changed_index(old: Sequence[T], new: Sequence[T]) -> int | None:
    """Return the first changed index, or None when new only appends to old."""
    for idx, (old_item, new_item) in enumerate(zip(old, new)):
        if old_item != new_item:
            return idx
    if len(new) < len(old):
        return len(new)
    return None


def message_range_kwargs(first_changed: int, end_exclusive: int) -> dict:
    return {"msg_start": first_changed, "msg_end": end_exclusive}


def tools_range_kwargs(first_changed: int, end_exclusive: int) -> dict:
    return {"tools_start": first_changed, "tools_end": end_exclusive}
