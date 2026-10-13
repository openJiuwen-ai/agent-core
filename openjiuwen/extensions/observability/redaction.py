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
import re

from openjiuwen.extensions.observability.config import ObservabilityConfig


_REDACTED_PREFIX = "sha256:"

# Error summaries stay readable diagnostics: masked in place, never hashed as
# a whole, and capped well below the general attribute cap so the trajectory
# status line stays compact.
_ERROR_SUMMARY_MAX_LENGTH = 4096

_SECRET_MASK = "***"

# ``key=value`` / ``key: value`` shapes, quoted or bare, including a leading
# bearer scheme so "authorization: Bearer <token>" is masked as one unit.
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"session[_-]?token|id[_-]?token|cookie|password|passwd|secret|"
    r"client[_-]?secret|private[_-]?key)"
    r"(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|(?:bearer\s+)?[^\s,;&\"]+)"
)
# Standalone "Bearer <token>" without a labeled key.
_BEARER_TOKEN_RE = re.compile(
    r"(?i)\bbearer\s+([a-z0-9._~+/=-]{8,})"
)


def _mask_sensitive_text(text: str) -> str:
    """Mask credential-shaped fragments while keeping the surrounding text."""

    def _mask_assignment(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}{_SECRET_MASK}"

    text = _SENSITIVE_ASSIGNMENT_RE.sub(_mask_assignment, text)
    return _BEARER_TOKEN_RE.sub(f"Bearer {_SECRET_MASK}", text)


def redact_error_summary(value: object, config: ObservabilityConfig | None) -> str:
    """Condense an error cause into a masked, single-line, bounded summary.

    This is the text the trajectory UI shows next to a Failed status. It is
    deliberately not hash-based: the reason must stay readable, so only
    credential-shaped fragments are masked in place.

    Args:
        value: The raw error reason text (any type; coerced to str).
        config: Active observability configuration; None keeps the summary cap.
    """
    text = _mask_sensitive_text("" if value is None else str(value))
    text = " ".join(text.split())
    max_length = (
        config.attribute_value_max_length
        if config is not None
        else _ERROR_SUMMARY_MAX_LENGTH
    )
    return truncate(text, min(max_length, _ERROR_SUMMARY_MAX_LENGTH))


def redact_error_stacktrace(value: object, config: ObservabilityConfig | None) -> str:
    """Mask credential-shaped fragments inside a stacktrace and cap its length."""
    text = _mask_sensitive_text("" if value is None else str(value))
    max_length = (
        config.attribute_value_max_length
        if config is not None
        else _ERROR_SUMMARY_MAX_LENGTH
    )
    return truncate(text, max_length)


def truncate(value: str, max_length: int) -> str:
    """Hard-cap an OTel attribute and identify the truncation layer."""
    if max_length <= 0 or len(value) <= max_length:
        return value
    omitted = len(value) - max_length
    return value[:max_length] + f"...<OTel attribute truncated: {omitted} chars omitted>"


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
    return truncate(text, config.attribute_value_max_length)


def redact_system_prompt(value: object, config: ObservabilityConfig) -> str:
    """Protect a system prompt without applying the attribute length cap.

    System instructions are the canonical input for trajectory comparison.
    Truncating them makes the truncation suffix part of their identity and can
    produce false prompt diffs. Explicit privacy redaction still takes
    precedence; otherwise the complete value is retained.
    """
    text = "" if value is None else str(value)
    if config.redact_prompts:
        return _hash(text)
    return text


def redact_completion(value: object, config: ObservabilityConfig) -> str:
    """Apply redaction policy to a completion fragment.

    Args:
        value: Original completion content (any type; coerced to str).
        config: Active observability configuration.
    """
    text = "" if value is None else str(value)
    if config.redact_completions:
        return _hash(text)
    return truncate(text, config.attribute_value_max_length)
