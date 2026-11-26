# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Conversation data and explicit runtime bindings, independent of executor state."""
from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm import BaseMessage


@dataclass(frozen=True)
class ConversationKey:
    session_id: str
    conversation_id: str

    def __post_init__(self) -> None:
        for value in (self.session_id, self.conversation_id):
            if not isinstance(value, str) or not value.strip():
                raise build_error(
                    StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="conversation IDs must be nonempty strings",
                )


@dataclass(frozen=True, kw_only=True)
class ConversationSnapshot:
    """Detached message data. Pending tool calls are valid during execution."""
    schema_version: int = 1
    messages: list[BaseMessage] = field(default_factory=list)
    offload_messages: dict[str, list[BaseMessage]] = field(default_factory=dict)

    def to_state(self, *, copy: bool = True) -> dict:
        """Validate and return the record fields.

        ``copy=False`` returns the snapshot's own lists. It is for a caller
        that serialises the result at once or that owns the snapshot alone.
        """
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="unsupported conversation schema version")
        if not isinstance(self.offload_messages, dict):
            raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="offload_messages must be a dictionary")
        for handle in self.offload_messages:
            if not isinstance(handle, str) or not handle:
                raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="invalid offload handle")
        for batch in [self.messages, *self.offload_messages.values()]:
            if not isinstance(batch, list) or any(not isinstance(message, BaseMessage) for message in batch):
                raise build_error(
                    StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="conversation messages must be BaseMessage lists",
                )
            for message in batch:
                handle = getattr(message, "offload_handle", None)
                if (handle and getattr(message, "offload_type", None) == "in_memory"
                        and handle not in self.offload_messages):
                    raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="conversation offload is missing")
        state = {"schema_version": self.schema_version, "messages": self.messages,
                 "offload_messages": self.offload_messages}
        return deepcopy(state) if copy else state


class SessionConversationStore(Protocol):
    """The host holds its Session operation lock across read, work and commit."""

    async def create(self, key: ConversationKey) -> ConversationSnapshot:
        ...

    async def read(self, key: ConversationKey) -> ConversationSnapshot:
        ...

    async def commit(self, key: ConversationKey, snapshot: ConversationSnapshot) -> None:
        ...


@dataclass(frozen=True)
class ConversationBinding:
    key: ConversationKey
    store: SessionConversationStore

    def child(self, *, kind: str, identity: str) -> "ConversationBinding":
        """Derive a private key from an existing persistent executor identity.

        The caller allocates the record explicitly. Display names are not
        lifecycle identities; nested children also include their parent key.
        """
        if any(not isinstance(value, str) or not value.strip() for value in (kind, identity)):
            raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="child conversation needs an identity")
        token = uuid5(NAMESPACE_URL, json.dumps([self.key.conversation_id, kind, identity], ensure_ascii=False)).hex
        return ConversationBinding(ConversationKey(self.key.session_id, f"child:{token}"), self.store)
