# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import datetime
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MessageType(str, Enum):
    """Message types for stdio communication protocol."""

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    HEALTH_CHECK = "HEALTH_CHECK"
    HEALTH_CHECK_RESPONSE = "HEALTH_CHECK_RESPONSE"
    SHUTDOWN = "SHUTDOWN"
    SHUTDOWN_ACK = "SHUTDOWN_ACK"
    ERROR = "ERROR"
    STREAM_CHUNK = "STREAM_CHUNK"
    DONE = "DONE"


@dataclass
class Message:
    """Message data structure for async process communication."""

    type: MessageType
    payload: Any
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.now)
    message_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert message to dictionary."""
        return {
            "type": self.type.value if isinstance(self.type, MessageType) else self.type,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """Create message from dictionary."""
        msg_type = MessageType(data["type"])
        timestamp = datetime.datetime.fromisoformat(data["timestamp"])
        return cls(
            type=msg_type,
            payload=data["payload"],
            timestamp=timestamp,
            message_id=data.get("message_id"),
        )


async def serialize_message(message: Message) -> bytes:
    """
    Serialize a Message object to JSON bytes asynchronously.

    Args:
        message: The Message object to serialize

    Returns:
        JSON-encoded bytes
    """
    await asyncio.sleep(0)
    data = message.to_dict()
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


async def deserialize_message(data: bytes) -> Message:
    """
    Deserialize JSON bytes to a Message object asynchronously.

    Args:
        data: JSON-encoded bytes

    Returns:
        Deserialized Message object
    """
    await asyncio.sleep(0)
    obj = json.loads(data.decode("utf-8"))
    return Message.from_dict(obj)


async def serialize_message_to_stream(message: Message, writer: asyncio.StreamWriter) -> None:
    """
    Serialize and write a Message to an async stream.

    Args:
        message: The Message object to serialize
        writer: Async stream writer
    """
    data = await serialize_message(message)
    writer.write(data + b"\n")
    await writer.drain()


async def deserialize_message_from_stream(reader: asyncio.StreamReader) -> Optional[Message]:
    """
    Read and deserialize a Message from an async stream.

    Args:
        reader: Async stream reader

    Returns:
        Deserialized Message object, or None if EOF
    """
    while True:
        data = await reader.readline()
        if not data:
            return None
        try:
            return await deserialize_message(data.rstrip(b"\n"))
        except Exception:
            # The child process may emit non-protocol logs to stdout before the
            # structured message stream is fully isolated. Skip those lines.
            continue
