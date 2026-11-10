# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
from typing import Optional
from urllib.parse import urlparse, urlunparse

_USERINFO_PATTERN = re.compile(r"//(?P<userinfo>[^/@\s]+)@")


def _redact_userinfo_regex(url: str) -> str:
    """Best-effort redaction for URLs that ``urlparse`` cannot handle."""

    def _replace(match: "re.Match[str]") -> str:
        userinfo = match.group("userinfo")
        if ":" not in userinfo:
            return match.group(0)
        username = userinfo.split(":", 1)[0]
        redacted = f"{username}:***" if username else ":***"
        return f"//{redacted}@"

    return _USERINFO_PATTERN.sub(_replace, url, count=1)


def redact_url_password(url: Optional[str]) -> Optional[str]:
    """
    Redact password from a URL for safe logging.

    Args:
        url: The URL that may contain credentials.

    Returns:
        URL with password replaced by '***'. If no password present, returns original URL.
        Returns original string if URL parsing fails. Returns None if url is None.

    Examples:
        >>> redact_url_password("redis://:secret@host:6379/0")
        'redis://:***@host:6379/0'
        >>> redact_url_password("redis://user:secret@host:6379/0")
        'redis://user:***@host:6379/0'
        >>> redact_url_password("redis://host:6379/0")
        'redis://host:6379/0'
    """
    if not url:
        return url

    try:
        parsed = urlparse(url)
        # ``parsed.password`` only inspects the userinfo part, so it never touches
        # ``parsed.port`` (which raises ValueError for invalid ports).
        if not parsed.password:
            return url

        # Work on the raw netloc instead of rebuilding it from ``parsed.hostname``
        # and ``parsed.port``: hostname drops IPv6 brackets and port raises
        # ValueError for out-of-range / non-numeric ports.
        userinfo, separator, hostport = parsed.netloc.rpartition("@")
        if not separator:
            return url
        username = userinfo.split(":", 1)[0]
        redacted_userinfo = f"{username}:***" if username else ":***"
        return urlunparse(parsed._replace(netloc=f"{redacted_userinfo}@{hostport}"))
    except Exception:
        # Last-resort regex redaction so that malformed URLs never leak secrets.
        return _redact_userinfo_regex(url)
