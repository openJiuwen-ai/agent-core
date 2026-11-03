# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

import datetime
import json
from enum import Enum
from typing import Any, Literal

from openjiuwen.core.runner.drunner.dmessage_queue.message import (
    DmqRequestMessage, DmqResponseMessage
)
from openjiuwen.core.runner.message_queue_base import QueueMessage
from openjiuwen.core.session import InteractionOutput
from openjiuwen.core.session.stream import OutputSchema, CustomSchema, TraceSchema
from openjiuwen.core.workflow import WorkflowOutput

MAX_RECURSE_DEPTH = 10
_ESCAPE_PREFIX = "__dsh_escaped__"

# Classes that can be deserialized in JSON, call .model_validate(payload) for deserialization
TYPE_REGISTRY = {
    "OutputSchema": OutputSchema,
    "CustomSchema": CustomSchema,
    "TraceSchema": TraceSchema,
    "InteractionOutput": InteractionOutput,
    "WorkflowOutput": WorkflowOutput,
    "DmqRequestMessage": DmqRequestMessage,
    "DmqResponseMessage": DmqResponseMessage,
    "QueueMessage": QueueMessage,
}


def serialize_message(
    msg: DmqRequestMessage | DmqResponseMessage,
    *,
    wire_version: Literal[1, 2] = 1,
    legacy_key_encoding: Literal["literal", "escaped"] = "literal",
) -> bytes:
    """Serialize using legacy-compatible writes unless version 2 is selected.

    Version 2 wraps plain dictionaries instead of modifying business keys.
    Enable it only after all receivers support it. Legacy writes reject marker
    collisions unless escaped key encoding is explicitly selected for peers
    using the unversioned escaped format.
    """
    if wire_version not in (1, 2):
        raise ValueError(f"Unsupported MQ wire version: {wire_version}")
    if legacy_key_encoding not in ("literal", "escaped"):
        raise ValueError(f"Unsupported legacy key encoding: {legacy_key_encoding}")
    data = _serialize_payload(
        msg, depth=0, wire_version=wire_version, legacy_key_encoding=legacy_key_encoding,
    )
    if wire_version == 2:
        data = {"__class__": "DmqWireEnvelope", "version": 2, "message": data}
    return json.dumps(data, default=_json_serializer).encode("utf-8")


def _json_serializer(obj):
    """Custom JSON serializer for datetime objects"""
    if isinstance(obj, datetime.datetime):
        return {
            "__type__": "datetime",
            "value": obj.isoformat()
        }
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _serialize_payload(
    payload: Any,
    depth: int,
    *,
    wire_version: int = 1,
    legacy_key_encoding: str = "literal",
) -> Any:
    """Serialize payload recursively with depth limit."""
    if depth > MAX_RECURSE_DEPTH:
        raise RecursionError(f"Payload nested too deep (> {MAX_RECURSE_DEPTH})")

    # Enum
    if isinstance(payload, Enum):
        return payload.value

    # BaseModel objects
    if hasattr(payload, "model_dump"):
        result = {"__class__": payload.__class__.__name__}

        # custom schema
        for field_name in payload.model_fields.keys():
            field_value = getattr(payload, field_name)
            result[field_name] = _serialize_payload(
                field_value, depth + 1, wire_version=wire_version, legacy_key_encoding=legacy_key_encoding,
            )

        if hasattr(payload, '__pydantic_extra__') and payload.__pydantic_extra__:
            for field_name, field_value in payload.__pydantic_extra__.items():
                result[field_name] = _serialize_payload(
                    field_value, depth + 1, wire_version=wire_version, legacy_key_encoding=legacy_key_encoding,
                )

        return result

    # List
    if isinstance(payload, list):
        return [
            _serialize_payload(v, depth + 1, wire_version=wire_version, legacy_key_encoding=legacy_key_encoding)
            for v in payload
        ]

    # Dict
    if isinstance(payload, dict):
        if wire_version == 2:
            return {
                "__type__": "plain_dict",
                "items": [[str(k), _serialize_payload(v, depth + 1, wire_version=2)] for k, v in payload.items()],
            }
        if legacy_key_encoding == "literal" and ("__class__" in payload or payload.get("__type__") == "datetime"):
            raise ValueError("Payload conflicts with legacy MQ markers; upgrade receivers and select wire_version=2")
        new_dict = {}
        for k, v in payload.items():
            if legacy_key_encoding == "escaped" and isinstance(k, str):
                if k in ("__class__", "__type__") or k.startswith(_ESCAPE_PREFIX):
                    k = _ESCAPE_PREFIX + k
            new_dict[k] = _serialize_payload(
                v, depth + 1, wire_version=wire_version, legacy_key_encoding=legacy_key_encoding,
            )
        return new_dict

    # Primitives (str, int, float, bool, None, etc.)
    return payload


def deserialize_message(
    data: bytes,
    *,
    legacy_key_encoding: Literal["literal", "escaped"] = "literal",
) -> DmqRequestMessage | DmqResponseMessage:
    """Read v2 envelopes or legacy data using the explicitly configured key format.

    Unversioned literal and escaped messages cannot be distinguished by their
    content. Select escaped only for queues known to contain escaped messages.
    """
    if legacy_key_encoding not in ("literal", "escaped"):
        raise ValueError(f"Unsupported legacy key encoding: {legacy_key_encoding}")
    raw = json.loads(data.decode("utf-8"))
    if isinstance(raw, dict) and raw.get("__class__") == "DmqWireEnvelope":
        if raw.get("version") != 2:
            raise ValueError(f"Unsupported MQ wire version: {raw.get('version')}")
        return _deserialize_payload(raw["message"], depth=0, wire_version=2)
    return _deserialize_payload(raw, depth=0, legacy_key_encoding=legacy_key_encoding)


def _json_deserializer(obj):
    """Custom JSON deserializer for datetime objects"""
    if isinstance(obj, dict) and obj.get("__type__") == "datetime":
        return datetime.datetime.fromisoformat(obj["value"])
    return obj


def _deserialize_payload(
    payload: Any,
    depth: int,
    *,
    wire_version: int = 1,
    legacy_key_encoding: str = "literal",
) -> Any:
    """Deserialize payload recursively with depth limit."""
    if depth > MAX_RECURSE_DEPTH:
        raise RecursionError(f"Payload nested too deep (> {MAX_RECURSE_DEPTH})")

    if wire_version == 2 and isinstance(payload, dict):
        if payload.get("__type__") == "plain_dict":
            return {
                key: _deserialize_payload(value, depth + 1, wire_version=2)
                for key, value in payload["items"]
            }
        if payload.get("__type__") == "datetime":
            return datetime.datetime.fromisoformat(payload["value"])

    if wire_version == 1 and isinstance(payload, dict) and payload.get("__type__") == "datetime":
        return datetime.datetime.fromisoformat(payload["value"])

    # Dict with __class__ marker - BaseModel object
    if isinstance(payload, dict) and "__class__" in payload:
        class_name = payload.pop("__class__")
        if class_name not in TYPE_REGISTRY:
            raise ValueError(f"Unknown payload class: {class_name}")

        payload_class = TYPE_REGISTRY[class_name]

        deserialized_fields = {}
        for key, value in payload.items():
            deserialized_fields[key] = _deserialize_payload(
                value, depth + 1, wire_version=wire_version, legacy_key_encoding=legacy_key_encoding,
            )

        return payload_class.model_validate(deserialized_fields)

    # List
    if isinstance(payload, list):
        return [
            _deserialize_payload(v, depth + 1, wire_version=wire_version, legacy_key_encoding=legacy_key_encoding)
            for v in payload
        ]

    # Plain dict (no __class__ marker)
    if isinstance(payload, dict):
        new_dict = {}
        for k, v in payload.items():
            if legacy_key_encoding == "escaped" and isinstance(k, str) and k.startswith(_ESCAPE_PREFIX):
                k = k[len(_ESCAPE_PREFIX):]
            new_dict[k] = _deserialize_payload(
                v, depth + 1, wire_version=wire_version, legacy_key_encoding=legacy_key_encoding,
            )
        return new_dict

    # Primitives
    return payload
