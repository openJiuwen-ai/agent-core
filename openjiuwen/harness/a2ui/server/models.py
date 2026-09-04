# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""The WebSocket message envelope: ``{id, type, conversationId, timestamp, payload}``.

Matches the Flutter ``a2ui_mobile_app`` client's ``Envelope`` class
byte-for-byte, so that client can talk to this server without any changes.
"""

import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field


def new_message_id() -> str:
    return uuid.uuid4().hex


def now_millis() -> int:
    return int(time.time() * 1000)


class Envelope(BaseModel):
    id: str = Field(default_factory=new_message_id)
    type: str
    conversationId: Optional[str] = None
    timestamp: int = Field(default_factory=now_millis)
    payload: dict[str, Any] = Field(default_factory=dict)


def make_envelope(
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> Envelope:
    return Envelope(type=event_type, conversationId=conversation_id, payload=payload or {})
