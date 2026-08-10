# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Canonical immutable trajectory value object.

The durable representation is ordinary OTLP JSON.  ``Trajectory`` owns a
private deep copy of that payload and exposes only detached projections, so
neither input mappings nor values returned to callers can mutate a snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from openjiuwen.agent_evolving.trajectory.schema import (
    MEMBER_ID,
    SESSION_ID,
    TEAM_ID,
    TRAJECTORY_ID,
)


def _copy_json(value: Any) -> Any:
    """Return a detached copy while normalising mapping implementations."""

    if isinstance(value, Mapping):
        return {str(key): _copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if isinstance(value, tuple):
        return [_copy_json(item) for item in value]
    return deepcopy(value)


def _decode_otlp_value(value: Any) -> Any:
    """Decode the JSON representation of an OTLP ``AnyValue``."""

    if not isinstance(value, Mapping):
        return _copy_json(value)
    if "stringValue" in value:
        return _copy_json(value["stringValue"])
    if "boolValue" in value:
        return _copy_json(value["boolValue"])
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return _copy_json(value["intValue"])
    if "doubleValue" in value:
        return _copy_json(value["doubleValue"])
    if "bytesValue" in value:
        return _copy_json(value["bytesValue"])
    if "arrayValue" in value:
        array_value = value["arrayValue"]
        values = array_value.get("values", []) if isinstance(array_value, Mapping) else []
        return [_decode_otlp_value(item) for item in values or []]
    if "kvlistValue" in value:
        kvlist_value = value["kvlistValue"]
        values = kvlist_value.get("values", []) if isinstance(kvlist_value, Mapping) else []
        return {
            str(item.get("key")): _decode_otlp_value(item.get("value", {}))
            for item in values or []
            if isinstance(item, Mapping) and item.get("key") is not None
        }
    return _copy_json(value)


def _resource_attributes(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read the first resource attribute set from a canonical OTLP payload."""

    resource_spans = payload.get("resourceSpans") or []
    for resource_span in resource_spans:
        if not isinstance(resource_span, Mapping):
            continue
        resource = resource_span.get("resource") or {}
        if not isinstance(resource, Mapping):
            continue
        attributes = resource.get("attributes") or []
        if isinstance(attributes, Mapping):
            return {str(key): _decode_otlp_value(value) for key, value in attributes.items()}
        result: dict[str, Any] = {}
        for item in attributes:
            if not isinstance(item, Mapping) or item.get("key") is None:
                continue
            result[str(item["key"])] = _decode_otlp_value(item.get("value", {}))
        if result:
            return result
    return {}


def _first_value(attributes: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in attributes and attributes[key] is not None:
            return attributes[key]
    return None


def _as_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_otlp_value(value: Any) -> dict[str, Any]:
    """Encode simple resource metadata as an OTLP ``AnyValue``."""

    value = _copy_json(value)
    if value is None:
        return {}
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_to_otlp_value(item) for item in value]}}
    if isinstance(value, Mapping):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(key), "value": _to_otlp_value(item)} for key, item in value.items() if item is not None
                ]
            }
        }
    return {"stringValue": str(value)}


class Trajectory:
    """Immutable value object owning one canonical OTLP trajectory payload."""

    __slots__ = ("_payload", "_sealed")

    def __init__(
        self,
        payload: Mapping[str, object],
        *,
        _allow_missing_session: bool = False,
    ) -> None:
        if not isinstance(payload, Mapping):
            raise TypeError("trajectory payload must be a mapping")

        resource_spans = payload.get("resourceSpans")
        if not isinstance(resource_spans, list) or not resource_spans:
            raise ValueError("trajectory payload must contain non-empty resourceSpans")
        if any(not isinstance(resource_span, Mapping) for resource_span in resource_spans):
            raise ValueError("resourceSpans entries must be mappings")

        attributes = _resource_attributes(payload)
        if not attributes.get(TRAJECTORY_ID):
            raise ValueError("trajectory payload requires a non-empty trajectory_id")

        if not _allow_missing_session:
            has_team_or_member = any(key in attributes for key in (TEAM_ID, MEMBER_ID))
            has_session = SESSION_ID in attributes
            if has_team_or_member and not has_session:
                raise ValueError("team_id/member_id requires session_id")

        object.__setattr__(self, "_payload", _copy_json(payload))
        object.__setattr__(self, "_sealed", True)

    @classmethod
    def from_otlp(cls, payload: Mapping[str, object]) -> "Trajectory":
        """Create a trajectory while taking ownership of a detached payload."""

        return cls(payload)

    @classmethod
    def from_historical_otlp(cls, payload: Mapping[str, object]) -> "Trajectory":
        """Read a historical payload whose session identity may be absent."""

        return cls(payload, _allow_missing_session=True)

    def to_otlp(self) -> dict[str, object]:
        """Return an independent JSON-like copy of the canonical payload."""

        return _copy_json(self._payload)

    @property
    def resource_attributes(self) -> dict[str, Any]:
        """Return decoded resource attributes as a detached mapping."""

        return _resource_attributes(self._payload)

    @property
    def trajectory_id(self) -> str:
        return str(self.resource_attributes[TRAJECTORY_ID])

    @property
    def session_id(self) -> str | None:
        return _as_optional_string(
            _first_value(
                self.resource_attributes,
                SESSION_ID,
            )
        )

    @property
    def team_id(self) -> str | None:
        return _as_optional_string(
            _first_value(
                self.resource_attributes,
                TEAM_ID,
            )
        )

    @property
    def member_id(self) -> str | None:
        return _as_optional_string(
            _first_value(
                self.resource_attributes,
                MEMBER_ID,
            )
        )

    @property
    def metadata(self) -> dict[str, Any]:
        """Alias for detached resource metadata used by Store query code."""

        return self.resource_attributes

    def with_resource_attributes(self, attributes: Mapping[str, Any]) -> "Trajectory":
        """Return a new trajectory with resource attributes merged in."""

        if not isinstance(attributes, Mapping):
            raise TypeError("attributes must be a mapping")
        payload = self.to_otlp()
        resource_spans = payload["resourceSpans"]
        resource_span = resource_spans[0]
        resource = resource_span.setdefault("resource", {})
        encoded_attributes = resource.setdefault("attributes", [])
        if isinstance(encoded_attributes, Mapping):
            encoded_attributes = [
                {"key": str(key), "value": _to_otlp_value(value)} for key, value in encoded_attributes.items()
            ]
            resource["attributes"] = encoded_attributes

        by_key = {
            item.get("key"): item
            for item in encoded_attributes
            if isinstance(item, Mapping) and item.get("key") is not None
        }
        for key, value in attributes.items():
            if value is None:
                continue
            item = {"key": str(key), "value": _to_otlp_value(value)}
            if str(key) in by_key:
                by_key[str(key)].update(item)
            else:
                encoded_attributes.append(item)
                by_key[str(key)] = item
        return type(self).from_otlp(payload)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("Trajectory is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("Trajectory is immutable")
        object.__delattr__(self, name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Trajectory):
            return NotImplemented
        return self._payload == other._payload

    def __repr__(self) -> str:
        return f"Trajectory(trajectory_id={self.trajectory_id!r}, spans={len(self._payload['resourceSpans'])})"


__all__ = ["Trajectory"]
