# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM-safe tool name sanitization for function-calling APIs.

OpenAI-compatible chat APIs restrict tool function names to
``^[a-zA-Z0-9_-]{1,64}$`` (OpenAI function calling spec). MCP itself allows
dots in tool names (2025-11-25 spec: ``A-Za-z0-9_-.``), so a connector tool
named ``aippt.doc_beautify`` is legal at the MCP layer but is rejected by
strict providers (OpenAI official API, DeepSeek, ...) with
``400 Invalid 'tools[N].function.name'`` — failing the whole request.

The sanitizer converts any tool name into an LLM-safe name:

- already-compliant names pass through unchanged (idempotent);
- illegal characters collapse to ``_``;
- overlong or colliding names get a deterministic digest suffix.

The original name must stay recoverable: ToolCard ids and names keep the raw
form registered by hosts; AbilityManager sanitizes only the model-facing
ToolInfo names and resolves the sanitized forms back to raw keys on execute,
so MCP invocation keeps using the raw name.
"""
import hashlib
import re
from typing import Iterable, Optional

# OpenAI function-name rule: letters, digits, underscore, hyphen; <= 64 chars.
LLM_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_ILLEGAL_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_LLM_TOOL_NAME_LEN = 64
_DIGEST_WIDTH = 8


def is_llm_safe_tool_name(name: str) -> bool:
    """True when ``name`` can be sent to OpenAI-compatible function calling."""
    return bool(name) and bool(LLM_TOOL_NAME_PATTERN.fullmatch(str(name)))


def sanitize_llm_tool_name(name: str, taken: Optional[Iterable[str]] = None) -> str:
    """Convert any tool name into an LLM-safe function name (idempotent, unique).

    Args:
        name: Original tool name; may contain dots, CJK, spaces, etc.
        taken: Names that must not be produced (existing raw keys plus already
            assigned LLM names). Pass a live set and keep it updated between
            calls so every produced name is globally unique.

    Returns:
        A name matching ``^[a-zA-Z0-9_-]{1,64}$``. Compliant inputs are
        returned unchanged; illegal characters collapse to ``_``; collisions
        and overlong names are disambiguated with a digest suffix derived
        from the original name, so the mapping is stable across requests.
    """
    occupied = set(taken) if taken else set()
    normalized = str(name or "").strip()
    if is_llm_safe_tool_name(normalized) and normalized not in occupied:
        return normalized
    cleaned = _ILLEGAL_CHARS.sub("_", normalized)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_") or "tool"
    if is_llm_safe_tool_name(cleaned) and cleaned not in occupied:
        return cleaned
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    base = cleaned[:_MAX_LLM_TOOL_NAME_LEN - _DIGEST_WIDTH - 1]
    candidate = f"{base}_{digest[:_DIGEST_WIDTH]}"
    counter = 0
    while candidate in occupied and counter < 0xFF:
        # Practically unreachable (digest of a distinct original name); the
        # counter only guarantees termination with pathological inputs.
        candidate = f"{base}_{digest[:_DIGEST_WIDTH - 2]}{counter:02x}"
        counter += 1
    return candidate
