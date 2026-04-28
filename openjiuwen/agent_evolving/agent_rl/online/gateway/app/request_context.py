# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Request validation helpers for gateway chat turns."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, Request


def resolve_trace_id(request: Request) -> str:
    """Return request trace id from header or synthesize one."""
    req_headers = getattr(request, "headers", {}) or {}
    return (
        req_headers.get("x-request-id") if hasattr(req_headers, "get") else None
    ) or uuid.uuid4().hex[:8]


def require_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate chat body contains non-empty messages list."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages must be a non-empty list")
    return messages


def require_user_id(request: Request) -> str:
    """Validate online-training user identity header."""
    user_id = str(request.headers.get("x-user-id") or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="missing x-user-id header; online training requires a stable user id",
        )
    return user_id
