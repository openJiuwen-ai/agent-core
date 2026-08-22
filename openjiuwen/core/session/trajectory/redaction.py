# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Best-effort secret redaction for persisted trajectory payloads.

Trajectory files capture full prompts, tool arguments, and outputs, which
routinely contain credentials (auth headers, api keys in tool args). Unlike
the logging pipeline — which blanks whole fields via
``sanitize_event_for_logging`` — trajectories exist to preserve content, so
redaction here is pattern-based: known secret-shaped substrings are replaced
in place and the surrounding text is kept.

This is defense in depth, not a guarantee: novel secret formats pass
through. Redaction is always on — trajectory files must not contain
secrets, so there is deliberately no way to disable it.
"""
import re

REDACTED = "<REDACTED>"

# key: value / key=value pairs whose key names a credential. The value char
# class stops at quotes/whitespace/JSON delimiters so surrounding structure
# survives.
_KEY_VALUE = re.compile(
    r"""(?ix)
    (["']?)
    (api[_-]?key | authorization | access[_-]?token | refresh[_-]?token
     | client[_-]?secret | secret | password | passwd | credentials?
     | session[_-]?token | private[_-]?key | cookie | token)
    (["']?  \s* [:=] \s* ["']?)
    ([^"'\s,}{\]\[&]+)
    """,
)

# Standalone bearer tokens in header-like text.
_BEARER = re.compile(r"(?i)\b(bearer\s+)[a-z0-9._+/=-]{8,}")

# Well-known key formats: OpenAI/Stripe-style prefixes, GitHub tokens,
# Slack tokens, AWS access key ids.
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{16,}\b"
    r"|\bxox[baprs]-[A-Za-z0-9-]{10,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
)


def redact_text(text: str | None) -> str | None:
    """Replace secret-shaped substrings in `text` with ``<REDACTED>``.

    The bearer pass runs before the key-value pass: in
    ``Authorization: Bearer <tok>`` the key-value pattern would otherwise
    consume the word ``Bearer`` as the value and leave the token behind.
    """
    if not text:
        return text
    text = _BEARER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text)
    text = _KNOWN_TOKEN.sub(REDACTED, text)
    return text
