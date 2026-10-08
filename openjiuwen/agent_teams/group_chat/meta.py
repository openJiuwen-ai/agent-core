# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Identity and addressing rules for a public group-chat broadcast."""

from __future__ import annotations

import json
import uuid
from typing import Any

GROUP_CHAT_TYPE = "group_chat"
CONTEXT_TAIL = 5
BODY_LIMIT = 2000
MAX_MENTIONS = 100
MAX_CLIENT_MESSAGE_ID = 255


def stable_message_id(team_name: str, session_id: str, client_message_id: str) -> str:
    """Return the database id derived from one client retry key."""
    payload = json.dumps([team_name, session_id, client_message_id], ensure_ascii=False)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def group_metadata(message: Any) -> dict[str, Any]:
    """Return group-chat metadata when ``message`` is a public broadcast."""
    raw = getattr(message, "meta", None)
    if isinstance(raw, str) and raw:
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict) and raw.get("type") == GROUP_CHAT_TYPE:
        return raw
    return {}


def group_addressed(
    *,
    member_name: str,
    from_member_name: str,
    meta: dict[str, Any],
    role: str | None,
    session_id: str | None,
) -> bool:
    """Whether this member should receive model input for a group broadcast."""
    if role == "passive_human" or member_name == from_member_name:
        return False
    row_session = meta.get("session_id")
    if session_id and row_session and row_session != session_id:
        return False
    mentions = meta.get("mentions") or []
    return member_name in mentions
