"""Frozen role-only native output contract and exact-route capability evidence.

The common provider subset deliberately omits ``minLength``. The independent
runtime validator still requires nonempty feedback; native schema generation is
an additional constraint, never a replacement for validation or finish checks.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .identity import ModelBinding

ROLE_OUTPUT_PROTOCOL = "role_output_enforcement_v1"
ROLE_SCHEMA_ID = "role_status_feedback_v1"
SCHEMA_COUNTING_ID = "native_schema_block_estimate_v1"


def role_response_format() -> dict[str, Any]:
    """Return a fresh schema using the documented common provider subset."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": ROLE_SCHEMA_ID,
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["continue", "complete"]},
                    "feedback": {"type": "string", "description": "Nonempty feedback."},
                },
                "required": ["status", "feedback"],
                "additionalProperties": False,
            },
        },
    }


def response_format_for(kind: str, profile: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The fixed role schema never applies to answer generations or TextGrad."""
    if profile is None:
        return None
    if profile.get("protocol") != ROLE_OUTPUT_PROTOCOL or profile.get("mode") not in {
        "json_schema",
        "prompt_validator",
    }:
        raise ValueError("unknown frozen role output enforcement profile")
    return role_response_format() if kind == "role" and profile["mode"] == "json_schema" else None


def _read_evidence(item: Mapping[str, Any]) -> bytes:
    path = Path(item["path"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
        raise ValueError("role output capability evidence changed")
    return raw


def validate_role_output_profile(profile: Mapping[str, Any] | None, binding: ModelBinding) -> None:
    """Recheck the frozen mode and exact endpoint evidence without network IO."""
    if profile is None:
        # Existing injected/standalone clients explicitly retain local validation;
        # active experiments declare their mode in each backend manifest entry.
        return
    response_format_for("role", profile)
    if profile["mode"] == "prompt_validator":
        if not isinstance(profile.get("reason"), str) or not profile["reason"].strip():
            raise ValueError("prompt-validator role mode requires its declared unsupported-route reason")
        return
    if profile.get("schema_id") != ROLE_SCHEMA_ID or profile.get("schema_counting") != SCHEMA_COUNTING_ID:
        raise ValueError("role schema or native schema counting version differs from the frozen profile")
    catalog = json.loads(_read_evidence(profile["endpoint_catalog"]))["data"]
    routes = [
        row
        for row in catalog["endpoints"]
        if row.get("tag") == binding.provider_endpoint and row.get("provider_name") == binding.served_provider
    ]
    if catalog.get("id") != binding.model or len(routes) != 1 or routes[0].get("model_id") != binding.model:
        raise ValueError("native role schema evidence does not identify the approved exact route")
    if not {"response_format", "structured_outputs"}.issubset(routes[0].get("supported_parameters", ())):
        raise ValueError("the approved endpoint does not document native structured-output support")
    documentation = profile.get("documentation")
    if not isinstance(documentation, list) or len(documentation) < 2:
        raise ValueError("native role schemas require gateway and provider schema-subset documentation")
    for item in documentation:
        if not isinstance(item, Mapping) or not str(item.get("url", "")).startswith("https://"):
            raise ValueError("native role schema documentation needs an explicit official source URL")
        _read_evidence(item)


def schema_counting_messages(schema: Any) -> tuple[dict[str, str], ...]:
    """Estimate charged native schema as an extra fully framed system block.

    This counts the entire wire response_format, not only its inner properties.
    The existing backend factor and safety allowance also apply to this block.
    Providers do not expose their injected format instructions, so this remains
    an estimate with a fatal native-count bound check, not an exact tokenizer.
    """
    if schema is None:
        return ()
    if schema != role_response_format():
        raise ValueError("only the registered complete role response_format has a native schema counting profile")
    return ({"role": "system", "content": json.dumps(schema, ensure_ascii=False, sort_keys=True)},)
