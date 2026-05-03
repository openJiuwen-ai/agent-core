# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prompt/completion redaction utilities.

Default behaviour: pass-through with optional length cap. When
``redact_prompts`` / ``redact_completions`` is enabled, the value is
replaced with a SHA-256 prefix so trace consumers can correlate
identical inputs without seeing the content.
"""

from __future__ import annotations

import hashlib

from openjiuwen.agent_teams.observability.config import ObservabilityConfig


_REDACTED_PREFIX = "sha256:"


def _truncate(value: str, max_length: int) -> str:
    """Hard-cap string length and signal truncation."""
    if max_length <= 0 or len(value) <= max_length:
        return value
    return value[:max_length] + f"...<truncated {len(value) - max_length} chars>"


def _hash(value: str) -> str:
    """Replace the value with a short content hash for correlation."""
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
    return f"{_REDACTED_PREFIX}{digest[:16]}"


def redact_prompt(value: object, config: ObservabilityConfig) -> str:
    """Apply redaction policy to a prompt fragment.

    Always returns a string so the value can be stored as a span attribute.

    Args:
        value: Original prompt content (any type; coerced to str).
        config: Active observability configuration.
    """
    text = "" if value is None else str(value)
    if config.redact_prompts:
        return _hash(text)
    return _truncate(text, config.attribute_value_max_length)


def redact_completion(value: object, config: ObservabilityConfig) -> str:
    """Apply redaction policy to a completion fragment.

    Args:
        value: Original completion content (any type; coerced to str).
        config: Active observability configuration.
    """
    text = "" if value is None else str(value)
    if config.redact_completions:
        return _hash(text)
    return _truncate(text, config.attribute_value_max_length)
