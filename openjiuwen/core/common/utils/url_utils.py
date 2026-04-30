# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional
from urllib.parse import urlparse, urlunparse


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
        if not parsed.password:
            return url

        netloc_parts = []
        if parsed.username:
            netloc_parts.append(parsed.username)
        netloc_parts.append(":***")

        if parsed.hostname:
            netloc_parts.append("@")
            netloc_parts.append(parsed.hostname)
        if parsed.port:
            netloc_parts.append(f":{parsed.port}")

        new_netloc = "".join(netloc_parts)
        redacted = parsed._replace(netloc=new_netloc)
        return urlunparse(redacted)
    except Exception:
        return url
