# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Common utilities for AIGC decorators."""

import json

# Default fmtid for custom properties
DEFAULT_CUSTOM_PROPERTY_FMTID = "D5CDD505-2E9C-101B-9397-08002B2CF9AE"


AIGC_REQUIRED_FIELDS = [
    "Label",
    "ContentProducer",
    "ProduceID",
    "ContentPropagator",
    "PropagateID",
]


def is_aigc_complete(data: dict) -> bool:
    """Return True if all required AIGC fields are present and non-empty."""
    if not isinstance(data, dict):
        return False
    if data.get("Label") not in ("1", "2", "3"):
        return False
    for field in AIGC_REQUIRED_FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
    for field in ("ReservedCode1", "ReservedCode2"):
        if field in data and not isinstance(data[field], str):
            return False
    return True


def parse_aigc_json(raw: str) -> dict | None:
    """Parse an AIGC JSON string.

    Handles both flat format:
        {"Label": "1", "ProduceID": "...", ...}
    and wrapped format (used in image EXIF):
        {"AIGC": {"Label": "1", "ProduceID": "...", ...}}

    Returns None if parsing fails or input is not a string.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw, object_pairs_hook=unique_json_object)
        if isinstance(parsed, dict):
            # Handle wrapped format
            if "AIGC" in parsed and isinstance(parsed["AIGC"], dict):
                return parsed["AIGC"]
            return parsed
        return None
    except (ValueError, TypeError):
        return None


AIGC_FIELDS = tuple(AIGC_REQUIRED_FIELDS) + ("ReservedCode1", "ReservedCode2")


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def read_pos_json(raw: bytes):
    """Read a ProcessOn JSON part, preserving duplicate-key detection."""
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_json_object)


def canonical_signature(raw, fallback_signature):
    """Write flat seven-field JSON; retain an existing valid identity."""
    data = raw if isinstance(raw, dict) else parse_aigc_json(raw)
    if not is_aigc_complete(data):
        return fallback_signature
    data = dict(data)
    data.setdefault("ReservedCode1", "")
    data.setdefault("ReservedCode2", "")
    return json.dumps(data, ensure_ascii=False)


def select_signature(values, fallback_signature):
    """Do not silently discard conflicting complete provenance records."""
    complete = []
    for raw in values:
        data = raw if isinstance(raw, dict) else parse_aigc_json(raw)
        if is_aigc_complete(data):
            complete.append(json.loads(canonical_signature(data, fallback_signature)))
    if complete and any(data != complete[0] for data in complete[1:]):
        raise ValueError("Conflicting AIGC provenance records")
    return canonical_signature(complete[0] if complete else None, fallback_signature)
