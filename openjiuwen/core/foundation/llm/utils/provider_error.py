# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize provider SDK / HTTP errors for FrameworkError and logs.

Provider catch blocks must not dump raw HTML/XML response bodies into
``error_msg``. Ordinary diagnostics (timeouts, connection errors, JSON API
errors, locally built messages) stay unchanged.
"""

from __future__ import annotations

import html
import re
from typing import Any, Optional

MAX_PROVIDER_ERROR_CHARS = 4000

_MARKUP_RE = re.compile(r"<!doctype\s+html|<html(?:\s|>)|<\?xml", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_STATUS_IN_TEXT_RE = re.compile(
    r"(?:error\s+code|status(?:\s+code)?|http)\D{0,16}([1-5]\d{2})\b",
    re.IGNORECASE,
)
_HEADING_STATUS_RE = re.compile(r"\b([1-5]\d{2})\b")


def format_provider_exception(exc: BaseException, *, include_exc_type: bool = True) -> str:
    """Format a provider exception without flooding HTML/XML response bodies.

    Empty messages still surface the exception type, matching the previous
    ``TimeoutError`` / ``ReadError`` behavior used by stream-timeout and
    retry classifiers. HTTP status is optional: timeouts and transport
    failures keep their original text even when no status exists.
    """
    raw = str(exc).strip()
    if not raw:
        return type(exc).__name__
    summarized = summarize_provider_error_text(raw, status_code=_extract_http_status(exc))
    if include_exc_type:
        return f"{type(exc).__name__}: {summarized}"
    return summarized


def summarize_provider_error_text(text: str, *, status_code: Optional[int] = None) -> str:
    """Return a user-facing error body, summarizing HTML/XML pages."""
    raw = str(text or "")
    if not raw:
        return ""
    markup = _markup_payload(raw)
    if markup is not None:
        inferred = status_code or _extract_status_from_text(raw)
        return _summarize_markup(markup, status_code=inferred)
    if len(raw) > MAX_PROVIDER_ERROR_CHARS:
        overflow = len(raw) - MAX_PROVIDER_ERROR_CHARS
        return f"{raw[:MAX_PROVIDER_ERROR_CHARS]}... [truncated {overflow} chars]"
    return raw


def _extract_http_status(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "status"):
        parsed = _as_http_status(getattr(exc, attr, None))
        if parsed is not None:
            return parsed
    response = getattr(exc, "response", None)
    if response is not None:
        parsed = _as_http_status(getattr(response, "status_code", None))
        if parsed is not None:
            return parsed
    return None


def _as_http_status(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 100 <= value <= 599:
        return value
    if isinstance(value, str) and value.isdigit():
        code = int(value)
        if 100 <= code <= 599:
            return code
    return None


def _extract_status_from_text(text: str) -> Optional[int]:
    match = _STATUS_IN_TEXT_RE.search(text)
    if match is None:
        return None
    return _as_http_status(int(match.group(1)))


def _markup_payload(text: str) -> Optional[str]:
    match = _MARKUP_RE.search(text)
    if match is None:
        return None
    start = match.start()
    if start > 160:
        return None
    markup = text[start:]
    if not _is_error_page(markup):
        return None
    return markup


def _is_error_page(markup: str) -> bool:
    sample = markup.lstrip()[:8000].lower()
    if sample.startswith("<?xml"):
        return "<html" in sample or "<fault" in sample or len(markup) >= 512
    if not (sample.startswith("<!doctype html") or sample.startswith("<html")):
        return False
    return (
        any(token in sample for token in ("<head", "<body", "</html>", "<title", "<h1"))
        or len(markup) >= 256
    )


def _summarize_markup(markup: str, *, status_code: Optional[int]) -> str:
    heading = _extract_heading(markup)
    inferred = status_code or _status_from_heading(heading)
    kind = "HTML" if _looks_like_html(markup) else "XML"
    page = f"{kind} error page"
    if heading:
        page = f'{kind} error page "{heading}"'
    parts: list[str] = []
    if inferred is not None:
        parts.append(f"HTTP {inferred}")
    parts.append(f"{page} ({len(markup)} chars)")
    return (
        f"{', '.join(parts)}; "
        "response body is an HTML/XML document, not an API error payload"
    )


def _looks_like_html(markup: str) -> bool:
    sample = markup.lstrip()[:64].lower()
    return sample.startswith("<!doctype html") or sample.startswith("<html")


def _extract_heading(markup: str) -> str:
    for pattern in (_TITLE_RE, _H1_RE):
        match = pattern.search(markup)
        if match is None:
            continue
        heading = _plain_text(match.group(1))
        if heading:
            return heading[:120]
    return ""


def _status_from_heading(heading: str) -> Optional[int]:
    if not heading:
        return None
    match = _HEADING_STATUS_RE.search(heading)
    if match is None:
        return None
    return _as_http_status(int(match.group(1)))


def _plain_text(value: str) -> str:
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return " ".join(text.split()).replace('"', "'")
