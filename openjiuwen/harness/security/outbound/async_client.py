# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``aiohttp``-based host exit: every hop (initial URL and each 3xx target) is checked."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import aiohttp

from openjiuwen.harness.security.outbound.policy import check_outbound_url
from openjiuwen.harness.security.outbound.sync_client import (
    DEFAULT_MAX_REDIRECTS,
    strip_auth_on_host_change,
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_BODY_KWARGS = ("data", "json")


def _next_method(status: int, method: str) -> str:
    if status == 303 and method != "HEAD":
        return "GET"
    if status in (301, 302) and method == "POST":
        return "GET"
    return method


async def request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    **kwargs: Any,
) -> aiohttp.ClientResponse:
    """Open a response through the host exit; caller owns it (``async with resp``)."""
    kwargs.pop("allow_redirects", None)
    current_method = method.upper()
    current_url = url
    for _hop in range(max_redirects + 1):
        check_outbound_url(current_url)
        resp = await session.request(current_method, current_url, allow_redirects=False, **kwargs)
        status = resp.status
        location = resp.headers.get("Location")
        if status not in _REDIRECT_STATUSES or not location:
            return resp
        resp.release()
        next_url = urljoin(current_url, location)
        new_method = _next_method(status, current_method)
        if new_method != current_method:
            for key in _BODY_KWARGS:
                kwargs.pop(key, None)
        strip_auth_on_host_change(kwargs, current_url, next_url)
        current_method = new_method
        current_url = next_url
    raise aiohttp.ClientError(f"exceeded {max_redirects} redirects: {url}")


__all__ = ["request"]
