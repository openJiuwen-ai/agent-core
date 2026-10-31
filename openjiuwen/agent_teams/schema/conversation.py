# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public conversation records projected from the group-chat broadcast."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConversationMessage(BaseModel):
    """One public message, stored as a single JSON line."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    team_name: str
    session_id: str
    client_message_id: str
    sender: str
    sender_name: str
    content: str
    timestamp: int
    mentions: list[str] = Field(default_factory=list)
    attachments: list[dict] = Field(default_factory=list)


class ConversationAppendResult(BaseModel):
    """Outcome of archiving one public message."""

    model_config = ConfigDict(extra="forbid")

    message: ConversationMessage
    duplicate: bool
    notified_members: list[str] = Field(default_factory=list)
    context_path: str
