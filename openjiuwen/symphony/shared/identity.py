"""Stable, recursively sanitized identity metadata helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_PARTS = frozenset({"authorization", "credential", "password", "secret", "token"})
_SAFE_KEY_FIELDS = frozenset({"cache_key", "key_type", "partition_key", "public_key", "routing_key"})
_CREDENTIAL_KEY_PREFIXES = frozenset(
    {"access", "api", "consumer", "encryption", "private", "secret", "signing", "subscription"}
)
_CREDENTIAL_TOKEN_PREFIXES = frozenset({"access", "api", "auth", "bearer", "id", "refresh", "session"})
_SAFE_TOKEN_FIELDS = frozenset(
    {
        "max_tokens",
        "min_tokens",
        "reasoning_token_budget",
        "return_token_ids",
        "token_count",
        "tokenizer_name",
    }
)
_TOKEN_QUANTITY_PARTS = frozenset(
    {
        "accepted",
        "cached",
        "completion",
        "generated",
        "input",
        "max",
        "min",
        "output",
        "prompt",
        "reasoning",
        "rejected",
        "total",
    }
)
_ENDPOINT_FIELDS = frozenset({"api_base", "api_url", "base_url", "endpoint", "endpoint_url"})


def sanitize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Remove credentials recursively and replace endpoint values with normalized hashes."""

    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        text_key = str(key)
        normalized_key = normalize_metadata_key(text_key)
        if _is_token_field(normalized_key):
            if normalized_key == "token" or _is_credential_token(normalized_key):
                continue
            if _is_safe_token_field(normalized_key):
                sanitized[text_key] = _sanitize_value(value)
            else:
                sanitized[f"{text_key}_sha256"] = stable_metadata_sha256(value)
            continue
        if _is_sensitive_key(normalized_key):
            continue
        if _is_key_field(normalized_key):
            if normalized_key in _SAFE_KEY_FIELDS:
                sanitized[text_key] = _sanitize_value(value)
            elif _is_credential_key(normalized_key):
                continue
            else:
                sanitized[f"{text_key}_sha256"] = stable_metadata_sha256(value)
            continue
        if normalized_key in _ENDPOINT_FIELDS:
            if value is not None:
                sanitized[f"{text_key}_sha256"] = endpoint_sha256(str(value))
            continue
        sanitized[text_key] = _sanitize_value(value)
    return sanitized


def stable_metadata_sha256(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def endpoint_sha256(value: str) -> str:
    return hashlib.sha256(_normalize_endpoint(value).encode("utf-8")).hexdigest()


def normalize_metadata_key(key: str) -> str:
    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    return re.sub(r"[^a-zA-Z0-9]+", "_", with_boundaries).strip("_").lower()


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return sanitize_metadata(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    return value


def _is_sensitive_key(normalized_key: str) -> bool:
    parts = {part for part in normalized_key.split("_") if part}
    for part in parts:
        singular = part[:-1] if part.endswith("s") else part
        if singular in _SENSITIVE_PARTS:
            return True
        if any(fragment in part for fragment in ("authorization", "credential", "password", "secret", "token")):
            return True
    return False


def _is_key_field(normalized_key: str) -> bool:
    return (
        normalized_key == "key"
        or normalized_key.endswith("_key")
        or any(normalized_key == f"{prefix}key" for prefix in _CREDENTIAL_KEY_PREFIXES)
    )


def _is_credential_key(normalized_key: str) -> bool:
    compact = normalized_key.replace("_", "")
    return any(compact == f"{prefix}key" for prefix in _CREDENTIAL_KEY_PREFIXES)


def _is_token_field(normalized_key: str) -> bool:
    return any("token" in part for part in normalized_key.split("_") if part)


def _is_credential_token(normalized_key: str) -> bool:
    compact = normalized_key.replace("_", "")
    if any(compact == f"{prefix}token" for prefix in _CREDENTIAL_TOKEN_PREFIXES):
        return True
    parts = normalized_key.split("_")
    return any(
        parts[index] in _CREDENTIAL_TOKEN_PREFIXES and parts[index + 1] in {"token", "tokens"}
        for index in range(len(parts) - 1)
    )


def _is_safe_token_field(normalized_key: str) -> bool:
    if normalized_key in _SAFE_TOKEN_FIELDS:
        return True
    parts = normalized_key.split("_")
    if any(part.startswith("tokenizer") for part in parts):
        return True
    if normalized_key.endswith(
        (
            "_token_budget",
            "_token_budgets",
            "_token_count",
            "_token_counts",
            "_token_ids",
            "_token_length",
            "_token_limit",
        )
    ):
        return True
    if normalized_key.startswith(("disable_token_", "enable_token_", "include_token_", "return_token_")):
        return True
    return "tokens" in parts and bool(set(parts) & _TOKEN_QUANTITY_PARTS)


def _normalize_endpoint(value: str) -> str:
    text = value.strip()
    try:
        parsed = urlsplit(text)
        if not parsed.scheme or not parsed.hostname:
            return text.rstrip("/")
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower()
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = parsed.port
        if port is not None and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
            hostname = f"{hostname}:{port}"
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
        return urlunsplit((scheme, hostname, parsed.path.rstrip("/"), query, ""))
    except ValueError:
        return text.rstrip("/")
