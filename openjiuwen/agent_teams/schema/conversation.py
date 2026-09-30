# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public group conversation records, separate from the team's internal mailbox."""

from typing import Any

from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    """One immutable public message using the existing millisecond clock."""

    message_id: str
    team_name: str
    session_id: str
    client_message_id: str
    sender: str
    sender_name: str
    content: str
    mentions: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: int


class ConversationAppendResult(BaseModel):
    message: ConversationMessage
    notified_members: list[str] = Field(default_factory=list)
    duplicate: bool = False
    context_path: str
