# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JSON checkpoint values for the NativeHarness protocol adapter."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.core.session.vcs.codec import decode_context_state, encode_context_state
from openjiuwen.harness_protocol import DeliveryMode, HarnessInput
from openjiuwen.harness_providers.base import PendingTurn


class SavedInput(BaseModel):
    """An accepted input whose message and Turn identities survive restart."""

    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    content: Any
    metadata: dict[str, Any] = Field(default_factory=dict)
    mode: DeliveryMode

    @classmethod
    def capture(cls, turn: PendingTurn) -> "SavedInput":
        from openjiuwen.harness_protocol import json_value_to_builtin

        return cls(message_id=turn.message_id, turn_id=turn.turn_id,
                   content=json_value_to_builtin(turn.content.content),
                   metadata=json_value_to_builtin(turn.content.metadata), mode=turn.accepted_mode)

    def restore(self) -> PendingTurn:
        return PendingTurn(content=HarnessInput(content=self.content, metadata=self.metadata),
                           message_id=self.message_id, turn_id=self.turn_id, accepted_mode=self.mode)


class NativeCheckpoint(BaseModel):
    """Portable paused/idle snapshot; contains no executable objects or config."""

    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1)
    card_id: str
    cwd: str | None = None
    contexts: dict[str, dict[str, Any]]
    deepagent: dict[str, Any]
    paused_input: SavedInput | None = None
    paused_query: str | None = None
    queued: list[SavedInput] = Field(default_factory=list)
    output_state: dict[str, Any] = Field(default_factory=dict)

    def decode_contexts(self) -> dict[str, dict[str, Any]]:
        return {key: decode_context_state(value) for key, value in self.contexts.items()}


def encode_contexts(contexts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {key: encode_context_state(value) for key, value in contexts.items()}
