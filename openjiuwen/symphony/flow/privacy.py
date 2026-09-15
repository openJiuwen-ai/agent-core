"""Privacy helpers for distilled text and installable package materials."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from openjiuwen.symphony.models._redaction import redact_sensitive_json, redact_sensitive_text

_MAX_DISTILLED_TEXT_LENGTH = 2_000
_COMMON_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)


def redact_common_credentials(value: str) -> str:
    """Redact common bare credentials that do not carry a field name."""

    output = value
    for pattern in _COMMON_CREDENTIAL_PATTERNS:
        output = pattern.sub("<redacted-credential>", output)
    return output


def sanitize_distilled_text(value: object, *, source_queries: Iterable[str] = ()) -> str:
    """Redact credentials and verbatim source requests from model-derived text."""

    text = redact_common_credentials(redact_sensitive_text(str(value or "").strip()))
    blocked_candidates: set[str] = set()
    for query in source_queries:
        normalized_query = str(query).strip()
        for candidate in (normalized_query, redact_sensitive_text(normalized_query)):
            if candidate:
                blocked_candidates.add(candidate)
    blocked = sorted(blocked_candidates, key=len, reverse=True)
    for candidate in blocked:
        text = text.replace(candidate, "<redacted-request>")
    return text[:_MAX_DISTILLED_TEXT_LENGTH]


def redact_private_json(value: Any) -> Any:
    """Recursively redact named and common bare credentials from JSON-like data."""

    redacted = redact_sensitive_json(value)
    if isinstance(redacted, Mapping):
        return {str(key): redact_private_json(item) for key, item in redacted.items()}
    if isinstance(redacted, Sequence) and not isinstance(redacted, (str, bytes, bytearray)):
        return [redact_private_json(item) for item in redacted]
    if isinstance(redacted, str):
        return redact_common_credentials(redacted)
    return redacted


def model_response_text(value: Any) -> str:
    """Extract text from the minimal response shapes supported by SymphonyLLM."""

    if isinstance(value, str):
        return value
    for field_name in ("parser_content", "content"):
        field_value = getattr(value, field_name, None)
        if isinstance(field_value, str):
            return field_value
    raise ValueError("model returned no text")


__all__ = [
    "model_response_text",
    "redact_common_credentials",
    "redact_private_json",
    "sanitize_distilled_text",
]
