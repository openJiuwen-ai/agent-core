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
) -> bytes:
    """Serialize using legacy-compatible writes unless version 2 is selected.

    Version 2 wraps plain dictionaries instead of modifying business keys.
    Enable it only after all receivers support it. Legacy writes reject marker
    collisions which old readers cannot represent without loss.
    """
    if wire_version not in (1, 2):
        raise ValueError(f"Unsupported MQ wire version: {wire_version}")
    data = _serialize_payload(msg, depth=0, wire_version=wire_version)
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


def _serialize_payload(payload: Any, depth: int, *, wire_version: int = 1) -> Any:
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
            result[field_name] = _serialize_payload(field_value, depth + 1, wire_version=wire_version)

        if hasattr(payload, '__pydantic_extra__') and payload.__pydantic_extra__:
            for field_name, field_value in payload.__pydantic_extra__.items():
                result[field_name] = _serialize_payload(field_value, depth + 1, wire_version=wire_version)

        return result

    # List
    if isinstance(payload, list):
        return [_serialize_payload(v, depth + 1, wire_version=wire_version) for v in payload]

    # Dict
    if isinstance(payload, dict):
        if wire_version == 2:
            return {
                "__type__": "plain_dict",
                "items": [[str(k), _serialize_payload(v, depth + 1, wire_version=2)] for k, v in payload.items()],
            }
        if "__class__" in payload or payload.get("__type__") == "datetime":
            raise ValueError("Payload conflicts with legacy MQ markers; upgrade receivers and select wire_version=2")
        new_dict = {}
        for k, v in payload.items():
            new_dict[k] = _serialize_payload(v, depth + 1, wire_version=wire_version)
        return new_dict

    # Primitives (str, int, float, bool, None, etc.)
    return payload


def deserialize_message(data: bytes) -> DmqRequestMessage | DmqResponseMessage:
    """Read legacy messages literally or decode an explicit versioned envelope."""
    raw = json.loads(data.decode("utf-8"))
    if isinstance(raw, dict) and raw.get("__class__") == "DmqWireEnvelope":
        if raw.get("version") != 2:
            raise ValueError(f"Unsupported MQ wire version: {raw.get('version')}")
        return _deserialize_payload(raw["message"], depth=0, wire_version=2)
    obj = json.loads(data.decode("utf-8"), object_hook=_json_deserializer)
    return _deserialize_payload(obj, depth=0)


def _json_deserializer(obj):
    """Custom JSON deserializer for datetime objects"""
    if isinstance(obj, dict) and obj.get("__type__") == "datetime":
        return datetime.datetime.fromisoformat(obj["value"])
    return obj


def _deserialize_payload(payload: Any, depth: int, *, wire_version: int = 1) -> Any:
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

    # Dict with __class__ marker - BaseModel object
    if isinstance(payload, dict) and "__class__" in payload:
        class_name = payload.pop("__class__")
        if class_name not in TYPE_REGISTRY:
            raise ValueError(f"Unknown payload class: {class_name}")

        payload_class = TYPE_REGISTRY[class_name]

        deserialized_fields = {}
        for key, value in payload.items():
            deserialized_fields[key] = _deserialize_payload(value, depth + 1, wire_version=wire_version)

        return payload_class.model_validate(deserialized_fields)

    # List
    if isinstance(payload, list):
        return [_deserialize_payload(v, depth + 1, wire_version=wire_version) for v in payload]

    # Plain dict (no __class__ marker)
    if isinstance(payload, dict):
        new_dict = {}
        for k, v in payload.items():
            new_dict[k] = _deserialize_payload(v, depth + 1, wire_version=wire_version)
        return new_dict

    # Primitives
    return payload
