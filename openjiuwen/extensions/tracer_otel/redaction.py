# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prompt/completion redaction utilities for tracer_otel extension.

Lightweight inline version — does NOT depend on
``observability/redaction.py`` to avoid cross-layer coupling.
Logic is aligned with the observability module but receives
``OtelTracerConfig`` instead of ``ObservabilityConfig``.
"""

from __future__ import annotations

import hashlib

from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig


_REDACTED_PREFIX = "sha256:"
_TRUNCATED_SUFFIX = "...<truncated>"


def truncate(value: str, max_length: int) -> str:
    """Hard-cap string length and signal truncation."""
    if max_length <= 0 or len(value) <= max_length:
        return value
    return value[:max_length] + _TRUNCATED_SUFFIX


def hash_value(value: str) -> str:
    """Replace the value with a short content hash for correlation."""
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
    return f"{_REDACTED_PREFIX}{digest[:16]}"


def _should_redact(config: OtelTracerConfig, field: str | None = None) -> bool:
    """Resolve whether redaction should be applied.

    Args:
        config: OtelTracerConfig instance.
        field: "prompts" or "completions" to use the fine-grained override;
               None uses the legacy ``redaction_enabled`` flag.

    ``redact_prompts`` / ``redact_completions`` override ``redaction_enabled``
    when set to True/False.  When they are None, the legacy flag is used.
    """
    if field == "prompts":
        override = config.redact_prompts
        if override is not None:
            return override
    elif field == "completions":
        override = config.redact_completions
        if override is not None:
            return override
    return config.redaction_enabled


def redact(value: object, config: OtelTracerConfig, field: str | None = None) -> str:
    """Apply redaction policy.

    ``field``: "prompts" or "completions" to use the fine-grained override;
    None uses the legacy ``redaction_enabled`` flag.

    When redaction is enabled → SHA-256 hash.
    When redaction is disabled → truncate only.
    Always returns a string.
    """
    text = "" if value is None else str(value)
    if _should_redact(config, field):
        return hash_value(text)
    return truncate(text, config.max_attr_length)
